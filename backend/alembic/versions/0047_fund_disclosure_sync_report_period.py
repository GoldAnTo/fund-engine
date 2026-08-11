"""Add frozen report periods to fund-disclosure configuration and runs.

Revision ID: 0047
Revises: 0046
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0047"
down_revision: Union[str, None] = "0046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fund_disclosure_sync_config_versions",
        sa.Column("report_period", sa.Date(), nullable=True),
    )
    op.add_column(
        "fund_disclosure_sync_runs",
        sa.Column("report_period", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fund_disclosure_sync_runs", "report_period")
    op.drop_column("fund_disclosure_sync_config_versions", "report_period")
