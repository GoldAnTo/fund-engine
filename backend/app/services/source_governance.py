"""Turn intake declarations into immutable, inspectable source contracts."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import DocumentVersion
from app.models.source_governance import ProviderRecord, SourceContract


USER_CONTROLLED_TYPES = frozenset({"pasted_snapshot", "uploaded_file"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _permission(metadata: dict[str, Any], name: str, *, default: bool) -> bool:
    permissions = metadata.get("permissions")
    value = permissions.get(name) if isinstance(permissions, dict) else None
    return value if isinstance(value, bool) else default


class SourceGovernanceService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def record_event_intake(
        self,
        *,
        document: DocumentVersion,
        source_type: str,
        source_metadata: dict[str, Any] | None,
        declared_by: str,
    ) -> SourceContract:
        existing = self._session.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == document.id
            )
        )
        if existing is not None:
            return existing
        metadata = dict(source_metadata or {})
        user_controlled = source_type in USER_CONTROLLED_TYPES
        now = _utcnow()
        contract = SourceContract(
            document_version_id=document.id,
            source_type=source_type,
            provider_or_tenant=str(
                metadata.get("provider_name")
                or metadata.get("tenant")
                or declared_by
            ),
            allow_ai_processing=_permission(metadata, "ai_processing", default=user_controlled),
            allow_display=_permission(metadata, "display", default=user_controlled),
            allow_export=_permission(metadata, "export", default=False),
            allow_api=_permission(metadata, "api", default=False),
            region=str(metadata.get("region") or "not_recorded"),
            effective_from=None,
            effective_until=None,
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
