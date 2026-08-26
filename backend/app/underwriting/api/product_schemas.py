"""Strict wire contracts for the independent investment-research product."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from app.underwriting.api.schemas import UnderwritingModel


SHA256_PATTERN = r"^[0-9a-f]{64}$"
Currency = Literal["CNY", "USD"]
ResearchObjectKind = Literal["industry", "company", "security"]
AnswerabilityState = Literal["answerable", "partially_answerable", "not_answerable"]
AssessmentDirection = Literal[
    "provisional_bullish", "provisional_neutral", "provisional_cautious"
]
AssessmentConfidence = Literal["low", "medium", "high"]
PublicationStatus = Literal["user_frozen", "superseded"]
AgendaGenerationMethod = Literal["deterministic_template", "ai_generated"]
FxQuoteDirection = Literal["quote_per_base"]
MarketSnapshotRef = Annotated[
    StrictStr,
    Field(
        pattern=(
            r"^(?:price|fx|capital_structure|security_rights):"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        )
    ),
]


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value


def _require_finite_decimal(value: object, field_name: str) -> object:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite decimal")
    try:
        candidate = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a finite decimal") from exc
    if not candidate.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal")
    return value


def _require_unique(values: tuple[object, ...], field_name: str) -> tuple[object, ...]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return values


class ProductObjectSearchItemResponse(UnderwritingModel):
    object_id: UUID
    identity_version_id: UUID
    kind: ResearchObjectKind
    external_key: str
    canonical_name: str
    symbol: str | None
    exchange: str | None
    share_class: str | None
    trading_currency: Currency | None


class ProductObjectSearchResponse(UnderwritingModel):
    items: tuple[ProductObjectSearchItemResponse, ...]


class IndustryCompanyBrowseItemResponse(UnderwritingModel):
    """Public identity projection for an Industry's directly related companies."""

    object_id: UUID
    kind: ResearchObjectKind
    external_key: str
    canonical_name: str
    symbol: str | None
    exchange: str | None
    share_class: str | None
    trading_currency: Currency | None


class IndustryCompanyBrowseResponse(UnderwritingModel):
    items: tuple[IndustryCompanyBrowseItemResponse, ...]


class CreateResearchProjectRequest(UnderwritingModel):
    primary_company_id: UUID
    target_security_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)

    @field_validator("target_security_ids")
    @classmethod
    def unique_targets(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        return _require_unique(value, "target_security_ids")  # type: ignore[return-value]


class ProjectCompanyIdentityResponse(UnderwritingModel):
    object_id: UUID
    identity_version_id: UUID
    canonical_name: str


class ProjectSecurityIdentityResponse(ProjectCompanyIdentityResponse):
    symbol: str
    exchange: str
    share_class: str
    trading_currency: Currency


class ResearchProjectResponse(UnderwritingModel):
    id: UUID
    primary_company_id: UUID
    target_security_ids: tuple[UUID, ...]
    company_identity: ProjectCompanyIdentityResponse
    security_identities: tuple[ProjectSecurityIdentityResponse, ...]
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class ResearchProjectListResponse(UnderwritingModel):
    items: tuple[ResearchProjectResponse, ...]


class CreateProductMandateRequest(UnderwritingModel):
    horizon_years: StrictInt = Field(ge=3, le=5)
    base_currency: Currency
    required_return: Decimal
    permanent_loss_limit: Decimal
    comparison_set: tuple[StrictStr, ...] = Field(min_length=1, max_length=100)
    benchmark_key: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    required_excess_return: Decimal | None = None
    effective_at: datetime
    expires_at: datetime | None = None
    expected_parent_id: UUID | None = None

    @field_validator("required_return", "permanent_loss_limit", mode="before")
    @classmethod
    def finite_required_numbers(cls, value: object, info):
        return _require_finite_decimal(value, info.field_name)

    @field_validator("required_excess_return", mode="before")
    @classmethod
    def finite_excess_return(cls, value: object):
        return (
            value
            if value is None
            else _require_finite_decimal(value, "required_excess_return")
        )

    @field_validator("comparison_set")
    @classmethod
    def unique_comparison_set(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 160 for item in value):
            raise ValueError("comparison_set entries must be non-empty and bounded")
        return _require_unique(value, "comparison_set")  # type: ignore[return-value]

    @field_validator("effective_at")
    @classmethod
    def aware_effective(cls, value: datetime) -> datetime:
        return _require_aware(value, "effective_at")

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        return value if value is None else _require_aware(value, "expires_at")

    @model_validator(mode="after")
    def require_benchmark_pair(self):
        if (self.benchmark_key is None) != (self.required_excess_return is None):
            raise ValueError(
                "benchmark_key and required_excess_return must both be present or absent"
            )
        return self


class ProductMandateResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    mandate_key: str
    horizon_years: int
    base_currency: Currency
    required_return: Decimal
    permanent_loss_limit: Decimal
    comparison_set: tuple[str, ...]
    benchmark_key: str | None
    required_excess_return: Decimal | None
    effective_at: datetime
    expires_at: datetime | None
    version: int
    supersedes_id: UUID | None
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class CreateResearchScopeRequest(UnderwritingModel):
    primary_company_id: UUID
    target_security_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)
    industry_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    covered_segments: tuple[StrictStr, ...] = Field(default=(), max_length=100)
    user_focus: StrictStr | None = Field(default=None, min_length=1, max_length=2000)
    exclusions: tuple[StrictStr, ...] = Field(default=(), max_length=100)
    expected_parent_id: UUID | None = None

    @field_validator("target_security_ids", "industry_ids")
    @classmethod
    def unique_ids(cls, value: tuple[UUID, ...], info):
        return _require_unique(value, info.field_name)

    @field_validator("covered_segments", "exclusions")
    @classmethod
    def unique_text(cls, value: tuple[str, ...], info):
        if any(not item.strip() or len(item) > 256 for item in value):
            raise ValueError(f"{info.field_name} entries must be non-empty and bounded")
        return _require_unique(value, info.field_name)


