"""Automatic research runs and tasks.

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Some environments were stamped past 0010 while the domain-event sequence
    # was missing. Repair it defensively before auto-research emits events.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE SEQUENCE IF NOT EXISTS domain_events_seq_seq")
        op.execute("ALTER SEQUENCE domain_events_seq_seq OWNED BY domain_events.seq")
        op.execute(
            "ALTER TABLE domain_events ALTER COLUMN seq "
            "SET DEFAULT nextval('domain_events_seq_seq')"
        )
    op.create_table(
        "research_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("budget_used", sa.Integer(), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["research_case_id"], ["research_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_runs_case_created", "research_runs", ["research_case_id", "created_at"])
    op.create_table(
        "research_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("thesis_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("gap_reason", sa.Text(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"]),
        sa.ForeignKeyConstraint(["research_case_id"], ["research_cases.id"]),
        sa.ForeignKeyConstraint(["thesis_id"], ["theses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("task_type IN ('support','contradict','result','alternative')", name="ck_research_task_type"),
    )
    op.create_index("ix_research_tasks_run_status", "research_tasks", ["run_id", "status"])
    op.create_index("ix_research_tasks_thesis_type", "research_tasks", ["thesis_id", "task_type"])


def downgrade() -> None:
    op.drop_index("ix_research_tasks_thesis_type", table_name="research_tasks")
    op.drop_index("ix_research_tasks_run_status", table_name="research_tasks")
    op.drop_table("research_tasks")
    op.drop_index("ix_research_runs_case_created", table_name="research_runs")
    op.drop_table("research_runs")
