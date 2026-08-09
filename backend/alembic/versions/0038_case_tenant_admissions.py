"""Bind event research cases to immutable tenant admissions.

Revision ID: 0038
Revises: 0037
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0038"
down_revision: Union[str, None] = "0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_tenant_admissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "research_case_id",
            sa.Uuid(),
            sa.ForeignKey("research_cases.id"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(length=256), nullable=False),
        sa.Column(
            "initial_document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id"),
            nullable=False,
        ),
        sa.Column("admitted_by", sa.String(length=128), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "research_case_id", name="uq_case_tenant_admissions_case"
        ),
    )
    op.create_index(
        "ix_case_tenant_admissions_tenant",
        "case_tenant_admissions",
        ["tenant_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE TRIGGER no_update_case_tenant_admissions BEFORE UPDATE ON "
            "case_tenant_admissions FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            "CREATE TRIGGER no_delete_case_tenant_admissions BEFORE DELETE ON "
            "case_tenant_admissions FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS no_update_case_tenant_admissions "
            "ON case_tenant_admissions;"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS no_delete_case_tenant_admissions "
            "ON case_tenant_admissions;"
        )
    op.drop_index("ix_case_tenant_admissions_tenant", "case_tenant_admissions")
    op.drop_table("case_tenant_admissions")
