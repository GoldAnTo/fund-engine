"""Read the evidence currently mapped into an event-research scope."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.event_research import (
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.errors import ConflictError, ValidationFailedError
from app.models.acquisition import AcquisitionJob, AutomaticAdmissionDecision
from app.models.ledger import CaseTenantAdmission, EvidenceLink, ResearchCase, Thesis
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.research_orchestration import AcquisitionQueryPlan, AcquisitionSeries


MAPPABLE_REVIEW_STATES = frozenset({"reviewed", "automatically_admitted"})
AUTOMATIC_MAPPING_POLICY_VERSION = "event-goal-coverage-v1"


@dataclass(frozen=True, slots=True)
class ValidatedAutomaticRunEvidence:
    assignment: EventResearchScopeEvidenceAssignment
    link: EvidenceLink
    thesis: Thesis


def validated_automatic_run_evidence(
    session: Session,
    *,
    tenant_id: str,
    case_id: uuid.UUID,
    scope_version_id: uuid.UUID,
    research_run_id: uuid.UUID,
) -> list[ValidatedAutomaticRunEvidence]:
    """Read one run's factor and event automatic evidence or fail on broken lineage."""
    scope = session.get(EventResearchScopeVersion, scope_version_id)
    latest_scope_id = session.scalar(
        select(EventResearchScopeVersion.id)
        .join(
            CaseTenantAdmission,
            CaseTenantAdmission.research_case_id
            == EventResearchScopeVersion.research_case_id,
        )
        .where(
            EventResearchScopeVersion.research_case_id == case_id,
            CaseTenantAdmission.tenant_id == tenant_id,
        )
        .order_by(
            EventResearchScopeVersion.version.desc(),
            EventResearchScopeVersion.id.desc(),
        )
        .limit(1)
    )
    run = session.get(ResearchRun, research_run_id)
    if (
        scope is None
        or scope.research_case_id != case_id
        or latest_scope_id != scope.id
        or run is None
        or run.research_case_id != case_id
    ):
        raise ValidationFailedError(
            "automatic evidence frozen run or tenant admission is stale"
        )

    rows: list[ValidatedAutomaticRunEvidence] = []
    lineage_rows = list(
        session.execute(
            select(
                EventResearchScopeEvidenceAssignment,
                EvidenceLink,
                AutomaticAdmissionDecision,
                AcquisitionJob,
                AcquisitionQueryPlan,
                AcquisitionSeries,
                Thesis,
                EventResearchScopeFactor.id,
            )
            .outerjoin(
                EvidenceLink,
                EvidenceLink.id
                == EventResearchScopeEvidenceAssignment.evidence_link_id,
            )
            .outerjoin(
                AutomaticAdmissionDecision,
                AutomaticAdmissionDecision.id
                == EventResearchScopeEvidenceAssignment.automatic_admission_decision_id,
            )
            .outerjoin(
                AcquisitionJob,
                AcquisitionJob.id == AutomaticAdmissionDecision.job_id,
            )
            .outerjoin(
                AcquisitionQueryPlan,
                AcquisitionQueryPlan.acquisition_job_id == AcquisitionJob.id,
            )
            .outerjoin(
                AcquisitionSeries,
                AcquisitionSeries.id == AcquisitionQueryPlan.series_id,
            )
            .outerjoin(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .outerjoin(
                EventResearchScopeFactor,
                and_(
                    EventResearchScopeFactor.scope_version_id == scope.id,
                    EventResearchScopeFactor.statement
                    == EventResearchScopeEvidenceAssignment.factor_statement,
                ),
            )
            .where(
                EventResearchScopeEvidenceAssignment.scope_version_id == scope.id,
                EventResearchScopeEvidenceAssignment.research_run_id == run.id,
                EventResearchScopeEvidenceAssignment.assignment_kind == "automatic",
            )
            .order_by(
                EventResearchScopeEvidenceAssignment.created_at,
                EventResearchScopeEvidenceAssignment.id,
            )
        )
    )
    for (
        assignment,
        link,
        decision,
        job,
        plan,
        series,
        thesis,
        active_factor,
    ) in lineage_rows:
        provenance = (
            assignment.automatic_provenance_json
            if isinstance(assignment.automatic_provenance_json, dict)
            else {}
        )
        goal_id = assignment.acquisition_goal_id
        shared_lineage_invalid = (
            link is None
            or link.review_state != "automatically_admitted"
            or decision is None
            or decision.outcome != "admitted"
            or link.automatic_admission_decision_id != decision.id
            or job is None
            or job.tenant_id != tenant_id
            or job.research_case_id != case_id
            or job.research_run_id != run.id
            or job.thesis_id != link.thesis_id
            or (job.status, job.stage)
            not in {("succeeded", "succeeded"), ("partial", "partial")}
            or plan is None
            or series is None
            or not isinstance(goal_id, str)
            or not goal_id.strip()
            or plan.goal_id != goal_id
            or not isinstance(job.policy_snapshot, dict)
            or not isinstance(job.policy_snapshot.get("version"), str)
            or not job.policy_snapshot["version"].strip()
            or decision.policy_version != job.policy_snapshot["version"]
            or plan.policy_version != job.policy_snapshot["version"]
            or series.goal_id != goal_id
            or series.tenant_id != tenant_id
            or series.research_case_id != case_id
            or series.research_run_id != run.id
            or series.scope_version_id != scope.id
            or series.thesis_id != link.thesis_id
            or not isinstance(job.request_snapshot, dict)
            or job.request_snapshot.get("goal_id") != goal_id
            or job.request_snapshot.get("research_run_id") != str(run.id)
            or job.request_snapshot.get("scope_version_id") != str(scope.id)
            or thesis is None
            or thesis.research_case_id != case_id
            or provenance.get("admission_decision_id") != str(decision.id)
            or provenance.get("job_id") != str(job.id)
            or provenance.get("goal_id") != goal_id
            or provenance.get("policy_version") != AUTOMATIC_MAPPING_POLICY_VERSION
        )
        if shared_lineage_invalid:
            raise ValidationFailedError(
                "automatic evidence lineage is inconsistent with the frozen run"
            )
        event_level = goal_id == f"event:{case_id}:alternative_explanation"
        if event_level:
            mapping_invalid = (
                assignment.disposition != "unmapped"
                or assignment.factor_statement is not None
                or provenance.get("mapping_scope") != "event"
                or link.role != "contextualizes"
            )
        else:
            mapping_invalid = (
                assignment.disposition != "mapped"
                or assignment.factor_statement != thesis.statement
                or active_factor is None
                or provenance.get("mapping_scope") != "factor"
            )
        if mapping_invalid:
            raise ValidationFailedError(
                "automatic evidence lineage is inconsistent with the frozen run"
            )
        rows.append(
            ValidatedAutomaticRunEvidence(
                assignment=assignment,
                link=link,
                thesis=thesis,
            )
        )
    return rows


def lock_event_scope_case(session: Session, case_id: uuid.UUID) -> ResearchCase | None:
    """Serialize scope rewrites and evidence publication for one event case.

    PostgreSQL holds this ``FOR UPDATE`` lock until the caller's outer command
    transaction commits.  SQLite accepts the clause as a no-op, preserving the
    same service API for local tests.
    """
    return session.scalar(
        select(ResearchCase)
        .where(ResearchCase.id == case_id)
        .with_for_update()
        .execution_options(populate_existing=True)
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
    return set(canonical_scope_thesis_ids(session, case_id, scope.id))


def canonical_scope_thesis_ids(
    session: Session,
    case_id: uuid.UUID,
    scope_version_id: uuid.UUID,
) -> list[uuid.UUID]:
    """Resolve one earliest thesis per factor in persisted factor order.

    An empty list represents either an empty scope or an invalid partially
    mapped scope; command callers must not prepare a partial research run.
    """
    statements = list(
        dict.fromkeys(
            session.scalars(
                select(EventResearchScopeFactor.statement)
                .where(EventResearchScopeFactor.scope_version_id == scope_version_id)
                .order_by(
                    EventResearchScopeFactor.position,
                    EventResearchScopeFactor.id,
                )
            )
        )
    )
    if not statements:
        return []
    theses = session.scalars(
        select(Thesis)
        .where(Thesis.research_case_id == case_id)
        .where(Thesis.statement.in_(statements))
        .order_by(Thesis.statement, Thesis.created_at, Thesis.id)
    )
    # Scope synchronization reuses the earliest thesis for each factor
    # statement.  Mirror that canonicalization here so a duplicate historical
    # thesis with identical wording cannot make an obsolete proposal current.
    canonical: dict[str, uuid.UUID] = {}
    for thesis in theses:
        canonical.setdefault(thesis.statement, thesis.id)
    if any(statement not in canonical for statement in statements):
        return []
    return [canonical[statement] for statement in statements]


def current_mapped_evidence_ids(
    session: Session, case_id: uuid.UUID
) -> list[uuid.UUID]:
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
                EvidenceLink.id
                == EventResearchScopeEvidenceAssignment.evidence_link_id,
            )
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .where(EventResearchScopeEvidenceAssignment.scope_version_id == scope.id)
            .where(EventResearchScopeEvidenceAssignment.disposition == "mapped")
            .where(
                EventResearchScopeEvidenceAssignment.factor_statement.in_(
                    active_statements
                )
            )
            .where(
                EventResearchScopeEvidenceAssignment.factor_statement
                == Thesis.statement
            )
            .where(Thesis.research_case_id == case_id)
            .where(EvidenceLink.review_state.in_(MAPPABLE_REVIEW_STATES))
        )
    )


