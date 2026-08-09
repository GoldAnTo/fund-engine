"""Wire contracts for the event-driven research workbench."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from pydantic import Field, model_validator

from app.schemas.v1.common import V1Model
from app.services.event_research_factors import (
    EventResearchScopeFactorValue,
    normalize_event_research_factors,
    normalize_event_research_scope_factors,
)
from app.services.source_admission import SourceStatus


class ExtractEventResearchRequest(V1Model):
    raw_input: str = Field(min_length=1)
    source_url: str | None = None
    source_type: Literal["pasted_snapshot", "uploaded_file", "licensed_provider"] = "pasted_snapshot"
    source_metadata: dict[str, Any] = Field(default_factory=dict)


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
    source_type: Literal["pasted_snapshot", "uploaded_file", "licensed_provider"] = "pasted_snapshot"
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    event_title: str = Field(min_length=1)
    company_name: str | None = None
    ticker: str | None = None
    event_at: datetime | None = None
    market_reaction: str | None = None
    research_question: str = Field(min_length=1)
    candidate_factors: list[str] = Field(min_length=3, max_length=5)
    created_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_candidate_factors(self) -> "CreateEventResearchRequest":
        self.candidate_factors = normalize_event_research_factors(self.candidate_factors)
        return self


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


class EventResearchScopeFactorDTO(V1Model):
    statement: str = Field(min_length=1)
    description: str | None = None


class UpdateEventResearchScopeRequest(V1Model):
    factors: list[str | EventResearchScopeFactorDTO] = Field(min_length=3, max_length=5)
    changed_by: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_factors(self) -> "UpdateEventResearchScopeRequest":
        values = normalize_event_research_scope_factors([
            factor if isinstance(factor, str) else EventResearchScopeFactorValue(
                statement=factor.statement, description=factor.description
            )
            for factor in self.factors
        ])
        self.factors = [
            EventResearchScopeFactorDTO(
                statement=factor.statement, description=factor.description
            )
            for factor in values
        ]
        return self


class UpdateEventResearchScopeResponse(V1Model):
    version: int
    factors: list[EventResearchScopeFactorDTO]
    reclassified_evidence_count: int
    unmapped_evidence_count: int


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


class CaseRelationCaseDTO(V1Model):
    case_id: str
    title: str
    lifecycle_status: str


class CaseRelationDTO(V1Model):
    id: str
    source_case: CaseRelationCaseDTO
    target_case: CaseRelationCaseDTO
    relation_type: Literal["shared_driver", "follow_up_validation", "potential_conflict", "shared_material"]
    reason: str
    created_by: str
    review_state: Literal["machine_generated", "reviewed", "rejected"]
    created_at: datetime


class ResearchNetworkResponse(V1Model):
    reviewed_relations: list[CaseRelationDTO]
    candidate_relations: list[CaseRelationDTO]


class EventReviewQueueItemDTO(V1Model):
    """A pending event-evidence proposal, including source admission context."""

    proposal_id: str
    proposal_version: int
    status: str
    proposed_at: datetime
    link_id: str
    thesis_id: str | None
    case_id: str
    thesis_statement: str | None
    ai_role: str
    ai_reason: str
    ai_scope: dict
    statement_id: str | None
    statement_text: str | None
    statement_kind: str | None
    span_id: str | None
    verbatim_text: str | None
    locator: dict
    document_version_id: str | None
    document_source_url: str | None
    document_published_at: datetime | None
    available_at: datetime | None
    source_title: str | None
    source_status: SourceStatus
    source_status_reason: str
    can_accept: bool
    proposal_reason: str
    position: int | None


class EventReviewQueueSummaryDTO(V1Model):
    total: int
    reviewed: int
    pending: int
    invalid_source: int
    current_round: int
    next_action: str | None


class EventReviewQueueResponse(V1Model):
    items: list[EventReviewQueueItemDTO]
    summary: EventReviewQueueSummaryDTO


class EventResearchFactorDTO(V1Model):
    statement: str
    description: str | None = None
    position: int
    reviewed_support_count: int
    reviewed_contradiction_count: int
    pending_proposal_count: int
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
    confidence: str
    citations: list[EventKeyEvidenceDTO]


class PublishEventConclusionRequest(V1Model):
    text: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)


class PublishEventConclusionResponse(V1Model):
    conclusion_id: str
    state: str


class EventNextActionDTO(V1Model):
    kind: str
    label: str
    count: int | None = None


class EventWorkbenchProgressDTO(V1Model):
    verified: int
    pending: int
    invalid_source: int
    current_gap: str | None


class EventResearchScopeDTO(V1Model):
    version: int
    factors: list[EventResearchScopeFactorDTO]
    unmapped_evidence_count: int


class EventWorkbenchDTO(V1Model):
    event: EventResearchListItemDTO
    lifecycle: EventResearchLifecycleDTO
    conclusion: EventConclusionDraftDTO
    factors: list[EventResearchFactorDTO]
    evidence: list[EventKeyEvidenceDTO]
    progress: EventWorkbenchProgressDTO
    scope: EventResearchScopeDTO
    next_action: EventNextActionDTO
