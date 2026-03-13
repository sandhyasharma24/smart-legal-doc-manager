"""
Document Service
================
All business logic for documents and versions lives here.
Endpoints stay thin – they validate input, call this service, and return.
"""

from typing import List, Optional, Tuple
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, status

from app.models.document import Document, DocumentVersion, User
from app.schemas.document import DocumentCreate, DocumentVersionUpdate, DocumentTitleUpdate
from app.services.diff_service import is_content_significantly_different


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_doc_or_404(db: Session, document_id: int) -> Document:
    doc = (
        db.query(Document)
        .options(joinedload(Document.versions).joinedload(DocumentVersion.created_by_user))
        .filter(Document.id == document_id, Document.is_deleted == False)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


def _next_version_number(db: Session, document_id: int) -> int:
    from sqlalchemy import func
    max_ver = (
        db.query(func.max(DocumentVersion.version_number))
        .filter(DocumentVersion.document_id == document_id)
        .scalar()
    )
    return (max_ver or 0) + 1


# ── Document CRUD ─────────────────────────────────────────────────────────────

def create_document(db: Session, payload: DocumentCreate, current_user: User) -> Document:
    doc = Document(
        title=payload.title,
        owner_id=current_user.id,
    )
    db.add(doc)
    db.flush()  # get doc.id without committing

    version = DocumentVersion(
        document_id=doc.id,
        version_number=1,
        content_text=payload.content_text,
        change_summary=payload.change_summary or "Initial version",
        created_by=current_user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(doc)
    return doc


def list_documents(db: Session, skip: int = 0, limit: int = 50) -> List[Document]:
    return (
        db.query(Document)
        .options(joinedload(Document.versions))
        .filter(Document.is_deleted == False)
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_document(db: Session, document_id: int) -> Document:
    return _get_doc_or_404(db, document_id)


def update_document_title(
    db: Session, document_id: int, payload: DocumentTitleUpdate, current_user: User
) -> Document:
    """Update title only – does NOT create a new version."""
    doc = _get_doc_or_404(db, document_id)
    if doc.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the document owner")
    doc.title = payload.title
    db.commit()
    db.refresh(doc)
    return doc


def add_version(
    db: Session,
    document_id: int,
    payload: DocumentVersionUpdate,
    current_user: User,
) -> Tuple[DocumentVersion, bool, float]:
    """
    Save a new version. Returns (version, is_significant, similarity_percent).
    Raises 409 if content is identical to the latest version.
    """
    doc = _get_doc_or_404(db, document_id)
    latest = doc.latest_version

    # Guard: reject identical content
    if latest and latest.content_text == payload.content_text:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Content is identical to the current version. No new version created.",
        )

    is_sig, similarity = (
        is_content_significantly_different(latest.content_text, payload.content_text)
        if latest else (True, 0.0)
    )

    version = DocumentVersion(
        document_id=doc.id,
        version_number=_next_version_number(db, doc.id),
        content_text=payload.content_text,
        change_summary=payload.change_summary,
        created_by=current_user.id,
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version, is_sig, similarity


def soft_delete_document(db: Session, document_id: int, current_user: User) -> None:
    """Soft-delete the document (and implicitly all its versions from queries)."""
    doc = _get_doc_or_404(db, document_id)
    if doc.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the document owner")
    doc.is_deleted = True
    db.commit()


def hard_delete_document(db: Session, document_id: int, current_user: User) -> None:
    """Permanently remove document + all versions. Requires explicit force flag in endpoint."""
    doc = (
        db.query(Document)
        .filter(Document.id == document_id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the document owner")
    db.query(DocumentVersion).filter(DocumentVersion.document_id == document_id).delete()
    db.delete(doc)
    db.commit()


# ── Version CRUD ──────────────────────────────────────────────────────────────

def list_versions(db: Session, document_id: int) -> List[DocumentVersion]:
    _get_doc_or_404(db, document_id)
    return (
        db.query(DocumentVersion)
        .options(joinedload(DocumentVersion.created_by_user))
        .filter(
            DocumentVersion.document_id == document_id,
            DocumentVersion.is_deleted == False,
        )
        .order_by(DocumentVersion.version_number)
        .all()
    )


def get_version(db: Session, document_id: int, version_number: int) -> DocumentVersion:
    _get_doc_or_404(db, document_id)
    ver = (
        db.query(DocumentVersion)
        .options(joinedload(DocumentVersion.created_by_user))
        .filter(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == version_number,
            DocumentVersion.is_deleted == False,
        )
        .first()
    )
    if not ver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    return ver


def soft_delete_version(
    db: Session, document_id: int, version_number: int, current_user: User
) -> None:
    """Soft-delete a single version. Does NOT affect sibling versions."""
    doc = _get_doc_or_404(db, document_id)
    if doc.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the document owner")

    ver = get_version(db, document_id, version_number)
    active_versions = [v for v in doc.versions if not v.is_deleted]
    if len(active_versions) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the only remaining version. Delete the entire document instead.",
        )
    ver.is_deleted = True
    db.commit()
