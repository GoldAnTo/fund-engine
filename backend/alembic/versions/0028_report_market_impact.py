"""Append report market-window observations and confounders.

Revision ID: 0028
Revises: 0027
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = ("report_market_observations", "report_market_confounders")


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "report_market_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("report_claim_id", sa.Uuid(), sa.ForeignKey("report_claims.id"), nullable=False),
        sa.Column("report_relation_id", sa.Uuid(), sa.ForeignKey("report_relations.id"), nullable=True),
        sa.Column("stock_id", sa.Uuid(), sa.ForeignKey("stocks.id"), nullable=True),
        sa.Column("valuation_snapshot_id", sa.Uuid(), sa.ForeignKey("valuation_snapshots.id"), nullable=True),
        sa.Column("window", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("metric_name", sa.String(length=64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("collection_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"window" IN (\'1d\', \'5d\')', name="ck_report_market_window"),
        sa.CheckConstraint(
            "kind IN ('target_market', 'peer_control', 'industry_control')",
            name="ck_report_market_observation_kind",
        ),
        sa.CheckConstraint(
            "status IN ('verified', 'insufficient')",
            name="ck_report_market_observation_status",
        ),
        sa.UniqueConstraint("collection_key", name="uq_report_market_observation_key"),
    )
    op.create_index(
        "ix_report_market_observations_claim_window",
        "report_market_observations",
        ["report_claim_id", "window"],
    )
    op.create_table(
        "report_market_confounders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("report_claim_id", sa.Uuid(), sa.ForeignKey("report_claims.id"), nullable=False),
        sa.Column("source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=False),
        sa.Column("window", sa.String(length=8), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("collection_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('"window" IN (\'1d\', \'5d\')', name="ck_report_confounder_window"),
        sa.CheckConstraint(
            "kind IN ('announcement', 'earnings', 'policy', 'news')",
            name="ck_report_confounder_kind",
        ),
        sa.UniqueConstraint("collection_key", name="uq_report_market_confounder_key"),
    )
    op.create_index(
        "ix_report_market_confounders_claim_window",
        "report_market_confounders",
        ["report_claim_id", "window"],
    )
    for table in _IMMUTABLE_TABLES:
        _trigger(table, "update")
        _trigger(table, "delete")


def downgrade() -> None:
    for table in _IMMUTABLE_TABLES:
        count = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(
                f"refusing to downgrade: {table} contains {count} immutable records; "
                "export or purge them first"
            )
    if op.get_bind().dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};")
    op.drop_index("ix_report_market_confounders_claim_window", table_name="report_market_confounders")
    op.drop_table("report_market_confounders")
    op.drop_index("ix_report_market_observations_claim_window", table_name="report_market_observations")
    op.drop_table("report_market_observations")
