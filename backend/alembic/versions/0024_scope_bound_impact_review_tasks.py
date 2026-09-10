"""Scope-bind and atomically deduplicate company-impact review tasks.

Revision ID: 0024
Revises: 0023
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode keeps the ALTER portable to SQLite while preserving the same
    # unique key PostgreSQL uses to resolve concurrent review discoveries.
    with op.batch_alter_table("task_items") as batch:
        batch.add_column(
            sa.Column(
                "scope_version_id",
                sa.Uuid(),
                sa.ForeignKey(
                    "event_research_scope_versions.id",
                    name="fk_task_items_scope_version",
                ),
                nullable=True,
            )
        )
        batch.create_unique_constraint(
            "uq_task_items_type_ref_scope",
            ["task_type", "ref_type", "ref_id", "scope_version_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("task_items") as batch:
        batch.drop_constraint("uq_task_items_type_ref_scope", type_="unique")
        batch.drop_column("scope_version_id")
