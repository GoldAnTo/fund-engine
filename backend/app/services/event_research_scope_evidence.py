"""Read the evidence currently mapped into an event-research scope."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_research import (
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import EvidenceLink, Thesis


def current_mapped_evidence_ids(session: Session, case_id: uuid.UUID) -> list[uuid.UUID]:
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    if scope is None:
        return []
    active_statements = select(EventResearchScopeFactor.statement).where(
        EventResearchScopeFactor.scope_version_id == scope.id
    )
    return list(
        session.scalars(
            select(EventResearchScopeEvidenceAssignment.evidence_link_id)
            .join(
                EvidenceLink,
                EvidenceLink.id == EventResearchScopeEvidenceAssignment.evidence_link_id,
            )
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .where(EventResearchScopeEvidenceAssignment.scope_version_id == scope.id)
            .where(EventResearchScopeEvidenceAssignment.disposition == "mapped")
            .where(EventResearchScopeEvidenceAssignment.factor_statement.in_(active_statements))
            .where(EventResearchScopeEvidenceAssignment.factor_statement == Thesis.statement)
            .where(Thesis.research_case_id == case_id)
            .where(EvidenceLink.review_state == "reviewed")
        )
    )
