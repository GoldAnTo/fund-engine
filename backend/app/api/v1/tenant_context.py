"""Resolve one trusted research tenant for protected API routes.

The client never selects a tenant through request JSON, query strings or a
free-form header.  Hosting configuration maps opaque bearer tokens to tenant
IDs; a reverse proxy can inject the same Authorization header after validating
an enterprise SSO session.
"""
from __future__ import annotations

import json
import os
from secrets import compare_digest
from dataclasses import dataclass

from fastapi import Depends, Header

from app.errors import AuthenticationRequiredError, PermissionDeniedError


_TENANT_TOKEN_ENV = "RESEARCH_TENANT_TOKENS"


@dataclass(frozen=True)
class ResearchActor:
    """The tenant and host-configured capabilities of one bearer token."""

    tenant_id: str
    roles: frozenset[str]


def _configured_tokens() -> tuple[tuple[str, ResearchActor], ...]:
    raw = os.getenv(_TENANT_TOKEN_ENV, "")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(value, dict):
        return ()
    configured: list[tuple[str, ResearchActor]] = []
    for token, configuration in value.items():
        if not isinstance(token, str) or not token.strip():
            continue
        # Keep the original {"opaque-token": "tenant"} shape valid while
        # allowing hosting configuration to grant narrow administrative roles.
        if isinstance(configuration, str) and configuration.strip():
            configured.append((token, ResearchActor(configuration, frozenset())))
            continue
        if not isinstance(configuration, dict):
            continue
        tenant_id = configuration.get("tenant_id")
        roles = configuration.get("roles", [])
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            continue
        if not isinstance(roles, list) or not all(
            isinstance(role, str) and role.strip() for role in roles
        ):
            continue
        configured.append((token, ResearchActor(tenant_id, frozenset(roles))))
    return tuple(configured)


def configured_tenant_ids() -> frozenset[str]:
    """Tenant IDs present in host-owned credential configuration."""
    return frozenset(actor.tenant_id for _, actor in _configured_tokens())


def require_research_actor(
    authorization: str | None = Header(default=None),
) -> ResearchActor:
    """Resolve the actor only from the host-configured bearer credential."""
    if authorization is None:
        raise AuthenticationRequiredError("research credentials are required")
    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if scheme.casefold() != "bearer" or not separator or not token:
        raise AuthenticationRequiredError("research bearer credentials are required")
    for expected_token, actor in _configured_tokens():
        if compare_digest(token, expected_token):
            return actor
    raise PermissionDeniedError("research tenant is not permitted")


def require_research_tenant(
    authorization: str | None = Header(default=None),
) -> str:
    """Return the tenant bound to a configured opaque bearer credential."""
    return require_research_actor(authorization).tenant_id


def require_case_administrator(
    actor: ResearchActor = Depends(require_research_actor),
) -> ResearchActor:
    """Require the narrowly scoped role that may assign legacy Case ownership."""
    if "case_administrator" not in actor.roles:
        raise PermissionDeniedError("case administrator permission is required")
    return actor
