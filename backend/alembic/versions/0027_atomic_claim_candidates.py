"""Add immutable source-grounded atomic claim candidates and reviews.

Revision ID: 0027
Revises: 0026
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = ("atomic_claim_candidates", "atomic_claim_reviews")


def _immutable(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")
        op.execute(f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();")


def upgrade() -> None:
    op.create_table(
        "atomic_claim_candidates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_span_id", sa.Uuid(), sa.ForeignKey("source_spans.id"), nullable=False),
        sa.Column("canonical_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False),
        sa.Column("quote_end", sa.Integer(), nullable=False),
        sa.Column("quote_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("assertion_actor", sa.Text(), nullable=True),
        sa.Column("authority_level", sa.String(length=32), nullable=False),
        sa.Column("structured_fields", sa.JSON(), nullable=False),
        sa.Column("validation_result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_atomic_claim_candidates_source_span_id", "atomic_claim_candidates", ["source_span_id"])
    op.create_table(
        "atomic_claim_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("atomic_claim_candidate_id", sa.Uuid(), sa.ForeignKey("atomic_claim_candidates.id"), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("atomic_claim_candidate_id", "idempotency_key", name="uq_atomic_claim_reviews_idempotency"),
    )
    op.create_index("ix_atomic_claim_reviews_atomic_claim_candidate_id", "atomic_claim_reviews", ["atomic_claim_candidate_id"])
    for table in _TABLES:
        _immutable(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")
            op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
        op.drop_table(table)