class ResearchScopePayloadResponse(UnderwritingModel):
    primary_company_id: UUID
    target_security_ids: tuple[UUID, ...]
    industry_ids: tuple[UUID, ...]
    covered_segments: tuple[str, ...]
    user_focus: str | None
    exclusions: tuple[str, ...]


class ResearchScopeResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    version: int
    payload: ResearchScopePayloadResponse
    supersedes_id: UUID | None
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class AgendaGeneratorRequest(UnderwritingModel):
    method: AgendaGenerationMethod
    template_key: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    template_version: StrictStr | None = Field(
        default=None, min_length=1, max_length=120
    )
    model_name: StrictStr | None = Field(default=None, min_length=1, max_length=160)
    prompt_template_version: StrictStr | None = Field(
        default=None, min_length=1, max_length=120
    )
    input_summary_hash: str = Field(pattern=SHA256_PATTERN)
    output_hash: str = Field(pattern=SHA256_PATTERN)


class CreateResearchAgendaRequest(UnderwritingModel):
    scope_id: UUID
    items: tuple[StrictStr, ...] = Field(min_length=1, max_length=100)
    generator: AgendaGeneratorRequest
    expected_parent_id: UUID | None = None

    @field_validator("items")
    @classmethod
    def unique_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 1000 for item in value):
            raise ValueError("agenda items must be non-empty and bounded")
        return _require_unique(value, "items")  # type: ignore[return-value]


class AgendaGeneratorResponse(UnderwritingModel):
    method: AgendaGenerationMethod
    template_key: str | None
    template_version: str | None
    model_name: str | None
    prompt_template_version: str | None
    input_summary_hash: str = Field(pattern=SHA256_PATTERN)
    output_hash: str = Field(pattern=SHA256_PATTERN)


class ResearchAgendaPayloadResponse(UnderwritingModel):
    items: tuple[str, ...]


class ResearchAgendaResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    version: int
    scope_id: UUID
    payload: ResearchAgendaPayloadResponse
    generator_provenance: AgendaGeneratorResponse
    supersedes_id: UUID | None
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class CreateProductHistoricalBasisRequest(UnderwritingModel):
    cutoff_at: datetime
    source_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    definition_bundle_hash: str = Field(pattern=SHA256_PATTERN)
    parser_bundle_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator("cutoff_at")
    @classmethod
    def aware_cutoff(cls, value: datetime) -> datetime:
        return _require_aware(value, "cutoff_at")


