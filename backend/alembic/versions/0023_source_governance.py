"""Add immutable contracts for source use and provider retrieval.

Revision ID: 0023
Revises: 0022
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _immutable(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute(f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def upgrade() -> None:
    op.create_table(
        "source_contracts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("provider_or_tenant", sa.String(length=256), nullable=False),
        sa.Column("allow_ai_processing", sa.Boolean(), nullable=False),
        sa.Column("allow_display", sa.Boolean(), nullable=False),
        sa.Column("allow_export", sa.Boolean(), nullable=False),
        sa.Column("allow_api", sa.Boolean(), nullable=False),
        sa.Column("region", sa.String(length=128), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_policy", sa.String(length=128), nullable=False),
        sa.Column("deletion_policy", sa.Text(), nullable=False),
        sa.Column("downstream_restrictions", sa.JSON(), nullable=False),
        sa.Column("contract_version", sa.String(length=128), nullable=True),
        sa.Column("intake_metadata", sa.JSON(), nullable=False),
        sa.Column("declared_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_version_id", name="uq_source_contracts_document"),
    )
    op.create_index("ix_source_contracts_document", "source_contracts", ["document_version_id"])
    op.create_table(
        "provider_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_version_id", sa.Uuid(), sa.ForeignKey("document_versions.id"), nullable=False),
        sa.Column("provider_name", sa.String(length=256), nullable=False),
        sa.Column("provider_record_id", sa.String(length=512), nullable=False),
        sa.Column("request_scope", sa.JSON(), nullable=False),
        sa.Column("retrieval_reference", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contract_version", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_version_id", name="uq_provider_records_document"),
    )
    op.create_index("ix_provider_records_document", "provider_records", ["document_version_id"])
    for table in ("source_contracts", "provider_records"):
        _immutable(table)


def downgrade() -> None:
    for table in ("provider_records", "source_contracts"):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
        op.drop_table(table)
