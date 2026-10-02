"""document project and folder

Revision ID: d4f7a9c1e2b3
Revises: c3e8f1a2b4d6
Create Date: 2026-10-02 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4f7a9c1e2b3"
down_revision: Union[str, Sequence[str], None] = "c3e8f1a2b4d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("project", sa.String(length=256), nullable=False, server_default=""))
        batch.add_column(sa.Column("folder", sa.String(length=1024), nullable=False, server_default=""))
        batch.create_index("ix_documents_project", ["project"])


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_project")
        batch.drop_column("folder")
        batch.drop_column("project")
