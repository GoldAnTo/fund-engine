"""Persist append-only event conclusion drafts and publications.

Revision ID: 0014
Revises: 0013
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_research_conclusions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("primary_factor", sa.Text(), nullable=True),
        sa.Column("evidence_link_ids", sa.JSON(), nullable=False),
        sa.Column("based_on_conclusion_id", sa.Uuid(), sa.ForeignKey("event_research_conclusions.id"), nullable=True),
        sa.Column("reviewer", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_event_research_conclusions_case", "event_research_conclusions", ["research_case_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE TRIGGER no_update_event_research_conclusions BEFORE UPDATE ON event_research_conclusions FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute("CREATE TRIGGER no_delete_event_research_conclusions BEFORE DELETE ON event_research_conclusions FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_update_event_research_conclusions ON event_research_conclusions;")
        op.execute("DROP TRIGGER IF EXISTS no_delete_event_research_conclusions ON event_research_conclusions;")
    op.drop_index("ix_event_research_conclusions_case", table_name="event_research_conclusions")
    op.drop_table("event_research_conclusions")
