"""Add bounded ledger-read indexes for China impact data collection.

Revision ID: 0022
Revises: 0021
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # These are ordinary B-tree indexes, supported by both SQLite and PostgreSQL.
    op.create_index(
        "ix_valuation_snapshots_stock_metric_as_of",
        "valuation_snapshots",
        ["stock_id", "metric_name", "as_of_date"],
    )
    op.create_index(
        "ix_holding_disclosures_stock_published_report",
        "holding_disclosures",
        ["stock_id", "published_at", "report_period"],
    )
    op.create_index("ix_stocks_company_market", "stocks", ["company_id", "market"])


def downgrade() -> None:
    op.drop_index("ix_stocks_company_market", table_name="stocks")
    op.drop_index(
        "ix_holding_disclosures_stock_published_report",
        table_name="holding_disclosures",
    )
    op.drop_index(
        "ix_valuation_snapshots_stock_metric_as_of",
        table_name="valuation_snapshots",
    )
