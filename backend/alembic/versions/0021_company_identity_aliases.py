"""Add append-only identities for legacy immutable companies.

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_identity_aliases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("company_type", sa.String(64), nullable=False),
        sa.Column("canonical_identity", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_type", "canonical_identity", name="uq_company_identity_alias"),
    )
    if op.get_bind().dialect.name == "postgresql":
        for action in ("update", "delete"):
            op.execute(
                f"CREATE TRIGGER no_{action}_company_identity_aliases BEFORE {action.upper()} "
                "ON company_identity_aliases FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_delete_company_identity_aliases ON company_identity_aliases;")
        op.execute("DROP TRIGGER IF EXISTS no_update_company_identity_aliases ON company_identity_aliases;")
    op.drop_table("company_identity_aliases")
