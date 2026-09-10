"""Add durable impact refresh claims and canonical company identity.

Revision ID: 0020
Revises: 0019
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.add_column("companies", sa.Column("canonical_identity", sa.String(512), nullable=True))
    op.create_index("uq_companies_type_canonical_identity", "companies", ["type", "canonical_identity"], unique=True)
    op.create_table(
        "event_impact_refresh_claims",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("scope_version_id", sa.Uuid(), sa.ForeignKey("event_research_scope_versions.id"), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=True),
        sa.Column("refresh_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope_version_id", "refresh_key", name="uq_event_impact_refresh_claim"),
    )
    _trigger("event_impact_refresh_claims", "update")
    _trigger("event_impact_refresh_claims", "delete")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_delete_event_impact_refresh_claims ON event_impact_refresh_claims;")
        op.execute("DROP TRIGGER IF EXISTS no_update_event_impact_refresh_claims ON event_impact_refresh_claims;")
    op.drop_table("event_impact_refresh_claims")
    op.drop_index("uq_companies_type_canonical_identity", table_name="companies")
    op.drop_column("companies", "canonical_identity")
