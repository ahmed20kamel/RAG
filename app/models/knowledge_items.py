"""Taught knowledge: the item, its immutable versions, and the trail behind both.

An item is an identity that persists across edits; a version is a frozen statement of
what it said at one point. Retrieval only ever reads the version an item currently
points at, so editing is additive — nothing that was once approved is overwritten, and
"who changed what, when and why" is answerable from the rows rather than from memory.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.knowledge import KnowledgeScope, KnowledgeStatus, KnowledgeType
from app.models.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class KnowledgeItem(Base):
    """The identity of one piece of taught knowledge."""

    __tablename__ = "knowledge_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    type: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    scope: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=KnowledgeStatus.PENDING, index=True, nullable=False
    )

    # Which version retrieval reads. Null until the first version is written, and moved
    # only by an edit — never by a status change.
    active_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Scope owners. A USER item belongs to one person, a TEAM item to one team; GLOBAL
    # items carry neither and are visible to everyone who may read knowledge.
    owner_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    owner_team_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("teams.id", ondelete="SET NULL"), index=True, nullable=True
    )
    department: Mapped[str] = mapped_column(String(128), default="", index=True, nullable=False)

    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, index=True, nullable=False
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # The retrieval query filters on exactly this pair before anything else, so it
        # is the index that decides whether the knowledge arm stays inside its budget.
        Index("ix_knowledge_status_scope", "status", "scope"),
        Index("ix_knowledge_type_status", "type", "status"),
        Index("ix_knowledge_owner_type", "owner_user_id", "type"),
    )

    @property
    def is_retrievable(self) -> bool:
        return self.status == KnowledgeStatus.ACTIVE and self.active_version_id is not None


class KnowledgeVersion(Base):
    """One frozen statement of an item. Never updated after insert.

    Immutability is what makes the history trustworthy: a reviewer approved a specific
    wording, and that wording has to stay readable afterwards even once the item has
    moved on. An edit writes a new row and repoints the item.
    """

    __tablename__ = "knowledge_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Why the author says this is true — a contract clause, a meeting, a person. Free
    # text because attribution in practice is a sentence, not an identifier.
    source_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Set when the claim points at something already in the knowledge base, which is
    # what lets an answer show a taught fact beside the document it came from.
    source_document_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    source_section_id: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # The author's own confidence, recorded rather than inferred. It ranks candidates;
    # it never promotes anything past review.
    confidence: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, default="", nullable=False)

    created_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    change_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)

    __table_args__ = (Index("ix_knowledge_versions_item_no", "item_id", "version_no", unique=True),)


class KnowledgeReview(Base):
    """Every decision taken on an item, in order. Append-only."""

    __tablename__ = "knowledge_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    decision: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    from_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    reviewer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )


class KnowledgeUsage(Base):
    """Which approved item reached which answer.

    Recorded so a wrong item can be traced to everything it touched, and so "why this
    answer?" has rows to stand on rather than a reconstruction after the fact. The
    question is stored as a digest: what matters is linking repeat asks, not keeping a
    searchable log of what everyone typed.
    """

    __tablename__ = "knowledge_usages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_items.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    question_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # How it reached the answer: as evidence offered, as a directive applied, or as a
    # value the answer actually stated.
    influence: Mapped[str] = mapped_column(String(32), default="offered", nullable=False)
    # True when this item disagreed with another source and both were shown.
    conflicted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # The answer this belongs to, so a trace and its usages can be read together.
    answer_id: Mapped[str] = mapped_column(String(36), default="", index=True, nullable=False)
    # What the retrieval scored it, kept so a weak match stays visible afterwards.
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )

    __table_args__ = (Index("ix_knowledge_usages_item_time", "item_id", "used_at"),)


class LearningCandidate(Base):
    """A potential lesson noticed in conversation, waiting on a human.

    A candidate is the buffer between what someone said and what the system believes.
    Detection fills it; only an explicit human action empties it, and accepting one
    produces a PENDING proposal rather than anything active. The approval boundary is
    never crossed here — a candidate that is accepted still has to be reviewed.
    """

    __tablename__ = "learning_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), default="", index=True, nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )

    detected_type: Mapped[str] = mapped_column(String(32), default=KnowledgeType.FACT, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    suggested_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # offered → the person was asked; accepted → it became an item; dismissed → declined.
    state: Mapped[str] = mapped_column(String(32), default="offered", index=True, nullable=False)
    promoted_item_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    #: Which cue family fired, so a person can see why they were asked.
    signal: Mapped[str] = mapped_column(String(32), default="", index=True, nullable=False)
    #: The answer this arose from, linking a correction back to what it corrects.
    answer_id: Mapped[str] = mapped_column(String(36), default="", index=True, nullable=False)
    #: For a correction: the item whose wording is being disputed, when one is known.
    corrects_item_id: Mapped[str | None] = mapped_column(String(36), index=True, nullable=True)
    #: Where it would land if accepted. Recorded at detection so the person sees it.
    proposed_scope: Mapped[str] = mapped_column(String(32), default="user", nullable=False)
    #: Why it was dismissed or rejected, kept so the decision is auditable.
    resolution_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Re-exported so callers reach one module for the model and its vocabulary.
__all__ = [
    "KnowledgeItem",
    "KnowledgeReview",
    "KnowledgeScope",
    "KnowledgeStatus",
    "KnowledgeType",
    "KnowledgeUsage",
    "KnowledgeVersion",
    "LearningCandidate",
]
