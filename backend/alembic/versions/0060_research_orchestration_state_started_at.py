"""Persist the start time of the current orchestration state.

Revision ID: 0060
Revises: 0059
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "research_orchestrations",
        sa.Column("state_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        true_transition = (
            "SELECT MAX(e.created_at) FROM research_orchestration_events e "
            "WHERE e.orchestration_id = research_orchestrations.id "
            "AND e.payload_json ->> 'to_state' = research_orchestrations.state "
            "AND e.payload_json ->> 'from_state' <> "
            "e.payload_json ->> 'to_state'"
        )
    else:
        true_transition = (
            "SELECT MAX(e.created_at) FROM research_orchestration_events e "
            "WHERE e.orchestration_id = research_orchestrations.id "
            "AND json_extract(e.payload_json, '$.to_state') = "
            "research_orchestrations.state "
            "AND json_extract(e.payload_json, '$.from_state') <> "
            "json_extract(e.payload_json, '$.to_state')"
        )
    op.execute(
        sa.text(
            "UPDATE research_orchestrations SET state_started_at = "
            f"COALESCE(({true_transition}), created_at) "
            "WHERE state_started_at IS NULL"
        )
    )
    with op.batch_alter_table("research_orchestrations") as batch_op:
        batch_op.alter_column(
            "state_started_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("research_orchestrations") as batch_op:
        batch_op.drop_column("state_started_at")
