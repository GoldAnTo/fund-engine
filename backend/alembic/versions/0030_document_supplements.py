"""Link independent recovery text snapshots to their frozen original.

Revision ID: 0030
Revises: 0029
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("document_versions") as batch:
            batch.add_column(
                sa.Column("supplements_document_version_id", sa.Uuid(), nullable=True)
            )
            batch.add_column(
                sa.Column("claimed_page_reference", sa.String(length=256), nullable=True)
            )
            batch.create_foreign_key(
                "fk_document_versions_supplements_document_version_id",
                "document_versions",
                ["supplements_document_version_id"],
                ["id"],
            )
    else:
        op.add_column(
            "document_versions",
            sa.Column("supplements_document_version_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_document_versions_supplements_document_version_id",
            "document_versions",
            "document_versions",
            ["supplements_document_version_id"],
            ["id"],
        )
        op.add_column(
            "document_versions",
            sa.Column("claimed_page_reference", sa.String(length=256), nullable=True),
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_document_versions_supplements_document_version_id",
        "document_versions",
        type_="foreignkey",
    )
    op.drop_column("document_versions", "claimed_page_reference")
    op.drop_column("document_versions", "supplements_document_version_id")
