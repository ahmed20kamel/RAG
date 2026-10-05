"""metrics: wording of unanswered questions

Revision ID: f7b3c9d2e8a4
Revises: e5a8b2c4d6f1
Create Date: 2026-10-05 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f7b3c9d2e8a4"
down_revision: Union[str, Sequence[str], None] = "e5a8b2c4d6f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("request_metrics") as batch:
        batch.add_column(sa.Column("question", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("request_metrics") as batch:
        batch.drop_column("question")
