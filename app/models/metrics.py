"""One row per answered request, for monitoring.

Kept apart from `answer_traces` on purpose. A trace records what one answer was built
from and exists to explain it; this records how the system behaved — how long each
stage took, how the request ended — and exists to notice when that changes. Traces are
written only for answers; this is written for every request, refusals and failures
included, because a rising refusal or error rate is exactly what monitoring is for.

No question text. The hash is enough to group repeats, and the answer id links to the
trace — which an administrator can already open — when the text is needed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RequestMetric(Base):
    __tablename__ = "request_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    #: chat | stream | erp
    channel: Mapped[str] = mapped_column(String(16), default="chat", nullable=False)
    #: answered | refused | clarified | web | error
    outcome: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    refusal_reason: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    error_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    total_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retrieval_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    generation_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Every stage the pipeline timed, as reported in `timings_ms`.
    stages: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    retrieved: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cited: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conflicts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    unsupported_values: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completion_pass: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    model: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    reranker: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    question_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    answer_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (Index("ix_request_metrics_outcome_time", "outcome", "created_at"),)
