"""
Data models for the Smart Legal Document Manager.

Design decisions:
- Document holds only metadata (title, owner, soft-delete flag).
- DocumentVersion is an append-only log of every content snapshot.
- Deleting a *version* sets version.is_deleted = True.
- Deleting the whole *document* sets document.is_deleted = True (cascade soft-delete).
- Hard-deleting all versions of a document requires explicit `force=True` parameter.
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from app.db.session import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # relationships
    documents = relationship("Document", back_populates="owner")
    versions = relationship("DocumentVersion", back_populates="created_by_user")


class Document(Base):
    """
    Represents a *legal document entity* – not any specific version of it.
    Title updates here do NOT create a new version.
    """
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Soft-delete: is_deleted=True hides the document; hard-delete cascades.
    is_deleted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    # relationships
    owner = relationship("User", back_populates="documents")
    versions = relationship(
        "DocumentVersion",
        back_populates="document",
        order_by="DocumentVersion.version_number",
    )

    @property
    def latest_version(self):
        active = [v for v in self.versions if not v.is_deleted]
        return active[-1] if active else None

    @property
    def version_count(self) -> int:
        return sum(1 for v in self.versions if not v.is_deleted)


class DocumentVersion(Base):
    """
    Immutable snapshot of document content at a point in time.
    Once written, content_text must NEVER be overwritten.
    """
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_doc_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    version_number = Column(Integer, nullable=False)          # 1-based, auto-incremented per doc
    content_text = Column(Text, nullable=False)               # immutable after insert
    change_summary = Column(String(1000))                     # optional human note

    # Who & when
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    # Soft-delete individual version without touching siblings
    is_deleted = Column(Boolean, default=False, nullable=False)

    # relationships
    document = relationship("Document", back_populates="versions")
    created_by_user = relationship("User", back_populates="versions")
