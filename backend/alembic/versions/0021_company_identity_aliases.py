"""Add append-only identities for legacy immutable companies.

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union
from datetime import datetime, timezone
from unicodedata import normalize

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
    _backfill_legacy_aliases()


def _backfill_legacy_aliases() -> None:
    """One-time NFKC alias backfill for immutable pre-0020 companies."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, type, name FROM companies WHERE canonical_identity IS NULL"
        )
    ).mappings()
    for row in rows:
        identity = " ".join(normalize("NFKC", row["name"]).split()).casefold()
        statement = sa.text(
            "INSERT INTO company_identity_aliases "
            "(id, company_id, company_type, canonical_identity, created_at) "
            "VALUES (:id, :company_id, :company_type, :canonical_identity, :created_at) "
            "ON CONFLICT (company_type, canonical_identity) DO NOTHING"
        )
        bind.execute(
            statement,
            {
                "id": __import__("uuid").uuid4(),
                "company_id": row["id"],
                "company_type": row["type"],
                "canonical_identity": identity,
                "created_at": datetime.now(timezone.utc),
            },
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS no_delete_company_identity_aliases ON company_identity_aliases;")
        op.execute("DROP TRIGGER IF EXISTS no_update_company_identity_aliases ON company_identity_aliases;")
    op.drop_table("company_identity_aliases")
