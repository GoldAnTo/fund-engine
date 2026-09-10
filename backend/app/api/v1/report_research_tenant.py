"""Trusted tenant resolution for the report-research dispatch projection.

The immutable source ledger records which tenant may access each primary
document.  A caller must not choose that tenant through a request parameter:
this dependency derives it from an opaque bearer credential that the hosting
application configures server-side.  A future identity provider can replace
this dependency without changing the list route or its query boundary.
"""
from __future__ import annotations

import json
import os
from secrets import compare_digest

from fastapi import Header

from app.errors import AuthenticationRequiredError, PermissionDeniedError


_TENANT_TOKEN_ENV = "REPORT_RESEARCH_TENANT_TOKENS"


def _configured_tenant_tokens() -> tuple[tuple[str, str], ...]:
    """Return valid opaque-token to tenant mappings from server configuration.

    Invalid or absent configuration intentionally produces no trusted mapping:
    a request then fails closed rather than falling back to a caller-supplied
    tenant ID.
    """
    raw = os.getenv(_TENANT_TOKEN_ENV, "")
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(configured, dict):
        return ()
    return tuple(
        (token, tenant_id)
        for token, tenant_id in configured.items()
        if isinstance(token, str)
        and token.strip()
        and isinstance(tenant_id, str)
        and tenant_id.strip()
    )


def require_report_research_tenant(
    authorization: str | None = Header(default=None),
) -> str:
    """Resolve one tenant from a server-configured bearer credential.

    A tenant ID is never read from a query parameter or an arbitrary client
    header.  The mapping is deliberately small and only protects the new
    dispatch-list read surface until the hosting identity/RBAC boundary is
    introduced application-wide.
    """
    if authorization is None:
        raise AuthenticationRequiredError("report research credentials are required")
    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if scheme.casefold() != "bearer" or not separator or not token:
        raise AuthenticationRequiredError("report research bearer credentials are required")
    for expected_token, tenant_id in _configured_tenant_tokens():
        if compare_digest(token, expected_token):
            return tenant_id
    raise PermissionDeniedError("report research tenant is not permitted")
