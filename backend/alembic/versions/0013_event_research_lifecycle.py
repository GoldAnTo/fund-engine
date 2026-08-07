"""Persist immutable event framing and its mutable lifecycle projection.

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = ("event_research_briefs", "event_research_factor_drafts")


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
        "event_research_briefs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("raw_input", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("event_title", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("ticker", sa.String(length=32), nullable=True),
        sa.Column("event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("market_reaction", sa.Text(), nullable=True),
        sa.Column("research_question", sa.Text(), nullable=False),
        sa.Column("extraction_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_research_briefs_case", "event_research_briefs", ["research_case_id"])

    op.create_table(
        "event_research_factor_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("research_case_id", "position", name="uq_event_research_factor_drafts_position"),
    )
    op.create_index(
        "ix_event_research_factor_drafts_case",
        "event_research_factor_drafts",
        ["research_case_id"],
    )

    op.create_table(
        "event_research_lifecycles",
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("active_run_id", sa.Uuid(), sa.ForeignKey("research_runs.id"), nullable=True),
        sa.Column("current_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status_summary", sa.Text(), nullable=False),
        sa.Column("current_gap", sa.Text(), nullable=True),
        sa.Column("next_human_action", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('extracting', 'researching', 'awaiting_key_review', "
            "'continuing', 'awaiting_scope', 'draft_ready', 'published', 'exhausted')",
            name="ck_event_research_lifecycle_status",
        ),
    )

    for table in _IMMUTABLE_TABLES:
        _add_immutable_triggers(table)


def downgrade() -> None:
    for table in reversed(_IMMUTABLE_TABLES):
        _drop_immutable_triggers(table)
    op.drop_table("event_research_lifecycles")
    op.drop_index("ix_event_research_factor_drafts_case", table_name="event_research_factor_drafts")
    op.drop_table("event_research_factor_drafts")
    op.drop_index("ix_event_research_briefs_case", table_name="event_research_briefs")
    op.drop_table("event_research_briefs")
