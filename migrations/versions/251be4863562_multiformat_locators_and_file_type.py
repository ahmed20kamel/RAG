"""Locators on chunks, and the detected file type on documents.

Three additive columns, each with a server default, so every row already in the table
gets a correct value rather than a null: a chunk indexed before this release came from
Markdown, which genuinely has no locator and no page, and a document already in the
library is genuinely a Markdown document. Nothing needs re-ingesting.

Autogenerate also proposed a foreign key on `learning_candidates`. That is unrelated
drift from an unnamed constraint SQLite never recorded, and it was removed — a migration
should contain the change it is named after and nothing else, or a rollback takes
something with it that nobody agreed to.

Revision ID: 251be4863562
Revises: 27eb1ca1cfd4
Create Date: 2026-09-20 16:52:26.292231
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "251be4863562"
down_revision: Union[str, Sequence[str], None] = "27eb1ca1cfd4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chunks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("locator", sa.String(length=256), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("page", sa.Integer(), nullable=True))

    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "file_type", sa.String(length=16), nullable=False, server_default="markdown"
            )
        )
        batch_op.create_index(
            batch_op.f("ix_documents_file_type"), ["file_type"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_documents_file_type"))
        batch_op.drop_column("file_type")

    with op.batch_alter_table("chunks", schema=None) as batch_op:
        batch_op.drop_column("page")
        batch_op.drop_column("locator")
