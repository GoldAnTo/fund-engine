"""Add frozen report periods to fund-disclosure configuration and runs.

Revision ID: 0048
Revises: 0047
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0048"
down_revision: Union[str, None] = "0047"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in (
        "fund_disclosure_sync_config_versions",
        "fund_disclosure_sync_runs",
    ):
        columns = {column["name"] for column in inspector.get_columns(table_name)}
        if "report_period" not in columns:
            op.add_column(table_name, sa.Column("report_period", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("fund_disclosure_sync_runs", "report_period")
    op.drop_column("fund_disclosure_sync_config_versions", "report_period")
