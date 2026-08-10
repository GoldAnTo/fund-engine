"""Record immutable target, actual, candidate and human forecast verdicts.

Revision ID: 0041
Revises: 0040
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0041"
down_revision: Union[str, None] = "0040"
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
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("key_factor_id", sa.Uuid(), nullable=False),
        sa.Column("report_claim_id", sa.Uuid(), nullable=False),
        sa.Column("forecast_source_statement_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_source_statement_id", sa.Uuid(), nullable=True),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("entity_key", sa.String(length=128), nullable=False),
        sa.Column("baseline_value", sa.Numeric(precision=24, scale=6), nullable=True),
        sa.Column("expected_value", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("forecast_period_start", sa.Date(), nullable=False),
        sa.Column("forecast_period_end", sa.Date(), nullable=False),
        sa.Column("comparator", sa.String(length=32), nullable=False),
        sa.Column("relative_tolerance", sa.Numeric(precision=12, scale=8), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("comparator IN ('at_least', 'at_most', 'within_tolerance')", name="ck_forecast_target_comparator"),
        sa.CheckConstraint("relative_tolerance IS NULL OR relative_tolerance >= 0", name="ck_forecast_target_tolerance"),
        sa.ForeignKeyConstraint(["research_case_id"], ["research_cases.id"]),
        sa.ForeignKeyConstraint(["key_factor_id"], ["key_factors.id"]),
        sa.ForeignKeyConstraint(["report_claim_id"], ["report_claims.id"]),
        sa.ForeignKeyConstraint(["forecast_source_statement_id"], ["source_statements.id"]),
        sa.ForeignKeyConstraint(["baseline_source_statement_id"], ["source_statements.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecast_target_versions_research_case_id", "forecast_target_versions", ["research_case_id"])
    op.create_index("ix_forecast_target_versions_key_factor_id", "forecast_target_versions", ["key_factor_id"])
    op.create_table(
        "actual_metric_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("forecast_target_id", sa.Uuid(), nullable=False),
        sa.Column("source_statement_id", sa.Uuid(), nullable=False),
        sa.Column("entity_key", sa.String(length=128), nullable=False),
        sa.Column("observed_value", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("observed_period_start", sa.Date(), nullable=False),
        sa.Column("observed_period_end", sa.Date(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by", sa.String(length=128), nullable=False),
        sa.Column("record_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["forecast_target_id"], ["forecast_target_versions.id"]),
        sa.ForeignKeyConstraint(["source_statement_id"], ["source_statements.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_actual_metric_observations_forecast_target_id", "actual_metric_observations", ["forecast_target_id"])
    op.create_table(
        "forecast_evaluation_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("forecast_target_id", sa.Uuid(), nullable=False),
        sa.Column("actual_observation_id", sa.Uuid(), nullable=False),
        sa.Column("cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')", name="ck_forecast_evaluation_outcome"),
        sa.ForeignKeyConstraint(["forecast_target_id"], ["forecast_target_versions.id"]),
        sa.ForeignKeyConstraint(["actual_observation_id"], ["actual_metric_observations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecast_evaluation_candidates_forecast_target_id", "forecast_evaluation_candidates", ["forecast_target_id"])
    op.create_table(
        "forecast_verdicts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('confirmed', 'modified', 'rejected')", name="ck_forecast_verdict_decision"),
        sa.CheckConstraint("outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')", name="ck_forecast_verdict_outcome"),
        sa.ForeignKeyConstraint(["candidate_id"], ["forecast_evaluation_candidates.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["forecast_verdicts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecast_verdicts_candidate_id", "forecast_verdicts", ["candidate_id"])
    if op.get_bind().dialect.name == "postgresql":
        for table in _IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
            )
            op.execute(
                f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
    op.drop_index("ix_forecast_verdicts_candidate_id", table_name="forecast_verdicts")
    op.drop_table("forecast_verdicts")
    op.drop_index("ix_forecast_evaluation_candidates_forecast_target_id", table_name="forecast_evaluation_candidates")
    op.drop_table("forecast_evaluation_candidates")
    op.drop_index("ix_actual_metric_observations_forecast_target_id", table_name="actual_metric_observations")
    op.drop_table("actual_metric_observations")
    op.drop_index("ix_forecast_target_versions_key_factor_id", table_name="forecast_target_versions")
    op.drop_index("ix_forecast_target_versions_research_case_id", table_name="forecast_target_versions")
    op.drop_table("forecast_target_versions")
