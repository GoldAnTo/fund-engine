"""instruments and exposure: companies, stocks, funds, valuations, holdings, theme roles

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IMMUTABLE_TABLES = (
    "companies",
    "stocks",
    "fund_companies",
    "funds",
    "valuation_snapshots",
    "holding_disclosures",
    "theme_roles",
)


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "stocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("market", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "fund_companies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "funds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("fund_type", sa.String(64), nullable=False),
        sa.Column("scale", sa.Numeric(), nullable=True),
        sa.Column("establish_date", sa.Date(), nullable=True),
        sa.Column(
            "management_company_id",
            sa.Uuid(),
            sa.ForeignKey("fund_companies.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "valuation_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "stock_id",
            sa.Uuid(),
            sa.ForeignKey("stocks.id"),
            nullable=False,
        ),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(64), nullable=False),
        sa.Column("metric_value", sa.Numeric(), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("definition", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "holding_disclosures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "fund_id",
            sa.Uuid(),
            sa.ForeignKey("funds.id"),
            nullable=False,
        ),
        sa.Column(
            "stock_id",
            sa.Uuid(),
            sa.ForeignKey("stocks.id"),
            nullable=False,
        ),
        sa.Column("weight", sa.Numeric(), nullable=False),
        sa.Column("report_period", sa.Date(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "theme_roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Uuid(),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=True,
        ),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("applicable_from", sa.Date(), nullable=True),
        sa.Column("applicable_to", sa.Date(), nullable=True),
        sa.Column(
            "source_statement_id",
            sa.Uuid(),
            sa.ForeignKey("source_statements.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Defence-in-depth: reject UPDATE/DELETE for every new ledger table.
    # reject_mutable_ledger() was created in migration 0001.
    for table in IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    for table in IMMUTABLE_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
    op.drop_table("theme_roles")
    op.drop_table("holding_disclosures")
    op.drop_table("valuation_snapshots")
    op.drop_table("funds")
    op.drop_table("fund_companies")
    op.drop_table("stocks")
    op.drop_table("companies")
