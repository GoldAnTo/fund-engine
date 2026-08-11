"""Bind new market observations to frozen source statements.

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
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("market_observations") as batch:
            batch.add_column(sa.Column("source_statement_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_market_observations_source_statement_id",
                "source_statements",
                ["source_statement_id"],
                ["id"],
            )
    else:
        op.add_column(
            "market_observations",
            sa.Column(
                "source_statement_id",
                sa.Uuid(),
                sa.ForeignKey("source_statements.id"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("market_observations") as batch:
            batch.drop_constraint(
                "fk_market_observations_source_statement_id",
                type_="foreignkey",
            )
            batch.drop_column("source_statement_id")
    else:
        op.drop_column("market_observations", "source_statement_id")
