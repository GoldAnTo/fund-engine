"""Store reviewed, source-backed Case market-instrument bindings.

Revision ID: 0032
Revises: 0031
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_instrument_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("stock_id", sa.Uuid(), sa.ForeignKey("stocks.id"), nullable=True),
        sa.Column("source_statement_id", sa.Uuid(), sa.ForeignKey("source_statements.id"), nullable=False),
        sa.Column("relationship_role", sa.String(length=32), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("relationship_role IN ('directly_affected', 'supply_chain', 'competitor', 'beneficiary', 'risk_exposure')", name="ck_market_instrument_bindings_role"),
        sa.CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_market_instrument_bindings_review_state"),
    )
    op.create_index("ix_market_instrument_bindings_case", "market_instrument_bindings", ["research_case_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE TRIGGER no_update_market_instrument_bindings BEFORE UPDATE ON market_instrument_bindings FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute("CREATE TRIGGER no_delete_market_instrument_bindings BEFORE DELETE ON market_instrument_bindings FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_update_market_instrument_bindings ON market_instrument_bindings;")
        op.execute("DROP TRIGGER IF EXISTS no_delete_market_instrument_bindings ON market_instrument_bindings;")
    op.drop_table("market_instrument_bindings")
