"""Record immutable intake source provenance for event cases.

Revision ID: 0022
Revises: 0021
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("event_research_briefs", sa.Column("source_type", sa.String(length=32), nullable=False, server_default="pasted_snapshot"))
    op.add_column("event_research_briefs", sa.Column("source_metadata", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("event_research_briefs", "source_metadata")
    op.drop_column("event_research_briefs", "source_type")
