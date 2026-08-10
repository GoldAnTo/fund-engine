"""Record Case-scoped fund disclosure sync configurations and replays.

Revision ID: 0040
Revises: 0039
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0040"
down_revision: Union[str, None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fund_disclosure_sync_config_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("frequency", sa.String(length=32), nullable=False),
        sa.Column("fund_codes", sa.JSON(), nullable=False),
        sa.Column("stock_codes", sa.JSON(), nullable=False),
        sa.Column("allow_display", sa.Boolean(), nullable=False),
        sa.Column("changed_by", sa.String(length=128), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("frequency IN ('weekly', 'monthly')", name="ck_fund_disclosure_sync_config_frequency"),
        sa.ForeignKeyConstraint(["research_case_id"], ["research_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("research_case_id", "version", name="uq_fund_disclosure_sync_config_case_version"),
    )
    op.create_index("ix_fund_disclosure_sync_config_versions_research_case_id", "fund_disclosure_sync_config_versions", ["research_case_id"])
    op.create_table(
        "fund_disclosure_sync_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("config_version_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("fund_codes", sa.JSON(), nullable=False),
        sa.Column("stock_codes", sa.JSON(), nullable=False),
        sa.Column("allow_display", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("trigger IN ('manual', 'scheduled', 'retry')", name="ck_fund_disclosure_sync_runs_trigger"),
        sa.ForeignKeyConstraint(["config_version_id"], ["fund_disclosure_sync_config_versions.id"]),
        sa.ForeignKeyConstraint(["research_case_id"], ["research_cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fund_disclosure_sync_runs_research_case_id", "fund_disclosure_sync_runs", ["research_case_id"])
    op.create_index("ix_fund_disclosure_sync_runs_config_version_id", "fund_disclosure_sync_runs", ["config_version_id"])
    op.create_table(
        "fund_disclosure_sync_run_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["fund_disclosure_sync_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seq", name="uq_fund_disclosure_sync_run_events_run_seq"),
    )
    op.create_index("ix_fund_disclosure_sync_run_events_run_id", "fund_disclosure_sync_run_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_fund_disclosure_sync_run_events_run_id", table_name="fund_disclosure_sync_run_events")
    op.drop_table("fund_disclosure_sync_run_events")
    op.drop_index("ix_fund_disclosure_sync_runs_config_version_id", table_name="fund_disclosure_sync_runs")
    op.drop_index("ix_fund_disclosure_sync_runs_research_case_id", table_name="fund_disclosure_sync_runs")
    op.drop_table("fund_disclosure_sync_runs")
    op.drop_index("ix_fund_disclosure_sync_config_versions_research_case_id", table_name="fund_disclosure_sync_config_versions")
    op.drop_table("fund_disclosure_sync_config_versions")
