"""Bind newly created event conclusion drafts to their factor scope.

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Historical conclusions cannot reliably be assigned the scope that was
    # current when they were drafted. Preserve them with NULL for audit; the
    # publish service requires a non-NULL, current scope and forces a new draft.
    op.add_column(
        "event_research_conclusions",
        sa.Column("scope_version_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_event_research_conclusions_scope_version",
        "event_research_conclusions",
        "event_research_scope_versions",
        ["scope_version_id"],
        ["id"],
    )
    op.create_index(
        "ix_event_research_conclusions_scope_version",
        "event_research_conclusions",
        ["scope_version_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_research_conclusions_scope_version",
        table_name="event_research_conclusions",
    )
    op.drop_constraint(
        "fk_event_research_conclusions_scope_version",
        "event_research_conclusions",
        type_="foreignkey",
    )
    op.drop_column("event_research_conclusions", "scope_version_id")
