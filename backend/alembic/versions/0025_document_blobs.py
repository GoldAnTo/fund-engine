"""Persist immutable local object references for uploaded source documents.

Revision ID: 0025
Revises: 0024
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "document_blobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(256), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_version_id", name="uq_document_blobs_document_version"),
        sa.UniqueConstraint("storage_key", name="uq_document_blobs_storage_key"),
    )
    op.create_index("ix_document_blobs_document_version_id", "document_blobs", ["document_version_id"])
    _trigger("document_blobs", "update")
    _trigger("document_blobs", "delete")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_delete_document_blobs ON document_blobs;")
        op.execute("DROP TRIGGER IF EXISTS no_update_document_blobs ON document_blobs;")
    op.drop_index("ix_document_blobs_document_version_id", table_name="document_blobs")
    op.drop_table("document_blobs")
