"""Record audit-visible availability for market snapshots.

Revision ID: 0029
Revises: 0028
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Do not backfill this from created_at: ingestion time is not evidence of
    # when a historical quote was externally visible.
    op.add_column(
        "valuation_snapshots",
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_valuation_snapshots_availability",
        "valuation_snapshots",
        ["stock_id", "as_of_date", "available_at"],
    )
    op.create_table(
        "report_fund_exposures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("report_claim_id", sa.Uuid(), sa.ForeignKey("report_claims.id"), nullable=False),
        sa.Column("report_relation_id", sa.Uuid(), sa.ForeignKey("report_relations.id"), nullable=False),
        sa.Column("stock_id", sa.Uuid(), sa.ForeignKey("stocks.id"), nullable=False),
        sa.Column("fund_id", sa.Uuid(), sa.ForeignKey("funds.id"), nullable=True),
        sa.Column("holding_disclosure_id", sa.Uuid(), sa.ForeignKey("holding_disclosures.id"), nullable=True),
        sa.Column("window", sa.String(length=8), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("weight", sa.Numeric(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("collection_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"window" IN (\'1d\', \'5d\')', name="ck_report_fund_window"),
        sa.CheckConstraint("status IN ('verified', 'insufficient')", name="ck_report_fund_exposure_status"),
        sa.UniqueConstraint("collection_key", name="uq_report_fund_exposure_key"),
    )
    op.create_index(
        "ix_report_fund_exposures_claim_window",
        "report_fund_exposures",
        ["report_claim_id", "window"],
    )
    if op.get_bind().dialect.name == "postgresql":
        for action in ("update", "delete"):
            op.execute(
                f"CREATE TRIGGER no_{action}_report_fund_exposures BEFORE {action.upper()} "
                "ON report_fund_exposures FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
            )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM report_fund_exposures")).scalar_one()
    if count:
        raise RuntimeError("refusing to downgrade: report_fund_exposures contains immutable records")
    if op.get_bind().dialect.name == "postgresql":
        for action in ("delete", "update"):
            op.execute(f"DROP TRIGGER IF EXISTS no_{action}_report_fund_exposures ON report_fund_exposures;")
    op.drop_index("ix_report_fund_exposures_claim_window", table_name="report_fund_exposures")
    op.drop_table("report_fund_exposures")
    op.drop_index("ix_valuation_snapshots_availability", table_name="valuation_snapshots")
    op.drop_column("valuation_snapshots", "available_at")
