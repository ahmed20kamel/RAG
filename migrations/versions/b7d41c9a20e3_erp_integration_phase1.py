"""Integration clients, replay guard, idempotency and the call log.

Four new tables and not one change to an existing one. That is deliberate: the machine
surface is additive, so rolling it back removes the surface and leaves everything the
web interface depends on untouched. Nothing in the document, knowledge or answer-trace
tables gains a column here — carrying tenant and caller identity into `answer_traces`
belongs to the tracing phase, and doing it early would put a column in a table whose
retention rules have not been decided yet.

No secret is stored. `integration_clients` keeps a key id, a version number and a
fingerprint; the secret itself is derived from a master key that lives in the
environment, so a copy of this database cannot sign anything.

Revision ID: b7d41c9a20e3
Revises: 251be4863562
Create Date: 2026-09-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d41c9a20e3"
down_revision: Union[str, Sequence[str], None] = "251be4863562"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_clients",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("secret_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("secret_fingerprint", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("previous_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("service_user_id", sa.String(length=36), nullable=False),
        sa.Column("allowed_scopes", sa.JSON(), nullable=False),
        sa.Column("web_search_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("rate_limit_burst", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_concurrency", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("daily_quota", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        # RESTRICT rather than CASCADE: deleting the service account must fail loudly
        # while a credential still points at it, not silently take the credential with
        # it and leave the calling system authenticating against nothing.
        sa.ForeignKeyConstraint(["service_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_clients_key_id", "integration_clients", ["key_id"], unique=True)
    op.create_index("ix_integration_clients_tenant_id", "integration_clients", ["tenant_id"])

    op.create_table(
        "integration_nonces",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # The replay guard itself. Recording by inserting, rather than looking up and
        # then writing, closes the window in which two copies of one captured request
        # could both pass the check before either was written down.
        sa.UniqueConstraint("key_id", "nonce", name="uq_integration_nonce"),
    )
    op.create_index("ix_integration_nonces_seen", "integration_nonces", ["seen_at"])

    op.create_table(
        "integration_idempotency",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="in_progress"),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("answer_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "idempotency_key", name="uq_integration_idempotency"),
    )
    op.create_index(
        "ix_integration_idempotency_client_id", "integration_idempotency", ["client_id"]
    )
    op.create_index(
        "ix_integration_idempotency_created_at", "integration_idempotency", ["created_at"]
    )

    op.create_table(
        "integration_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("external_user_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("endpoint", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("answer_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("answered", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("answer_source", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("source_counts", sa.JSON(), nullable=False),
        sa.Column("query_chars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("erp_fact_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_requests_request_id", "integration_requests", ["request_id"])
    op.create_index("ix_integration_requests_client_id", "integration_requests", ["client_id"])
    op.create_index("ix_integration_requests_tenant_id", "integration_requests", ["tenant_id"])
    op.create_index("ix_integration_requests_created_at", "integration_requests", ["created_at"])
    # The daily quota counts rows against this pair, so it is the index that decides
    # whether the quota check costs anything.
    op.create_index(
        "ix_integration_requests_client_time", "integration_requests", ["client_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("integration_requests")
    op.drop_table("integration_idempotency")
    op.drop_table("integration_nonces")
    op.drop_table("integration_clients")
