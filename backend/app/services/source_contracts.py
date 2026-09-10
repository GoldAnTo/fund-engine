"""Permission-safe admission of pasted report recovery snapshots."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import (
    CaseDocumentVersion,
    DocumentSourceRecord,
    DocumentSupplementLink,
    DocumentVersion,
    RecoverySupplementSnapshot as RecoverySupplementSnapshotArtifact,
    ResearchCase,
    SourceContractVersion,
    SourceSpan,
)
from app.repositories.documents import DocumentRepository
from app.repositories.source_contracts import SourceContractRepository
from app.services.ingest import DocumentService


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class SourcePermission:
    """The restrictive capability terms that may flow into one snapshot."""

    may_display: bool = False
    may_search: bool = False
    may_ai_process: bool = False
    may_export: bool = False
    may_api_use: bool = False
    retention_until: datetime | None = None
    region: str = "GLOBAL"
    downstream_restrictions: str = ""
    effective_from: datetime | None = None
    effective_until: datetime | None = None

    @classmethod
    def from_contract(cls, contract: SourceContractVersion) -> "SourcePermission":
        return cls(
            may_display=contract.may_display,
            may_search=contract.may_search,
            may_ai_process=contract.may_ai_process,
            may_export=contract.may_export,
            may_api_use=contract.may_api_use,
            retention_until=contract.retention_until,
            region=contract.region,
            downstream_restrictions=contract.downstream_restrictions,
            effective_from=contract.effective_from,
            effective_until=contract.effective_until,
        )


def _earliest_non_null(*values: datetime | None) -> datetime | None:
    present = [_utc(value) for value in values if value is not None]
    return min(present) if present else None


def _strictest_region(left: str, right: str) -> str:
    normalized_left = left.strip()
    normalized_right = right.strip()
    if normalized_left == normalized_right:
        return normalized_left
    universal = {"", "*", "ALL", "GLOBAL"}
    if normalized_left.upper() in universal:
        return normalized_right
    if normalized_right.upper() in universal:
        return normalized_left
    raise ValueError("source contracts have incompatible region restrictions")


def _combined_restrictions(left: str, right: str) -> str:
    normalized_left = " ".join(left.split())
    normalized_right = " ".join(right.split())
    unrestricted = {"", "none", "unrestricted"}
    if normalized_left.lower() in unrestricted:
        return normalized_right
    if normalized_right.lower() in unrestricted:
        return normalized_left
    if normalized_left == normalized_right:
        return normalized_left
    # Free-text restrictions cannot safely be ranked, so the effective terms
    # deliberately retain both.  A downstream consumer must satisfy each.
    return f"{normalized_left} AND {normalized_right}"


def intersect_permissions(
    original: SourcePermission, supplement: SourcePermission
) -> SourcePermission:
    """Return the only capabilities permitted by both source contracts."""

    effective_from_values = [
        _utc(value)
        for value in (original.effective_from, supplement.effective_from)
        if value is not None
    ]
    return SourcePermission(
        may_display=original.may_display and supplement.may_display,
        may_search=original.may_search and supplement.may_search,
        may_ai_process=original.may_ai_process and supplement.may_ai_process,
        may_export=original.may_export and supplement.may_export,
        may_api_use=original.may_api_use and supplement.may_api_use,
        retention_until=_earliest_non_null(
            original.retention_until, supplement.retention_until
        ),
        region=_strictest_region(original.region, supplement.region),
        downstream_restrictions=_combined_restrictions(
            original.downstream_restrictions, supplement.downstream_restrictions
        ),
        effective_from=max(effective_from_values) if effective_from_values else None,
        effective_until=_earliest_non_null(
            original.effective_until, supplement.effective_until
        ),
    )


def require_ai_processing(
    permission: SourcePermission | SourceContractVersion | None,
    *,
    now: datetime | None = None,
) -> SourcePermission:
    """Fail closed unless a currently effective source contract permits AI."""

    if permission is None:
        raise ValueError("missing source contract for AI processing")
    effective = (
        SourcePermission.from_contract(permission)
        if isinstance(permission, SourceContractVersion)
        else permission
    )
    at = _utc(now or datetime.now(timezone.utc))
    if effective.effective_from is not None and at < _utc(effective.effective_from):
        raise ValueError("source contract is not yet effective for AI processing")
    if effective.effective_until is not None and at > _utc(effective.effective_until):
        raise ValueError("source contract is expired for AI processing")
    if not effective.may_ai_process:
        raise ValueError("source contract does not permit AI processing")
    return effective


@dataclass(frozen=True)
class RecoverySupplementSnapshot:
    document: DocumentVersion | None
    source_record: DocumentSourceRecord | None
    link: DocumentSupplementLink | None
    span: SourceSpan | None
    artifact: RecoverySupplementSnapshotArtifact | None = None


@dataclass(frozen=True)
class RecoverySupplementArtifactRead:
    """Case-owned recovery text and its stable locator for later extraction."""

    id: uuid.UUID
    content: str
    locator: dict[str, str | None]
    source_identity: str
    content_sha256: str


class RecoverySupplementSnapshotService:
    """Admit an immutable pasted recovery source without extracting research."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._documents = DocumentService(DocumentRepository(session))
        self._contracts = SourceContractRepository(session)

    def admit(
        self,
        *,
        research_case_id: uuid.UUID,
        original_document_version_id: uuid.UUID,
        content: str,
        claimed_page_reference: str | None,
        created_by: str,
        tenant_id: str,
        supplement_contract_version_id: uuid.UUID,
    ) -> RecoverySupplementSnapshot:
        case = self._session.get(ResearchCase, research_case_id)
        original = self._session.get(DocumentVersion, original_document_version_id)
        if case is None:
            raise ValueError("report research case not found")
        if original is None or self._session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == research_case_id,
                CaseDocumentVersion.document_version_id == original_document_version_id,
            )
        ) is None:
            raise ValueError("report document must belong to its research case")

        supplement_contract = self._session.get(
            SourceContractVersion, supplement_contract_version_id
        )
        if supplement_contract is None or supplement_contract.tenant_id != tenant_id:
            raise ValueError("source contracts must share the requested tenant")
        initial_admission = self._contracts.report_case_initial_admission(
            research_case_id
        )
        if initial_admission is None:
            raise ValueError("report case has no frozen initial source admission")
        if initial_admission.tenant_id != tenant_id:
            raise ValueError("source contract tenant is not permitted for report case")
        initial_record = self._contracts.source_record_with_contract(
            initial_admission.document_source_record_id
        )
        if initial_record is None:
            raise ValueError("report case initial source record is missing")
        original_record, original_contract = initial_record
        if (
            original_record.document_version_id != original_document_version_id
            or original_record.tenant_id != tenant_id
        ):
            raise ValueError("report case initial source admission is inconsistent")
        original_records = [(original_record, original_contract)]

        original_permission: SourcePermission | None = None
        for original_record, original_contract in original_records:
            if original_record.tenant_id != tenant_id:
                raise ValueError("source contracts must share the requested tenant")
            record_permission = require_ai_processing(original_contract)
            original_permission = (
                record_permission
                if original_permission is None
                else intersect_permissions(original_permission, record_permission)
            )
        assert original_permission is not None
        supplement_permission = require_ai_processing(supplement_contract)
        effective_permission = intersect_permissions(
            original_permission, supplement_permission
        )
        require_ai_processing(effective_permission)

        raw = content.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        source_identity = f"pasted://report-supplement/{digest}"
        existing_artifact = self._contracts.recovery_snapshot_for_identity(
            research_case_id=research_case_id,
            original_document_version_id=original.id,
            source_identity=source_identity,
        )
        if existing_artifact is not None:
            return RecoverySupplementSnapshot(
                document=None,
                source_record=None,
                link=None,
                span=None,
                artifact=existing_artifact,
            )
        existing = self._session.scalar(
            select(DocumentVersion).where(DocumentVersion.content_sha256 == digest)
        )
        if existing is not None and existing.id == original.id:
            raise ValueError("recovery supplement cannot self-link its original document")
        original_contract_ids = [str(contract.id) for _, contract in original_records]
        effective_contract = self._contracts.append_contract(
            provider_name="intersection:" + "+".join(
                [contract.provider_name for _, contract in original_records]
                + [supplement_contract.provider_name]
            ),
            tenant_id=tenant_id,
            may_display=effective_permission.may_display,
            may_search=effective_permission.may_search,
            may_ai_process=effective_permission.may_ai_process,
            may_export=effective_permission.may_export,
            may_api_use=effective_permission.may_api_use,
            region=effective_permission.region,
            effective_from=effective_permission.effective_from or datetime.now(timezone.utc),
            effective_until=effective_permission.effective_until,
            retention_until=effective_permission.retention_until,
            deletion_policy=" AND ".join(
                [contract.deletion_policy for _, contract in original_records]
                + [supplement_contract.deletion_policy]
            ),
            downstream_restrictions=effective_permission.downstream_restrictions,
            approved_by="source-contract-intersection-service",
            reason=(
                "recovery supplement permission intersection of "
                + ", ".join(original_contract_ids + [str(supplement_contract.id)])
            ),
            created_at=datetime.now(timezone.utc),
        )
        if existing is not None:
            artifact = self._contracts.add_recovery_supplement_snapshot(
                research_case_id=research_case_id,
                original_document_version_id=original.id,
                source_contract_version_id=effective_contract.id,
                tenant_id=tenant_id,
                source_identity=source_identity,
                content_sha256=digest,
                verbatim_text=content,
                parser_version="user-pasted-report-v1",
                claimed_page_reference=claimed_page_reference,
                created_by=created_by,
                created_at=datetime.now(timezone.utc),
            )
            return RecoverySupplementSnapshot(
                document=None,
                source_record=None,
                link=None,
                span=None,
                artifact=artifact,
            )
        supplement = self._documents.freeze(
            raw=raw,
            source_url=source_identity,
            parser_version="user-pasted-report-v1",
            title=original.title,
            natural_key=None,
            language="zh",
            parse_state="partial",
        )
        self._documents.attach_to_case(
            research_case_id=research_case_id, document_version_id=supplement.id
        )
        record = self._contracts.add_document_source_record(
            document_version_id=supplement.id,
            source_contract_version_id=effective_contract.id,
            admission_type="pasted_snapshot",
            tenant_id=tenant_id,
            source_actor=created_by,
            provider_record_id=None,
            verification_state="verified",
            acquisition_request={
                "recovery_of_document_version_id": str(original.id),
                "original_contract_version_ids": original_contract_ids,
                "supplement_contract_version_id": str(supplement_contract.id),
            },
            created_at=datetime.now(timezone.utc),
        )
        link = self._contracts.add_supplement_link(
            original_document_version_id=original.id,
            supplement_document_version_id=supplement.id,
            claimed_page_reference=claimed_page_reference,
            created_by=created_by,
            created_at=datetime.now(timezone.utc),
        )
        span = self._documents.add_span(
            document_version_id=supplement.id,
            locator={
                "input_kind": "recovery_text",
                "claimed_page_reference": claimed_page_reference,
                "parser": "user-pasted-report-v1",
            },
            verbatim_text=content,
            text_sha256=digest,
        )
        return RecoverySupplementSnapshot(
            document=supplement, source_record=record, link=link, span=span
        )

    def read_case_artifact(
        self, *, research_case_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> RecoverySupplementArtifactRead:
        snapshot = self._contracts.recovery_snapshot_for_case(
            research_case_id=research_case_id, snapshot_id=snapshot_id
        )
        if snapshot is None:
            raise ValueError("recovery supplement snapshot not found for research case")
        return RecoverySupplementArtifactRead(
            id=snapshot.id,
            content=snapshot.verbatim_text,
            locator={
                "input_kind": "recovery_text",
                "claimed_page_reference": snapshot.claimed_page_reference,
                "parser": snapshot.parser_version,
            },
            source_identity=snapshot.source_identity,
            content_sha256=snapshot.content_sha256,
        )
