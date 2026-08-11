"""Turn intake declarations into immutable, inspectable source contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import DocumentVersion
from app.models.source_governance import ProviderRecord, SourceContract


USER_CONTROLLED_TYPES = frozenset({"pasted_snapshot", "uploaded_file"})
RESEARCH_SOURCE_TYPES = frozenset(
    {
        "pasted_snapshot",
        "uploaded_file",
        "licensed_provider",
        "public_url",
        "company_disclosure",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _permission(metadata: dict[str, Any], name: str, *, default: bool) -> bool:
    permissions = metadata.get("permissions")
    value = permissions.get(name) if isinstance(permissions, dict) else None
    return value if isinstance(value, bool) else default


def _effective_at(metadata: dict[str, Any], name: str) -> datetime | None:
    """Read an explicit source-contract date without silently inventing one."""
    raw = metadata.get(name)
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str):
        try:
            value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be an ISO-8601 date or timestamp") from exc
    else:
        raise ValueError(f"{name} must be an ISO-8601 date or timestamp")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_research_source_type(
    *,
    source_type: str,
    source_metadata: dict[str, Any] | None,
    document: DocumentVersion,
    incoming_source_url: str | None = None,
) -> str:
    metadata = dict(source_metadata or {})
    raw = metadata.get("research_source_type", source_type)
    if raw is None:
        raw = source_type
    research_source_type = str(raw).strip()
    if research_source_type not in RESEARCH_SOURCE_TYPES:
        raise ValueError("research_source_type is not supported")
    if research_source_type == "company_disclosure":
        try:
            parsed = urlparse(
                incoming_source_url
                if incoming_source_url is not None
                else document.source_url or ""
            )
        except ValueError as exc:
            raise ValueError(
                "company_disclosure requires an HTTP(S) source_url"
            ) from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ValueError("company_disclosure requires an HTTP(S) source_url")
    return research_source_type


class SourceGovernanceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def declared_event_intake_allows_research(
        self,
        *,
        source_type: str,
        source_metadata: dict[str, Any] | None,
        at: datetime | None = None,
    ) -> bool:
        """Evaluate this intake declaration without borrowing another Case's terms.

        Content-addressed documents can be deduplicated, but a later Case's
        explicit declaration must never be widened by a prior document-level
        contract.  Callers use this as an additional continuation gate; the
        stored contract remains the durable audit record for the original
        document version.
        """
        metadata = dict(source_metadata or {})
        user_controlled = source_type in USER_CONTROLLED_TYPES
        if not _permission(metadata, "ai_processing", default=user_controlled):
            return False
        if not _permission(metadata, "display", default=user_controlled):
            return False
        effective_from = _effective_at(metadata, "effective_from")
        effective_until = _effective_at(metadata, "effective_until")
        if (
            effective_from is not None
            and effective_until is not None
            and effective_until < effective_from
        ):
            raise ValueError("effective_until must not be before effective_from")
        now = at or _utcnow()
        return not (
            (effective_from is not None and now < effective_from)
            or (effective_until is not None and now > effective_until)
        )

    @staticmethod
    def _assert_existing_contract_compatible(
        *,
        existing: SourceContract,
        source_type: str,
        source_metadata: dict[str, Any] | None,
        document: DocumentVersion,
        incoming_source_url: str | None,
    ) -> None:
        """Fail closed when deduplicated bytes arrive under different terms.

        A document version has one immutable source contract.  Until contracts
        are modelled per Case admission, attaching the same bytes under a
        different declaration would make the read path display the first
        declaration's terms.  Reject that attachment instead of silently
        widening the later Case's stated permission.
        """
        metadata = dict(source_metadata or {})
        user_controlled = source_type in USER_CONTROLLED_TYPES
        # Tenant ownership is an independent boundary from a provider name.
        # Event routes inject the authenticated tenant, so it must win the
        # compatibility key rather than be masked by client-supplied provider
        # metadata.
        incoming_provider = metadata.get("tenant") or metadata.get("provider_name")
        incoming_restrictions = list(
            metadata.get("downstream_restrictions")
            or (
                ["仅限当前 Case 研究与人工审核"]
                if user_controlled
                else ["权限未完整记录；不得作为正式证据"]
            )
        )
        incoming = {
            "source_type": source_type,
            "research_source_type": _normalize_research_source_type(
                source_type=source_type,
                source_metadata=source_metadata,
                document=document,
                incoming_source_url=incoming_source_url,
            ),
            "provider_or_tenant": str(incoming_provider)
            if incoming_provider is not None
            else None,
            "allow_ai_processing": _permission(
                metadata, "ai_processing", default=user_controlled
            ),
            "allow_display": _permission(
                metadata, "display", default=user_controlled
            ),
            "allow_export": _permission(metadata, "export", default=False),
            "allow_api": _permission(metadata, "api", default=False),
            "region": str(metadata.get("region") or "not_recorded"),
            "effective_from": _effective_at(metadata, "effective_from"),
            "effective_until": _effective_at(metadata, "effective_until"),
            "retention_policy": str(
                metadata.get("retention_policy") or "case_retained"
            ),
            "deletion_policy": str(
                metadata.get("deletion_policy") or "not_recorded"
            ),
            "downstream_restrictions": incoming_restrictions,
            "contract_version": str(metadata["contract_version"])
            if metadata.get("contract_version")
            else None,
        }
        if (
            incoming["effective_from"] is not None
            and incoming["effective_until"] is not None
            and incoming["effective_until"] < incoming["effective_from"]
        ):
            raise ValueError("effective_until must not be before effective_from")
        for field, value in incoming.items():
            if value is None and field == "provider_or_tenant":
                continue
            if getattr(existing, field) != value:
                raise ValueError(
                    "deduplicated original has a different source contract; "
                    "do not reuse it under incompatible permissions"
                )

    def record_event_intake(
        self,
        *,
        document: DocumentVersion,
        source_type: str,
        source_metadata: dict[str, Any] | None,
        declared_by: str,
        incoming_source_url: str | None = None,
    ) -> SourceContract:
        existing = self._session.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == document.id
            )
        )
        if existing is not None:
            self._assert_existing_contract_compatible(
                existing=existing,
                source_type=source_type,
                source_metadata=source_metadata,
                document=document,
                incoming_source_url=incoming_source_url,
            )
            return existing
        metadata = dict(source_metadata or {})
        user_controlled = source_type in USER_CONTROLLED_TYPES
        research_source_type = _normalize_research_source_type(
            source_type=source_type,
            source_metadata=source_metadata,
            document=document,
            incoming_source_url=incoming_source_url,
        )
        now = _utcnow()
        effective_from = _effective_at(metadata, "effective_from")
        effective_until = _effective_at(metadata, "effective_until")
        if (
            effective_from is not None
            and effective_until is not None
            and effective_until < effective_from
        ):
            raise ValueError("effective_until must not be before effective_from")
        contract = SourceContract(
            document_version_id=document.id,
            source_type=source_type,
            research_source_type=research_source_type,
            provider_or_tenant=str(
                metadata.get("tenant")
                or metadata.get("provider_name")
                or declared_by
            ),
            allow_ai_processing=_permission(metadata, "ai_processing", default=user_controlled),
            allow_display=_permission(metadata, "display", default=user_controlled),
            allow_export=_permission(metadata, "export", default=False),
            allow_api=_permission(metadata, "api", default=False),
            region=str(metadata.get("region") or "not_recorded"),
            effective_from=effective_from,
            effective_until=effective_until,
            retention_policy=str(metadata.get("retention_policy") or "case_retained"),
            deletion_policy=str(metadata.get("deletion_policy") or "not_recorded"),
            downstream_restrictions=list(metadata.get("downstream_restrictions") or (["仅限当前 Case 研究与人工审核"] if user_controlled else ["权限未完整记录；不得作为正式证据"])),
            contract_version=(str(metadata["contract_version"]) if metadata.get("contract_version") else None),
            intake_metadata=metadata,
            declared_by=declared_by,
            created_at=now,
        )
        self._session.add(contract)
        if source_type == "licensed_provider" and metadata.get("provider_name") and metadata.get("provider_record_id"):
            self._session.add(
                ProviderRecord(
                    document_version_id=document.id,
                    provider_name=str(metadata["provider_name"]),
                    provider_record_id=str(metadata["provider_record_id"]),
                    request_scope=dict(metadata.get("request_scope") or {}),
                    retrieval_reference=(str(metadata["retrieval_reference"]) if metadata.get("retrieval_reference") else None),
                    content_sha256=document.content_sha256,
                    retrieved_at=now,
                    contract_version=(str(metadata["contract_version"]) if metadata.get("contract_version") else None),
                    created_at=now,
                )
            )
        self._session.flush()
        return contract

    def record_supplement_intake(
        self,
        *,
        document: DocumentVersion,
        original_contract: SourceContract | None,
        source_metadata: dict[str, Any] | None,
        declared_by: str,
    ) -> SourceContract:
        """Freeze a recovery snapshot under the intersection of both sources.

        The text author may grant less permission than the original report;
        neither side can widen the other.  An absent original contract fails
        closed for AI processing, while display remains possible for the
        researcher who is completing the recovery.
        """
        existing = self._session.scalar(
            select(SourceContract).where(SourceContract.document_version_id == document.id)
        )
        if existing is not None:
            return existing
        metadata = dict(source_metadata or {})
        now = _utcnow()
        research_source_type = _normalize_research_source_type(
            source_type="pasted_snapshot",
            source_metadata=source_metadata,
            document=document,
        )
        original_ai = original_contract.allow_ai_processing if original_contract is not None else False
        original_display = original_contract.allow_display if original_contract is not None else True
        original_export = original_contract.allow_export if original_contract is not None else False
        original_api = original_contract.allow_api if original_contract is not None else False
        restrictions = list(original_contract.downstream_restrictions or []) if original_contract is not None else ["原资料权限未完整记录；补充正文不得用于 AI 或正式证据"]
        restrictions.append("补充正文为独立快照；页码仅为用户声明，不能替代原件定位")
        contract = SourceContract(
            document_version_id=document.id,
            source_type="pasted_snapshot",
            research_source_type=research_source_type,
            provider_or_tenant=str(metadata.get("provider_name") or declared_by),
            allow_ai_processing=original_ai and _permission(metadata, "ai_processing", default=True),
            allow_display=original_display and _permission(metadata, "display", default=True),
            allow_export=original_export and _permission(metadata, "export", default=False),
            allow_api=original_api and _permission(metadata, "api", default=False),
            region=str(metadata.get("region") or (original_contract.region if original_contract is not None else "not_recorded")),
            effective_from=None,
            effective_until=original_contract.effective_until if original_contract is not None else None,
            retention_policy=str(metadata.get("retention_policy") or (original_contract.retention_policy if original_contract is not None else "case_retained")),
            deletion_policy=str(metadata.get("deletion_policy") or (original_contract.deletion_policy if original_contract is not None else "not_recorded")),
            downstream_restrictions=restrictions,
            contract_version=(str(metadata["contract_version"]) if metadata.get("contract_version") else original_contract.contract_version if original_contract is not None else None),
            intake_metadata=metadata,
            declared_by=declared_by,
            created_at=now,
        )
        self._session.add(contract)
        self._session.flush()
        return contract
