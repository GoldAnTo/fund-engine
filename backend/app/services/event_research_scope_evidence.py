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
from app.models.operational import EventResearchLifecycle


def lock_event_scope_case(
    session: Session, case_id: uuid.UUID
) -> EventResearchLifecycle | EventResearchScopeVersion | None:
    """Serialize scope rewrites and evidence publication for one event case.

    PostgreSQL holds this ``FOR UPDATE`` lock until the caller's outer command
    transaction commits.  SQLite accepts the clause as a no-op, preserving the
    same service API for local tests.
    """
    lifecycle = session.scalar(
        select(EventResearchLifecycle)
        .where(EventResearchLifecycle.research_case_id == case_id)
        .with_for_update()
    )
    if lifecycle is not None:
        return lifecycle
    return session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
        .with_for_update()
    )


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


def append_current_scope_evidence_assignment(
    session: Session,
    *,
    case_id: uuid.UUID,
    evidence_link_id: uuid.UUID,
    factor_statement: str,
    created_at,
) -> EventResearchScopeEvidenceAssignment | None:
    """Append the current scope's classification for a newly reviewed link.

    Evidence publication is retried through a command-idempotency boundary, so
    the scope/link uniqueness check keeps this append-only projection safe when
    the publisher is invoked more than once in one transaction.
    """
    # Lock before choosing the latest scope.  Scope updates acquire the same
    # row before snapshotting reviewed links, so neither side can miss the
    # other's append before its transaction commits.
    lock_event_scope_case(session, case_id)
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    if scope is None:
        return None
    assignment = session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.scope_version_id == scope.id,
            EventResearchScopeEvidenceAssignment.evidence_link_id == evidence_link_id,
        )
    )
    if assignment is not None:
        return assignment
    is_active = session.scalar(
        select(EventResearchScopeFactor.id).where(
            EventResearchScopeFactor.scope_version_id == scope.id,
            EventResearchScopeFactor.statement == factor_statement,
        )
    ) is not None
    assignment = EventResearchScopeEvidenceAssignment(
        scope_version_id=scope.id,
        evidence_link_id=evidence_link_id,
        factor_statement=factor_statement if is_active else None,
        disposition="mapped" if is_active else "unmapped",
        created_at=created_at,
    )
    session.add(assignment)
    session.flush()
    return assignment
