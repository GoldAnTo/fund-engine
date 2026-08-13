"""Separate preparation worker heartbeats from research-run availability.

Revision ID: 0053
Revises: 0052
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("research_worker_heartbeats") as batch:
        batch.add_column(
            sa.Column(
                "worker_kind",
                sa.String(length=32),
                nullable=False,
                server_default="research_run",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("research_worker_heartbeats") as batch:
        batch.drop_column("worker_kind")
