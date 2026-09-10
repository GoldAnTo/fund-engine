"""Add trusted OIDC users and explicit Case access grants.

Revision ID: 0061
Revises: 0060
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("tenant_id", sa.String(length=256), nullable=False),
        sa.Column("display_name", sa.String(length=256), nullable=False),
        sa.Column("normalized_email", sa.String(length=320), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("issuer", "subject", name="uq_research_users_oidc_subject"),
        sa.UniqueConstraint(
            "tenant_id",
            "normalized_email",
            name="uq_research_users_tenant_email",
        ),
    )
    op.create_index(
        "ix_research_users_tenant_active",
        "research_users",
        ["tenant_id", "active"],
        unique=False,
    )
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER prevent_research_user_oidc_identity_update
            BEFORE UPDATE OF issuer, subject ON research_users
            FOR EACH ROW
            WHEN OLD.issuer IS NOT NEW.issuer OR OLD.subject IS NOT NEW.subject
            BEGIN
                SELECT RAISE(ABORT, 'research user OIDC identity is immutable');
            END
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION reject_research_user_oidc_identity_change()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.issuer IS DISTINCT FROM NEW.issuer
                   OR OLD.subject IS DISTINCT FROM NEW.subject THEN
                    RAISE EXCEPTION 'research user OIDC identity is immutable'
                        USING ERRCODE = '23514',
                              CONSTRAINT =
                                  'ck_research_users_oidc_identity_immutable';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER prevent_research_user_oidc_identity_update
            BEFORE UPDATE OF issuer, subject ON research_users
            FOR EACH ROW
            EXECUTE FUNCTION reject_research_user_oidc_identity_change()
            """
        )

    op.create_table(
        "case_access_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("granted_by_principal_id", sa.String(length=1024), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('owner', 'editor', 'reviewer', 'viewer')",
            name="ck_case_access_grants_role",
        ),
        sa.ForeignKeyConstraint(["research_case_id"], ["research_cases.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["research_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_case_id",
            "user_id",
            name="uq_case_access_grants_case_user",
        ),
    )
    op.create_index(
        "ix_case_access_grants_case",
        "case_access_grants",
        ["research_case_id"],
        unique=False,
    )
    op.create_index(
        "ix_case_access_grants_user",
        "case_access_grants",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS prevent_research_user_oidc_identity_update")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS prevent_research_user_oidc_identity_update "
            "ON research_users"
        )
    op.drop_index("ix_case_access_grants_user", table_name="case_access_grants")
    op.drop_index("ix_case_access_grants_case", table_name="case_access_grants")
    op.drop_table("case_access_grants")
    op.drop_index("ix_research_users_tenant_active", table_name="research_users")
    op.drop_table("research_users")
    if dialect == "postgresql":
        op.execute(
            "DROP FUNCTION IF EXISTS reject_research_user_oidc_identity_change()"
        )
