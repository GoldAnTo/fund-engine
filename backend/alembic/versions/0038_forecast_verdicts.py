"""Add immutable, human-published forecast verdict records.

Revision ID: 0038
Revises: 0037
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = (
    "forecast_target_versions",
    "actual_metric_observations",
    "forecast_evaluation_candidates",
    "forecast_verdicts",
)


def upgrade() -> None:
    op.create_table(
        "forecast_target_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False, index=True),
        sa.Column("key_factor_id", sa.Uuid(), sa.ForeignKey("key_factors.id"), nullable=False, index=True),
        sa.Column("report_claim_id", sa.Uuid(), sa.ForeignKey("report_claims.id"), nullable=False),
        sa.Column("forecast_source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=False),
        sa.Column("baseline_source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id")),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("entity_key", sa.String(length=128), nullable=False),
        sa.Column("baseline_value", sa.Numeric(24, 6)),
        sa.Column("expected_value", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("forecast_period_start", sa.Date(), nullable=False),
        sa.Column("forecast_period_end", sa.Date(), nullable=False),
        sa.Column("comparator", sa.String(length=32), nullable=False),
        sa.Column("relative_tolerance", sa.Numeric(12, 8)),
        sa.Column("reviewed_by", sa.String(length=128), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("comparator IN ('at_least', 'at_most', 'within_tolerance')", name="ck_forecast_target_comparator"),
        sa.CheckConstraint("relative_tolerance IS NULL OR relative_tolerance >= 0", name="ck_forecast_target_tolerance"),
    )
    op.create_table(
        "actual_metric_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("forecast_target_id", sa.Uuid(), sa.ForeignKey("forecast_target_versions.id"), nullable=False, index=True),
        sa.Column("source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=False),
        sa.Column("entity_key", sa.String(length=128), nullable=False),
        sa.Column("observed_value", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("observed_period_start", sa.Date(), nullable=False),
        sa.Column("observed_period_end", sa.Date(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by", sa.String(length=128), nullable=False),
        sa.Column("record_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "forecast_evaluation_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("forecast_target_id", sa.Uuid(), sa.ForeignKey("forecast_target_versions.id"), nullable=False, index=True),
        sa.Column("actual_observation_id", sa.Uuid(), sa.ForeignKey("actual_metric_observations.id"), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False, server_default="machine_generated"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')", name="ck_forecast_evaluation_outcome"),
    )
    op.create_table(
        "forecast_verdicts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("candidate_id", sa.Uuid(), sa.ForeignKey("forecast_evaluation_candidates.id"), nullable=False, index=True),
        sa.Column("supersedes_id", sa.Uuid(), sa.ForeignKey("forecast_verdicts.id")),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('confirmed', 'modified', 'rejected')", name="ck_forecast_verdict_decision"),
        sa.CheckConstraint("outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')", name="ck_forecast_verdict_outcome"),
    )
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _IMMUTABLE_TABLES:
            op.execute(f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
            op.execute(f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
    for table in reversed(_IMMUTABLE_TABLES):
        op.drop_table(table)
