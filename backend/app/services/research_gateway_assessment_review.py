"""Read-only projection of final assessments behind an authorized Gateway draft."""

from __future__ import annotations

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ConflictError, NotFoundError
from app.models.ledger import AIAssessment, EvidenceSnapshot, Thesis
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_gateway import ResearchRunSpec
from app.schemas.v1.research_gateway_content import (
    AssessmentReviewDTO,
    AssessmentReviewItemDTO,
    EvidenceSummaryDTO,
    QualityFlag,
)
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.research_gateway_artifacts import validated_gateway_source_bindings


def quality_flags(evidence: list[EvidenceSummaryDTO]) -> list[QualityFlag]:
    """Describe observed metadata, never infer semantic truth or independence."""
    flags: list[QualityFlag] = []
    periods = {
        row.observed_period for row in evidence if row.observed_period is not None
    }
    if len(periods) > 1:
        flags.append("mixed_data_periods")
    if any(row.observed_period is None for row in evidence):
        flags.append("unknown_data_period")
    if evidence and all(
        row.source_authority in {"user_supplied", "user_material", "intake_material"}
        for row in evidence
    ):
        flags.append("user_material_only")
    if not any(
        row.source_authority in {"primary_disclosure", "official_disclosure"}
        for row in evidence
    ):
        flags.append("no_primary_disclosure")
    flags.extend(["source_independence_unverified", "retrieval_direction_unverified"])
    return flags


def _unavailable(reason="lineage_unavailable") -> AssessmentReviewDTO:
    return AssessmentReviewDTO(
        state="unavailable", reason_code=reason, items=[], quality_flags=[]
    )


def read_assessment_review(
    session: Session,
    spec: ResearchRunSpec,
    run: ResearchRun,
    evidence: list[EvidenceSummaryDTO],
    *,
    truncated: bool,
) -> AssessmentReviewDTO:
    """Caller must first authorize the exact run's existing report and evidence."""
    if truncated:
        return _unavailable("evidence_list_truncated")
    try:
        return _read(session, spec, run, evidence)
    except (
        ConflictError,
        NotFoundError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
    ):
        return _unavailable()


def _read(
    session: Session,
    spec: ResearchRunSpec,
    run: ResearchRun,
    evidence: list[EvidenceSummaryDTO],
) -> AssessmentReviewDTO:
    if run.id != spec.native_run_id or run.research_case_id != spec.native_case_id:
        raise ValueError("invalid review run")
    scope = load_automatic_research_scope(session, run)
    validated_gateway_source_bindings(session, spec, run, scope)
    factor_ids = scope.factor_ids
    if (
        not factor_ids
        or len(factor_ids) > 200
        or len(set(factor_ids)) != len(factor_ids)
    ):
        raise ValueError("invalid review factors")
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(
                ResearchTask.run_id == run.id,
                ResearchTask.research_case_id == spec.native_case_id,
                ResearchTask.round == run.round,
                ResearchTask.task_type == "result",
                ResearchTask.status == "done",
                ResearchTask.stage == "completed",
            )
            .limit(len(factor_ids) + 1)
        )
    )
    if len(tasks) != len(factor_ids) or {task.thesis_id for task in tasks} != set(
        factor_ids
    ):
        raise ValueError("invalid review tasks")
    tasks_by_thesis = {task.thesis_id: task for task in tasks}
    by_id = {UUID(row.evidence_link_id): row for row in evidence}
    if len(by_id) != len(evidence) or len(evidence) > 200:
        raise ValueError("invalid review evidence")
    items = []
    used_links: set[UUID] = set()
    for factor in scope.factors:
        task = tasks_by_thesis[factor.thesis_id]
        if not isinstance(task.result, dict):
            raise TypeError("invalid review result")
        assessment_id = UUID(str(task.result["assessment_id"]))
        assessment = session.get(AIAssessment, assessment_id)
        snapshot = (
            session.get(EvidenceSnapshot, assessment.snapshot_id)
            if assessment
            else None
        )
        thesis = session.get(Thesis, task.thesis_id)
        if (
            assessment is None
            or snapshot is None
            or thesis is None
            or thesis.research_case_id != spec.native_case_id
            or thesis.statement != factor.statement
            or snapshot.thesis_id != thesis.id
            or assessment.displayed_as_provisional is not True
            or assessment.creator_type != "ai"
            or not isinstance(snapshot.evidence_link_ids, list)
        ):
            raise ValueError("invalid review lineage")
        link_ids = [UUID(str(value)) for value in snapshot.evidence_link_ids]
        if len(link_ids) != len(set(link_ids)) or used_links.intersection(link_ids):
            raise ValueError("duplicate review evidence")
        rows = [by_id[link_id] for link_id in link_ids]
        if any(
            row.thesis_id != str(thesis.id) or row.thesis_statement != factor.statement
            for row in rows
        ):
            raise ValueError("invalid review evidence thesis")
        used_links.update(link_ids)
        items.append(
            AssessmentReviewItemDTO(
                assessment_id=str(assessment.id),
                snapshot_id=str(snapshot.id),
                task_id=str(task.id),
                thesis_id=str(thesis.id),
                thesis_statement=thesis.statement,
                conclusion=assessment.conclusion,
                rationale=assessment.rationale,
                gaps=assessment.gaps,
                evidence_link_ids=[str(link_id) for link_id in link_ids],
                quality_flags=quality_flags(rows),
            )
        )
    if used_links != set(by_id):
        raise ValueError("incomplete review evidence union")
    return AssessmentReviewDTO(
        state="available",
        reason_code=None,
        items=items,
        quality_flags=quality_flags(evidence),
    )
