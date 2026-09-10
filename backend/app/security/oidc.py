"""RS256 OIDC validation and stable local-user resolution.

Roles are accepted only from two documented claim shapes: a top-level
``roles`` list and Keycloak's ``realm_access.roles`` list. When both are
present their normalized string values are combined.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import PermissionDeniedError, UpstreamUnavailableError
from app.models.identity import ResearchUser
from app.security.principal import ResearchPrincipal

_HTTP_TIMEOUT_SECONDS = 5.0
_JWKS_CACHE_TTL_SECONDS = 300.0
_FORCED_REFRESH_COOLDOWN_SECONDS = 30.0
_FETCH_FAILURE_COOLDOWN_SECONDS = 5.0
_CLOCK_SKEW_SECONDS = 30
_INVALID_TOKEN_MESSAGE = "OIDC token is not permitted"
_UPSTREAM_MESSAGE = "OIDC key service unavailable"

FetchJWKS = Callable[[str, float], object]


@dataclass(frozen=True, slots=True)
class OIDCSettings:
    issuer: str
    audience: str
    jwks_url: str
    tenant_claim: str
    allow_insecure_http: bool = False

    @classmethod
    def from_environment(cls) -> OIDCSettings:
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, environment: Mapping[str, str]) -> OIDCSettings:
        names = (
            "OIDC_ISSUER",
            "OIDC_AUDIENCE",
            "OIDC_JWKS_URL",
            "OIDC_TENANT_CLAIM",
        )
        values = {name: environment.get(name, "").strip() for name in names}
        if any(not values[name] for name in names):
            raise UpstreamUnavailableError("OIDC authentication unavailable")
        raw_allow_insecure_http = environment.get(
            "OIDC_ALLOW_INSECURE_HTTP", ""
        ).strip()
        normalized_allow_insecure_http = raw_allow_insecure_http.casefold()
        if normalized_allow_insecure_http not in {"", "false", "true"}:
            raise UpstreamUnavailableError("OIDC authentication unavailable")
        allow_insecure_http = normalized_allow_insecure_http == "true"
        if (
            allow_insecure_http
            and environment.get("APP_ENV", "").strip().casefold() != "local"
        ):
            raise UpstreamUnavailableError("OIDC authentication unavailable")
        issuer = values["OIDC_ISSUER"]
        jwks_url = values["OIDC_JWKS_URL"]
        if not _is_allowed_oidc_url(
            issuer,
            allow_insecure_http=allow_insecure_http,
            issuer=True,
        ) or not _is_allowed_oidc_url(
            jwks_url,
            allow_insecure_http=allow_insecure_http,
            issuer=False,
        ):
            raise UpstreamUnavailableError("OIDC authentication unavailable")
        return cls(
            issuer=issuer,
            audience=values["OIDC_AUDIENCE"],
            jwks_url=jwks_url,
            tenant_claim=values["OIDC_TENANT_CLAIM"],
            allow_insecure_http=allow_insecure_http,
        )


def _is_allowed_oidc_url(
    value: str,
    *,
    allow_insecure_http: bool,
    issuer: bool,
) -> bool:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    if (
        not parsed.netloc
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http" or not allow_insecure_http:
        return False
    if _is_explicit_loopback_host(hostname):
        return True
    return not issuer and _is_single_label_service_host(hostname)


def _is_explicit_loopback_host(hostname: str) -> bool:
    return hostname.casefold() in {"localhost", "127.0.0.1", "::1"}


def _is_single_label_service_host(hostname: str) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
            hostname,
        )
    )


def _default_fetch_jwks(url: str, timeout_seconds: float) -> object:
    timeout = httpx.Timeout(timeout_seconds)
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpstreamUnavailableError(_UPSTREAM_MESSAGE) from exc


class OIDCVerifier:
    """Validate one bearer token and resolve its immutable local user."""

    def __init__(
        self,
        *,
        settings: OIDCSettings,
        fetch_jwks: FetchJWKS = _default_fetch_jwks,
        cache_ttl_seconds: float = _JWKS_CACHE_TTL_SECONDS,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._fetch_jwks = fetch_jwks
        self._cache_ttl_seconds = max(1.0, min(cache_ttl_seconds, 600.0))
        self._forced_refresh_cooldown_seconds = min(
            _FORCED_REFRESH_COOLDOWN_SECONDS,
            self._cache_ttl_seconds,
        )
        self._fetch_failure_cooldown_seconds = min(
            _FETCH_FAILURE_COOLDOWN_SECONDS,
            self._cache_ttl_seconds,
        )
        self._monotonic_clock = monotonic_clock
        self._keys: dict[str, Any] = {}
        self._loaded_at = 0.0
        self._last_forced_refresh_at: float | None = None
        self._last_fetch_failure_at: float | None = None
        self._lock = threading.Lock()

    def resolve_principal(self, token: str, *, session: Session) -> ResearchPrincipal:
        claims = self._validate_token(token)
        subject = _required_string(claims.get("sub"))
        tenant_id = _required_string(claims.get(self.settings.tenant_claim))
        if subject is None or tenant_id is None:
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
        display_name = _display_name(claims, subject)
        normalized_email = _normalized_email(claims.get("email"))
        roles = _roles(claims)
        expires_at = _expires_at(claims.get("exp"))
        user = self._resolve_user(
            session,
            subject=subject,
            tenant_id=tenant_id,
            display_name=display_name,
            normalized_email=normalized_email,
        )
        return ResearchPrincipal(
            user_id=user.id,
            issuer=self.settings.issuer,
            subject=subject,
            tenant_id=tenant_id,
            display_name=display_name,
            roles=roles,
            expires_at=expires_at,
        )

    def _validate_token(self, token: str) -> dict[str, object]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE) from exc
        if header.get("alg") != "RS256":
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
        kid = _required_string(header.get("kid"))
        if kid is None:
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
        key = self._signing_key(kid)
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=_CLOCK_SKEW_SECONDS,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "verify_aud": True,
                },
            )
        except jwt.PyJWTError as exc:
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE) from exc
        if not isinstance(claims, dict):
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
        return claims

    def _signing_key(self, kid: str) -> Any:
        keys = self._load_keys(force=False)
        key = keys.get(kid)
        if key is not None:
            return key
        keys = self._load_keys(force=True)
        key = keys.get(kid)
        if key is None:
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
        return key

    def _load_keys(self, *, force: bool) -> dict[str, Any]:
        with self._lock:
            now = self._monotonic_clock()
            cache_is_fresh = (
                bool(self._keys) and now - self._loaded_at < self._cache_ttl_seconds
            )
            if not force and cache_is_fresh:
                return self._keys
            if (
                self._last_fetch_failure_at is not None
                and now - self._last_fetch_failure_at
                < self._fetch_failure_cooldown_seconds
            ):
                raise UpstreamUnavailableError(_UPSTREAM_MESSAGE)
            if (
                force
                and cache_is_fresh
                and self._last_forced_refresh_at is not None
                and now - self._last_forced_refresh_at
                < self._forced_refresh_cooldown_seconds
            ):
                return self._keys
            try:
                document = self._fetch_jwks(
                    self.settings.jwks_url, _HTTP_TIMEOUT_SECONDS
                )
                keys = _parse_jwks(document)
            except Exception:
                self._last_fetch_failure_at = self._monotonic_clock()
                raise UpstreamUnavailableError(_UPSTREAM_MESSAGE) from None
            completed_at = self._monotonic_clock()
            self._last_fetch_failure_at = None
            if force:
                self._last_forced_refresh_at = completed_at
            self._keys = keys
            self._loaded_at = completed_at
            return keys

    def _resolve_user(
        self,
        session: Session,
        *,
        subject: str,
        tenant_id: str,
        display_name: str,
        normalized_email: str | None,
    ) -> ResearchUser:
        user = session.scalar(
            select(ResearchUser).where(
                ResearchUser.issuer == self.settings.issuer,
                ResearchUser.subject == subject,
            )
        )
        now = datetime.now(UTC)
        if user is None:
            candidate = ResearchUser(
                issuer=self.settings.issuer,
                subject=subject,
                tenant_id=tenant_id,
                display_name=display_name,
                normalized_email=normalized_email,
                active=True,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(candidate)
                    session.flush()
                user = candidate
            except IntegrityError:
                user = session.scalar(
                    select(ResearchUser).where(
                        ResearchUser.issuer == self.settings.issuer,
                        ResearchUser.subject == subject,
                    )
                )
                if user is None:
                    raise PermissionDeniedError(
                        "research user identity conflicts with an existing account"
                    ) from None
        if user.tenant_id != tenant_id:
            raise PermissionDeniedError("OIDC tenant change is not permitted")
        if not user.active:
            raise PermissionDeniedError("research user is disabled")
        try:
            with session.begin_nested():
                user.display_name = display_name
                user.normalized_email = normalized_email
                user.last_seen_at = now
                user.updated_at = now
                session.flush()
        except IntegrityError as exc:
            raise PermissionDeniedError(
                "research user identity conflicts with an existing account"
            ) from exc
        return user


def _parse_jwks(document: object) -> dict[str, Any]:
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise UpstreamUnavailableError(_UPSTREAM_MESSAGE)
    parsed: dict[str, Any] = {}
    for value in document["keys"]:
        if not isinstance(value, dict):
            raise UpstreamUnavailableError(_UPSTREAM_MESSAGE)
        if value.get("use") != "sig" or value.get("alg") != "RS256":
            continue
        kid = _required_string(value.get("kid"))
        if (
            kid is None
            or kid in parsed
            or value.get("kty") != "RSA"
            or _required_string(value.get("n")) is None
            or _required_string(value.get("e")) is None
        ):
            raise UpstreamUnavailableError(_UPSTREAM_MESSAGE)
        try:
            parsed[kid] = RSAAlgorithm.from_jwk(json.dumps(value))
        except (ValueError, TypeError) as exc:
            raise UpstreamUnavailableError(_UPSTREAM_MESSAGE) from exc
    if not parsed:
        raise UpstreamUnavailableError(_UPSTREAM_MESSAGE)
    return parsed


def _required_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _display_name(claims: dict[str, object], subject: str) -> str:
    for name in ("name", "preferred_username"):
        value = _required_string(claims.get(name))
        if value is not None:
            return value
    return subject


def _normalized_email(value: object) -> str | None:
    normalized = _required_string(value)
    return normalized.casefold() if normalized is not None else None


def _roles(claims: dict[str, object]) -> frozenset[str]:
    roles: set[str] = set()
    top_level = claims.get("roles")
    if top_level is not None:
        roles.update(_role_list(top_level))
    realm_access = claims.get("realm_access")
    if realm_access is not None:
        if not isinstance(realm_access, dict):
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
        realm_roles = realm_access.get("roles")
        if realm_roles is not None:
            roles.update(_role_list(realm_roles))
    return frozenset(roles)


def _role_list(value: object) -> set[str]:
    if not isinstance(value, list):
        raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
    result: set[str] = set()
    for role in value:
        normalized = _required_string(role)
        if normalized is None:
            raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
        result.add(normalized)
    return result


def _expires_at(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE)
    try:
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise PermissionDeniedError(_INVALID_TOKEN_MESSAGE) from exc


_verifier_lock = threading.Lock()
_configured_verifier: OIDCVerifier | None = None


def get_oidc_verifier() -> OIDCVerifier:
    """Return a process-local verifier whose cache follows current settings."""
    global _configured_verifier
    settings = OIDCSettings.from_environment()
    with _verifier_lock:
        if _configured_verifier is None or _configured_verifier.settings != settings:
            _configured_verifier = OIDCVerifier(settings=settings)
        return _configured_verifier
