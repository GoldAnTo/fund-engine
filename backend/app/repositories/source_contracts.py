"""Append-only persistence for report source contracts and provenance."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import (
    DocumentSourceRecord,
    DocumentSupplementLink,
    RecoverySupplementSnapshot,
    ReportCaseInitialAdmission,
    SourceContractVersion,
)


class SourceContractRepository:
    """Append and read source-admission facts without mutation APIs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append_contract(
        self,
        *,
        provider_name: str,
        tenant_id: str,
        may_display: bool,
        may_search: bool,
        may_ai_process: bool,
        may_export: bool,
        may_api_use: bool,
        region: str,
        effective_from: datetime,
        effective_until: datetime | None,
        retention_until: datetime | None,
        deletion_policy: str,
        downstream_restrictions: str,
        approved_by: str,
        reason: str,
        created_at: datetime,
    ) -> SourceContractVersion:
        contract = SourceContractVersion(
            provider_name=provider_name,
            tenant_id=tenant_id,
            may_display=may_display,
            may_search=may_search,
            may_ai_process=may_ai_process,
            may_export=may_export,
            may_api_use=may_api_use,
            region=region,
            effective_from=effective_from,
            effective_until=effective_until,
            retention_until=retention_until,
            deletion_policy=deletion_policy,
            downstream_restrictions=downstream_restrictions,
            approved_by=approved_by,
            reason=reason,
            created_at=created_at,
        )
        self._session.add(contract)
        self._session.flush()
        return contract

    def add_document_source_record(
        self,
        *,
        document_version_id: uuid.UUID,
        source_contract_version_id: uuid.UUID,
        admission_type: str,
        tenant_id: str,
        source_actor: str,
        provider_record_id: str | None,
        verification_state: str,
        acquisition_request: dict,
        created_at: datetime,
    ) -> DocumentSourceRecord:
        record = DocumentSourceRecord(
            document_version_id=document_version_id,
            source_contract_version_id=source_contract_version_id,
            admission_type=admission_type,
            tenant_id=tenant_id,
            source_actor=source_actor,
            provider_record_id=provider_record_id,
            verification_state=verification_state,
            acquisition_request=acquisition_request,
            created_at=created_at,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def ensure_document_source_record(
        self,
        **kwargs,
    ) -> DocumentSourceRecord:
        """Append one admission record unless this exact immutable fact exists."""
        existing = self._session.scalar(
            select(DocumentSourceRecord)
            .where(
                DocumentSourceRecord.document_version_id
                == kwargs["document_version_id"]
            )
            .where(DocumentSourceRecord.tenant_id == kwargs["tenant_id"])
            .where(
                DocumentSourceRecord.source_contract_version_id
                == kwargs["source_contract_version_id"]
            )
            .limit(1)
        )
        if existing is not None:
            return existing
        return self.add_document_source_record(**kwargs)

    def add_supplement_link(
        self,
        *,
        original_document_version_id: uuid.UUID,
        supplement_document_version_id: uuid.UUID,
        claimed_page_reference: str | None,
        created_by: str,
        created_at: datetime,
    ) -> DocumentSupplementLink:
        link = DocumentSupplementLink(
            original_document_version_id=original_document_version_id,
            supplement_document_version_id=supplement_document_version_id,
            claimed_page_reference=claimed_page_reference,
            created_by=created_by,
            created_at=created_at,
        )
        self._session.add(link)
        self._session.flush()
        return link

    def add_report_case_initial_admission(
        self,
        *,
        research_case_id: uuid.UUID,
        document_source_record_id: uuid.UUID,
        tenant_id: str,
        created_at: datetime,
    ) -> ReportCaseInitialAdmission:
        """Append the one tenant-scoped source record that owns a report Case."""
        admission = ReportCaseInitialAdmission(
            research_case_id=research_case_id,
            document_source_record_id=document_source_record_id,
            tenant_id=tenant_id,
            created_at=created_at,
        )
        self._session.add(admission)
        self._session.flush()
        return admission

    def report_case_initial_admission(
        self, research_case_id: uuid.UUID
    ) -> ReportCaseInitialAdmission | None:
        return self._session.scalar(
            select(ReportCaseInitialAdmission)
            .where(ReportCaseInitialAdmission.research_case_id == research_case_id)
            .limit(1)
        )

    def source_record_with_contract(
        self, document_source_record_id: uuid.UUID
    ) -> tuple[DocumentSourceRecord, SourceContractVersion] | None:
        return self._session.execute(
            select(DocumentSourceRecord, SourceContractVersion)
            .join(
                SourceContractVersion,
                SourceContractVersion.id == DocumentSourceRecord.source_contract_version_id,
            )
            .where(DocumentSourceRecord.id == document_source_record_id)
        ).one_or_none()

    def contract_for_record(
        self, document_source_record_id: uuid.UUID
    ) -> SourceContractVersion | None:
        return self._session.scalar(
            select(SourceContractVersion)
            .join(
                DocumentSourceRecord,
                DocumentSourceRecord.source_contract_version_id == SourceContractVersion.id,
            )
            .where(DocumentSourceRecord.id == document_source_record_id)
        )

    def source_record_for_document(
        self, document_version_id: uuid.UUID, *, tenant_id: str
    ) -> DocumentSourceRecord | None:
        return self._session.scalar(
            select(DocumentSourceRecord)
            .where(DocumentSourceRecord.document_version_id == document_version_id)
            .where(DocumentSourceRecord.tenant_id == tenant_id)
            .order_by(
                DocumentSourceRecord.created_at.desc(), DocumentSourceRecord.id.desc()
            )
            .limit(1)
        )

    def source_records_with_contracts_for_document(
        self, document_version_id: uuid.UUID, *, tenant_id: str
    ) -> list[tuple[DocumentSourceRecord, SourceContractVersion]]:
        """Return every tenant-scoped provenance contract, oldest first."""
        return list(
            self._session.execute(
                select(DocumentSourceRecord, SourceContractVersion)
                .join(
                    SourceContractVersion,
                    SourceContractVersion.id
                    == DocumentSourceRecord.source_contract_version_id,
                )
                .where(DocumentSourceRecord.document_version_id == document_version_id)
                .where(DocumentSourceRecord.tenant_id == tenant_id)
                .order_by(DocumentSourceRecord.created_at, DocumentSourceRecord.id)
            )
        )

    def add_recovery_supplement_snapshot(
        self,
        *,
        research_case_id: uuid.UUID,
        original_document_version_id: uuid.UUID,
        source_contract_version_id: uuid.UUID,
        tenant_id: str,
        source_identity: str,
        content_sha256: str,
        verbatim_text: str,
        parser_version: str,
        claimed_page_reference: str | None,
        created_by: str,
        created_at: datetime,
    ) -> RecoverySupplementSnapshot:
        snapshot = RecoverySupplementSnapshot(
            research_case_id=research_case_id,
            original_document_version_id=original_document_version_id,
            source_contract_version_id=source_contract_version_id,
            tenant_id=tenant_id,
            source_identity=source_identity,
            content_sha256=content_sha256,
            verbatim_text=verbatim_text,
            parser_version=parser_version,
            claimed_page_reference=claimed_page_reference,
            created_by=created_by,
            created_at=created_at,
        )
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def recovery_snapshot_for_identity(
        self,
        *,
        research_case_id: uuid.UUID,
        original_document_version_id: uuid.UUID,
        source_identity: str,
    ) -> RecoverySupplementSnapshot | None:
        return self._session.scalar(
            select(RecoverySupplementSnapshot)
            .where(RecoverySupplementSnapshot.research_case_id == research_case_id)
            .where(
                RecoverySupplementSnapshot.original_document_version_id
                == original_document_version_id
            )
            .where(RecoverySupplementSnapshot.source_identity == source_identity)
            .order_by(RecoverySupplementSnapshot.created_at, RecoverySupplementSnapshot.id)
            .limit(1)
        )

    def recovery_snapshot_for_case(
        self, *, research_case_id: uuid.UUID, snapshot_id: uuid.UUID
    ) -> RecoverySupplementSnapshot | None:
        return self._session.scalar(
            select(RecoverySupplementSnapshot)
            .where(RecoverySupplementSnapshot.research_case_id == research_case_id)
            .where(RecoverySupplementSnapshot.id == snapshot_id)
        )
