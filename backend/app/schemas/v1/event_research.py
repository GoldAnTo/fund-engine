"""Wire contracts for the event-driven research workbench."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import Field

from app.schemas.v1.common import V1Model


class ExtractEventResearchRequest(V1Model):
    raw_input: str = Field(min_length=1)
    source_url: str | None = None


class ExtractEventResearchResponse(V1Model):
    event_title: str | None
    company_name: str | None
    ticker: str | None
    event_at: datetime | None
    market_reaction: str | None
    summary: str | None
    research_question: str
    candidate_factors: list[str]
    confirmation_required: bool = True


class CreateEventResearchRequest(V1Model):
    raw_input: str = Field(min_length=1)
    source_url: str | None = None
    event_title: str = Field(min_length=1)
    company_name: str | None = None
    ticker: str | None = None
    event_at: datetime | None = None
    market_reaction: str | None = None
    research_question: str = Field(min_length=1)
    candidate_factors: list[Annotated[str, Field(min_length=1)]] = Field(
        min_length=3, max_length=5
    )
    created_by: str = Field(min_length=1)


class EventResearchLifecycleDTO(V1Model):
    status: str
    active_run_id: str | None
    current_round: int
    status_summary: str
    current_gap: str | None
    next_human_action: str | None


class CreateEventResearchResponse(V1Model):
    case_id: str
    brief_id: str
    lifecycle: EventResearchLifecycleDTO


class EventResearchListItemDTO(V1Model):
    case_id: str
    event_title: str
    company_name: str | None
    ticker: str | None
    event_at: datetime | None
    lifecycle_status: str
    status_summary: str
    next_human_action: str | None
    updated_at: datetime


class EventResearchListResponse(V1Model):
    items: list[EventResearchListItemDTO]


class EventResearchFactorDTO(V1Model):
    statement: str
    position: int
    reviewed_support_count: int
    reviewed_contradiction_count: int
    current_gap: str | None


class EventKeyEvidenceDTO(V1Model):
    case_id: str
    factor_statement: str
    role: str
    review_state: str
    source_title: str | None
    source_url: str | None
    excerpt: str
    locator: dict
    available_at: datetime


class EventConclusionDraftDTO(V1Model):
    state: str
    text: str
    citations: list[EventKeyEvidenceDTO]


class EventNextActionDTO(V1Model):
    kind: str
    label: str
    count: int | None = None


class EventWorkbenchDTO(V1Model):
    event: EventResearchListItemDTO
    lifecycle: EventResearchLifecycleDTO
    conclusion: EventConclusionDraftDTO
    factors: list[EventResearchFactorDTO]
    evidence: list[EventKeyEvidenceDTO]
    next_action: EventNextActionDTO
