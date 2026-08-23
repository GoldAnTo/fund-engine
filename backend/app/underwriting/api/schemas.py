"""Strict wire DTOs for the independent underwriting API."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UnderwritingModel(BaseModel):
    """Versioned underwriting contract: reject fields outside the wire spec."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["underwriting.v1"] = "underwriting.v1"


class UnderwritingErrorBody(BaseModel):
    """The error payload nested inside an underwriting response envelope."""

    model_config = ConfigDict(extra="forbid")
    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class UnderwritingErrorEnvelope(UnderwritingModel):
    error: UnderwritingErrorBody


ResearchObjectKind = Literal["industry", "company", "security"]
RelationType = Literal["industry_exposes_company", "company_has_security"]
LedgerKind = Literal["reality", "belief", "decision", "calibration"]
BlockerCode = Literal[
    "missing_key_baseline",
    "unresolved_source_conflict",
    "mechanism_unidentified",
    "financial_model_not_closed",
    "expectation_surface_unidentifiable",
    "source_unavailable",
    "future_information_leakage",
]
EligibleAction = Literal[
    "observe",
    "wait_for_validation",
    "eligible_for_probe_entry",
    "eligible_for_staged_entry",
    "do_not_enter",
]
AnswerabilityState = Literal["answerable", "partially_answerable", "not_answerable"]


class ResearchObjectCreate(UnderwritingModel):
    kind: ResearchObjectKind
    external_key: str = Field(min_length=1, max_length=160)
    canonical_name: str = Field(min_length=1)


class ResearchObjectResponse(UnderwritingModel):
    id: UUID
    kind: ResearchObjectKind
    external_key: str
    canonical_name: str
    created_at: datetime


class ObjectRelationCreate(UnderwritingModel):
    parent_id: UUID
    child_id: UUID
    relation_type: RelationType


class ObjectRelationResponse(UnderwritingModel):
    id: UUID
    parent_id: UUID
    child_id: UUID
    relation_type: RelationType
    created_at: datetime


class BasisCreate(UnderwritingModel):
    cutoff: datetime
    price_as_of: datetime | None = None
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BasisResponse(UnderwritingModel):
    id: UUID
    cutoff: datetime
    price_as_of: datetime | None
    source_manifest_hash: str
    created_at: datetime


class MandateCreate(UnderwritingModel):
    mandate_key: str = Field(min_length=1, max_length=120)
    horizon_years: int = Field(ge=3, le=5)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    required_return: Decimal = Field(ge=Decimal("0"), lt=Decimal("1"))
    permanent_loss_limit: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    comparison_set: list[str] = Field(min_length=1)
    expected_parent_id: UUID | None = None


class MandateResponse(UnderwritingModel):
    id: UUID
    mandate_key: str
    horizon_years: int
    base_currency: str
    required_return: Decimal
    permanent_loss_limit: Decimal
    comparison_set: list[str]
    version: int
    supersedes_id: UUID | None
    created_at: datetime


class LedgerEntryCreate(UnderwritingModel):
    basis_id: UUID
    ledger_kind: LedgerKind
    family_key: str = Field(min_length=1, max_length=160)
    entry_type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]
    effective_at: datetime
    available_at: datetime
    source_boundary: str = Field(min_length=1)
    expected_parent_id: UUID | None = None


class LedgerEntryResponse(UnderwritingModel):
    id: UUID
    object_id: UUID
    basis_id: UUID
    ledger_kind: LedgerKind
    family_key: str
    entry_type: str
    version: int
    payload: dict[str, Any]
    effective_at: datetime
    available_at: datetime
    source_boundary: str
    content_hash: str
    supersedes_id: UUID | None
    created_at: datetime


class AnswerabilityCreate(UnderwritingModel):
    basis_id: UUID
    hard_blockers: list[BlockerCode]
    research_debt_keys: list[str]
    resolvable_within_mandate: bool
    requested_action: EligibleAction
    resolution_requirements: list[str]
    expected_parent_id: UUID | None = None


class AnswerabilityResponse(UnderwritingModel):
    id: UUID
    object_id: UUID
    basis_id: UUID
    state: AnswerabilityState
    blockers: list[BlockerCode]
    research_debt_keys: list[str]
    resolvable_within_mandate: bool
    allowed_action: EligibleAction
    resolution_requirements: list[str]
    version: int
    supersedes_id: UUID | None
    created_at: datetime


