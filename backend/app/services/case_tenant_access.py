"""Case ownership checks derived from immutable tenant admissions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import CaseTenantAdmission


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
            admitted_at=datetime.now(timezone.utc),
        )
        self._session.add(admission)
        self._session.flush()
        return admission

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
