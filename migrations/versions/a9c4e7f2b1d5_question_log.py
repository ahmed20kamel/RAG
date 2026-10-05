"""question_log: every question, kept and learned from

Revision ID: a9c4e7f2b1d5
Revises: f7b3c9d2e8a4
Create Date: 2026-10-05 13:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9c4e7f2b1d5"
down_revision: Union[str, Sequence[str], None] = "f7b3c9d2e8a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("conversation_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("question", sa.Text(), nullable=False, server_default=""),
        sa.Column("canonical", sa.Text(), nullable=False, server_default=""),
        sa.Column("searched_as", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(16), nullable=False, server_default=""),
        sa.Column("answer", sa.Text(), nullable=False, server_default=""),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("document_ids", sa.JSON(), nullable=False),
        sa.Column("stamp", sa.String(128), nullable=False, server_default=""),
        sa.Column("answer_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("feedback", sa.String(8), nullable=False, server_default=""),
        sa.Column("resolved_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_question_log_user_id", "question_log", ["user_id"])
    op.create_index("ix_question_log_answer_id", "question_log", ["answer_id"])
    op.create_index("ix_question_log_created_at", "question_log", ["created_at"])
    op.create_index("ix_question_log_user_time", "question_log", ["user_id", "created_at"])
    op.create_index("ix_question_log_conversation", "question_log", ["user_id", "conversation_id"])


def downgrade() -> None:
    op.drop_table("question_log")