class SnapshotResponse(UnderwritingModel):
    object_id: UUID
    basis_id: UUID
    cutoff: datetime
    entries: list[LedgerEntryResponse]
    snapshot_hash: str


class ResearchRevisionArtifactResponse(UnderwritingModel):
    reference: str
    artifact_type: str
    identity: str
    content_hash: str
    source_locators: list[str]
    unit: str | None
    period_start: datetime | None
    period_end: datetime | None
    available_at: datetime | None
    status: str | None


class ResearchRevisionResponse(UnderwritingModel):
    id: UUID
    object_id: UUID
    basis_id: UUID
    version_kind: str
    sequence: int
    content_hash: str
    cutoff: datetime
    source_manifest_hash: str
    parent_refs: list[ResearchRevisionArtifactResponse]


class ResearchRevisionHistoryResponse(UnderwritingModel):
    object_id: UUID
    version_kind: str
    revisions: list[ResearchRevisionResponse]


class ResearchRevisionChangeResponse(UnderwritingModel):
    group: Literal["evidence", "mechanism", "industry_model", "answerability"]
    change_type: Literal["added", "removed", "replaced"]
    artifact_type: str
    identity: str
    before: ResearchRevisionArtifactResponse | None
    after: ResearchRevisionArtifactResponse | None


class ResearchRevisionDiffResponse(UnderwritingModel):
    from_revision_id: UUID
    to_revision_id: UUID
    from_content_hash: str
    to_content_hash: str
    entries: list[ResearchRevisionChangeResponse]
    diff_hash: str


class ResearchArchiveItemResponse(UnderwritingModel):
    object_id: UUID
    object_kind: ResearchObjectKind
    canonical_name: str
    external_key: str
    version_kind: str
    version_count: int
    lineage_state: Literal["readable", "unreadable"]
    latest_revision_id: UUID | None
    latest_sequence: int | None
    cutoff: datetime | None
    source_manifest_hash: str | None


class ResearchArchiveListResponse(UnderwritingModel):
    items: list[ResearchArchiveItemResponse]
    next_cursor: str | None


class EconomicSourceResponse(UnderwritingModel):
    source_id: str
    title: str
    locator: str
    authority: str


class EconomicObservationResponse(UnderwritingModel):
    metric_key: str
    value: Decimal | None
    unit: str
    source_id: str
    source_locator: str
    available_at: datetime
    observation_status: str
    source_role: str
    dimensions: dict[str, str]


class CandidateMechanismResponse(UnderwritingModel):
    key: str
    status: Literal["candidate"]
    source_ids: list[str]
    formula: str


class FormalMechanismResponse(UnderwritingModel):
    key: str
    status: Literal["formal"]
    source_ids: list[str]
    formula: str


class EvidenceOnlyIndustryStateResponse(UnderwritingModel):
    status: Literal["not_compiled"]
    reason: str


class EvidenceOnlyScenarioResponse(UnderwritingModel):
    key: str
    status: Literal["not_compiled"]


class EvidenceOnlyExposureResponse(UnderwritingModel):
    status: Literal["not_compiled"]
    reason: str


class EvidenceOnlyEarningsResponse(UnderwritingModel):
    status: Literal["not_compiled"]
    reason: str


class EvidenceOnlyEconomicModelResponse(UnderwritingModel):
    object_id: UUID
    basis_id: UUID
    cutoff: datetime
    research_version_id: UUID
    snapshot_hash: str
    sources: list[EconomicSourceResponse]
    observations: list[EconomicObservationResponse]
    candidate_mechanisms: list[CandidateMechanismResponse]
    formal_mechanisms: list[FormalMechanismResponse]
    industry_state: EvidenceOnlyIndustryStateResponse | None = None
    scenarios: list[EvidenceOnlyScenarioResponse] = Field(default_factory=list)
    company_exposure: EvidenceOnlyExposureResponse | None = None
    earnings_engine: EvidenceOnlyEarningsResponse | None = None
    answerability: AnswerabilityResponse
    eligible_action: Literal["wait_for_validation"]
    valuation: None = None
