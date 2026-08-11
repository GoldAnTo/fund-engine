"""Add immutable filing-version metadata to historical holding disclosures.

Revision ID: 0049
Revises: 0048
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0049"
down_revision: Union[str, None] = "0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("holding_disclosures") as batch:
        batch.add_column(
            sa.Column(
                "filing_kind", sa.String(length=16), nullable=False, server_default="other"
            )
        )
        batch.add_column(
            sa.Column("supersedes_disclosure_id", sa.Uuid(), nullable=True)
        )
        batch.create_check_constraint(
            "ck_holding_disclosures_filing_kind",
            "filing_kind IN ('quarterly', 'annual', 'correction', 'other')",
        )
        batch.create_foreign_key(
            "fk_holding_disclosures_supersedes_disclosure_id",
            "holding_disclosures",
            ["supersedes_disclosure_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("holding_disclosures") as batch:
        batch.drop_constraint("fk_holding_disclosures_supersedes_disclosure_id", type_="foreignkey")
        batch.drop_constraint("ck_holding_disclosures_filing_kind", type_="check")
        batch.drop_column("supersedes_disclosure_id")
        batch.drop_column("filing_kind")
