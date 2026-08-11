"""Persist reproducible source-bound key-factor candidate parsing.

Revision ID: 0047
Revises: 0046
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = ("key_factor_candidate_runs", "key_factor_candidates")


def upgrade() -> None:
    op.create_table(
        "key_factor_candidate_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("source_statement_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("skipped_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('completed')", name="ck_key_factor_candidate_runs_status"),
        sa.ForeignKeyConstraint(["research_case_id"], ["research_cases.id"]),
        sa.ForeignKeyConstraint(["source_statement_id"], ["source_statements.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_key_factor_candidate_runs_research_case_id", "key_factor_candidate_runs", ["research_case_id"])
    op.create_index("ix_key_factor_candidate_runs_source_statement_id", "key_factor_candidate_runs", ["source_statement_id"])
    op.create_table(
        "key_factor_candidates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("expected_direction", sa.String(length=16), nullable=False),
        sa.Column("verification_window_start", sa.Date(), nullable=False),
        sa.Column("verification_window_end", sa.Date(), nullable=False),
        sa.Column("support_condition", sa.Text(), nullable=False),
        sa.Column("refutation_condition", sa.Text(), nullable=False),
        sa.Column("next_verification_event", sa.Text(), nullable=False),
        sa.Column("evidence_excerpt", sa.Text(), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("review_state IN ('machine_generated')", name="ck_key_factor_candidates_review_state"),
        sa.ForeignKeyConstraint(["run_id"], ["key_factor_candidate_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_key_factor_candidates_run_id", "key_factor_candidates", ["run_id"])
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
    op.drop_index("ix_key_factor_candidates_run_id", table_name="key_factor_candidates")
    op.drop_table("key_factor_candidates")
    op.drop_index("ix_key_factor_candidate_runs_source_statement_id", table_name="key_factor_candidate_runs")
    op.drop_index("ix_key_factor_candidate_runs_research_case_id", table_name="key_factor_candidate_runs")
    op.drop_table("key_factor_candidate_runs")
