"""Add bounded lookup indexes for report market collection.

Revision ID: 0030
Revises: 0029
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_source_spans_document_version", "source_spans", ["document_version_id"])


def downgrade() -> None:
    op.drop_index("ix_source_spans_document_version", table_name="source_spans")
