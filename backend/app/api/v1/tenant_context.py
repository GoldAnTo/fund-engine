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

from fastapi import Header

from app.errors import AuthenticationRequiredError, PermissionDeniedError


_TENANT_TOKEN_ENV = "RESEARCH_TENANT_TOKENS"


def _configured_tokens() -> tuple[tuple[str, str], ...]:
    raw = os.getenv(_TENANT_TOKEN_ENV, "")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(value, dict):
        return ()
    return tuple(
        (token, tenant_id)
        for token, tenant_id in value.items()
        if isinstance(token, str)
        and token.strip()
        and isinstance(tenant_id, str)
        and tenant_id.strip()
    )


def require_research_tenant(
    authorization: str | None = Header(default=None),
) -> str:
    """Return the tenant bound to a configured opaque bearer credential."""
    if authorization is None:
        raise AuthenticationRequiredError("research credentials are required")
    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if scheme.casefold() != "bearer" or not separator or not token:
        raise AuthenticationRequiredError("research bearer credentials are required")
    for expected_token, tenant_id in _configured_tokens():
        if compare_digest(token, expected_token):
            return tenant_id
    raise PermissionDeniedError("research tenant is not permitted")
