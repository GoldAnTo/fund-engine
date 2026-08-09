"""Add append-only human reviews for AI-proposed Case relations.

Revision ID: 0035
Revises: 0034
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_relation_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("case_relation_id", sa.Uuid(), sa.ForeignKey("case_relations.id"), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("reviewed_relation_id", sa.Uuid(), sa.ForeignKey("case_relations.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('confirmed', 'modified', 'rejected', 'needs_more_evidence')",
            name="ck_case_relation_reviews_outcome",
        ),
        sa.UniqueConstraint(
            "case_relation_id", "idempotency_key", name="uq_case_relation_reviews_idempotency"
        ),
    )
    op.create_index(
        "ix_case_relation_reviews_case_relation_id",
        "case_relation_reviews",
        ["case_relation_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE TRIGGER no_update_case_relation_reviews BEFORE UPDATE ON case_relation_reviews FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute("CREATE TRIGGER no_delete_case_relation_reviews BEFORE DELETE ON case_relation_reviews FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_update_case_relation_reviews ON case_relation_reviews;")
        op.execute("DROP TRIGGER IF EXISTS no_delete_case_relation_reviews ON case_relation_reviews;")
    op.drop_table("case_relation_reviews")
