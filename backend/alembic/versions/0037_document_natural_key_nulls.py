"""Represent absent document natural keys as NULL, never an empty string.

Revision ID: 0037
Revises: 0036
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0037"
down_revision: Union[str, None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS no_update_document_versions ON document_versions;"
        )
    op.execute("UPDATE document_versions SET natural_key = NULL WHERE natural_key = '';")
    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER no_update_document_versions BEFORE UPDATE ON "
            "document_versions FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    # Empty natural keys are invalid under the unique constraint; NULL is the
    # only safe representation for an absent semantic deduplication key.
    pass
