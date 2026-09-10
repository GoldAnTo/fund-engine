"""Trusted persisted identity fixtures for direct service tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.identity import ResearchUser
from app.security.principal import ResearchPrincipal


def persist_research_principal(
    session: Session,
    *,
    tenant_id: str,
    label: str = "test-researcher",
) -> ResearchPrincipal:
    now = datetime.now(UTC)
    user = ResearchUser(
        issuer="https://test-identity.invalid/realms/research",
        subject=f"{label}:{uuid.uuid4()}",
        tenant_id=tenant_id,
        display_name=label,
        normalized_email=None,
        active=True,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    return ResearchPrincipal(
        user_id=user.id,
        issuer=user.issuer,
        subject=user.subject,
        tenant_id=user.tenant_id,
        display_name=user.display_name,
        roles=frozenset(),
        expires_at=now + timedelta(hours=1),
    )
