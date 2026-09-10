"""Trusted external identities and mutable Case access grants."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DDL,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base

CaseAccessRole = Literal["owner", "editor", "reviewer", "viewer"]


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class ResearchUser(Base):
    """An application user resolved from one immutable OIDC subject."""

    __tablename__ = "research_users"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_research_users_oidc_subject"),
        UniqueConstraint(
            "tenant_id",
            "normalized_email",
            name="uq_research_users_tenant_email",
        ),
        Index("ix_research_users_tenant_active", "tenant_id", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CaseAccessGrant(Base):
    """A mutable authorization assignment managed by the authorization service."""

    __tablename__ = "case_access_grants"
    __table_args__ = (
        UniqueConstraint(
            "research_case_id",
            "user_id",
            name="uq_case_access_grants_case_user",
        ),
        CheckConstraint(
            "role IN ('owner', 'editor', 'reviewer', 'viewer')",
            name="ck_case_access_grants_role",
        ),
        Index("ix_case_access_grants_case", "research_case_id"),
        Index("ix_case_access_grants_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_cases.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("research_users.id"), nullable=False
    )
    role: Mapped[CaseAccessRole] = mapped_column(String(16), nullable=False)
    granted_by_principal_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


_SQLITE_OIDC_IDENTITY_TRIGGER = DDL(
    """
    CREATE TRIGGER prevent_research_user_oidc_identity_update
    BEFORE UPDATE OF issuer, subject ON research_users
    FOR EACH ROW
    WHEN OLD.issuer IS NOT NEW.issuer OR OLD.subject IS NOT NEW.subject
    BEGIN
        SELECT RAISE(ABORT, 'research user OIDC identity is immutable');
    END
    """
).execute_if(dialect="sqlite")

_POSTGRES_OIDC_IDENTITY_TRIGGER_FUNCTION = DDL(
    """
    CREATE OR REPLACE FUNCTION reject_research_user_oidc_identity_change()
    RETURNS trigger AS $$
    BEGIN
        IF OLD.issuer IS DISTINCT FROM NEW.issuer
           OR OLD.subject IS DISTINCT FROM NEW.subject THEN
            RAISE EXCEPTION 'research user OIDC identity is immutable'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_research_users_oidc_identity_immutable';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """
).execute_if(dialect="postgresql")

_POSTGRES_OIDC_IDENTITY_TRIGGER = DDL(
    """
    CREATE TRIGGER prevent_research_user_oidc_identity_update
    BEFORE UPDATE OF issuer, subject ON research_users
    FOR EACH ROW
    EXECUTE FUNCTION reject_research_user_oidc_identity_change()
    """
).execute_if(dialect="postgresql")

_POSTGRES_DROP_OIDC_IDENTITY_TRIGGER_FUNCTION = DDL(
    "DROP FUNCTION IF EXISTS reject_research_user_oidc_identity_change()"
).execute_if(dialect="postgresql")

event.listen(ResearchUser.__table__, "after_create", _SQLITE_OIDC_IDENTITY_TRIGGER)
event.listen(
    ResearchUser.__table__,
    "after_create",
    _POSTGRES_OIDC_IDENTITY_TRIGGER_FUNCTION,
)
event.listen(ResearchUser.__table__, "after_create", _POSTGRES_OIDC_IDENTITY_TRIGGER)
event.listen(
    ResearchUser.__table__,
    "after_drop",
    _POSTGRES_DROP_OIDC_IDENTITY_TRIGGER_FUNCTION,
)