def current_conclusion_evidence_ids(
    session: Session, case_id: uuid.UUID
) -> list[uuid.UUID]:
    """Return factor-mapped plus event-level contextual evidence for a report."""
    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    if scope is None:
        return []
    assignments = session.scalars(
        select(EventResearchScopeEvidenceAssignment)
        .join(
            EvidenceLink,
            EvidenceLink.id == EventResearchScopeEvidenceAssignment.evidence_link_id,
        )
        .where(EventResearchScopeEvidenceAssignment.scope_version_id == scope.id)
        .where(EvidenceLink.review_state.in_(MAPPABLE_REVIEW_STATES))
        .order_by(
            EventResearchScopeEvidenceAssignment.created_at,
            EventResearchScopeEvidenceAssignment.id,
        )
    )
    return [
        assignment.evidence_link_id
        for assignment in assignments
        if assignment.disposition == "mapped"
        or (
            assignment.assignment_kind == "automatic"
            and assignment.factor_statement is None
            and isinstance(assignment.automatic_provenance_json, dict)
            and assignment.automatic_provenance_json.get("mapping_scope") == "event"
        )
    ]


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
        .where(
            EventResearchScopeEvidenceAssignment.factor_statement.in_(active_factors)
        )
        .where(
            EventResearchScopeEvidenceAssignment.factor_statement == Thesis.statement
        )
        .where(Thesis.research_case_id == case_id)
        .where(EvidenceLink.review_state.in_(MAPPABLE_REVIEW_STATES))
    )
    evidence_by_factor: dict[str, set[uuid.UUID]] = {
        factor: set() for factor in active_factors
    }
    for factor_statement, evidence_link_id in rows:
        if factor_statement is not None:
            evidence_by_factor[factor_statement].add(evidence_link_id)
    return sum(len(link_ids) for link_ids in evidence_by_factor.values()) >= 2 and all(
        evidence_by_factor.values()
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
    link = session.get(EvidenceLink, evidence_link_id)
    thesis = session.get(Thesis, link.thesis_id) if link is not None else None
    if (
        link is None
        or link.review_state != "reviewed"
        or thesis is None
        or thesis.research_case_id != case_id
    ):
        raise ValidationFailedError(
            "reviewed evidence mapping requires a reviewed link owned by the Case"
        )
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
    is_active = (
        session.scalar(
            select(EventResearchScopeFactor.id).where(
                EventResearchScopeFactor.scope_version_id == scope.id,
                EventResearchScopeFactor.statement == factor_statement,
            )
        )
        is not None
    )
    assignment = EventResearchScopeEvidenceAssignment(
        scope_version_id=scope.id,
        evidence_link_id=evidence_link_id,
        factor_statement=factor_statement if is_active else None,
        disposition="mapped" if is_active else "unmapped",
        assignment_kind="reviewed",
        research_run_id=None,
        acquisition_goal_id=None,
        automatic_admission_decision_id=None,
        automatic_provenance_json=None,
        created_at=created_at,
    )
    session.add(assignment)
    session.flush()
    return assignment


def append_automatic_scope_evidence_assignment(
    session: Session,
    *,
    case_id: uuid.UUID,
    scope_version_id: uuid.UUID,
    research_run_id: uuid.UUID,
    goal_id: str,
    evidence_link_id: uuid.UUID,
    automatic_admission_decision_id: uuid.UUID,
    factor_statement: str | None,
    provenance: dict,
    created_at,
) -> EventResearchScopeEvidenceAssignment:
    """Append one goal-bound automatic mapping from immutable admission lineage."""
    if not isinstance(goal_id, str) or not goal_id.strip():
        raise ValidationFailedError("automatic evidence goal_id must not be blank")
    if (
        not isinstance(provenance, dict)
        or not isinstance(provenance.get("policy_version"), str)
        or not provenance["policy_version"].strip()
    ):
        raise ValidationFailedError("automatic evidence provenance is incomplete")
    event_level = goal_id.strip() == f"event:{case_id}:alternative_explanation"
    if event_level and factor_statement is not None:
        raise ValidationFailedError(
            "alternative explanation evidence must use event-level mapping"
        )
    if not event_level and (
        not isinstance(factor_statement, str) or not factor_statement.strip()
    ):
        raise ValidationFailedError("factor evidence mapping requires a factor")
    mapping_scope = "event" if event_level else "factor"
    supplied_scope = provenance.get("mapping_scope", mapping_scope)
    if supplied_scope != mapping_scope:
        raise ValidationFailedError("automatic evidence mapping scope is inconsistent")
    lock_event_research_lifecycle(session, case_id)
    scope = session.get(EventResearchScopeVersion, scope_version_id)
    latest_scope_id = session.scalar(
        select(EventResearchScopeVersion.id)
        .where(EventResearchScopeVersion.research_case_id == case_id)
        .order_by(
            EventResearchScopeVersion.version.desc(),
            EventResearchScopeVersion.id.desc(),
        )
        .limit(1)
    )
    run = session.get(ResearchRun, research_run_id)
    link = session.get(EvidenceLink, evidence_link_id)
    decision = session.get(AutomaticAdmissionDecision, automatic_admission_decision_id)
    job = session.get(AcquisitionJob, decision.job_id) if decision is not None else None
    plan_row = (
        session.execute(
            select(AcquisitionQueryPlan, AcquisitionSeries)
            .join(
                AcquisitionSeries,
                AcquisitionSeries.id == AcquisitionQueryPlan.series_id,
            )
            .where(AcquisitionQueryPlan.acquisition_job_id == job.id)
        ).one_or_none()
        if job is not None
        else None
    )
    plan, series = plan_row if plan_row is not None else (None, None)
    thesis = session.get(Thesis, link.thesis_id) if link is not None else None
    if (
        scope is None
        or scope.research_case_id != case_id
        or latest_scope_id != scope.id
        or run is None
        or run.research_case_id != case_id
        or link is None
        or link.review_state != "automatically_admitted"
        or link.automatic_admission_decision_id != automatic_admission_decision_id
        or decision is None
        or decision.outcome != "admitted"
        or job is None
        or job.research_case_id != case_id
        or job.research_run_id != run.id
        or job.thesis_id != link.thesis_id
        or plan is None
        or series is None
        or plan.goal_id != goal_id
        or series.goal_id != goal_id
        or series.tenant_id != job.tenant_id
        or series.research_case_id != case_id
        or series.research_run_id != run.id
        or series.scope_version_id != scope.id
        or series.thesis_id != link.thesis_id
        or not isinstance(job.request_snapshot, dict)
        or job.request_snapshot.get("goal_id") != goal_id
        or job.request_snapshot.get("research_run_id") != str(run.id)
        or job.request_snapshot.get("scope_version_id") != str(scope.id)
        or thesis is None
        or thesis.research_case_id != case_id
    ):
        raise ValidationFailedError(
            "automatic evidence does not belong to the frozen Case/run/goal"
        )

    normalized_provenance = {
        "policy_version": provenance["policy_version"].strip(),
        "admission_decision_id": str(automatic_admission_decision_id),
        "job_id": str(job.id),
        "goal_id": goal_id.strip(),
        "mapping_scope": mapping_scope,
    }
    existing = session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.scope_version_id == scope.id,
            EventResearchScopeEvidenceAssignment.evidence_link_id == link.id,
        )
    )
    if existing is not None:
        expected = (
            "automatic",
            run.id,
            goal_id.strip(),
            automatic_admission_decision_id,
            normalized_provenance,
        )
        actual = (
            existing.assignment_kind,
            existing.research_run_id,
            existing.acquisition_goal_id,
            existing.automatic_admission_decision_id,
            existing.automatic_provenance_json,
        )
        if actual != expected:
            raise ConflictError(
                "evidence already has a different append-only scope assignment"
            )
        return existing

    is_active = (
        not event_level
        and session.scalar(
            select(EventResearchScopeFactor.id).where(
                EventResearchScopeFactor.scope_version_id == scope.id,
                EventResearchScopeFactor.statement == factor_statement,
            )
        )
        is not None
    )
    relation_matches = not event_level and thesis.statement == factor_statement
    assignment = EventResearchScopeEvidenceAssignment(
        scope_version_id=scope.id,
        evidence_link_id=link.id,
        factor_statement=(factor_statement if is_active and relation_matches else None),
        disposition=("mapped" if is_active and relation_matches else "unmapped"),
        assignment_kind="automatic",
        research_run_id=run.id,
        acquisition_goal_id=goal_id.strip(),
        automatic_admission_decision_id=automatic_admission_decision_id,
        automatic_provenance_json=normalized_provenance,
        created_at=created_at,
    )
    session.add(assignment)
    session.flush()
    return assignment
