"""What went into one answer, kept so it can be explained afterwards.

This is the store behind "why this answer?". It records what the answer was built from —
which passages, which approved claims, which rules applied, which conflicts were found
and how each was settled — and nothing about how the model reasoned. Chain-of-thought is
neither requested nor stored: an explanation made of evidence and recorded decisions can
be checked against the sources, while a narrated rationale can only be believed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AnswerTrace(Base):
    __tablename__ = "answer_traces"

    # Returned to the client on the answer, so a later "why?" needs no re-run.
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )

    question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    question_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    #: Citations with their document, section and score — the document half of the answer.
    document_evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Approved items that reached the prompt, with version, score and how far they got.
    knowledge_used: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Rules applied and preferences that touched the wording, kept apart from evidence.
    policies_applied: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: Every disagreement found, with both values, both sources, and the basis used.
    conflicts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    conflict_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unresolved_conflicts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    knowledge_layer_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )

    __table_args__ = (Index("ix_answer_traces_user_time", "user_id", "created_at"),)
