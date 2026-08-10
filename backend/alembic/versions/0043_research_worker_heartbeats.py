"""Record liveness of the durable ResearchRun worker.

Revision ID: 0043
Revises: 0042
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_worker_heartbeats",
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )


def downgrade() -> None:
    op.drop_table("research_worker_heartbeats")
