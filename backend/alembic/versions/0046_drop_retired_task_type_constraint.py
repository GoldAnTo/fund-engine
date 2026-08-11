"""Remove the retired research-task type constraint from stamped databases.

Revision ID: 0046
Revises: 0045
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0046"
down_revision: Union[str, None] = "0045"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ``ResearchTask.task_type`` is intentionally unconstrained in the active
# model: work types evolve with the run planner.  Older stamped PostgreSQL
# databases retained this removed v0011 constraint and therefore disagreed
# with ORM metadata.
RETIRED_TASK_TYPE_CONSTRAINT = ("research_tasks", "ck_research_task_type")


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    table_name, constraint_name = RETIRED_TASK_TYPE_CONSTRAINT
    existing = {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(table_name)
    }
    if constraint_name in existing:
        op.drop_constraint(constraint_name, table_name, type_="check")


def downgrade() -> None:
    raise RuntimeError(
        "0046 removes a retired planner constraint and is intentionally irreversible."
    )
