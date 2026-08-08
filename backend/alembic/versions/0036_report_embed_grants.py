"""Add auditable, read-only grants for report Wiki embeds.

Revision ID: 0036
Revises: 0035
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_IMMUTABLE_TABLES = ("embed_grants", "embed_grant_revocations")


def _trigger(table: str, action: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE TRIGGER no_{action}_{table} BEFORE {action.upper()} ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def upgrade() -> None:
    op.create_table(
        "embed_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id", sa.Uuid(), sa.ForeignKey("research_cases.id"), nullable=False
        ),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("issued_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("token_sha256", name="uq_embed_grants_token_sha256"),
    )
    op.create_index(
        "ix_embed_grants_case_expiry", "embed_grants", ["research_case_id", "expires_at"]
    )
    op.create_table(
        "embed_grant_revocations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "embed_grant_id", sa.Uuid(), sa.ForeignKey("embed_grants.id"), nullable=False
        ),
        sa.Column("revoked_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("embed_grant_id", name="uq_embed_grant_revocation_grant"),
    )
    op.create_index(
        "ix_embed_grant_revocations_grant", "embed_grant_revocations", ["embed_grant_id"]
    )
    for table in _IMMUTABLE_TABLES:
        _trigger(table, "update")
        _trigger(table, "delete")


def downgrade() -> None:
    bind = op.get_bind()
    for table in _IMMUTABLE_TABLES:
        count = bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
        if count:
            raise RuntimeError(f"refusing to downgrade: {table} contains {count} immutable records")
    if bind.dialect.name == "postgresql":
        for table in reversed(_IMMUTABLE_TABLES):
            for action in ("delete", "update"):
                op.execute(f"DROP TRIGGER IF EXISTS no_{action}_{table} ON {table};")
    op.drop_index("ix_embed_grant_revocations_grant", table_name="embed_grant_revocations")
    op.drop_table("embed_grant_revocations")
    op.drop_index("ix_embed_grants_case_expiry", table_name="embed_grants")
    op.drop_table("embed_grants")
