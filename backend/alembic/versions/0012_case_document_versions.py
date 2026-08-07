"""Attach immutable document versions to the case that ingested them.

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "case_document_versions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("research_case_id", "document_version_id", name="uq_case_document_versions"),
    )
    op.create_index(
        "ix_case_document_versions_case_document",
        TABLE,
        ["research_case_id", "document_version_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_update_{TABLE} BEFORE UPDATE ON {TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{TABLE} BEFORE DELETE ON {TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{TABLE} ON {TABLE};")
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{TABLE} ON {TABLE};")
    op.drop_index("ix_case_document_versions_case_document", table_name=TABLE)
    op.drop_table(TABLE)
