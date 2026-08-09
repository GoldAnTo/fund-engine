"""Store append-only, reviewed Case relationships.

Revision ID: 0020
Revises: 0019
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("target_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("relation_type IN ('shared_driver', 'follow_up_validation', 'potential_conflict', 'shared_material')", name="ck_case_relations_type"),
        sa.CheckConstraint("review_state IN ('machine_generated', 'reviewed', 'rejected')", name="ck_case_relations_review_state"),
        sa.CheckConstraint("source_case_id <> target_case_id", name="ck_case_relations_distinct_cases"),
    )
    op.create_index("ix_case_relations_source", "case_relations", ["source_case_id"])
    op.create_index("ix_case_relations_target", "case_relations", ["target_case_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE TRIGGER no_update_case_relations BEFORE UPDATE ON case_relations FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute("CREATE TRIGGER no_delete_case_relations BEFORE DELETE ON case_relations FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_update_case_relations ON case_relations;")
        op.execute("DROP TRIGGER IF EXISTS no_delete_case_relations ON case_relations;")
    op.drop_index("ix_case_relations_target", table_name="case_relations")
    op.drop_index("ix_case_relations_source", table_name="case_relations")
    op.drop_table("case_relations")
