"""Request/response contracts for the knowledge layer."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.knowledge import KnowledgeScope, KnowledgeStatus, KnowledgeType


class KnowledgeVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version_no: int
    content: str
    source_text: str
    source_document_id: str | None
    source_section_id: str | None
    confidence: float
    explanation: str
    change_reason: str
    created_by: str | None
    created_at: datetime


class KnowledgeReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    decision: str
    from_status: str
    to_status: str
    reviewer_id: str | None
    reason: str
    created_at: datetime


class KnowledgeUsageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    question_hash: str
    user_id: str | None
    influence: str
    conflicted: bool
    used_at: datetime


class KnowledgeItemResponse(BaseModel):
    """One item plus the version currently in force, flattened for the list view."""

    id: str
    type: KnowledgeType
    scope: KnowledgeScope
    status: KnowledgeStatus
    content: str = ""
    source_text: str = ""
    source_document_id: str | None = None
    confidence: float = 0.0
    version_no: int = 1
    explanation: str = ""
    tags: list[str] = Field(default_factory=list)

    owner_user_id: str | None = None
    owner_team_id: str | None = None
    department: str = ""
    created_by: str | None = None
    created_by_name: str = ""
    approved_by: str | None = None
    approved_by_name: str = ""

    created_at: datetime
    updated_at: datetime
    approved_at: datetime | None = None
    activated_at: datetime | None = None
    archived_at: datetime | None = None

    # What this reader may do with it, resolved server-side so the interface never has
    # to reimplement the permission rules and drift from them.
    can_edit: bool = False
    can_approve: bool = False
    can_archive: bool = False


class KnowledgeDetailResponse(KnowledgeItemResponse):
    versions: list[KnowledgeVersionResponse] = Field(default_factory=list)
    reviews: list[KnowledgeReviewResponse] = Field(default_factory=list)
    usages: list[KnowledgeUsageResponse] = Field(default_factory=list)
    usage_count: int = 0


class KnowledgeListResponse(BaseModel):
    total: int
    items: list[KnowledgeItemResponse]


class TeachRequest(BaseModel):
    """Explicit teaching. Nothing here activates the item on its own."""

    type: KnowledgeType
    content: str = Field(min_length=1, max_length=4000)
    scope: KnowledgeScope = KnowledgeScope.USER
    source_text: str = Field(default="", max_length=2000)
    source_document_id: str | None = None
    source_section_id: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    explanation: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    team_id: str | None = None
    department: str = Field(default="", max_length=128)


class KnowledgeEditRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    change_reason: str = Field(default="", max_length=1000)
    source_text: str | None = Field(default=None, max_length=2000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    explanation: str | None = Field(default=None, max_length=2000)


class KnowledgeDecisionRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


class KnowledgeStatsResponse(BaseModel):
    total: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    by_scope: dict[str, int] = Field(default_factory=dict)
    pending_review: int = 0
    active: int = 0