class ProductHistoricalBasisResponse(UnderwritingModel):
    id: UUID
    cutoff_at: datetime
    price_as_of: None = None
    source_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    definition_bundle_hash: str = Field(pattern=SHA256_PATTERN)
    parser_bundle_hash: str = Field(pattern=SHA256_PATTERN)
    boundary_schema_version: Literal["product.historical-basis.v1"]
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class CreatePriceSnapshotRequest(UnderwritingModel):
    security_identity_id: UUID
    price: Decimal
    currency: Currency
    price_type: StrictStr = Field(min_length=1, max_length=64)
    adjustment_basis: StrictStr = Field(min_length=1, max_length=64)
    market_at: datetime
    available_at: datetime
    source_id: StrictStr = Field(min_length=1, max_length=256)
    raw_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator("price", mode="before")
    @classmethod
    def finite_price(cls, value: object):
        return _require_finite_decimal(value, "price")

    @field_validator("market_at", "available_at")
    @classmethod
    def aware_times(cls, value: datetime, info):
        return _require_aware(value, info.field_name)


class PriceSnapshotResponse(UnderwritingModel):
    id: UUID
    security_identity_id: UUID
    price: Decimal
    currency: Currency
    price_type: str
    adjustment_basis: str
    market_at: datetime
    available_at: datetime
    source_id: str
    raw_hash: str = Field(pattern=SHA256_PATTERN)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class CreateFXSnapshotRequest(UnderwritingModel):
    base_currency: Currency
    quote_currency: Currency
    rate: Decimal
    quote_direction: FxQuoteDirection
    market_at: datetime
    available_at: datetime
    source_id: StrictStr = Field(min_length=1, max_length=256)
    raw_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator("rate", mode="before")
    @classmethod
    def finite_rate(cls, value: object):
        return _require_finite_decimal(value, "rate")

    @field_validator("market_at", "available_at")
    @classmethod
    def aware_times(cls, value: datetime, info):
        return _require_aware(value, info.field_name)


class FXSnapshotResponse(UnderwritingModel):
    id: UUID
    base_currency: Currency
    quote_currency: Currency
    rate: Decimal
    quote_direction: FxQuoteDirection
    market_at: datetime
    available_at: datetime
    source_id: str
    raw_hash: str = Field(pattern=SHA256_PATTERN)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class CreateCapitalStructureSnapshotRequest(UnderwritingModel):
    company_id: UUID
    currency: Currency
    cash: Decimal
    debt: Decimal
    minority_interest: Decimal
    investments: Decimal
    pension_liabilities: Decimal
    other_adjustments: Decimal
    basic_shares: Decimal
    diluted_shares: Decimal
    potential_dilution_descriptors: tuple[StrictStr, ...] = Field(
        default=(), max_length=100
    )
    report_period_start: datetime
    report_period_end: datetime
    market_at: datetime
    available_at: datetime
    source_id: StrictStr = Field(min_length=1, max_length=256)
    raw_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator(
        "cash",
        "debt",
        "minority_interest",
        "investments",
        "pension_liabilities",
        "other_adjustments",
        "basic_shares",
        "diluted_shares",
        mode="before",
    )
    @classmethod
    def finite_numbers(cls, value: object, info):
        return _require_finite_decimal(value, info.field_name)

    @field_validator(
        "report_period_start", "report_period_end", "market_at", "available_at"
    )
    @classmethod
    def aware_times(cls, value: datetime, info):
        return _require_aware(value, info.field_name)

    @field_validator("potential_dilution_descriptors")
    @classmethod
    def unique_descriptors(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 256 for item in value):
            raise ValueError(
                "potential dilution descriptors must be non-empty and bounded"
            )
        return _require_unique(value, "potential_dilution_descriptors")  # type: ignore[return-value]


