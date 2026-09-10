"""Persist append-only, scope-bound event impact traces.

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = (
    "event_impact_hypotheses",
    "company_impact_relations",
    "company_impact_relation_reviews",
    "company_impact_observations",
)


def _add_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def _drop_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")


def upgrade() -> None:
    op.create_table(
        "event_impact_hypotheses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("event_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score_components", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "classification IN ('candidate', 'key', 'alternative', 'background', 'unresolved')",
            name="ck_event_impact_hypotheses_classification",
        ),
    )
    op.create_index(
        "ix_event_impact_hypotheses_case_scope_rank",
        "event_impact_hypotheses",
        ["research_case_id", "scope_version_id", "rank"],
    )

    op.create_table(
        "company_impact_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "hypothesis_id", sa.Uuid(), sa.ForeignKey("event_impact_hypotheses.id"), nullable=False
        ),
        sa.Column(
            "scope_version_id",
            sa.Uuid(),
            sa.ForeignKey("event_research_scope_versions.id"),
            nullable=False,
        ),
        sa.Column("affected_company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("relation_kind", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("mechanism", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "relation_kind IN ('supplier', 'customer', 'competitor', 'partner', 'industry_peer')",
            name="ck_company_impact_relations_relation_kind",
        ),
        sa.CheckConstraint(
            "direction IN ('benefits', 'harms', 'mixed', 'unknown')",
            name="ck_company_impact_relations_direction",
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'verified', 'rejected', 'insufficient')",
            name="ck_company_impact_relations_status",
        ),
    )
    op.create_index(
        "ix_company_impact_relations_hypothesis_company",
        "company_impact_relations",
        ["hypothesis_id", "affected_company_id"],
    )

    op.create_table(
        "company_impact_relation_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "relation_id", sa.Uuid(), sa.ForeignKey("company_impact_relations.id"), nullable=False
        ),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'rejected', 'needs_more')",
            name="ck_company_impact_relation_reviews_outcome",
        ),
    )
    op.create_index(
        "ix_company_impact_relation_reviews_relation_created",
        "company_impact_relation_reviews",
        ["relation_id", "created_at"],
    )

    op.create_table(
        "company_impact_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "relation_id", sa.Uuid(), sa.ForeignKey("company_impact_relations.id"), nullable=False
        ),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=True
        ),
        sa.Column(
            "valuation_snapshot_id", sa.Uuid(), sa.ForeignKey("valuation_snapshots.id"), nullable=True
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('event', 'relation', 'operating', 'market', 'peer_control', 'fund')",
            name="ck_company_impact_observations_kind",
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'verified', 'rejected', 'insufficient')",
            name="ck_company_impact_observations_status",
        ),
    )
    op.create_index(
        "ix_company_impact_observations_relation_kind_status",
        "company_impact_observations",
        ["relation_id", "kind", "status"],
    )

    for table in _IMMUTABLE_TABLES:
        _add_immutable_triggers(table)


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        _drop_immutable_triggers(table)
    op.drop_index(
        "ix_company_impact_observations_relation_kind_status",
        table_name="company_impact_observations",
    )
    op.drop_table("company_impact_observations")
    op.drop_index(
        "ix_company_impact_relation_reviews_relation_created",
        table_name="company_impact_relation_reviews",
    )
    op.drop_table("company_impact_relation_reviews")
    op.drop_index(
        "ix_company_impact_relations_hypothesis_company",
        table_name="company_impact_relations",
    )
    op.drop_table("company_impact_relations")
    op.drop_index(
        "ix_event_impact_hypotheses_case_scope_rank",
        table_name="event_impact_hypotheses",
    )
    op.drop_table("event_impact_hypotheses")
