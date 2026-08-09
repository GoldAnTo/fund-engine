"""Link reviewed atomic claims to the formal statement they publish.

Revision ID: 0028
Revises: 0027
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("source_statements", sa.Column("atomic_claim_candidate_id", sa.Uuid(), nullable=True))
    op.add_column("atomic_claim_reviews", sa.Column("published_source_statement_id", sa.Uuid(), nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key("fk_source_statements_atomic_claim_candidate_id", "source_statements", "atomic_claim_candidates", ["atomic_claim_candidate_id"], ["id"])
        op.create_foreign_key("fk_atomic_claim_reviews_published_source_statement_id", "atomic_claim_reviews", "source_statements", ["published_source_statement_id"], ["id"])
    op.create_index("ix_source_statements_atomic_claim_candidate_id", "source_statements", ["atomic_claim_candidate_id"])


def downgrade() -> None:
    op.drop_index("ix_source_statements_atomic_claim_candidate_id", table_name="source_statements")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_atomic_claim_reviews_published_source_statement_id", "atomic_claim_reviews", type_="foreignkey")
        op.drop_constraint("fk_source_statements_atomic_claim_candidate_id", "source_statements", type_="foreignkey")
    op.drop_column("atomic_claim_reviews", "published_source_statement_id")
    op.drop_column("source_statements", "atomic_claim_candidate_id")