class CapitalStructureSnapshotResponse(UnderwritingModel):
    id: UUID
    company_id: UUID
    currency: Currency
    cash: Decimal
    debt: Decimal
    minority_interest: Decimal
    investments: Decimal
    pension_liabilities: Decimal
    other_adjustments: Decimal
    basic_shares: Decimal
    diluted_shares: Decimal
    potential_dilution_descriptors: tuple[str, ...]
    report_period_start: datetime
    report_period_end: datetime
    market_at: datetime
    available_at: datetime
    source_id: str
    raw_hash: str = Field(pattern=SHA256_PATTERN)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class CreateSecurityRightsRequest(UnderwritingModel):
    security_identity_id: UUID
    economic_units: Decimal
    votes_per_unit: Decimal
    conversion_ratio: Decimal
    adr_ratio: Decimal
    dividend_rights_per_unit: Decimal
    effective_from: datetime
    effective_to: datetime | None = None
    source_id: StrictStr = Field(min_length=1, max_length=256)
    raw_hash: str = Field(pattern=SHA256_PATTERN)
    expected_parent_id: UUID | None = None

    @field_validator(
        "economic_units",
        "votes_per_unit",
        "conversion_ratio",
        "adr_ratio",
        "dividend_rights_per_unit",
        mode="before",
    )
    @classmethod
    def finite_numbers(cls, value: object, info):
        return _require_finite_decimal(value, info.field_name)

    @field_validator("effective_from")
    @classmethod
    def aware_effective_from(cls, value: datetime) -> datetime:
        return _require_aware(value, "effective_from")

    @field_validator("effective_to")
    @classmethod
    def aware_effective_to(cls, value: datetime | None) -> datetime | None:
        return value if value is None else _require_aware(value, "effective_to")

    @model_validator(mode="after")
    def strict_effective_interval(self):
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be later than effective_from")
        return self


class SecurityRightsResponse(UnderwritingModel):
    id: UUID
    security_identity_id: UUID
    version: int
    economic_units: Decimal
    votes_per_unit: Decimal
    conversion_ratio: Decimal
    adr_ratio: Decimal
    dividend_rights_per_unit: Decimal
    effective_from: datetime
    effective_to: datetime | None
    source_id: str
    raw_hash: str = Field(pattern=SHA256_PATTERN)
    supersedes_id: UUID | None
    content_hash: str = Field(pattern=SHA256_PATTERN)
    created_at: datetime


class SecurityRightsHeadResponse(UnderwritingModel):
    id: UUID
    effective_from: datetime
    effective_to: datetime | None


class SecurityRightsResolutionReasonResponse(UnderwritingModel):
    code: Literal[
        "effective_version_found", "no_history", "before_head", "successor_required"
    ]
    action: Literal[
        "reuse_effective", "create_initial", "adjust_market_at", "append_successor"
    ]


class EffectiveSecurityRightsResponse(UnderwritingModel):
    security_identity_id: UUID
    as_of: datetime
    effective: SecurityRightsResponse | None
    head: SecurityRightsHeadResponse | None
    append_allowed: bool
    expected_parent_id: UUID | None
    minimum_effective_from: datetime | None
    reason: SecurityRightsResolutionReasonResponse


class WorkspaceDraftContentResponse(UnderwritingModel):
    publication_status: Literal["draft"]
    mandate_id: UUID | None
    scope_id: UUID | None
    agenda_id: UUID | None
    historical_basis_id: UUID | None
    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID | None
    security_rights_ids: tuple[UUID, ...]
    user_focus: str | None


class WorkspaceDraftResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    base_revision_id: UUID | None
    lock_version: int
    content: WorkspaceDraftContentResponse
    created_at: datetime
    updated_at: datetime


class PatchWorkspaceDraftRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)
    mandate_id: UUID | None = None
    scope_id: UUID | None = None
    agenda_id: UUID | None = None
    historical_basis_id: UUID | None = None
    price_snapshot_ids: tuple[UUID, ...] | None = Field(default=None, max_length=32)
    fx_snapshot_ids: tuple[UUID, ...] | None = Field(default=None, max_length=32)
    capital_structure_snapshot_id: UUID | None = None
    security_rights_ids: tuple[UUID, ...] | None = Field(default=None, max_length=32)
    user_focus: StrictStr | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("price_snapshot_ids", "fx_snapshot_ids", "security_rights_ids")
    @classmethod
    def unique_references(cls, value: tuple[UUID, ...] | None, info):
        return value if value is None else _require_unique(value, info.field_name)


class PreviewProductRevisionRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)


class PublishProductRevisionRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)


class AssessmentPreviewResponse(UnderwritingModel):
    answerability: AnswerabilityState
    direction: AssessmentDirection | None
    confidence: AssessmentConfidence | None
    publication_status: PublicationStatus
    blockers: tuple[str, ...]
    resolution_requirements: tuple[str, ...]
    next_review_at: datetime | None
    parent_assessment_id: UUID | None
    content_hash: str = Field(pattern=SHA256_PATTERN)


class RevisionBoundaryResponse(UnderwritingModel):
    historical_basis_id: UUID
    mandate_id: UUID
    scope_id: UUID
    agenda_id: UUID
    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    parent_revision_id: UUID | None


class ProductManifestProjectRefResponse(UnderwritingModel):
    schema_version: Literal["underwriting.v1"] = Field(
        default="underwriting.v1", exclude=True
    )
    project_id: UUID
    content_hash: str = Field(pattern=SHA256_PATTERN)


class ProductManifestMembershipRefResponse(UnderwritingModel):
    schema_version: Literal["underwriting.v1"] = Field(
        default="underwriting.v1", exclude=True
    )
    membership_id: UUID
    security_id: UUID
    content_hash: str = Field(pattern=SHA256_PATTERN)


class ProductManifestPreviewResponse(UnderwritingModel):
    schema_version: Literal["underwriting.research-revision-manifest.v1"]
    project_id: UUID
    project_ref: ProductManifestProjectRefResponse
    project_membership_refs: tuple[ProductManifestMembershipRefResponse, ...] = Field(
        min_length=1
    )
    primary_object_id: UUID
    boundary_ref: Literal["$boundary"]
    mandate_id: UUID
    scope_id: UUID
    agenda_id: UUID
    historical_basis_id: UUID
    price_snapshot_ids: tuple[UUID, ...] = Field(min_length=1)
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...] = Field(min_length=1)
    market_snapshot_refs: tuple[MarketSnapshotRef, ...] = Field(min_length=3)
    model_refs: tuple[StrictStr, ...] = Field(max_length=0)
    assessment_ref: Literal["$assessment"]
    memo_ref: None
    parent_revision_id: UUID | None

    @field_validator(
        "price_snapshot_ids",
        "fx_snapshot_ids",
        "security_rights_ids",
        "market_snapshot_refs",
    )
    @classmethod
    def unique_manifest_references(cls, value: tuple[object, ...], info):
        return _require_unique(value, info.field_name)

    @field_validator("project_membership_refs")
    @classmethod
    def unique_manifest_memberships(
        cls, value: tuple[ProductManifestMembershipRefResponse, ...]
    ) -> tuple[ProductManifestMembershipRefResponse, ...]:
        membership_ids = tuple(item.membership_id for item in value)
        security_ids = tuple(item.security_id for item in value)
        _require_unique(membership_ids, "project_membership_refs.membership_id")
        _require_unique(security_ids, "project_membership_refs.security_id")
        return value


class PublicationPreviewResponse(UnderwritingModel):
    project_id: UUID
    expected_lock_version: int
    boundary_as_of: datetime
    assessment: AssessmentPreviewResponse
    boundary: RevisionBoundaryResponse
    boundary_hash: str = Field(pattern=SHA256_PATTERN)
    manifest: ProductManifestPreviewResponse
    manifest_hash: str = Field(pattern=SHA256_PATTERN)


class ProductRevisionResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    object_id: UUID
    basis_id: UUID
    boundary_id: UUID
    manifest_id: UUID
    version_kind: Literal["independent_research"]
    sequence: int
    content_hash: str = Field(pattern=SHA256_PATTERN)
    cutoff: datetime
    source_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    manifest_hash: str = Field(pattern=SHA256_PATTERN)
    parent_revision_id: UUID | None
    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    market_snapshot_ids: tuple[UUID, ...]
    answerability: AnswerabilityState
    direction: AssessmentDirection | None
    confidence: AssessmentConfidence | None
    publication_status: PublicationStatus
