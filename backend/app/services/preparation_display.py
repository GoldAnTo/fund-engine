"""Display-policy checks shared by preparation reads and human gates."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import AtomicClaimCandidate, CaseTenantAdmission, SourceSpan
from app.models.source_governance import SourceContract
from app.services.source_admission import source_contract_is_active


def preparation_artifact_allows_display(
    session: Session, case_id: uuid.UUID, payload: object
) -> bool:
    """Return whether a preparation artifact can be shown to a human.

    The admitted input document is always an input to preparation.  Candidate
    artifacts may additionally refer to spans from other frozen documents, so
    those contracts must also allow current display.  Unknown candidate
    references fail closed.
    """
    document_id = session.scalar(
        select(CaseTenantAdmission.initial_document_version_id).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    if document_id is not None:
        contract = session.scalar(
            select(SourceContract).where(SourceContract.document_version_id == document_id)
        )
        if contract is not None and (
            not contract.allow_display or not source_contract_is_active(contract)
        ):
            return False

    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        return True
    candidate_ids: set[uuid.UUID] = set()
    for item in payload["candidates"]:
        if not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str):
            return False
        try:
            candidate_ids.add(uuid.UUID(item["candidate_id"]))
        except ValueError:
            return False
    if not candidate_ids:
        return True
    rows = list(
        session.execute(
            select(AtomicClaimCandidate.id, SourceContract)
            .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
            .outerjoin(
                SourceContract,
                SourceContract.document_version_id == SourceSpan.document_version_id,
            )
            .where(AtomicClaimCandidate.id.in_(candidate_ids))
        )
    )
    if {candidate_id for candidate_id, _ in rows} != candidate_ids:
        return False
    return all(
        contract is None or (contract.allow_display and source_contract_is_active(contract))
        for _, contract in rows
    )
