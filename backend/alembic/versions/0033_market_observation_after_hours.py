"""Record after-hours handling for immutable market observations.

Revision ID: 0033
Revises: 0032
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_observations",
        sa.Column("after_hours_treatment", sa.Text(), nullable=False, server_default="not_recorded"),
    )
    op.alter_column("market_observations", "after_hours_treatment", server_default=None)


def downgrade() -> None:
    op.drop_column("market_observations", "after_hours_treatment")
