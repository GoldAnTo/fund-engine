"""Persist one-click automatic research identity and terminal state.

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


_LIFECYCLE_STATUS_WITH_COMPLETED = (
    "status IN ('extracting', 'researching', 'awaiting_key_review', "
    "'continuing', 'awaiting_scope', 'draft_ready', 'completed', "
    "'published', 'exhausted')"
)
_LEGACY_LIFECYCLE_STATUS = (
    "status IN ('extracting', 'researching', 'awaiting_key_review', "
    "'continuing', 'awaiting_scope', 'draft_ready', 'published', 'exhausted')"
)


def upgrade() -> None:
    with op.batch_alter_table("event_research_briefs") as batch:
        batch.add_column(
            sa.Column(
                "workflow_mode",
                sa.String(length=16),
                nullable=False,
                server_default="reviewed",
            )
        )
        batch.create_check_constraint(
            "ck_event_research_briefs_workflow_mode",
            "workflow_mode IN ('reviewed', 'automatic')",
        )

    with op.batch_alter_table("event_research_lifecycles") as batch:
        batch.drop_constraint("ck_event_research_lifecycle_status", type_="check")
        batch.create_check_constraint(
            "ck_event_research_lifecycle_status",
            _LIFECYCLE_STATUS_WITH_COMPLETED,
        )


def downgrade() -> None:
    op.execute(
        "UPDATE event_research_lifecycles "
        "SET status = 'draft_ready' WHERE status = 'completed'"
    )
    with op.batch_alter_table("event_research_lifecycles") as batch:
        batch.drop_constraint("ck_event_research_lifecycle_status", type_="check")
        batch.create_check_constraint(
            "ck_event_research_lifecycle_status",
            _LEGACY_LIFECYCLE_STATUS,
        )

    with op.batch_alter_table("event_research_briefs") as batch:
        batch.drop_constraint(
            "ck_event_research_briefs_workflow_mode", type_="check"
        )
        batch.drop_column("workflow_mode")
