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
from app.models.ledger import EvidenceLink, ResearchCase, Thesis
from app.models.operational import EventResearchLifecycle


def lock_event_scope_case(
    session: Session, case_id: uuid.UUID
) -> ResearchCase | None:
    """Serialize scope rewrites and evidence publication for one event case.

    PostgreSQL holds this ``FOR UPDATE`` lock until the caller's outer command
    transaction commits.  SQLite accepts the clause as a no-op, preserving the
    same service API for local tests.
    """
    return session.scalar(
        select(ResearchCase).where(ResearchCase.id == case_id).with_for_update()
    )


def lock_event_research_lifecycle(
    session: Session, case_id: uuid.UUID
) -> EventResearchLifecycle | None:
    """Lock an event lifecycle after taking its stable case lock.

    Every lifecycle writer uses this order (``ResearchCase`` then lifecycle)
    so a scope change, evidence publication, conclusion publication, and
    worker handoff cannot each flush a stale lifecycle projection.
    """
    lock_event_scope_case(session, case_id)
    return session.scalar(
        select(EventResearchLifecycle)
        .where(EventResearchLifecycle.research_case_id == case_id)
        .with_for_update()
    )


def current_scope_thesis_ids(
    session: Session, case_id: uuid.UUID
) -> set[uuid.UUID] | None:
    """Return the latest event scope's active thesis IDs.

    ``None`` preserves legacy non-versioned event behavior; an empty set is a
    real (albeit invalid for new commands) versioned scope with no active
    factors.  Callers use this distinction to avoid hiding pre-scope history.
    """
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    if scope is None:
        return None
    active_statements = select(EventResearchScopeFactor.statement).where(
        EventResearchScopeFactor.scope_version_id == scope.id
    )
    theses = session.scalars(
        select(Thesis)
        .where(Thesis.research_case_id == case_id)
        .where(Thesis.statement.in_(active_statements))
        .order_by(Thesis.statement, Thesis.created_at, Thesis.id)
    )
    # Scope synchronization reuses the earliest thesis for each factor
    # statement.  Mirror that canonicalization here so a duplicate historical
    # thesis with identical wording cannot make an obsolete proposal current.
    canonical: dict[str, uuid.UUID] = {}
    for thesis in theses:
        canonical.setdefault(thesis.statement, thesis.id)
    return set(canonical.values())


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


def has_current_scope_evidence_coverage(session: Session, case_id: uuid.UUID) -> bool:
    """Whether reviewed mapped evidence covers every active factor.

    A conclusion draft needs at least one reviewed, current-scope mapping per
    factor and at least two reviewed links overall.  Historical assignments,
    unmapped links, and evidence for removed factors do not qualify.
    """
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    if scope is None:
        return False
    active_factors = set(
        session.scalars(
            select(EventResearchScopeFactor.statement).where(
                EventResearchScopeFactor.scope_version_id == scope.id
            )
        )
    )
    if not active_factors:
        return False
    rows = session.execute(
        select(
            EventResearchScopeEvidenceAssignment.factor_statement,
            EventResearchScopeEvidenceAssignment.evidence_link_id,
        )
        .join(
            EvidenceLink,
            EvidenceLink.id == EventResearchScopeEvidenceAssignment.evidence_link_id,
        )
        .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
        .where(EventResearchScopeEvidenceAssignment.scope_version_id == scope.id)
        .where(EventResearchScopeEvidenceAssignment.disposition == "mapped")
        .where(EventResearchScopeEvidenceAssignment.factor_statement.in_(active_factors))
        .where(EventResearchScopeEvidenceAssignment.factor_statement == Thesis.statement)
        .where(Thesis.research_case_id == case_id)
        .where(EvidenceLink.review_state == "reviewed")
    )
    evidence_by_factor: dict[str, set[uuid.UUID]] = {
        factor: set() for factor in active_factors
    }
    for factor_statement, evidence_link_id in rows:
        if factor_statement is not None:
            evidence_by_factor[factor_statement].add(evidence_link_id)
    return (
        sum(len(link_ids) for link_ids in evidence_by_factor.values()) >= 2
        and all(evidence_by_factor.values())
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
    # The public helper is also used by direct callers, so it must take the
    # full case -> lifecycle lock itself before choosing the latest scope.
    lock_event_research_lifecycle(session, case_id)
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
