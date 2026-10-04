"""document owner

Revision ID: e5a8b2c4d6f1
Revises: d4f7a9c1e2b3
Create Date: 2026-10-04 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5a8b2c4d6f1"
down_revision: Union[str, Sequence[str], None] = "d4f7a9c1e2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing documents get no owner: they stay visible to those who may read every
    # document, and to nobody else.
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("owner_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_documents_owner_id", ["owner_id"])


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_owner_id")
        batch.drop_column("owner_id")
