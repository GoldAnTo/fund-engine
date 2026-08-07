"""Event-research creation commands and extraction endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.v1.event_research import (
    CreateEventResearchRequest,
    CreateEventResearchResponse,
    EventResearchLifecycleDTO,
    ExtractEventResearchRequest,
    ExtractEventResearchResponse,
)
from app.services.event_extraction import EventExtractionService
from app.services.event_research import EventResearchService


router = APIRouter(prefix="/event-research", tags=["event-research-v1"])


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
