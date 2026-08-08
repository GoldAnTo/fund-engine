"""Event-research creation commands and extraction endpoint."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.v1.event_research import (
    CreateEventResearchRequest,
    CreateEventResearchResponse,
    EventResearchLifecycleDTO,
    ExtractEventResearchRequest,
    ExtractEventResearchResponse,
    EventResearchListResponse,
    EventReviewQueueResponse,
    EventWorkbenchDTO,
    PublishEventConclusionRequest,
    PublishEventConclusionResponse,
    UpdateEventResearchScopeRequest,
    UpdateEventResearchScopeResponse,
)
from app.queries.event_research import EventResearchQueries
from app.services.event_extraction import EventExtractionService
from app.services.event_research import EventResearchService
from app.services.event_conclusion import EventConclusionService
from app.services.event_review_queue import EventReviewQueueService
from app.services.event_research_scope import EventResearchScopeService
from app.queries.event_impact import EventImpactQueries
from app.schemas.v1.event_impact import (
    EventImpactTraceDTO,
    ReviewImpactRelationRequest,
    ReviewImpactRelationResponse,
)
from app.services.event_impact import EventImpactResearchService


router = APIRouter(prefix="/event-research", tags=["event-research-v1"])


@router.get("", response_model=EventResearchListResponse)
def list_event_research(
    status: str | None = None, db: Session = Depends(get_db)
) -> EventResearchListResponse:
    return EventResearchQueries(db).list(status=status)


@router.post("/extract", response_model=ExtractEventResearchResponse)
def extract_event(payload: ExtractEventResearchRequest) -> ExtractEventResearchResponse:
    extracted = EventExtractionService().extract(
        raw_input=payload.raw_input, source_url=payload.source_url
    )
    return ExtractEventResearchResponse(
        event_title=extracted.event_title,
        company_name=extracted.company_name,
        ticker=extracted.ticker,
        event_at=extracted.event_at,
        market_reaction=extracted.market_reaction,
        summary=extracted.summary,
        research_question=extracted.research_question,
        candidate_factors=list(extracted.candidate_factors),
        confirmation_required=extracted.confirmation_required,
    )


@router.post("", response_model=CreateEventResearchResponse, status_code=status.HTTP_201_CREATED)
def create_event_research(
    payload: CreateEventResearchRequest, db: Session = Depends(get_db)
) -> CreateEventResearchResponse:
    created = EventResearchService(db).create(payload)
    lifecycle = created.lifecycle
    return CreateEventResearchResponse(
        case_id=created.case_id,
        brief_id=created.brief_id,
        lifecycle=EventResearchLifecycleDTO(
            status=lifecycle.status,
            active_run_id=str(lifecycle.active_run_id) if lifecycle.active_run_id else None,
            current_round=lifecycle.current_round,
            status_summary=lifecycle.status_summary,
            current_gap=lifecycle.current_gap,
            next_human_action=lifecycle.next_human_action,
        ),
    )


@router.put("/{case_id}/scope", response_model=UpdateEventResearchScopeResponse)
def update_event_research_scope(
    case_id: uuid.UUID,
    payload: UpdateEventResearchScopeRequest,
    db: Session = Depends(get_db),
) -> UpdateEventResearchScopeResponse:
    updated = EventResearchScopeService(db).update(
        case_id, factors=payload.factors, changed_by=payload.changed_by
    )
    db.commit()
    return UpdateEventResearchScopeResponse(
        version=updated.version,
        factors=[
            {"statement": factor.statement, "description": factor.description}
            for factor in updated.factors
        ],
        reclassified_evidence_count=updated.reclassified_evidence_count,
        unmapped_evidence_count=updated.unmapped_evidence_count,
    )


@router.get("/{case_id}/workbench", response_model=EventWorkbenchDTO)
def event_research_workbench(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> EventWorkbenchDTO:
    return EventResearchQueries(db).workbench(case_id)


@router.get("/{case_id}/impact-trace", response_model=EventImpactTraceDTO)
def event_impact_trace(case_id: uuid.UUID, db: Session = Depends(get_db)) -> EventImpactTraceDTO:
    return EventImpactQueries(db).trace(case_id)


@router.post(
    "/impact-relations/{relation_id}/review",
    response_model=ReviewImpactRelationResponse,
    status_code=status.HTTP_201_CREATED,
)
def review_impact_relation(
    relation_id: uuid.UUID,
    payload: ReviewImpactRelationRequest,
    db: Session = Depends(get_db),
) -> ReviewImpactRelationResponse:
    review = EventImpactResearchService(db).review_relation(
        relation_id, outcome=payload.outcome, reason=payload.reason, reviewer=payload.reviewer
    )
    db.commit()
    return ReviewImpactRelationResponse(review_id=str(review.id), relation_id=str(relation_id), outcome=review.outcome)


@router.get("/{case_id}/review-queue", response_model=EventReviewQueueResponse)
def event_review_queue(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> EventReviewQueueResponse:
    return EventReviewQueueService(db).review_queue(case_id)


@router.post("/{case_id}/conclusion/publish", response_model=PublishEventConclusionResponse, status_code=status.HTTP_201_CREATED)
def publish_event_conclusion(
    case_id: uuid.UUID, payload: PublishEventConclusionRequest, db: Session = Depends(get_db)
) -> PublishEventConclusionResponse:
    published = EventConclusionService(db).publish(
        case_id, text=payload.text, reviewer=payload.reviewer
    )
    db.commit()
    return PublishEventConclusionResponse(conclusion_id=str(published.id), state=published.state)
