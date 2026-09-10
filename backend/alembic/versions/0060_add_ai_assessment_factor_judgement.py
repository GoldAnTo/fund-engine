"""Freeze structured factor judgements with AI assessments.

Revision ID: 0060
Revises: 0059
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("ai_assessments") as batch:
        batch.add_column(
            sa.Column("factor_judgement", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_assessments") as batch:
        batch.drop_column("factor_judgement")
