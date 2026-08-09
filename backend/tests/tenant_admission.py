"""Explicit tenant-admission helper for protected Case route tests."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, DocumentVersion
from app.services.case_tenant_access import CaseTenantAccess


def admit_case(
    session: Session,
    case_id: uuid.UUID,
    *,
    tenant_id: str = "test-team",
    document_version_id: uuid.UUID | None = None,
) -> None:
    """Attach a frozen test source and make the test's ownership decision explicit."""
    document_id = document_version_id
    if document_id is None:
        document_id = session.scalar(
            select(DocumentVersion.id).order_by(DocumentVersion.acquired_at)
        )
    if document_id is None:
        now = datetime.now(timezone.utc)
        document = DocumentVersion(
            content_sha256=uuid.uuid4().hex,
            source_url="https://example.test/test-admission-source",
            available_at=now,
            acquired_at=now,
            parser_version="test-fixture",
        )
        session.add(document)
        session.flush()
        document_id = document.id

    attached = session.scalar(
        select(CaseDocumentVersion.id).where(
            CaseDocumentVersion.research_case_id == case_id,
            CaseDocumentVersion.document_version_id == document_id,
        )
    )
    if attached is None:
        session.add(
            CaseDocumentVersion(
                research_case_id=case_id,
                document_version_id=document_id,
                linked_at=datetime.now(timezone.utc),
            )
        )
        session.flush()
    CaseTenantAccess(session).admit_initial_case(
        case_id=case_id,
        tenant_id=tenant_id,
        initial_document_version_id=document_id,
        admitted_by="test-fixture",
    )
