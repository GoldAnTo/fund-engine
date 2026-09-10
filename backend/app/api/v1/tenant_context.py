"""Resolve one trusted research tenant for protected API routes.

The client never selects a tenant through request JSON, query strings or a
free-form header.  Hosting configuration maps opaque bearer tokens to tenant
IDs; a reverse proxy can inject the same Authorization header after validating
an enterprise SSO session.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from secrets import compare_digest
from unicodedata import category

from fastapi import Depends, Header

from app.errors import AuthenticationRequiredError, PermissionDeniedError

_TENANT_TOKEN_ENV = "RESEARCH_TENANT_TOKENS"


@dataclass(frozen=True)
class ResearchActor:
    """The tenant and host-configured capabilities of one bearer token."""

    tenant_id: str
    roles: frozenset[str]
    subject_id: str | None = None


def _contains_control_characters(value: str) -> bool:
    return any(category(character) == "Cc" for character in value)


def _normalized_tenant_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or _contains_control_characters(normalized):
        return None
    return normalized


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
        if isinstance(configuration, str):
            tenant_id = _normalized_tenant_id(configuration)
            if tenant_id is not None:
                configured.append((token, ResearchActor(tenant_id, frozenset())))
            continue
        if not isinstance(configuration, dict):
            continue
        tenant_id = _normalized_tenant_id(configuration.get("tenant_id"))
        roles = configuration.get("roles", [])
        if tenant_id is None:
            continue
        if not isinstance(roles, list) or not all(
            isinstance(role, str) and role.strip() for role in roles
        ):
            continue
        subject_id = None
        if "subject_id" in configuration:
            subject_id = configuration["subject_id"]
            if (
                not isinstance(subject_id, str)
                or not subject_id.strip()
                or len(subject_id) > 128
                or _contains_control_characters(subject_id)
            ):
                continue
        configured.append(
            (token, ResearchActor(tenant_id, frozenset(roles), subject_id))
        )
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


def require_gateway_actor(
    actor: ResearchActor = Depends(require_research_actor),
) -> ResearchActor:
    """Require the host-configured human subject used by Gateway routes."""
    if not actor.subject_id:
        raise PermissionDeniedError(
            "a stable research subject is required for Gateway access"
        )
    return actor


def require_case_administrator(
    actor: ResearchActor = Depends(require_research_actor),
) -> ResearchActor:
    """Require the narrowly scoped role that may assign legacy Case ownership."""
    if "case_administrator" not in actor.roles:
        raise PermissionDeniedError("case administrator permission is required")
    return actor
