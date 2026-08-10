"""Store append-only reviewed market-expression records.

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _immutable(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute(f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def _table_columns(table: str) -> set[str]:
    """Return the columns visible to this migration on every supported dialect."""
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def _prepare_legacy_report_claims() -> str | None:
    """Make a pre-migration report_claims table safe to upgrade without loss.

    Early local databases could be bootstrapped from ORM metadata before this
    migration existed.  Those tables use ``statement``/``kind`` rather than
    the reviewed market-expression fields below.  Preserve every legacy row
    in an explicitly named table, then copy it into the new reviewed schema.
    """
    inspector = sa.inspect(op.get_bind())
    if "report_claims" not in inspector.get_table_names():
        return None

    required = {
        "id",
        "research_case_id",
        "source_statement_id",
        "text",
        "claim_kind",
        "asserted_by",
        "review_state",
        "created_at",
    }
    if required.issubset(_table_columns("report_claims")):
        return "already_current"

    legacy_table = "report_claims_legacy_pre_0021"
    if legacy_table in inspector.get_table_names():
        raise RuntimeError(
            "Cannot reconcile report_claims: preserved legacy table already exists. "
            "Resolve it explicitly before retrying the migration."
        )
    op.rename_table("report_claims", legacy_table)
    return legacy_table


def _copy_legacy_report_claims(legacy_table: str | None) -> None:
    if legacy_table is None or legacy_table == "already_current":
        return
    op.execute(
        f"""
        INSERT INTO report_claims (
            id, research_case_id, source_statement_id, text, claim_kind,
            asserted_period, asserted_by, review_state, reviewed_by,
            review_reason, reviewed_at, created_at
        )
        SELECT
            id,
            research_case_id,
            source_statement_id,
            statement,
            CASE
                WHEN kind IN ('disclosed_fact', 'forecast', 'research_opinion') THEN kind
                ELSE 'research_opinion'
            END,
            NULL,
            'legacy-schema-reconciliation',
            'machine_generated',
            NULL,
            'Migrated from immutable pre-0021 report_claims record.',
            NULL,
            created_at
        FROM {legacy_table}
        """
    )


def upgrade() -> None:
    legacy_report_claims = _prepare_legacy_report_claims()
    if legacy_report_claims != "already_current":
        op.create_table(
            "report_claims",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
            sa.Column("source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=True),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("claim_kind", sa.String(length=32), nullable=False),
            sa.Column("asserted_period", sa.Date(), nullable=True),
            sa.Column("asserted_by", sa.Text(), nullable=False),
            sa.Column("review_state", sa.String(length=32), nullable=False),
            sa.Column("reviewed_by", sa.String(length=128), nullable=True),
            sa.Column("review_reason", sa.Text(), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.CheckConstraint("claim_kind IN ('disclosed_fact', 'forecast', 'research_opinion')", name="ck_report_claims_kind"),
            sa.CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_report_claims_review_state"),
        )
        _copy_legacy_report_claims(legacy_report_claims)
    op.create_index("ix_report_claims_case", "report_claims", ["research_case_id"])
    op.create_table(
        "key_factors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("report_claim_id", sa.Uuid(), sa.ForeignKey("report_claims.id"), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("expected_direction", sa.String(length=16), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("allowed_source_types", sa.JSON(), nullable=False),
        sa.Column("verification_window_start", sa.Date(), nullable=True),
        sa.Column("verification_window_end", sa.Date(), nullable=True),
        sa.Column("support_condition", sa.Text(), nullable=False),
        sa.Column("refutation_condition", sa.Text(), nullable=False),
        sa.Column("next_verification_event", sa.Text(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expected_direction IN ('positive', 'negative', 'neutral')", name="ck_key_factors_direction"),
        sa.CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_key_factors_review_state"),
    )
    op.create_index("ix_key_factors_case", "key_factors", ["research_case_id"])
    op.create_table(
        "claim_verifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key_factor_id", sa.Uuid(), sa.ForeignKey("key_factors.id"), nullable=False),
        sa.Column("source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('supported', 'contradicted', 'insufficient_evidence', 'not_due')", name="ck_claim_verifications_outcome"),
        sa.CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_claim_verifications_review_state"),
    )
    op.create_index("ix_claim_verifications_factor", "claim_verifications", ["key_factor_id"])
    op.create_table(
        "fundamental_impacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("key_factor_id", sa.Uuid(), sa.ForeignKey("key_factors.id"), nullable=False),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("stock_id", sa.Uuid(), sa.ForeignKey("stocks.id"), nullable=True),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("expected_direction", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=True),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("expected_direction IN ('positive', 'negative', 'neutral')", name="ck_fundamental_impacts_direction"),
        sa.CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_fundamental_impacts_review_state"),
    )
    op.create_index("ix_fundamental_impacts_case", "fundamental_impacts", ["research_case_id"])
    op.create_table(
        "market_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("key_factor_id", sa.Uuid(), sa.ForeignKey("key_factors.id"), nullable=True),
        sa.Column("stock_id", sa.Uuid(), sa.ForeignKey("stocks.id"), nullable=False),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_label", sa.String(length=64), nullable=False),
        sa.Column("benchmark", sa.Text(), nullable=False),
        sa.Column("price_source", sa.String(length=128), nullable=False),
        sa.Column("relative_return", sa.Numeric(), nullable=True),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_market_observations_review_state"),
    )
    op.create_index("ix_market_observations_case", "market_observations", ["research_case_id"])
    for table in ("report_claims", "key_factors", "claim_verifications", "fundamental_impacts", "market_observations"):
        _immutable(table)


def downgrade() -> None:
    tables = ("market_observations", "fundamental_impacts", "claim_verifications", "key_factors", "report_claims")
    if op.get_bind().dialect.name == "postgresql":
        for table in tables:
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
    for table in tables:
        op.drop_table(table)
