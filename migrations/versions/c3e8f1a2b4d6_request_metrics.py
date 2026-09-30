"""request metrics for monitoring

Revision ID: c3e8f1a2b4d6
Revises: b7d41c9a20e3
Create Date: 2026-09-28 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e8f1a2b4d6"
down_revision: Union[str, Sequence[str], None] = "b7d41c9a20e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "request_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("refusal_reason", sa.String(length=64), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        sa.Column("retrieval_ms", sa.Integer(), nullable=False),
        sa.Column("generation_ms", sa.Integer(), nullable=False),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("retrieved", sa.Integer(), nullable=False),
        sa.Column("cited", sa.Integer(), nullable=False),
        sa.Column("conflicts", sa.Integer(), nullable=False),
        sa.Column("unsupported_values", sa.Integer(), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("completion_pass", sa.Boolean(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("reranker", sa.String(length=32), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("answer_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_request_metrics_created_at", "request_metrics", ["created_at"])
    op.create_index("ix_request_metrics_outcome", "request_metrics", ["outcome"])
    op.create_index(
        "ix_request_metrics_outcome_time", "request_metrics", ["outcome", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_request_metrics_outcome_time", table_name="request_metrics")
    op.drop_index("ix_request_metrics_outcome", table_name="request_metrics")
    op.drop_index("ix_request_metrics_created_at", table_name="request_metrics")
    op.drop_table("request_metrics")
