"""Machine callers: their credentials, their replay guard, and what they asked for.

Four tables, and they exist because a browser session is the wrong shape for a server.
A session is issued to a person who typed a password, lasts twelve hours, and dies when
the tab does. A server needs a credential that outlives a working day, is revocable in
one write, and proves that *this* request — not merely this caller — is genuine.

Nothing here touches an existing table. A leaked integration key must be containable by
deleting rows that nothing else depends on.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.database import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class IntegrationClient(Base):
    """One external system, bound to exactly one tenant.

    The binding is the whole security model in Phase 1. There is no `tenant_id` column
    anywhere in the document tables, so a single instance cannot separate two tenants'
    documents no matter what a request claims. What it *can* do — and what this row
    makes possible — is refuse a request whose declared tenant is not the one this
    credential was issued for. One tenant, one credential, one instance.
    """

    __tablename__ = "integration_clients"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The tenant this credential speaks for. A request declaring any other is refused
    #: before retrieval runs, so no other tenant's data is ever fetched to be filtered.
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    #: The credential's public half. This is all that travels: the secret itself is
    #: never sent, because a secret in a header can be read and re-used by anything in
    #: the path, which would leave the signature proving only that the holder of the
    #: header could copy it.
    key_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)

    #: The secret is not stored. It is derived on demand from a master key held in the
    #: environment — HMAC(master, "<key_id>:<version>") — so this table holds nothing
    #: that can be turned into a working credential, while the server can still
    #: reconstruct the shared key an HMAC signature has to be checked against.
    #:
    #: Storing a digest instead, as the sessions table does, is not available here:
    #: verifying a signature needs the key itself, and a digest is by construction not
    #: the key. The choice is between a recoverable secret and a decorative signature.
    secret_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    #: SHA-256 of the current derived secret. Nothing authenticates against it; it is
    #: here so that a master key which has changed announces itself as one named error
    #: instead of as every request suddenly failing to verify.
    secret_fingerprint: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    #: Rotation overlap. During the window both versions verify, so the caller can
    #: deploy the new secret without a restart coordinated across two systems.
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    previous_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: The `users` row this credential acts as. A real row, not a synthetic object,
    #: because `answer_traces.user_id` is a foreign key and an audit trail that cannot
    #: be joined is not an audit trail. Its role must be `service`, which is checked at
    #: every request rather than trusted from creation time.
    service_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    #: Knowledge scopes this credential may read. Phase 1 issues `["global"]`. A request
    #: may narrow this and may never widen it.
    allowed_scopes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    #: The operator's ceiling on web search, independent of what a request asks for.
    #: Two locks on the same door: the request must ask, and the operator must have
    #: allowed it. Either one closed means closed.
    web_search_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    rate_limit_burst: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    daily_quota: Mapped[int] = mapped_column(Integer, default=500, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_usable(self) -> bool:
        return self.is_active and self.revoked_at is None


class IntegrationNonce(Base):
    """One request's single-use token, kept only long enough to refuse it twice.

    Replay protection is a uniqueness constraint, not a lookup: the insert either
    succeeds or it does not, and there is no window between checking and recording in
    which two copies of the same captured request could both pass.
    """

    __tablename__ = "integration_nonces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("key_id", "nonce", name="uq_integration_nonce"),
        # Swept by age on every authenticated call, so this is the index the sweep runs
        # on rather than a full scan of a table that grows with every request.
        Index("ix_integration_nonces_seen", "seen_at"),
    )


class IntegrationIdempotency(Base):
    """What a repeated request should be given back instead of a second answer.

    A retry after a timeout is the expected case, not the exceptional one: generation
    takes tens of seconds and networks drop. Without this table every retry costs a full
    generation and writes a second `answer_traces` row, which is worse than the wasted
    time — one question answered twice in two wordings makes the audit trail read like a
    contradiction that never happened.
    """

    __tablename__ = "integration_idempotency"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    #: SHA-256 of the request body. The same key with a different body is a bug in the
    #: caller, and returning the first answer would hide it.
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # in_progress → a request holding this key is running; done → the reply is stored.
    state: Mapped[str] = mapped_column(String(16), default="in_progress", nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_json: Mapped[str] = mapped_column(Text, default="", nullable=False)
    answer_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("client_id", "idempotency_key", name="uq_integration_idempotency"),
    )


class IntegrationRequest(Base):
    """One line per call, for tracing a question across two systems.

    Deliberately holds no question text, no answer text and no ERP values. The answer
    trace already keeps the question under the existing retention rules; duplicating it
    here would put the same sensitive text in a second place with a second lifetime.
    What this table is for is the join: an ERP log line carries `request_id`, and from
    it this row reaches `answer_id`, and from there the evidence.
    """

    __tablename__ = "integration_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    #: The caller's own correlation id, echoed back on the reply. The whole point of
    #: the table.
    request_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    client_id: Mapped[str] = mapped_column(String(36), index=True, default="", nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, default="", nullable=False)
    #: An identifier, never a name or an address.
    external_user_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    endpoint: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    answer_id: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    answer_source: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    #: Counts only — how many of each kind of citation the answer rested on.
    source_counts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    #: Size, not content.
    query_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    erp_fact_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )

    __table_args__ = (
        Index("ix_integration_requests_client_time", "client_id", "created_at"),
    )


__all__ = [
    "IntegrationClient",
    "IntegrationIdempotency",
    "IntegrationNonce",
    "IntegrationRequest",
]
