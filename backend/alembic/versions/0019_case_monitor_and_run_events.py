"""Add immutable monitor versions and structured research-run activity.

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = ("case_monitor_versions", "research_run_events")


def _add_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )
    op.execute(
        f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
        "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def _drop_immutable_triggers(table: str) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
    op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")


def upgrade() -> None:
    op.create_table(
        "case_monitor_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("frequency", sa.String(length=64), nullable=False),
        sa.Column("factor_ids", sa.JSON(), nullable=False),
        sa.Column("allowed_source_types", sa.JSON(), nullable=False),
        sa.Column("next_verification_event", sa.Text(), nullable=False),
        sa.Column("budget", sa.Integer(), nullable=False),
        sa.Column("changed_by", sa.String(length=128), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("research_case_id", "version", name="uq_case_monitor_versions_case_version"),
    )
    op.create_index("ix_case_monitor_versions_case", "case_monitor_versions", ["research_case_id"])
    op.add_column(
        "research_runs",
        sa.Column(
            "monitor_version_id",
            sa.Uuid(),
            sa.ForeignKey("case_monitor_versions.id"),
            nullable=True,
        ),
    )
    op.create_table(
        "research_run_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_research_run_events_run_seq"),
    )
    op.create_index("ix_research_run_events_run", "research_run_events", ["run_id"])
    for table in _IMMUTABLE_TABLES:
        _add_immutable_triggers(table)


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        _drop_immutable_triggers(table)
    op.drop_index("ix_research_run_events_run", table_name="research_run_events")
    op.drop_table("research_run_events")
    op.drop_column("research_runs", "monitor_version_id")
    op.drop_index("ix_case_monitor_versions_case", table_name="case_monitor_versions")
    op.drop_table("case_monitor_versions")
