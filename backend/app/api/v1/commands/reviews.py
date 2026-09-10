"""Review commands (prototype 审核工作区): queue + link/assessment reviews."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.errors import NotFoundError
from app.queries.review_queue import ReviewQueueQueries
from app.repositories.operational import TaskRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.commands import (
    AssessmentReviewRequest,
    AssessmentReviewResponse,
    EvidenceReviewDTO,
    LinkReviewRequest,
    LinkReviewResponse,
    ReviewQueueResponse,
)
from app.services.assessment import AssessmentService
from app.services.auto_research import AutoResearchService
from app.services.review import ReviewService
from app.services.case_tenant_access import CaseTenantAccess
from app.api.v1.dependencies import RequireCaseRoute
from app.models.ledger import AIAssessment, EvidenceLink, EvidenceSnapshot, Thesis

router = APIRouter(tags=["review-commands-v1"])


@router.get("/review-queue", response_model=ReviewQueueResponse)
def review_queue(
    case_policy: RequireCaseRoute,
    case_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    if case_id is not None:
        CaseTenantAccess(db).require_case(case_id, actor.tenant_id)
        case_policy.require(case_id)
    return ReviewQueueQueries(db).list_items(
        case_id=case_id,
        tenant_id=actor.tenant_id,
        authorized_case_ids=case_policy.authorized_case_ids(),
        limit=limit,
    )


@router.post(
    "/evidence-links/{link_id}/reviews",
    response_model=LinkReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def review_link(
    case_policy: RequireCaseRoute,
    link_id: uuid.UUID,
    payload: LinkReviewRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    case_id = db.scalar(
        select(Thesis.research_case_id)
        .select_from(EvidenceLink)
        .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
        .where(EvidenceLink.id == link_id)
    )
    if case_id is None:
        raise NotFoundError(f"evidence link {link_id} not found")
    CaseTenantAccess(db).require_case(case_id, actor.tenant_id)
    case_policy.require(case_id)
    review = translate_validation(
        ReviewService(ResearchRepository(db)).review_link,
        link_id,
        outcome=payload.outcome,
        relation=payload.relation,
        factor_role=payload.factor_role,
        scope_boundary=payload.scope_boundary,
        reason=payload.reason,
        reviewer=actor.server_actor,
    )
    commit_or_rollback(db)
    return LinkReviewResponse(
        review=EvidenceReviewDTO(
            id=str(review.id),
            evidence_link_id=str(review.evidence_link_id),
            outcome=review.outcome,
            relation=review.relation,
            factor_role=review.factor_role,
            scope_boundary=review.scope_boundary,
            reason=review.reason,
            reviewer=review.reviewer,
            created_at=review.created_at.isoformat(),
        )
    )


@router.post(
    "/assessments/{assessment_id}/reviews",
    response_model=AssessmentReviewResponse,
    status_code=status.HTTP_201_CREATED,
)
def review_assessment(
    case_policy: RequireCaseRoute,
    assessment_id: uuid.UUID,
    payload: AssessmentReviewRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    repo = ResearchRepository(db)
    if repo.get_ai_assessment(assessment_id) is None:
        raise NotFoundError(f"assessment {assessment_id} not found")
    case_id = db.scalar(
        select(Thesis.research_case_id)
        .select_from(AIAssessment)
        .join(EvidenceSnapshot, EvidenceSnapshot.id == AIAssessment.snapshot_id)
        .join(Thesis, Thesis.id == EvidenceSnapshot.thesis_id)
        .where(AIAssessment.id == assessment_id)
    )
    if case_id is None:
        raise NotFoundError(f"assessment {assessment_id} not found")
    CaseTenantAccess(db).require_case(case_id, actor.tenant_id)
    case_policy.require(case_id)
    review = translate_validation(
        AssessmentService(repo, db).review,
        assessment_id,
        outcome=payload.outcome,
        conclusion=payload.conclusion,
        reason=payload.reason,
        reviewer=actor.server_actor,
    )
    TaskRepository(db).close_review_task(
        task_type="review_assessment",
        ref_type="ai_assessment",
        ref_id=assessment_id,
    )
    AutoResearchService(db).reconcile_runs_for_output(
        key="assessment_id",
        value=assessment_id,
        trigger_ref=f"assessment:{assessment_id}",
    )
    commit_or_rollback(db)
    return AssessmentReviewResponse(
        id=str(review.id),
        ai_assessment_id=str(review.ai_assessment_id),
        outcome=review.outcome,
        conclusion=review.conclusion,
        reason=review.reason,
        reviewer=review.reviewer,
        created_at=review.created_at.isoformat(),
    )
