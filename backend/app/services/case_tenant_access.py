"""Case ownership checks derived from immutable tenant admissions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, ResearchCase


class CaseTenantAccess:
    def __init__(self, session: Session) -> None:
        self._session = session

    def admit_initial_case(
        self,
        *,
        case_id: uuid.UUID,
        tenant_id: str,
        initial_document_version_id: uuid.UUID,
        admitted_by: str,
        admission_reason: str | None = None,
    ) -> CaseTenantAdmission:
        existing = self._session.scalar(
            select(CaseTenantAdmission).where(
                CaseTenantAdmission.research_case_id == case_id
            )
        )
        if existing is not None:
            raise ValidationFailedError("event research case already has a tenant admission")
        admission = CaseTenantAdmission(
            research_case_id=case_id,
            tenant_id=tenant_id,
            initial_document_version_id=initial_document_version_id,
            admitted_by=admitted_by,
            admission_reason=admission_reason,
            admitted_at=datetime.now(timezone.utc),
        )
        self._session.add(admission)
        self._session.flush()
        return admission

    def admit_legacy_case(
        self,
        *,
        case_id: uuid.UUID,
        tenant_id: str,
        initial_document_version_id: uuid.UUID,
        admitted_by: str,
        admission_reason: str,
    ) -> CaseTenantAdmission:
        """Make an explicit, auditable ownership decision for a pre-tenant Case.

        The caller must nominate a document that was already attached to this
        Case.  We never infer ownership from a global content hash, provider,
        or the legacy ``created_by`` field.
        """
        if self._session.get(ResearchCase, case_id) is None:
            raise NotFoundError("event research case not found")
        attached = self._session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == case_id,
                CaseDocumentVersion.document_version_id == initial_document_version_id,
            )
        )
        if attached is None:
            raise ValidationFailedError(
                "initial document must already be attached to this case"
            )
        return self.admit_initial_case(
            case_id=case_id,
            tenant_id=tenant_id,
            initial_document_version_id=initial_document_version_id,
            admitted_by=admitted_by,
            admission_reason=admission_reason,
        )

    def require_case(self, case_id: uuid.UUID, tenant_id: str) -> CaseTenantAdmission:
        admission = self._session.scalar(
            select(CaseTenantAdmission).where(
                CaseTenantAdmission.research_case_id == case_id,
                CaseTenantAdmission.tenant_id == tenant_id,
            )
        )
        if admission is None:
            # Do not reveal whether a Case exists for another tenant.
            raise NotFoundError("event research case not found")
        return admission

    def case_ids(self, tenant_id: str):
        return select(CaseTenantAdmission.research_case_id).where(
            CaseTenantAdmission.tenant_id == tenant_id
        )
