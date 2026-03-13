from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.document import User
from app.core.security import get_current_user
from app.schemas.document import (
    DocumentCreate, DocumentOut, DocumentSummary,
    DocumentTitleUpdate, DocumentVersionUpdate,
    VersionOut, VersionSummary, DiffResult,
)
from app.services import document_service
from app.services.diff_service import compute_diff
from app.workers.notification_worker import dispatch_notification

router = APIRouter(prefix="/documents", tags=["Documents"])


# ── helpers for schema projection ────────────────────────────────────────────

def _version_out(ver) -> VersionOut:
    return VersionOut(
        id=ver.id,
        document_id=ver.document_id,
        version_number=ver.version_number,
        content_text=ver.content_text,
        change_summary=ver.change_summary,
        created_by=ver.created_by,
        created_by_username=ver.created_by_user.username if ver.created_by_user else None,
        created_at=ver.created_at,
        is_deleted=ver.is_deleted,
    )


def _doc_out(doc) -> DocumentOut:
    latest = doc.latest_version
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        owner_id=doc.owner_id,
        owner_username=doc.owner.username if doc.owner else None,
        is_deleted=doc.is_deleted,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        version_count=doc.version_count,
        latest_version=_version_out(latest) if latest else None,
    )


# ── Document CRUD ─────────────────────────────────────────────────────────────

@router.post("", response_model=DocumentOut, status_code=201)
def create_document(
    payload: DocumentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new document (auto-saves as version 1)."""
    doc = document_service.create_document(db, payload, current_user)
    return _doc_out(doc)


@router.get("", response_model=List[DocumentOut])
def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all active documents."""
    docs = document_service.list_documents(db, skip, limit)
    return [_doc_out(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = document_service.get_document(db, document_id)
    return _doc_out(doc)


@router.patch("/{document_id}/title", response_model=DocumentOut)
def update_title(
    document_id: int,
    payload: DocumentTitleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update document title WITHOUT creating a new content version."""
    doc = document_service.update_document_title(db, document_id, payload, current_user)
    return _doc_out(doc)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    force: bool = Query(False, description="Set true to permanently delete all versions"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Soft-delete the document (default).
    Use ?force=true to permanently erase the document and all its versions.
    """
    if force:
        document_service.hard_delete_document(db, document_id, current_user)
    else:
        document_service.soft_delete_document(db, document_id, current_user)


# ── Version management ────────────────────────────────────────────────────────

@router.post("/{document_id}/versions", response_model=VersionOut, status_code=201)
def add_version(
    document_id: int,
    payload: DocumentVersionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Save new content as the next version.
    - Returns 409 if content is identical to the latest version.
    - Triggers background notification if change is significant.
    - Response is returned immediately (notification is non-blocking).
    """
    version, is_significant, similarity = document_service.add_version(
        db, document_id, payload, current_user
    )
    doc = document_service.get_document(db, document_id)

    if is_significant:
        dispatch_notification(
            document_id=document_id,
            document_title=doc.title,
            version_number=version.version_number,
            author_username=current_user.username,
            owner_email=doc.owner.email,
            similarity_percent=similarity,
        )

    return _version_out(version)


@router.get("/{document_id}/versions", response_model=List[VersionSummary])
def list_versions(
    document_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    versions = document_service.list_versions(db, document_id)
    return [
        VersionSummary(
            id=v.id,
            version_number=v.version_number,
            change_summary=v.change_summary,
            created_by_username=v.created_by_user.username if v.created_by_user else None,
            created_at=v.created_at,
            is_deleted=v.is_deleted,
        )
        for v in versions
    ]


@router.get("/{document_id}/versions/{version_number}", response_model=VersionOut)
def get_version(
    document_id: int,
    version_number: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ver = document_service.get_version(db, document_id, version_number)
    return _version_out(ver)


@router.delete("/{document_id}/versions/{version_number}", status_code=status.HTTP_204_NO_CONTENT)
def delete_version(
    document_id: int,
    version_number: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Soft-delete a single version.
    Does NOT affect other versions or the document itself.
    Cannot delete the last remaining version (delete the document instead).
    """
    document_service.soft_delete_version(db, document_id, version_number, current_user)


# ── Diff / Comparison ─────────────────────────────────────────────────────────

@router.get("/{document_id}/diff", response_model=DiffResult)
def compare_versions(
    document_id: int,
    version_a: int = Query(..., description="Earlier version number"),
    version_b: int = Query(..., description="Later version number"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Compare two versions of a document.
    Returns a structured diff with line-by-line changes and summary statistics.
    """
    doc = document_service.get_document(db, document_id)
    ver_a = document_service.get_version(db, document_id, version_a)
    ver_b = document_service.get_version(db, document_id, version_b)
    return compute_diff(doc, ver_a, ver_b)
