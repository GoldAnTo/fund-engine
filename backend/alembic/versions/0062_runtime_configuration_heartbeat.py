"""Persist redacted service-owned configuration readiness.

Revision ID: 0062
Revises: 0061
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0062"
down_revision: Union[str, None] = "0061"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_worker_heartbeats",
        sa.Column(
            "configuration_status",
            sa.String(length=16),
            server_default="unknown",
            nullable=False,
        ),
    )
    op.add_column(
        "research_worker_heartbeats",
        sa.Column(
            "configuration_issues",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    with op.batch_alter_table("research_worker_heartbeats") as batch_op:
        batch_op.create_check_constraint(
            "ck_research_worker_heartbeats_configuration_status",
            "configuration_status IN ('configured', 'misconfigured', 'unknown')",
        )


def downgrade() -> None:
    with op.batch_alter_table("research_worker_heartbeats") as batch_op:
        batch_op.drop_constraint(
            "ck_research_worker_heartbeats_configuration_status",
            type_="check",
        )
    op.drop_column("research_worker_heartbeats", "configuration_issues")
    op.drop_column("research_worker_heartbeats", "configuration_status")
