"""Expand source-contract declarer identities for prefixed tenant IDs.

Revision ID: 0071
Revises: 0070
"""
from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0071"
down_revision: Union[str, None] = "0070"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def _alter_declared_by(*, old_length: int, new_length: int) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("source_contracts") as batch:
            batch.alter_column(
                "declared_by",
                existing_type=sa.String(length=old_length),
                type_=sa.String(length=new_length),
                existing_nullable=False,
            )
        return
    op.alter_column(
        "source_contracts",
        "declared_by",
        existing_type=sa.String(length=old_length),
        type_=sa.String(length=new_length),
        existing_nullable=False,
    )


def upgrade() -> None:
    _alter_declared_by(old_length=128, new_length=512)


def downgrade() -> None:
    oversized_identity = op.get_bind().scalar(
        sa.text(
            "SELECT 1 FROM source_contracts "
            "WHERE length(declared_by) > :old_length LIMIT 1"
        ),
        {"old_length": 128},
    )
    if oversized_identity is not None:
        raise RuntimeError(
            "cannot downgrade source_contracts.declared_by without data loss; "
            "migrate declarer identities to 128 characters or fewer first"
        )
    _alter_declared_by(old_length=512, new_length=128)
