"""Persist the thesis set selected for each automatic research run.

Revision ID: 0016
Revises: 0015
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("research_runs", sa.Column("scope_thesis_ids", sa.JSON(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            UPDATE research_runs
            SET scope_thesis_ids = scoped.thesis_ids
            FROM (
                SELECT research_tasks.run_id, json_agg(DISTINCT research_tasks.thesis_id) AS thesis_ids
                FROM research_tasks
                WHERE research_tasks.thesis_id IS NOT NULL
                GROUP BY research_tasks.run_id
            ) AS scoped
            WHERE research_runs.id = scoped.run_id
            """
        )


def downgrade() -> None:
    op.drop_column("research_runs", "scope_thesis_ids")
