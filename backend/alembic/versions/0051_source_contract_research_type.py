"""Separate source acquisition from the research source category.

Revision ID: 0051
Revises: 0050
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0051"
down_revision: Union[str, None] = "0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_immutable_update_trigger() -> None:
    op.execute(
        "CREATE TRIGGER no_update_source_contracts BEFORE UPDATE ON "
        "source_contracts FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
    )


def upgrade() -> None:
    bind = op.get_bind()
    is_postgresql = bind.dialect.name == "postgresql"

    op.add_column(
        "source_contracts",
        sa.Column("research_source_type", sa.String(length=32), nullable=True),
    )
    if is_postgresql:
        op.execute("DROP TRIGGER IF EXISTS no_update_source_contracts ON source_contracts;")

    op.execute(
        "UPDATE source_contracts SET research_source_type = source_type "
        "WHERE research_source_type IS NULL"
    )

    if is_postgresql:
        op.alter_column("source_contracts", "research_source_type", nullable=False)
        _create_immutable_update_trigger()
    else:
        with op.batch_alter_table("source_contracts") as batch:
            batch.alter_column(
                "research_source_type", existing_type=sa.String(length=32), nullable=False
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_column("source_contracts", "research_source_type")
    else:
        with op.batch_alter_table("source_contracts") as batch:
            batch.drop_column("research_source_type")
