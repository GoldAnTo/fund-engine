"""Freeze an auditable evidence-visibility horizon for report scopes.

Revision ID: 0035
Revises: 0034
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Record a distinct migration-time horizon for already-created scopes.

    0032 historically backfilled ``created_at`` from the report-span time.
    That timestamp is source provenance and must never be rewritten.  Existing
    attached disclosures which were already available at this migration's
    execution should remain visible, so legacy scope rows get one shared,
    auditable migration-time cutoff.  New application scopes store their own
    creation time as the cutoff.
    """
    migration_cutoff_at = datetime.now(timezone.utc)
    op.add_column(
        "report_research_scope_versions",
        sa.Column("visibility_cutoff_at", sa.DateTime(timezone=True), nullable=True),
    )
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        # 0032 made this ledger append-only.  PostgreSQL executes the
        # backfill as an UPDATE, so remove only that trigger inside Alembic's
        # migration transaction; a failure rolls the trigger drop back too.
        op.execute(
            "DROP TRIGGER no_update_report_research_scope_versions "
            "ON report_research_scope_versions"
        )
    op.get_bind().execute(
        sa.text(
            "UPDATE report_research_scope_versions "
            "SET visibility_cutoff_at = :migration_cutoff_at "
            "WHERE visibility_cutoff_at IS NULL"
        ),
        {"migration_cutoff_at": migration_cutoff_at},
    )
    if is_postgresql:
        op.execute(
            "CREATE TRIGGER no_update_report_research_scope_versions "
            "BEFORE UPDATE ON report_research_scope_versions "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
    with op.batch_alter_table("report_research_scope_versions") as batch:
        batch.alter_column("visibility_cutoff_at", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT count(*) FROM report_research_scope_versions")
    ).scalar_one()
    if count:
        raise RuntimeError(
            "refusing to downgrade: report_research_scope_versions contains "
            f"{count} immutable records"
        )
    with op.batch_alter_table("report_research_scope_versions") as batch:
        batch.drop_column("visibility_cutoff_at")
