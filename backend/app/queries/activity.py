"""Activity feed + evidence-changes read models (design §8.7).

Both feeds are derived from the ``domain_events`` outbox, which makes them
rebuildable and consistent with the ledger (design §9.4).  For the first
version we read the outbox directly — this is itself a valid projection and
avoids a second materialized table; a later consumer can cache it behind
``projection_checkpoints`` if volume demands.

Every returned item carries ``event_id`` so clients can page with a stable
cursor (after=event_id) and resume exactly where they left off.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, and_, cast, func, or_, select, tuple_
from app.errors import NotFoundError
from app.models.ledger import (AIAssessment, AtomicClaimCandidate, CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, EvidenceLink, EvidenceSnapshot, ResearchCase, SourceSpan, SourceStatement, Thesis)
from app.models.operational import Job, TaskItem
from app.models.proposals import Proposal
from app.services.review_tenant_access import proposal_target_predicate
from sqlalchemy.orm import Session

from app.models.events import DomainEvent

# Event types that describe evidence changing in a case (feed #2).
_EVIDENCE_EVENT_TYPES = frozenset(
    {
        "source_statement_extracted",
        "evidence_link_proposed",
        "evidence_link_published",
        "evidence_link_rejected",
        "causal_edge_published",
        "ai_assessment_frozen",
        "review_decision_recorded",
        "proposal_decided",
    }
)


def _same_uuid(column, value):
    # Never cast untrusted event strings to UUID; normalize native UUID/SQLite hex.
    return func.replace(cast(column, String), "-", "") == func.replace(cast(value, String), "-", "")


def resource_case_predicate(resource_type, resource_id, case_id):
    """Resolve references through persisted foreign keys, never event JSON."""
    direct = {"research_case": (ResearchCase, ResearchCase.id),
              "thesis": (Thesis, Thesis.research_case_id),
              "proposal": (Proposal, Proposal.research_case_id),
              "job": (Job, Job.research_case_id),
              "task_item": (TaskItem, TaskItem.research_case_id)}
    if resource_type in direct:
        model, owner = direct[resource_type]
        query = select(model.id).where(_same_uuid(model.id, resource_id), owner == case_id)
        if resource_type == "proposal":
            query = query.where(proposal_target_predicate())
        return query.correlate_except(model).exists()
    if resource_type == "evidence_link":
        return select(EvidenceLink.id).join(Thesis, Thesis.id == EvidenceLink.thesis_id).where(_same_uuid(EvidenceLink.id, resource_id), Thesis.research_case_id == case_id).correlate_except(EvidenceLink, Thesis).exists()
    if resource_type == "ai_assessment":
        return select(AIAssessment.id).join(EvidenceSnapshot, EvidenceSnapshot.id == AIAssessment.snapshot_id).join(Thesis, Thesis.id == EvidenceSnapshot.thesis_id).where(_same_uuid(AIAssessment.id, resource_id), Thesis.research_case_id == case_id).correlate_except(AIAssessment, EvidenceSnapshot, Thesis).exists()
    if resource_type == "document_version":
        return select(CaseDocumentVersion.id).join(DocumentVersion, DocumentVersion.id == CaseDocumentVersion.document_version_id).where(_same_uuid(DocumentVersion.id, resource_id), CaseDocumentVersion.research_case_id == case_id).correlate_except(CaseDocumentVersion, DocumentVersion).exists()
    if resource_type in {"atomic_claim_candidate", "source_statement"}:
        model = AtomicClaimCandidate if resource_type == "atomic_claim_candidate" else SourceStatement
        return select(model.id).join(SourceSpan, SourceSpan.id == model.source_span_id).join(CaseDocumentVersion, CaseDocumentVersion.document_version_id == SourceSpan.document_version_id).where(_same_uuid(model.id, resource_id), CaseDocumentVersion.research_case_id == case_id).correlate_except(model, SourceSpan, CaseDocumentVersion).exists()
    return None


def event_tenant_predicate(tenant_id, case_id=None):
    owner = CaseTenantAdmission.research_case_id
    alternatives = []
    for kind in ("research_case", "thesis", "proposal", "job", "task_item", "ai_assessment"):
        alternatives.append(and_(DomainEvent.aggregate_type == kind, resource_case_predicate(kind, DomainEvent.aggregate_id, owner)))
    # Proposed/rejected links have no link row: those emitters use proposal IDs.
    pending = DomainEvent.type.in_(("evidence_link_proposed", "evidence_link_rejected"))
    alternatives.append(and_(DomainEvent.aggregate_type == "evidence_link", or_(
        and_(pending, DomainEvent.ref_type == "proposal", _same_uuid(DomainEvent.aggregate_id, DomainEvent.ref_id), resource_case_predicate("proposal", DomainEvent.aggregate_id, owner)),
        and_(~pending, resource_case_predicate("evidence_link", DomainEvent.aggregate_id, owner)),
    )))
    # Documents are shared. An attachment authorizes exactly the event's case,
    # never all other cases that happen to reuse the same content hash.
    alternatives.append(and_(DomainEvent.aggregate_type.in_(("document_version", "published_material_decision")), DomainEvent.ref_type == "research_case", _same_uuid(owner, DomainEvent.ref_id), resource_case_predicate("document_version", DomainEvent.aggregate_id, owner)))
    query = select(CaseTenantAdmission.id).join(ResearchCase, ResearchCase.id == owner).where(CaseTenantAdmission.tenant_id == tenant_id, or_(*alternatives))
    if case_id is not None:
        query = query.where(owner == case_id)
    return query.correlate(DomainEvent).exists()


class ActivityQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _page(
        self,
        *,
        tenant_id: str | None,
        event_types: set[str] | None,
        case_id: uuid.UUID | None,
        actor_id: str | None,
        after_id: uuid.UUID | None,
        limit: int,
    ) -> list[DomainEvent]:
        query = select(DomainEvent).order_by(
            DomainEvent.created_at.desc(), DomainEvent.id.desc()
        )
        if event_types is not None:
            query = query.where(DomainEvent.type.in_(event_types))
        if tenant_id is not None:
            query = query.where(event_tenant_predicate(tenant_id, case_id))
        elif case_id is not None:
            query = query.where(DomainEvent.aggregate_id == str(case_id))
        if actor_id is not None:
            query = query.where(DomainEvent.actor == actor_id)
        if after_id is not None:
            cursor = self._db.scalar(query.where(DomainEvent.id == after_id))
            if cursor is None:
                raise NotFoundError("activity cursor not found")
            query = query.where(tuple_(DomainEvent.created_at, DomainEvent.id) < tuple_(cursor.created_at, cursor.id))
        return list(self._db.scalars(query.limit(limit + 1)))

    def activity(
        self,
        *,
        tenant_id: str | None = None,
        case_id: uuid.UUID | None = None,
        actor_id: str | None = None,
        event_type: str | None = None,
        after_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[DomainEvent], bool]:
        types = {event_type} if event_type else None
        rows = self._page(
            tenant_id=tenant_id,
            event_types=types,
            case_id=case_id,
            actor_id=actor_id,
            after_id=after_id,
            limit=limit,
        )
        has_more = len(rows) > limit
        return rows[:limit], has_more

    def evidence_changes(
        self,
        *,
        tenant_id: str | None = None,
        case_id: uuid.UUID | None = None,
        after_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> tuple[list[DomainEvent], bool]:
        rows = self._page(
            tenant_id=tenant_id,
            event_types=set(_EVIDENCE_EVENT_TYPES),
            case_id=case_id,
            actor_id=None,
            after_id=after_id,
            limit=limit,
        )
        has_more = len(rows) > limit
        return rows[:limit], has_more
