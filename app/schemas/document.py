from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field


# ── User ──────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    email: EmailStr
    full_name: Optional[str] = None
    password: str = Field(..., min_length=6)


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Document Version ───────────────────────────────────────────────────────────

class VersionOut(BaseModel):
    id: int
    document_id: int
    version_number: int
    content_text: str
    change_summary: Optional[str]
    created_by: int
    created_by_username: Optional[str] = None
    created_at: datetime
    is_deleted: bool

    model_config = {"from_attributes": True}


class VersionSummary(BaseModel):
    """Lightweight version info (no content_text) for listing."""
    id: int
    version_number: int
    change_summary: Optional[str]
    created_by_username: Optional[str] = None
    created_at: datetime
    is_deleted: bool

    model_config = {"from_attributes": True}


# ── Document ───────────────────────────────────────────────────────────────────

class DocumentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content_text: str
    change_summary: Optional[str] = Field(None, max_length=1000)


class DocumentTitleUpdate(BaseModel):
    """Update only the document title – does NOT create a new version."""
    title: str = Field(..., min_length=1, max_length=500)


class DocumentVersionUpdate(BaseModel):
    """Save new content – ALWAYS creates a new version."""
    content_text: str
    change_summary: Optional[str] = Field(None, max_length=1000)


class DocumentOut(BaseModel):
    id: int
    title: str
    owner_id: int
    owner_username: Optional[str] = None
    is_deleted: bool
    created_at: datetime
    updated_at: datetime
    version_count: int
    latest_version: Optional[VersionOut] = None

    model_config = {"from_attributes": True}


class DocumentSummary(BaseModel):
    id: int
    title: str
    owner_username: Optional[str] = None
    version_count: int
    is_deleted: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Diff / Comparison ─────────────────────────────────────────────────────────

class DiffLine(BaseModel):
    line_number_before: Optional[int]   # None for added lines
    line_number_after: Optional[int]    # None for removed lines
    tag: str                            # "equal" | "replace" | "insert" | "delete"
    content_before: Optional[str]       # original line text
    content_after: Optional[str]        # new line text


class DiffResult(BaseModel):
    document_id: int
    document_title: str
    version_a: int
    version_b: int
    created_at_a: datetime
    created_at_b: datetime
    author_a: Optional[str]
    author_b: Optional[str]
    stats: dict                         # added/removed/replaced/unchanged line counts
    lines: List[DiffLine]
    is_significant: bool                # True if change exceeds threshold
    similarity_percent: float


# ── Notification log ───────────────────────────────────────────────────────────

class NotificationLog(BaseModel):
    document_id: int
    version_number: int
    triggered: bool
    reason: str
