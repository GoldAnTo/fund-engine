"""evidence ledger: document versions and source spans

Revision ID: 0001
Revises:
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMMUTABLE_TABLES = ("document_versions", "source_spans")


def upgrade() -> None:
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column(
            "supersedes_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_document_versions_content_sha256",
        "document_versions",
        ["content_sha256"],
    )

    op.create_table(
        "source_spans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("verbatim_text", sa.Text(), nullable=False),
    )

    # Defence-in-depth: reject UPDATE/DELETE at the database level too, so a
    # connection that bypasses the application still cannot mutate the ledger.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_mutable_ledger()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'table % is append-only: UPDATE/DELETE is not allowed',
                TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
    op.execute("DROP FUNCTION IF EXISTS reject_mutable_ledger();")
    op.drop_table("source_spans")
    op.drop_table("document_versions")
