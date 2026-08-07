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
