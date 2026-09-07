"""Resolve review ownership through persisted research resources."""
from __future__ import annotations

import uuid

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.ledger import AIAssessment, EvidenceLink, EvidenceSnapshot, Thesis
from app.models.proposals import Proposal
from app.services.case_tenant_access import CaseTenantAccess


def proposal_tenant_predicate(tenant_id: str):
    """Filter ownership and target consistency in SQL, before pagination."""
    from app.models.ledger import CaseTenantAdmission

    case_ids = select(CaseTenantAdmission.research_case_id).where(
        CaseTenantAdmission.tenant_id == tenant_id
    )
    return Proposal.research_case_id.in_(case_ids) & proposal_target_predicate()


def proposal_target_predicate():
    """A proposal target must resolve within its persisted owning case."""
    target_id = Proposal.target_context["thesis_id"].as_string()
    # UUID storage is hex on SQLite and native UUID on PostgreSQL. Normalize
    # both sides without casting possibly malformed JSON into a database UUID.
    valid_thesis = select(Thesis.id).where(
        func.replace(cast(Thesis.id, String), "-", "") == func.replace(target_id, "-", ""),
        Thesis.research_case_id == Proposal.research_case_id,
    ).correlate(Proposal).exists()
    return or_(
        valid_thesis,
        (target_id.is_(None)) & Proposal.kind.not_in(("evidence_link", "causal_edge")),
    )


class ReviewTenantAccess:
    def __init__(self, session: Session):
        self._session = session
        self._cases = CaseTenantAccess(session)

    def require_thesis(self, thesis_id: uuid.UUID, tenant_id: str) -> Thesis:
        thesis = self._session.get(Thesis, thesis_id)
        if thesis is None:
            raise NotFoundError("review resource not found")
        self._cases.require_case(thesis.research_case_id, tenant_id)
        return thesis

    def require_link(self, link_id: uuid.UUID, tenant_id: str) -> EvidenceLink:
        link = self._session.get(EvidenceLink, link_id)
        if link is None:
            raise NotFoundError("review resource not found")
        self.require_thesis(link.thesis_id, tenant_id)
        return link

    def require_assessment(self, assessment_id: uuid.UUID, tenant_id: str) -> uuid.UUID:
        assessment = self._session.get(AIAssessment, assessment_id)
        snapshot = self._session.get(EvidenceSnapshot, assessment.snapshot_id) if assessment else None
        if snapshot is None:
            raise NotFoundError("review resource not found")
        return self.require_thesis(snapshot.thesis_id, tenant_id).research_case_id

    def require_proposal(self, proposal_id: uuid.UUID, tenant_id: str) -> Proposal:
        proposal = self._session.scalar(select(Proposal).where(
            Proposal.id == proposal_id, proposal_tenant_predicate(tenant_id)
        ))
        if proposal is None:
            raise NotFoundError("review resource not found")
        return proposal
