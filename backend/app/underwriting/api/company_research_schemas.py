"""Closed wire contracts for the high-level company-research entry flow."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, field_validator

from app.underwriting.api.product_schemas import SHA256_PATTERN, _require_aware
from app.underwriting.api.schemas import UnderwritingModel

PreparationStatus = Literal[
    "queued",
    "preparing_sources",
    "awaiting_evidence_review",
    "building_model",
    "awaiting_judgment_review",
    "ready_to_freeze",
    "recoverable_failure",
    "blocked",
    "completed",
]


class CompanyResearchPreviewRequest(UnderwritingModel):
    company_id: UUID
    cutoff_at: datetime

    @field_validator("cutoff_at")
    @classmethod
    def aware_cutoff(cls, value: datetime) -> datetime:
        return _require_aware(value, "cutoff_at")


class InitializeCompanyResearchRequest(CompanyResearchPreviewRequest):
    preview_hash: str = Field(pattern=SHA256_PATTERN)


class CompanyResearchIdentityResponse(UnderwritingModel):
    object_id: UUID
    external_key: str
    canonical_name: str


class CompanyResearchSecurityIdentityResponse(CompanyResearchIdentityResponse):
    symbol: str
    exchange: str
    share_class: str
    trading_currency: Literal["CNY", "USD"]


class CompanyResearchAgendaModuleResponse(UnderwritingModel):
    key: str
    label: str


class CompanyResearchPreviewResponse(UnderwritingModel):
    company: CompanyResearchIdentityResponse
    securities: tuple[CompanyResearchSecurityIdentityResponse, ...]
    strategy_version: str
    horizon_years: StrictInt = Field(ge=5, le=5)
    base_currency: Literal["CNY"]
    required_return: Decimal
    permanent_loss_limit: Decimal
    cutoff_at: datetime
    agenda: tuple[CompanyResearchAgendaModuleResponse, ...] = Field(
        min_length=9, max_length=9
    )
    preview_hash: str = Field(pattern=SHA256_PATTERN)


class CompanyResearchPreparationResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    request_hash: str = Field(pattern=SHA256_PATTERN)
    strategy_version: str
    status: PreparationStatus
    current_step: str | None
    progress: StrictInt = Field(ge=0, le=100)
    attempt: StrictInt = Field(ge=1)
    next_attempt_at: datetime | None
    last_error_code: str | None


class CompanyResearchProjectResponse(UnderwritingModel):
    project_id: UUID
    company_id: UUID
    preparation: CompanyResearchPreparationResponse


class CompanyResearchArtifactResponse(UnderwritingModel):
    id: UUID
    kind: str
    version: StrictInt = Field(ge=1)
    input_hash: str = Field(pattern=SHA256_PATTERN)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    payload: dict
    source_refs: tuple[dict, ...]


class CompanyResearchWorkbenchModuleResponse(UnderwritingModel):
    key: str
    state: Literal["not_started", "preparing", "needs_review", "ready", "blocked"]
    artifact: CompanyResearchArtifactResponse | None


class CompanyResearchWorkspaceCompanyResponse(CompanyResearchIdentityResponse):
    id: UUID


class CompanyResearchWorkspacePreparationResponse(UnderwritingModel):
    id: UUID
    status: PreparationStatus
    current_step: str | None
    progress: StrictInt = Field(ge=0, le=100)


class CompanyResearchWorkspaceDraftResponse(UnderwritingModel):
    id: UUID
    lock_version: StrictInt = Field(ge=1)
    base_revision_id: UUID | None


class CompanyResearchWorkspaceResponse(UnderwritingModel):
    project_id: UUID
    company: CompanyResearchWorkspaceCompanyResponse
    preparation: CompanyResearchWorkspacePreparationResponse
    modules: tuple[CompanyResearchWorkbenchModuleResponse, ...] = Field(min_length=9, max_length=9)
    source_count: StrictInt = Field(ge=0)
    gap_count: StrictInt = Field(ge=0)
    draft: CompanyResearchWorkspaceDraftResponse
    selected_revision: UUID | None
    change_summary: dict


class ReviewCompanyEvidenceRequest(UnderwritingModel):
    evidence_artifact_id: UUID
    fact_key: StrictStr = Field(min_length=1, max_length=160)
    decision: Literal["confirmed", "rejected"]
    expected_head_id: UUID


class CompanyResearchEvidenceReviewResponse(UnderwritingModel):
    evidence_artifact: CompanyResearchArtifactResponse
