"""Freeze the declared authority level with each document version.

Revision ID: 0029
Revises: 0028
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column(
            "source_authority",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "source_authority")
