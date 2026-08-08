"""Controlled issuance and verification seam for external report embeds."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import ResearchCase
from app.models.report_research import EmbedGrant, EmbedGrantRevocation


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EmbedAccessDenied(Exception):
    """A deliberately non-descriptive denial suitable for bearer-token reads."""

    def __init__(self, status_code: int) -> None:
        super().__init__("embed access denied")
        self.status_code = status_code


@dataclass(frozen=True)
class IssuedEmbedGrant:
    grant: EmbedGrant
    token: str


class EmbedAccessService:
    """Issue and validate immutable, case-scoped embed permissions.

    This service is intentionally not exposed through an unauthenticated HTTP
    management endpoint.  A future host identity layer or an operations CLI
    may call it after enforcing its own authorization boundary.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    @staticmethod
    def token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_origin(value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("embed origin must be an http(s) origin without a path")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("embed origin has an invalid port") from exc
        hostname = parsed.hostname.lower()
        authority = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None and port != {"http": 80, "https": 443}[parsed.scheme.lower()]:
            authority = f"{authority}:{port}"
        return f"{parsed.scheme.lower()}://{authority}"

    @classmethod
    def _normalize_origins(cls, origins: list[str]) -> list[str]:
        normalized = sorted({cls._normalize_origin(origin) for origin in origins})
        if not normalized:
            raise ValueError("embed grant requires at least one allowed origin")
        return normalized

    def issue(
        self,
        *,
        research_case_id: uuid.UUID,
        expires_at: datetime,
        allowed_origins: list[str],
        issued_by: str,
    ) -> IssuedEmbedGrant:
        if self._session.get(ResearchCase, research_case_id) is None:
            raise NotFoundError("report research case not found")
        if not issued_by.strip():
            raise ValueError("embed grant issued_by must not be blank")
        if expires_at.tzinfo is None:
            raise ValueError("embed grant expiry must be timezone-aware")
        expiry = expires_at.astimezone(timezone.utc)
        if expiry <= _utcnow():
            raise ValueError("embed grant expiry must be in the future")
        token = secrets.token_urlsafe(32)
        grant = EmbedGrant(
            research_case_id=research_case_id,
            token_sha256=self.token_digest(token),
            expires_at=expiry,
            allowed_origins=self._normalize_origins(allowed_origins),
            issued_by=issued_by.strip(),
        )
        self._session.add(grant)
        self._session.flush()
        return IssuedEmbedGrant(grant=grant, token=token)

    def revoke(
        self, grant_id: uuid.UUID, *, revoked_by: str, reason: str
    ) -> EmbedGrantRevocation:
        if not revoked_by.strip() or not reason.strip():
            raise ValueError("embed grant revocation actor and reason must not be blank")
        grant = self._session.get(EmbedGrant, grant_id)
        if grant is None:
            raise NotFoundError("embed grant not found")
        existing = self._session.scalar(
            select(EmbedGrantRevocation).where(EmbedGrantRevocation.embed_grant_id == grant_id)
        )
        if existing is not None:
            raise ValueError("embed grant is already revoked")
        revocation = EmbedGrantRevocation(
            embed_grant_id=grant.id,
            revoked_by=revoked_by.strip(),
            reason=reason.strip(),
        )
        self._session.add(revocation)
        self._session.flush()
        return revocation

    def require_read_only(
        self,
        token: str | None,
        research_case_id: uuid.UUID,
        origin: str | None,
    ) -> EmbedGrant:
        if not token:
            raise EmbedAccessDenied(401)
        grant = self._session.scalar(
            select(EmbedGrant).where(EmbedGrant.token_sha256 == self.token_digest(token))
        )
        if grant is None or self._is_expired(grant) or self._is_revoked(grant.id):
            raise EmbedAccessDenied(401)
        if grant.research_case_id != research_case_id:
            raise EmbedAccessDenied(403)
        if origin is None:
            raise EmbedAccessDenied(403)
        try:
            normalized_origin = self._normalize_origin(origin)
        except ValueError:
            raise EmbedAccessDenied(403) from None
        if normalized_origin not in set(grant.allowed_origins):
            raise EmbedAccessDenied(403)
        return grant

    def allows_preflight(self, research_case_id: uuid.UUID, origin: str) -> bool:
        """Whether one live grant authorizes this origin to discover the view.

        Browser preflight does not carry the bearer token, so its narrow
        authorization proof is an exact, active case/origin grant match.  It
        never reveals which grant (if any) supplied the permission.
        """
        try:
            normalized_origin = self._normalize_origin(origin)
        except ValueError:
            return False
        revoked = exists(
            select(EmbedGrantRevocation.id).where(
                EmbedGrantRevocation.embed_grant_id == EmbedGrant.id
            )
        )
        allowed_origin_sets = self._session.scalars(
            select(EmbedGrant.allowed_origins)
            .where(EmbedGrant.research_case_id == research_case_id)
            .where(EmbedGrant.expires_at > _utcnow())
            .where(~revoked)
        )
        return any(normalized_origin in set(origins) for origins in allowed_origin_sets)

    def _is_expired(self, grant: EmbedGrant) -> bool:
        expiry = grant.expires_at
        if expiry.tzinfo is None:  # SQLite returns naive DateTime values.
            expiry = expiry.replace(tzinfo=timezone.utc)
        return expiry <= _utcnow()

    def _is_revoked(self, grant_id: uuid.UUID) -> bool:
        return self._session.scalar(
            select(EmbedGrantRevocation.id).where(
                EmbedGrantRevocation.embed_grant_id == grant_id
            )
        ) is not None
