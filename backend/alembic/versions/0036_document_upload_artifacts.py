"""Retain immutable original bytes for uploaded document versions.

Revision ID: 0036
Revises: 0035
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_upload_artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_version", sa.String(length=96), nullable=False),
        sa.Column("storage_kind", sa.String(length=32), nullable=False),
        sa.Column("file_name", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.String(length=128), nullable=False),
        sa.Column("retention_policy", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "document_version_id", name="uq_document_upload_artifacts_document"
        ),
    )
    op.create_index(
        "ix_document_upload_artifacts_document_version_id",
        "document_upload_artifacts",
        ["document_version_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER no_update_document_upload_artifacts BEFORE UPDATE "
            "ON document_upload_artifacts FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            "CREATE TRIGGER no_delete_document_upload_artifacts BEFORE DELETE "
            "ON document_upload_artifacts FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS no_update_document_upload_artifacts "
            "ON document_upload_artifacts;"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS no_delete_document_upload_artifacts "
            "ON document_upload_artifacts;"
        )
    op.drop_table("document_upload_artifacts")
