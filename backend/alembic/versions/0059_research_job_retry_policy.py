"""Persist bounded automatic-research retry state.

Revision ID: 0059
Revises: 0058
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0059"
down_revision: Union[str, None] = "0058"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "failure_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "jobs", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("retry_policy_version", sa.String(64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("jobs", "retry_policy_version")
    op.drop_column("jobs", "next_retry_at")
    op.drop_column("jobs", "failure_count")
