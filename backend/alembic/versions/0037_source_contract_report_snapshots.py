"""Add immutable source contracts and independent report supplements.

Revision ID: 0037
Revises: 0036
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = (
    "source_contract_versions",
    "document_source_records",
    "document_supplement_links",
)

_SQLITE_REPLACE_GUARDS = {
    "source_contract_versions": "id = NEW.id",
    "document_source_records": (
        "id = NEW.id OR (document_version_id = NEW.document_version_id "
        "AND tenant_id = NEW.tenant_id "
        "AND source_contract_version_id = NEW.source_contract_version_id)"
    ),
    "document_supplement_links": (
        "id = NEW.id OR supplement_document_version_id = NEW.supplement_document_version_id"
    ),
}


def _trigger(table: str, action: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
    elif dialect == "sqlite":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW BEGIN "
            f"SELECT RAISE(ABORT, '{table} is append-only'); END;"
        )


def _replace_guard(table: str) -> None:
    if op.get_bind().dialect.name == "sqlite":
        condition = _SQLITE_REPLACE_GUARDS[table]
        op.execute(
            f"CREATE TRIGGER no_replace_{table} BEFORE INSERT ON {table} "
            f"FOR EACH ROW WHEN EXISTS (SELECT 1 FROM {table} WHERE {condition}) "
            "BEGIN "
            f"SELECT RAISE(ABORT, '{table} is append-only'); END;"
        )


def upgrade() -> None:
    op.create_table(
        "source_contract_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider_name", sa.String(length=256), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("may_display", sa.Boolean(), nullable=False),
        sa.Column("may_search", sa.Boolean(), nullable=False),
        sa.Column("may_ai_process", sa.Boolean(), nullable=False),
        sa.Column("may_export", sa.Boolean(), nullable=False),
        sa.Column("may_api_use", sa.Boolean(), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_policy", sa.Text(), nullable=False),
        sa.Column("downstream_restrictions", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_source_contract_versions_tenant", "source_contract_versions", ["tenant_id"]
    )
    op.create_table(
        "document_source_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "source_contract_version_id",
            sa.Uuid(),
            sa.ForeignKey("source_contract_versions.id"),
            nullable=False,
        ),
        sa.Column("admission_type", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("source_actor", sa.String(length=128), nullable=False),
        sa.Column("provider_record_id", sa.String(length=256), nullable=True),
        sa.Column("verification_state", sa.String(length=64), nullable=False),
        sa.Column("acquisition_request", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "admission_type IN ('licensed_provider', 'uploaded_file', "
            "'pasted_snapshot', 'public_url')",
            name="ck_document_source_records_admission_type",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "tenant_id",
            "source_contract_version_id",
            name="uq_document_source_records_document_tenant_contract",
        ),
    )
    op.create_index(
        "ix_document_source_records_tenant", "document_source_records", ["tenant_id"]
    )
    op.create_index(
        "ix_document_source_records_document_version",
        "document_source_records",
        ["document_version_id"],
    )
    op.create_table(
        "document_supplement_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "original_document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column(
            "supplement_document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("claimed_page_reference", sa.String(length=200), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "supplement_document_version_id",
            name="uq_document_supplement_links_supplement_document",
        ),
    )
    op.create_index(
        "ix_document_supplement_links_original_document_version",
        "document_supplement_links",
        ["original_document_version_id"],
    )
    op.create_index(
        "ix_document_supplement_links_supplement_document_version",
        "document_supplement_links",
        ["supplement_document_version_id"],
    )
    for table in _IMMUTABLE_TABLES:
        _trigger(table, "update")
        _trigger(table, "delete")
        _replace_guard(table)


def downgrade() -> None:
    bind = op.get_bind()
    for table in _IMMUTABLE_TABLES:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"refusing to downgrade: {table} contains {count} immutable records"
            )
    if bind.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};")
    elif bind.dialect.name == "sqlite":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("replace", "delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table};")
    op.drop_index(
        "ix_document_supplement_links_supplement_document_version",
        table_name="document_supplement_links",
    )
    op.drop_index(
        "ix_document_supplement_links_original_document_version",
        table_name="document_supplement_links",
    )
    op.drop_table("document_supplement_links")
    op.drop_index(
        "ix_document_source_records_document_version",
        table_name="document_source_records",
    )
    op.drop_index(
        "ix_document_source_records_tenant", table_name="document_source_records"
    )
    op.drop_table("document_source_records")
    op.drop_index(
        "ix_source_contract_versions_tenant", table_name="source_contract_versions"
    )
    op.drop_table("source_contract_versions")
