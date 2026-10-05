"""Every question a person asked, with what they were told — kept, and learned from.

One row per question, whatever the outcome. It is what lets the system follow a
conversation ("وكم مدته؟" after a question about a contract), answer a repeated question
again without the model while the files it quoted are unchanged, and search a wording
that once failed with the wording that later worked. Private to the person who asked:
nothing here is read on anyone else's behalf.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class QuestionRecord(Base):
    __tablename__ = "question_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    #: The browser's conversation, so a follow-up is read against the turn before it.
    conversation_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    #: As asked, and in the canonical form repeated questions are matched on.
    question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    canonical: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: What was actually searched, when it differs: a follow-up made whole, or a
    #: wording learned from an earlier attempt.
    searched_as: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: For a follow-up: the question its conversation is about. Empty otherwise.
    subject: Mapped[str] = mapped_column(Text, default="", nullable=False)

    #: answered | refused | clarified | web
    outcome: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: The full response, so a repeat can be answered without the model.
    response: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    #: Documents the answer quoted.
    document_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: What the reader's documents and knowledge looked like when it was answered.
    stamp: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    answer_id: Mapped[str] = mapped_column(String(36), default="", index=True, nullable=False)
    #: up | down | "" — from the reader's thumbs.
    feedback: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    #: For a question that went unanswered: the later question, by the same person,
    #: that got the answer.
    resolved_by: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )

    __table_args__ = (
        Index("ix_question_log_user_time", "user_id", "created_at"),
        Index("ix_question_log_conversation", "user_id", "conversation_id"),
    )
