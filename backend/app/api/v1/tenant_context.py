"""Resolve one trusted OIDC research principal for protected API routes."""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AuthenticationRequiredError, PermissionDeniedError
from app.security.oidc import get_oidc_verifier
from app.security.principal import ResearchPrincipal

ResearchActor = ResearchPrincipal


def require_research_actor(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> ResearchPrincipal:
    """Authenticate an RS256 bearer token and resolve its stable local user."""
    if authorization is None:
        raise AuthenticationRequiredError("research credentials are required")
    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if scheme.casefold() != "bearer" or not separator or not token:
        raise AuthenticationRequiredError("research bearer credentials are required")
    try:
        principal = get_oidc_verifier().resolve_principal(token, session=db)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return principal


def require_research_tenant(
    actor: ResearchPrincipal = Depends(require_research_actor),
) -> str:
    """Return the tenant derived from the authenticated principal."""
    return actor.tenant_id


def require_case_administrator(
    actor: ResearchPrincipal = Depends(require_research_actor),
) -> ResearchPrincipal:
    """Require the narrowly scoped role that may assign legacy Case ownership."""
    if "case_administrator" not in actor.roles:
        raise PermissionDeniedError("case administrator permission is required")
    return actor


def require_runtime_administrator(
    actor: ResearchPrincipal = Depends(require_research_actor),
) -> ResearchPrincipal:
    """Restrict cross-service diagnostics to tenant operations administrators."""
    if "tenant_administrator" not in actor.roles:
        raise PermissionDeniedError("runtime administrator permission is required")
    return actor


def require_metric_definition_administrator(
    actor: ResearchPrincipal = Depends(require_research_actor),
) -> ResearchPrincipal:
    """Limit writes to the shared metric-definition catalog."""
    if "metric_definition_administrator" not in actor.roles:
        raise PermissionDeniedError(
            "metric definition administrator permission is required"
        )
    return actor
