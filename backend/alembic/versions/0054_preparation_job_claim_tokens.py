"""Add an ownership lease to preparation worker jobs.

Revision ID: 0054
Revises: 0053
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable is intentional: non-preparation and pre-existing jobs never
    # need a lease until a preparation worker claims them.
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("claim_token", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("claim_token")
