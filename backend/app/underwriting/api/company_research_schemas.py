"""Closed wire contracts for the high-level company-research entry flow."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

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
CompanyResearchFailureStep = Literal[
    "evidence_index",
    "research_gaps",
    "business_map",
    "driver_map",
    "financial_bridge",
    "scenario_set",
    "valuation_set",
    "judgment_context",
    "memo",
    "model_bundle",
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

    @model_validator(mode="after")
    def valid_status_step_pair(self):
        pairs = {
            "preparing_sources": "evidence_index",
            "awaiting_evidence_review": "research_gaps",
            "awaiting_judgment_review": "judgment_context",
        }
        if self.status in pairs and self.current_step != pairs[self.status]:
            raise ValueError("preparation status and current_step are inconsistent")
        terminal = {
            "ready_to_freeze": ("memo", 95),
            "completed": (None, 100),
        }
        if (
            self.status in terminal
            and (
                self.current_step,
                self.progress,
            )
            != terminal[self.status]
        ):
            raise ValueError("preparation terminal lifecycle is inconsistent")
        return self


class CompanyResearchProjectResponse(UnderwritingModel):
    project_id: UUID
    company_id: UUID
    preparation: CompanyResearchPreparationResponse


CompanyResearchArtifactKind = Literal[
    "evidence_index",
    "research_gaps",
    "business_map",
    "driver_map",
    "financial_bridge",
    "scenario_set",
    "valuation_set",
    "judgment_context",
    "memo",
]
CompanyResearchModuleKey = Literal[
    "overview",
    "business_map",
    "operating_drivers",
    "evidence_and_gaps",
    "industry_competition_regulation",
    "financials_cash_flow_capital_allocation",
    "scenarios_valuation_implied_expectations",
    "counterevidence_risks_next_checks",
    "versions_changes_memo",
]
COMPANY_RESEARCH_MODULE_KEYS = (
    "overview",
    "business_map",
    "operating_drivers",
    "evidence_and_gaps",
    "industry_competition_regulation",
    "financials_cash_flow_capital_allocation",
    "scenarios_valuation_implied_expectations",
    "counterevidence_risks_next_checks",
    "versions_changes_memo",
)
_MODULE_ARTIFACT_KINDS: dict[str, tuple[str, ...]] = {
    "overview": ("judgment_context",),
    "business_map": ("business_map",),
    "operating_drivers": ("driver_map",),
    "evidence_and_gaps": ("evidence_index", "research_gaps"),
    "industry_competition_regulation": ("business_map",),
    "financials_cash_flow_capital_allocation": ("financial_bridge",),
    "scenarios_valuation_implied_expectations": ("scenario_set", "valuation_set"),
    "counterevidence_risks_next_checks": ("research_gaps", "judgment_context"),
    "versions_changes_memo": ("memo",),
}
_CANONICAL_DECIMAL_PATTERN = r"^(?:0|-?(?:0\.\d*[1-9]|[1-9]\d*(?:\.\d*[1-9])?))$"
CanonicalDecimalString = Annotated[StrictStr, Field(pattern=_CANONICAL_DECIMAL_PATTERN)]


class _ClosedCompanyResearchPayloadModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompanyResearchSourceReferenceResponse(_ClosedCompanyResearchPayloadModel):
    raw_hash: str = Field(pattern=SHA256_PATTERN)
    source_locator: StrictStr = Field(min_length=1)
    source_role: StrictStr = Field(min_length=1)
    source_url: StrictStr = Field(min_length=1)


class CompanyResearchLineageSourceReferenceResponse(
    CompanyResearchSourceReferenceResponse
):
    fact_key: StrictStr = Field(min_length=1)


class CompanyResearchArtifactParentResponse(_ClosedCompanyResearchPayloadModel):
    artifact_id: UUID
    artifact_kind: CompanyResearchArtifactKind
    content_hash: str = Field(pattern=SHA256_PATTERN)


class CompanyResearchArtifactRegistryReferenceResponse(
    _ClosedCompanyResearchPayloadModel
):
    id: UUID
    kind: CompanyResearchArtifactKind
    content_hash: str = Field(pattern=SHA256_PATTERN)


class CompanyResearchRawComponentResponse(_ClosedCompanyResearchPayloadModel):
    raw_file: StrictStr = Field(min_length=1)
    raw_hash: str = Field(pattern=SHA256_PATTERN)
    source_url: StrictStr = Field(min_length=1)
    source_locator: StrictStr = Field(min_length=1)


class CompanyResearchMarketSnapshotBindingResponse(_ClosedCompanyResearchPayloadModel):
    snapshot_id: UUID
    snapshot_kind: Literal["price", "fx", "capital_structure", "security_rights"]
    snapshot_content_hash: str = Field(pattern=SHA256_PATTERN)
    security_external_key: StrictStr | None
    source_ref: CompanyResearchLineageSourceReferenceResponse
    capture_envelope_id: UUID
    capture_content_hash: str = Field(pattern=SHA256_PATTERN)
    provenance_role: Literal["primary"]
    provider_policy_version: StrictStr = Field(min_length=1)
    raw_components: tuple[CompanyResearchRawComponentResponse, ...]


class CompanyResearchArtifactLineageResponse(_ClosedCompanyResearchPayloadModel):
    artifact_refs: tuple[CompanyResearchArtifactParentResponse, ...]
    market_snapshot_ids: tuple[UUID, ...]
    market_snapshot_bindings: tuple[CompanyResearchMarketSnapshotBindingResponse, ...]

    @model_validator(mode="after")
    def exact_unique_refs(self):
        parent_ids = tuple(item.artifact_id for item in self.artifact_refs)
        if len(set(parent_ids)) != len(parent_ids):
            raise ValueError("artifact parent refs must be unique")
        if len(set(self.market_snapshot_ids)) != len(self.market_snapshot_ids):
            raise ValueError("market snapshot refs must be unique")
        if tuple(item.snapshot_id for item in self.market_snapshot_bindings) != (
            self.market_snapshot_ids
        ):
            raise ValueError("market snapshot bindings must match exact refs")
        return self


class CompanyResearchExternalNumericSourceResponse(
    CompanyResearchLineageSourceReferenceResponse
):
    kind: Literal["external"]


class CompanyResearchComputationNumericSourceResponse(
    _ClosedCompanyResearchPayloadModel
):
    kind: Literal["artifact_computation"]
    artifact_refs: tuple[CompanyResearchArtifactParentResponse, ...] = Field(
        min_length=1
    )
    market_snapshot_ids: tuple[UUID, ...]
    equation_id: StrictStr = Field(min_length=1)

    @model_validator(mode="after")
    def unique_computation_refs(self):
        if len({item.artifact_id for item in self.artifact_refs}) != len(
            self.artifact_refs
        ):
            raise ValueError("numeric computation artifact refs must be unique")
        if len(set(self.market_snapshot_ids)) != len(self.market_snapshot_ids):
            raise ValueError("numeric computation market refs must be unique")
        return self


CompanyResearchNumericSourceResponse = Annotated[
    CompanyResearchExternalNumericSourceResponse
    | CompanyResearchComputationNumericSourceResponse,
    Field(discriminator="kind"),
]


class CompanyResearchNumericObservationResponse(_ClosedCompanyResearchPayloadModel):
    key: StrictStr = Field(min_length=1)
    value: CanonicalDecimalString
    unit: StrictStr = Field(min_length=1)
    currency: StrictStr = Field(min_length=1)
    period: StrictStr = Field(min_length=1)
    state: Literal["reported", "derived", "assumption", "gap"]
    source_ref: CompanyResearchNumericSourceResponse | None
    gap_key: StrictStr | None
    assumption_key: StrictStr | None

    @model_validator(mode="after")
    def one_semantic_provenance_path(self):
        provenance_count = sum(
            value is not None
            for value in (self.source_ref, self.gap_key, self.assumption_key)
        )
        if provenance_count != 1:
            raise ValueError("numeric observation requires exactly one provenance path")
        if self.state == "reported" and not isinstance(
            self.source_ref, CompanyResearchExternalNumericSourceResponse
        ):
            raise ValueError("reported numeric observation requires an external source")
        if self.state == "derived" and not isinstance(
            self.source_ref, CompanyResearchComputationNumericSourceResponse
        ):
            raise ValueError(
                "derived numeric observation requires a computation source"
            )
        if self.state == "assumption" and self.assumption_key is None:
            raise ValueError(
                "assumption numeric observation requires an assumption key"
            )
        if self.state == "gap" and self.gap_key is None:
            raise ValueError("gap numeric observation requires a gap key")
        return self


class _CompanyResearchEvidenceFactBaseResponse(_ClosedCompanyResearchPayloadModel):
    fact_key: StrictStr = Field(min_length=1)
    company_external_key: StrictStr = Field(min_length=1)
    business_module: StrictStr = Field(min_length=1)
    metric_key: StrictStr = Field(min_length=1)
    observation: CompanyResearchNumericObservationResponse
    period_start: date
    period_end: date
    published_at: datetime
    available_at: datetime
    source_role: StrictStr = Field(min_length=1)
    source_url: StrictStr = Field(min_length=1)
    source_locator: StrictStr = Field(min_length=1)
    raw_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def valid_period_and_availability(self):
        if self.period_start > self.period_end:
            raise ValueError("evidence period must be ordered")
        _require_aware(self.published_at, "published_at")
        _require_aware(self.available_at, "available_at")
        if self.available_at < self.published_at:
            raise ValueError("evidence availability cannot precede publication")
        return self


class CompanyResearchUnreviewedEvidenceFactResponse(
    _CompanyResearchEvidenceFactBaseResponse
):
    pass


class CompanyResearchReviewedEvidenceFactResponse(
    _CompanyResearchEvidenceFactBaseResponse
):
    review_decision: Literal["confirmed", "rejected"]


class CompanyResearchEvidenceIndexPayloadResponse(_ClosedCompanyResearchPayloadModel):
    fixture_content_hash: str = Field(pattern=SHA256_PATTERN)
    cutoff: datetime
    company_external_key: StrictStr = Field(min_length=1)
    security_external_keys: tuple[StrictStr, ...] = Field(min_length=1)
    facts: tuple[
        CompanyResearchReviewedEvidenceFactResponse
        | CompanyResearchUnreviewedEvidenceFactResponse,
        ...,
    ] = Field(min_length=1)

    @model_validator(mode="after")
    def closed_evidence_index(self):
        _require_aware(self.cutoff, "cutoff")
        if len(set(self.security_external_keys)) != len(self.security_external_keys):
            raise ValueError("security refs must be unique")
        if len({item.fact_key for item in self.facts}) != len(self.facts):
            raise ValueError("evidence fact refs must be unique")
        return self


class CompanyResearchLegacyGapResponse(_ClosedCompanyResearchPayloadModel):
    gap_key: StrictStr = Field(min_length=1)
    business_module: StrictStr = Field(min_length=1)
    reason: StrictStr = Field(min_length=1)


class CompanyResearchGapResponse(_ClosedCompanyResearchPayloadModel):
    code: StrictStr = Field(min_length=1)
    module_key: StrictStr = Field(min_length=1)
    severity: Literal["low", "medium", "high", "critical"]
    message: StrictStr = Field(min_length=1)


class CompanyResearchLegacyGapsPayloadResponse(_ClosedCompanyResearchPayloadModel):
    fixture_content_hash: str = Field(pattern=SHA256_PATTERN)
    company_external_key: StrictStr = Field(min_length=1)
    gaps: tuple[CompanyResearchLegacyGapResponse, ...]


class CompanyResearchModelGapsPayloadResponse(_ClosedCompanyResearchPayloadModel):
    gaps: tuple[CompanyResearchGapResponse, ...]
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchClassifiedEvidenceResponse(_ClosedCompanyResearchPayloadModel):
    fact_ref: CompanyResearchLineageSourceReferenceResponse
    metric_key: StrictStr = Field(min_length=1)
    category: Literal["revenue", "cost", "capital"]
    observation: CompanyResearchNumericObservationResponse
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def ordered_period(self):
        if self.period_start > self.period_end:
            raise ValueError("classified evidence period must be ordered")
        return self


class CompanyResearchBusinessModuleResponse(_ClosedCompanyResearchPayloadModel):
    module_key: StrictStr = Field(min_length=1)
    revenue_sources: tuple[StrictStr, ...] = Field(min_length=1)
    cost_structure: tuple[StrictStr, ...] = Field(min_length=1)
    capital_needs: tuple[StrictStr, ...] = Field(min_length=1)
    fact_refs: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    gap_refs: tuple[StrictStr, ...]
    classified_evidence: tuple[CompanyResearchClassifiedEvidenceResponse, ...]


class CompanyResearchBusinessMapPayloadResponse(_ClosedCompanyResearchPayloadModel):
    modules: tuple[CompanyResearchBusinessModuleResponse, ...] = Field(min_length=1)
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchLegacyBusinessModuleResponse(_ClosedCompanyResearchPayloadModel):
    key: StrictStr = Field(min_length=1)
    fact_keys: tuple[StrictStr, ...] = Field(min_length=1)


class CompanyResearchLegacyBusinessMapPayloadResponse(
    _ClosedCompanyResearchPayloadModel
):
    evidence_index_id: UUID
    evidence_content_hash: str = Field(pattern=SHA256_PATTERN)
    modules: tuple[CompanyResearchLegacyBusinessModuleResponse, ...] = Field(
        min_length=1
    )


class CompanyResearchDriverResponse(_ClosedCompanyResearchPayloadModel):
    driver_key: StrictStr = Field(min_length=1)
    module_key: StrictStr = Field(min_length=1)
    fact_refs: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    assumption_refs: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    equation: StrictStr = Field(min_length=1)
    output_metric: StrictStr = Field(min_length=1)
    equation_id: StrictStr | None
    values: tuple[CompanyResearchNumericObservationResponse, ...] = Field(min_length=1)
    assumption_rationale: StrictStr | None
    assumption_equation: StrictStr | None


class CompanyResearchDriverMapPayloadResponse(_ClosedCompanyResearchPayloadModel):
    drivers: tuple[CompanyResearchDriverResponse, ...] = Field(min_length=1)
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchFinancialBridgeRowResponse(_ClosedCompanyResearchPayloadModel):
    period: StrictStr = Field(min_length=1)
    revenue: CompanyResearchNumericObservationResponse
    operating_income: CompanyResearchNumericObservationResponse
    cash_tax_rate: CompanyResearchNumericObservationResponse
    depreciation: CompanyResearchNumericObservationResponse
    capex: CompanyResearchNumericObservationResponse
    working_capital_change: CompanyResearchNumericObservationResponse
    fcff: CompanyResearchNumericObservationResponse
    fact_refs: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    assumption_refs: tuple[CompanyResearchLineageSourceReferenceResponse, ...]


class CompanyResearchFinancialBridgePayloadResponse(_ClosedCompanyResearchPayloadModel):
    rows: tuple[CompanyResearchFinancialBridgeRowResponse, ...] = Field(
        min_length=5, max_length=5
    )
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchScenarioOverrideResponse(_ClosedCompanyResearchPayloadModel):
    driver_key: StrictStr = Field(min_length=1)
    observation: CompanyResearchNumericObservationResponse
    rationale: StrictStr | None
    equation: StrictStr | None

    @model_validator(mode="after")
    def normalized_multiplier(self):
        if self.observation.unit != "multiplier" or self.observation.currency != "N/A":
            raise ValueError("scenario overrides must use dimensionless multipliers")
        return self


class CompanyResearchScenarioResponse(_ClosedCompanyResearchPayloadModel):
    scenario_id: Literal["base", "bull", "bear"]
    mechanism_id: StrictStr = Field(min_length=1)
    driver_overrides: tuple[CompanyResearchScenarioOverrideResponse, ...] = Field(
        min_length=1
    )


class CompanyResearchScenarioSetPayloadResponse(_ClosedCompanyResearchPayloadModel):
    scenarios: tuple[CompanyResearchScenarioResponse, ...] = Field(
        min_length=3, max_length=3
    )
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")

    @model_validator(mode="after")
    def exact_scenarios(self):
        if {item.scenario_id for item in self.scenarios} != {"base", "bull", "bear"}:
            raise ValueError("scenario set must contain Base, Bull, and Bear")
        if len({item.mechanism_id for item in self.scenarios}) != 3:
            raise ValueError("scenario mechanisms must be distinct")
        return self


class CompanyResearchScenarioDcfValueResponse(_ClosedCompanyResearchPayloadModel):
    scenario_id: Literal["base", "bull", "bear"]
    enterprise_value: CompanyResearchNumericObservationResponse


class CompanyResearchValueRangeResponse(_ClosedCompanyResearchPayloadModel):
    minimum: CompanyResearchNumericObservationResponse
    maximum: CompanyResearchNumericObservationResponse


class CompanyResearchSecurityValueRangeResponse(_ClosedCompanyResearchPayloadModel):
    security_external_key: StrictStr = Field(min_length=1)
    usd_per_share: CompanyResearchValueRangeResponse
    cny_return: CompanyResearchValueRangeResponse


class CompanyResearchReverseDcfResponse(_ClosedCompanyResearchPayloadModel):
    driver_key: Literal["fcff_multiplier"]
    implied_value: CompanyResearchNumericObservationResponse
    achieved_residual: CompanyResearchNumericObservationResponse
    iteration_count: CompanyResearchNumericObservationResponse


class CompanyResearchRequiredReturnComparisonResponse(
    _ClosedCompanyResearchPayloadModel
):
    security_external_key: StrictStr = Field(min_length=1)
    required_return: CompanyResearchNumericObservationResponse
    achieved_return_range: CompanyResearchValueRangeResponse
    meets_required_return: StrictBool


class CompanyResearchValuationSetPayloadResponse(_ClosedCompanyResearchPayloadModel):
    scenario_dcf_values: tuple[CompanyResearchScenarioDcfValueResponse, ...] = Field(
        min_length=3, max_length=3
    )
    reverse_dcf: CompanyResearchReverseDcfResponse | None
    security_value_ranges: tuple[CompanyResearchSecurityValueRangeResponse, ...] = (
        Field(min_length=1)
    )
    required_return: CompanyResearchNumericObservationResponse
    required_return_comparisons: tuple[
        CompanyResearchRequiredReturnComparisonResponse, ...
    ] = Field(min_length=1)
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")

    @model_validator(mode="after")
    def exact_market_valuation(self):
        if {item.scenario_id for item in self.scenario_dcf_values} != {
            "base",
            "bull",
            "bear",
        }:
            raise ValueError("valuation must cover Base, Bull, and Bear")
        if not self.lineage.market_snapshot_ids:
            raise ValueError("valuation requires exact market refs")
        required_roles = {"price", "fx", "capital_structure", "security_rights"}
        if not required_roles <= {
            item.snapshot_kind for item in self.lineage.market_snapshot_bindings
        }:
            raise ValueError("valuation requires exact market refs")
        securities = {item.security_external_key for item in self.security_value_ranges}
        if securities != {
            item.security_external_key for item in self.required_return_comparisons
        }:
            raise ValueError("valuation comparisons must cover each security")
        return self


class CompanyResearchJudgmentContextPayloadResponse(_ClosedCompanyResearchPayloadModel):
    operating_baseline_available: StrictBool
    financial_bridge_closed: StrictBool
    market_security_bridge_available: StrictBool
    strongest_counterevidence: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    next_verification_events: tuple[StrictStr, ...]
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchMemoArtifactReferenceResponse(_ClosedCompanyResearchPayloadModel):
    artifact_kind: Literal[
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "valuation_set",
    ]
    content_hash: str = Field(pattern=SHA256_PATTERN)


class _CompanyResearchMemoPayloadBaseResponse(_ClosedCompanyResearchPayloadModel):
    assessment_status: Literal["not_answerable", "partially_answerable", "answerable"]
    business_map_ref: CompanyResearchMemoArtifactReferenceResponse
    driver_map_ref: CompanyResearchMemoArtifactReferenceResponse
    financial_bridge_ref: CompanyResearchMemoArtifactReferenceResponse
    scenario_set_ref: CompanyResearchMemoArtifactReferenceResponse
    valuation_set_ref: CompanyResearchMemoArtifactReferenceResponse | None
    gap_keys: tuple[StrictStr, ...]
    strongest_counterevidence: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    next_verification_events: tuple[StrictStr, ...]
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchMachineMemoPayloadResponse(
    _CompanyResearchMemoPayloadBaseResponse
):
    candidate_status: Literal["machine_draft"]


class CompanyResearchConfirmedMemoPayloadResponse(
    _CompanyResearchMemoPayloadBaseResponse
):
    candidate_status: Literal["human_confirmed"]
    reviewer: Literal["human:local-user"]
    markdown: StrictStr = Field(min_length=1, max_length=100_000)

    @field_validator("markdown")
    @classmethod
    def normalized_confirmation_markdown(cls, value: str) -> str:
        if not value.strip() or value != value.replace("\r\n", "\n").replace(
            "\r", "\n"
        ):
            raise ValueError("confirmed memo is invalid")
        return value


CompanyResearchMemoPayloadResponse = Annotated[
    CompanyResearchMachineMemoPayloadResponse
    | CompanyResearchConfirmedMemoPayloadResponse,
    Field(discriminator="candidate_status"),
]


class _CompanyResearchArtifactBaseResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    version: StrictInt = Field(ge=1)
    input_hash: str = Field(pattern=SHA256_PATTERN)
    content_hash: str = Field(pattern=SHA256_PATTERN)
    source_refs: tuple[CompanyResearchSourceReferenceResponse, ...] = Field(
        min_length=1
    )

    @model_validator(mode="after")
    def unique_source_refs(self):
        identities = tuple(
            (ref.source_role, ref.source_url, ref.source_locator, ref.raw_hash)
            for ref in self.source_refs
        )
        if len(set(identities)) != len(identities):
            raise ValueError("source_refs must be unique")
        return self


class CompanyResearchEvidenceIndexArtifactResponse(
    _CompanyResearchArtifactBaseResponse
):
    kind: Literal["evidence_index"]
    payload: CompanyResearchEvidenceIndexPayloadResponse

    @model_validator(mode="after")
    def evidence_sources_are_exact_parents(self):
        parents = {
            (item.source_role, item.source_url, item.source_locator, item.raw_hash)
            for item in self.source_refs
        }
        if any(
            (
                fact.observation.source_ref.source_role,
                fact.observation.source_ref.source_url,
                fact.observation.source_ref.source_locator,
                fact.observation.source_ref.raw_hash,
            )
            not in parents
            for fact in self.payload.facts
            if isinstance(
                fact.observation.source_ref,
                CompanyResearchExternalNumericSourceResponse,
            )
        ):
            raise ValueError("evidence facts require exact source parents")
        return self


class CompanyResearchGapsArtifactResponse(_CompanyResearchArtifactBaseResponse):
    kind: Literal["research_gaps"]
    payload: (
        CompanyResearchLegacyGapsPayloadResponse
        | CompanyResearchModelGapsPayloadResponse
    )

    @model_validator(mode="after")
    def valid_gap_parents(self):
        if isinstance(self.payload, CompanyResearchModelGapsPayloadResponse):
            kinds = tuple(
                item.artifact_kind for item in self.payload.lineage.artifact_refs
            )
            valid = {
                (
                    "evidence_index",
                    "business_map",
                    "driver_map",
                    "financial_bridge",
                    "scenario_set",
                ),
                (
                    "evidence_index",
                    "business_map",
                    "driver_map",
                    "financial_bridge",
                    "scenario_set",
                    "valuation_set",
                ),
            }
            if kinds not in valid:
                raise ValueError("research gaps require exact artifact parents")
        return self


class CompanyResearchBusinessMapArtifactResponse(_CompanyResearchArtifactBaseResponse):
    kind: Literal["business_map"]
    payload: (
        CompanyResearchLegacyBusinessMapPayloadResponse
        | CompanyResearchBusinessMapPayloadResponse
    )

    @model_validator(mode="after")
    def valid_business_map_parents(self):
        if isinstance(
            self.payload, CompanyResearchBusinessMapPayloadResponse
        ) and tuple(
            item.artifact_kind for item in self.payload.lineage.artifact_refs
        ) != ("evidence_index",):
            raise ValueError("business map requires the exact evidence parent")
        return self


class CompanyResearchDriverMapArtifactResponse(_CompanyResearchArtifactBaseResponse):
    kind: Literal["driver_map"]
    payload: CompanyResearchDriverMapPayloadResponse

    @model_validator(mode="after")
    def valid_driver_parents(self):
        if tuple(item.artifact_kind for item in self.payload.lineage.artifact_refs) != (
            "business_map",
        ):
            raise ValueError("driver map requires the exact business-map parent")
        return self


class CompanyResearchFinancialBridgeArtifactResponse(
    _CompanyResearchArtifactBaseResponse
):
    kind: Literal["financial_bridge"]
    payload: CompanyResearchFinancialBridgePayloadResponse

    @model_validator(mode="after")
    def valid_financial_parents(self):
        if tuple(item.artifact_kind for item in self.payload.lineage.artifact_refs) != (
            "driver_map",
        ):
            raise ValueError("financial bridge requires the exact driver parent")
        return self


class CompanyResearchScenarioSetArtifactResponse(_CompanyResearchArtifactBaseResponse):
    kind: Literal["scenario_set"]
    payload: CompanyResearchScenarioSetPayloadResponse

    @model_validator(mode="after")
    def valid_scenario_parents(self):
        if tuple(item.artifact_kind for item in self.payload.lineage.artifact_refs) != (
            "driver_map",
        ):
            raise ValueError("scenario set requires the exact driver parent")
        return self


class CompanyResearchValuationSetArtifactResponse(_CompanyResearchArtifactBaseResponse):
    kind: Literal["valuation_set"]
    payload: CompanyResearchValuationSetPayloadResponse

    @model_validator(mode="after")
    def valid_valuation_parents(self):
        if tuple(item.artifact_kind for item in self.payload.lineage.artifact_refs) != (
            "scenario_set",
            "financial_bridge",
        ):
            raise ValueError("valuation requires exact scenario and financial parents")
        return self


class CompanyResearchJudgmentContextArtifactResponse(
    _CompanyResearchArtifactBaseResponse
):
    kind: Literal["judgment_context"]
    payload: CompanyResearchJudgmentContextPayloadResponse

    @model_validator(mode="after")
    def valid_judgment_parents(self):
        kinds = tuple(item.artifact_kind for item in self.payload.lineage.artifact_refs)
        valid = {
            (
                "evidence_index",
                "business_map",
                "driver_map",
                "financial_bridge",
                "scenario_set",
                "research_gaps",
            ),
            (
                "evidence_index",
                "business_map",
                "driver_map",
                "financial_bridge",
                "scenario_set",
                "valuation_set",
                "research_gaps",
            ),
        }
        if kinds not in valid:
            raise ValueError("judgment context requires exact artifact parents")
        return self


class CompanyResearchMemoArtifactResponse(_CompanyResearchArtifactBaseResponse):
    kind: Literal["memo"]
    payload: CompanyResearchMemoPayloadResponse

    @model_validator(mode="after")
    def valid_memo_parents(self):
        if tuple(item.artifact_kind for item in self.payload.lineage.artifact_refs) != (
            "judgment_context",
        ):
            raise ValueError("memo requires the exact judgment parent")
        return self


CompanyResearchArtifactVariant = Annotated[
    CompanyResearchEvidenceIndexArtifactResponse
    | CompanyResearchGapsArtifactResponse
    | CompanyResearchBusinessMapArtifactResponse
    | CompanyResearchDriverMapArtifactResponse
    | CompanyResearchFinancialBridgeArtifactResponse
    | CompanyResearchScenarioSetArtifactResponse
    | CompanyResearchValuationSetArtifactResponse
    | CompanyResearchJudgmentContextArtifactResponse
    | CompanyResearchMemoArtifactResponse,
    Field(discriminator="kind"),
]


class CompanyResearchArtifactResponse(RootModel[CompanyResearchArtifactVariant]):
    @property
    def kind(self) -> str:
        return self.root.kind


class CompanyResearchWorkbenchModuleResponse(UnderwritingModel):
    key: CompanyResearchModuleKey
    state: Literal["not_started", "preparing", "needs_review", "ready", "blocked"]
    artifact_refs: tuple[CompanyResearchArtifactRegistryReferenceResponse, ...]
    valuation_state: Literal["not_applicable", "pending", "ready", "blocked"]

    @model_validator(mode="after")
    def valid_state_artifact_pair(self):
        kinds = tuple(ref.kind for ref in self.artifact_refs)
        allowed = _MODULE_ARTIFACT_KINDS[self.key]
        if any(kind not in allowed for kind in kinds):
            raise ValueError("module artifact kind is impossible")
        if kinds != tuple(kind for kind in allowed if kind in kinds):
            raise ValueError("module artifact refs must use canonical order")
        if self.state in {"ready", "needs_review"}:
            if not self.artifact_refs:
                raise ValueError("ready and needs_review modules require an artifact")
            if self.state == "needs_review" and "evidence_index" not in kinds:
                raise ValueError("only evidence can need review")
            if (
                self.key != "scenarios_valuation_implied_expectations"
                and kinds != allowed
            ):
                raise ValueError(
                    "ready module requires its exact static artifact contract"
                )
        elif self.artifact_refs:
            raise ValueError("non-ready modules cannot expose an artifact")
        if self.key == "scenarios_valuation_implied_expectations":
            if "valuation_set" in kinds and kinds != (
                "scenario_set",
                "valuation_set",
            ):
                raise ValueError("valuation cannot be exposed without its scenario")
            if self.valuation_state == "ready" and kinds != (
                "scenario_set",
                "valuation_set",
            ):
                raise ValueError("ready valuation requires scenario and valuation refs")
            if self.valuation_state == "blocked" and kinds != ("scenario_set",):
                raise ValueError("blocked valuation still requires the scenario ref")
            if self.valuation_state in {"ready", "blocked"} and self.state != "ready":
                raise ValueError("resolved valuation state requires a ready module")
            if self.valuation_state == "pending" and self.state == "ready":
                raise ValueError(
                    "ready scenario module requires a resolved valuation state"
                )
        elif self.valuation_state != "not_applicable":
            raise ValueError("valuation state only belongs to the scenario module")
        return self


class CompanyResearchWorkspaceCompanyResponse(CompanyResearchIdentityResponse):
    id: UUID


class CompanyResearchWorkspacePreparationResponse(UnderwritingModel):
    id: UUID
    status: PreparationStatus
    current_step: str | None
    progress: StrictInt = Field(ge=0, le=100)
    error: "CompanyResearchPreparationErrorResponse | None"

    @model_validator(mode="after")
    def valid_status_step_pair(self):
        if (
            self.status == "awaiting_evidence_review"
            and self.current_step != "research_gaps"
        ):
            raise ValueError("awaiting_evidence_review must expose research_gaps")
        if (
            self.status == "awaiting_judgment_review"
            and self.current_step != "judgment_context"
        ):
            raise ValueError("awaiting_judgment_review must expose judgment_context")
        terminal = {
            "ready_to_freeze": ("memo", 95),
            "completed": (None, 100),
        }
        if (
            self.status in terminal
            and (
                self.current_step,
                self.progress,
            )
            != terminal[self.status]
        ):
            raise ValueError("preparation terminal lifecycle is inconsistent")
        if self.status in {"blocked", "recoverable_failure"} and self.error is None:
            raise ValueError("failed preparation requires typed error semantics")
        if (
            self.status not in {"blocked", "recoverable_failure"}
            and self.error is not None
        ):
            raise ValueError("non-failed preparation cannot expose an error")
        if self.error is not None:
            if self.error.failed_step != self.current_step:
                raise ValueError("preparation error must match current step")
            if (self.status == "recoverable_failure") != self.error.retryable:
                raise ValueError("recoverable status and retryability are inconsistent")
            if (
                self.status == "recoverable_failure"
                and self.error.next_attempt_at is None
            ):
                raise ValueError("recoverable failure requires a retry time")
            if self.status == "blocked" and self.error.next_attempt_at is not None:
                raise ValueError("blocked preparation cannot expose a retry time")
        return self


class CompanyResearchPreparationErrorResponse(UnderwritingModel):
    code: StrictStr = Field(min_length=1)
    failed_step: CompanyResearchFailureStep
    retryable: StrictBool
    next_attempt_at: datetime | None

    @field_validator("next_attempt_at")
    @classmethod
    def aware_retry_time(cls, value):
        return _require_aware(value, "next_attempt_at") if value is not None else None


class CompanyResearchWorkspaceDraftResponse(UnderwritingModel):
    id: UUID
    lock_version: StrictInt = Field(ge=1)
    base_revision_id: UUID | None


class CompanyResearchChangeSummaryResponse(_ClosedCompanyResearchPayloadModel):
    artifact_versions: dict[CompanyResearchArtifactKind, StrictInt]
    reviewed_fact_count: StrictInt = Field(ge=0)

    @field_validator("artifact_versions")
    @classmethod
    def positive_versions(cls, value):
        if any(item < 1 for item in value.values()):
            raise ValueError("artifact versions must be positive")
        return value


class CompanyResearchWorkspaceResponse(UnderwritingModel):
    project_id: UUID
    company: CompanyResearchWorkspaceCompanyResponse
    preparation: CompanyResearchWorkspacePreparationResponse
    artifacts: tuple[CompanyResearchArtifactResponse, ...]
    modules: tuple[CompanyResearchWorkbenchModuleResponse, ...] = Field(
        min_length=9, max_length=9
    )
    source_count: StrictInt = Field(ge=0)
    gap_count: StrictInt = Field(ge=0)
    draft: CompanyResearchWorkspaceDraftResponse
    selected_revision: UUID | None
    change_summary: CompanyResearchChangeSummaryResponse

    @model_validator(mode="after")
    def closed_workspace_contract(self):
        if tuple(item.key for item in self.modules) != COMPANY_RESEARCH_MODULE_KEYS:
            raise ValueError("company research modules must use the exact order")
        evidence_review = self.preparation.status == "awaiting_evidence_review"
        registry = {}
        kinds = set()
        for wrapper in self.artifacts:
            artifact = wrapper.root
            if artifact.project_id != self.project_id:
                raise ValueError("artifact registry contains a foreign project")
            if artifact.id in registry or artifact.kind in kinds:
                raise ValueError("artifact registry must contain unique current heads")
            registry[artifact.id] = artifact
            kinds.add(artifact.kind)

        def require_exact_ref(ref):
            artifact_id = getattr(ref, "artifact_id", getattr(ref, "id", None))
            artifact_kind = getattr(ref, "artifact_kind", getattr(ref, "kind", None))
            artifact = registry.get(artifact_id)
            if artifact is None:
                raise ValueError("artifact ref is absent from the response registry")
            if (
                artifact.kind != artifact_kind
                or artifact.content_hash != ref.content_hash
            ):
                raise ValueError("artifact ref does not match the response registry")

        for module in self.modules:
            for ref in module.artifact_refs:
                require_exact_ref(ref)
            if module.state in {"ready", "needs_review"}:
                expected_kinds = _MODULE_ARTIFACT_KINDS[module.key]
                if (
                    module.key == "scenarios_valuation_implied_expectations"
                    and module.valuation_state == "blocked"
                ):
                    expected_kinds = ("scenario_set",)
                if tuple(ref.kind for ref in module.artifact_refs) != expected_kinds:
                    raise ValueError(
                        "ready module must expose every available required artifact"
                    )
            if module.state == "needs_review" and not evidence_review:
                raise ValueError("needs_review requires the evidence review gate")
            if (
                evidence_review
                and any(ref.kind == "evidence_index" for ref in module.artifact_refs)
                and module.state != "needs_review"
            ):
                raise ValueError("unreviewed evidence cannot be ready")
        for wrapper in self.artifacts:
            payload = wrapper.root.payload
            lineage = getattr(payload, "lineage", None)
            if lineage is not None:
                for ref in lineage.artifact_refs:
                    require_exact_ref(ref)

            def validate_computation_refs(item):
                if isinstance(item, CompanyResearchComputationNumericSourceResponse):
                    if lineage is None or item.artifact_refs != lineage.artifact_refs:
                        raise ValueError(
                            "numeric computation requires exact artifact lineage"
                        )
                    if item.market_snapshot_ids != lineage.market_snapshot_ids:
                        raise ValueError(
                            "numeric computation requires exact market lineage"
                        )
                    for ref in item.artifact_refs:
                        require_exact_ref(ref)
                    return
                if isinstance(item, BaseModel):
                    for child in item.__dict__.values():
                        validate_computation_refs(child)
                elif isinstance(item, (tuple, list)):
                    for child in item:
                        validate_computation_refs(child)
                elif isinstance(item, dict):
                    for child in item.values():
                        validate_computation_refs(child)

            validate_computation_refs(payload)

        evidence = next(
            (
                wrapper.root
                for wrapper in self.artifacts
                if wrapper.root.kind == "evidence_index"
            ),
            None,
        )
        evidence_facts = (
            evidence.payload.facts
            if isinstance(evidence, CompanyResearchEvidenceIndexArtifactResponse)
            else ()
        )
        fact_registry = {fact.fact_key: fact for fact in evidence_facts}

        def validate_reported_observations(item, *, require_confirmed):
            if isinstance(item, CompanyResearchNumericObservationResponse):
                if item.state != "reported":
                    return
                source = item.source_ref
                if not isinstance(source, CompanyResearchExternalNumericSourceResponse):
                    raise ValueError(
                        "reported observation requires exact evidence fact"
                    )
                fact = fact_registry.get(source.fact_key)
                if fact is None:
                    raise ValueError(
                        "reported observation requires exact evidence fact"
                    )
                if (
                    require_confirmed
                    and getattr(fact, "review_decision", None) != "confirmed"
                ):
                    raise ValueError(
                        "downstream reported observation requires confirmed fact"
                    )
                fact_observation = fact.observation
                if (
                    item.value,
                    item.unit,
                    item.currency,
                    item.period,
                    item.source_ref,
                ) != (
                    fact_observation.value,
                    fact_observation.unit,
                    fact_observation.currency,
                    fact_observation.period,
                    fact_observation.source_ref,
                ):
                    raise ValueError(
                        "reported observation differs from its evidence fact"
                    )
                return
            if isinstance(item, BaseModel):
                for child in item.__dict__.values():
                    validate_reported_observations(
                        child, require_confirmed=require_confirmed
                    )
            elif isinstance(item, (tuple, list)):
                for child in item:
                    validate_reported_observations(
                        child, require_confirmed=require_confirmed
                    )
            elif isinstance(item, dict):
                for child in item.values():
                    validate_reported_observations(
                        child, require_confirmed=require_confirmed
                    )

        for wrapper in self.artifacts:
            validate_reported_observations(
                wrapper.root.payload,
                require_confirmed=wrapper.root.kind != "evidence_index",
            )
        expected_versions = {
            wrapper.root.kind: wrapper.root.version for wrapper in self.artifacts
        }
        if self.change_summary.artifact_versions != expected_versions:
            raise ValueError("change summary versions must match every current head")
        expected_reviewed = sum(
            getattr(fact, "review_decision", None) in {"confirmed", "rejected"}
            for fact in evidence_facts
        )
        if self.change_summary.reviewed_fact_count != expected_reviewed:
            raise ValueError("reviewed fact count must equal decided evidence facts")
        source_identities = {
            (ref.source_role, ref.source_url, ref.source_locator, ref.raw_hash)
            for wrapper in self.artifacts
            for ref in wrapper.root.source_refs
        }
        if self.source_count != len(source_identities):
            raise ValueError("source count must equal unique current-head sources")
        gaps = next(
            (
                wrapper.root.payload.gaps
                for wrapper in self.artifacts
                if wrapper.root.kind == "research_gaps"
            ),
            (),
        )
        memo_gap_keys = next(
            (
                wrapper.root.payload.gap_keys
                for wrapper in self.artifacts
                if wrapper.root.kind == "memo"
            ),
            None,
        )
        expected_gap_count = (
            len(memo_gap_keys) if memo_gap_keys is not None else len(gaps)
        )
        if self.gap_count != expected_gap_count:
            raise ValueError("gap count must equal current research gaps")
        return self


class ReviewCompanyEvidenceRequest(UnderwritingModel):
    evidence_artifact_id: UUID
    fact_key: StrictStr = Field(min_length=1, max_length=160)
    decision: Literal["confirmed", "rejected"]
    expected_head_id: UUID


class CompanyResearchEvidenceReviewResponse(UnderwritingModel):
    evidence_artifact: CompanyResearchEvidenceIndexArtifactResponse


class ConfirmCompanyResearchJudgmentRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)
    expected_memo_id: UUID
    expected_memo_content_hash: StrictStr = Field(pattern=SHA256_PATTERN)
    markdown: StrictStr = Field(min_length=1, max_length=100_000)


class PreviewCompanyResearchPublicationRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)


class PublishCompanyResearchRequest(UnderwritingModel):
    expected_lock_version: StrictInt = Field(ge=1)
    expected_manifest_hash: StrictStr = Field(pattern=SHA256_PATTERN)


class CompanyResearchPublicationPreparationResponse(UnderwritingModel):
    id: UUID
    status: Literal["ready_to_freeze"]
    current_step: Literal["memo"]
    progress: Literal[95]


class CompanyResearchPublicationDraftResponse(UnderwritingModel):
    id: UUID
    lock_version: StrictInt = Field(ge=1)


class CompanyResearchFrozenMemoIdentityResponse(UnderwritingModel):
    id: UUID
    content_hash: StrictStr = Field(pattern=SHA256_PATTERN)


class CompanyResearchJudgmentConfirmationResponse(UnderwritingModel):
    project_id: UUID
    preparation: CompanyResearchPublicationPreparationResponse
    draft: CompanyResearchPublicationDraftResponse
    machine_memo: CompanyResearchFrozenMemoIdentityResponse
    confirmed_memo: CompanyResearchFrozenMemoIdentityResponse
    assessment_status: Literal["not_answerable", "partially_answerable", "answerable"]
    reviewer: Literal["human:local-user"]
    markdown: StrictStr = Field(min_length=1, max_length=100_000)
    confirmed_at: datetime

    @field_validator("confirmed_at")
    @classmethod
    def aware_confirmation_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "confirmed_at")


class CompanyResearchFrozenSecurityResponse(CompanyResearchSecurityIdentityResponse):
    company_id: UUID


class CompanyResearchFrozenAssessmentResponse(UnderwritingModel):
    answerability: Literal["not_answerable", "partially_answerable", "answerable"]
    direction: (
        Literal["provisional_bullish", "provisional_neutral", "provisional_cautious"]
        | None
    )
    confidence: Literal["low", "medium", "high"] | None
    content_hash: StrictStr = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def closed_optional_fields(self):
        if self.answerability == "not_answerable" and (
            self.direction is not None or self.confidence is not None
        ):
            raise ValueError("not_answerable assessment cannot carry investment fields")
        return self


class CompanyResearchValueRangeSummaryResponse(UnderwritingModel):
    minimum: CanonicalDecimalString
    maximum: CanonicalDecimalString
    currency: Literal["CNY", "USD"]


class CompanyResearchReturnRangeSummaryResponse(UnderwritingModel):
    minimum: CanonicalDecimalString
    maximum: CanonicalDecimalString


class CompanyResearchFrozenArtifactDescriptorResponse(UnderwritingModel):
    kind: CompanyResearchArtifactKind
    id: UUID
    version: StrictInt = Field(ge=1)
    input_hash: StrictStr = Field(pattern=SHA256_PATTERN)
    content_hash: StrictStr = Field(pattern=SHA256_PATTERN)


class CompanyResearchPublicationPreviewResponse(UnderwritingModel):
    project_id: UUID
    expected_lock_version: StrictInt = Field(ge=1)
    company: CompanyResearchIdentityResponse
    securities: tuple[CompanyResearchFrozenSecurityResponse, ...] = Field(min_length=1)
    cutoff_at: datetime
    historical_basis_id: UUID
    historical_basis_content_hash: StrictStr = Field(pattern=SHA256_PATTERN)
    strategy_version: StrictStr = Field(min_length=1)
    model_version: StrictStr = Field(min_length=1)
    assessment: CompanyResearchFrozenAssessmentResponse
    value_range: CompanyResearchValueRangeSummaryResponse | None
    return_range: CompanyResearchReturnRangeSummaryResponse | None
    blockers: tuple[StrictStr, ...]
    strongest_counterevidence: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    next_verification_events: tuple[StrictStr, ...]
    memo_markdown: StrictStr = Field(min_length=1, max_length=100_000)
    artifacts: tuple[CompanyResearchFrozenArtifactDescriptorResponse, ...] = Field(
        min_length=8, max_length=9
    )
    manifest_hash: StrictStr = Field(pattern=SHA256_PATTERN)

    @field_validator("cutoff_at")
    @classmethod
    def aware_publication_cutoff(cls, value: datetime) -> datetime:
        return _require_aware(value, "cutoff_at")

    @model_validator(mode="after")
    def closed_publication_projection(self):
        if any(item.company_id != self.company.object_id for item in self.securities):
            raise ValueError("security identity is bound to the wrong company")
        if len({item.object_id for item in self.securities}) != len(self.securities):
            raise ValueError("security identities must be unique")
        kinds = tuple(item.kind for item in self.artifacts)
        required = {
            "evidence_index",
            "research_gaps",
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "judgment_context",
            "memo",
        }
        if set(kinds) not in (required, required | {"valuation_set"}):
            raise ValueError("publication artifact descriptors are incomplete")
        canonical_order = (
            "evidence_index",
            "research_gaps",
            "business_map",
            "driver_map",
            "financial_bridge",
            "scenario_set",
            "valuation_set",
            "judgment_context",
            "memo",
        )
        if kinds != tuple(kind for kind in canonical_order if kind in set(kinds)):
            raise ValueError("publication artifact descriptors are out of order")
        if len(set(kinds)) != len(kinds) or len(
            {item.id for item in self.artifacts}
        ) != len(self.artifacts):
            raise ValueError("publication artifact descriptors must be unique")
        if self.assessment.answerability == "not_answerable" and (
            self.value_range is not None or self.return_range is not None
        ):
            raise ValueError(
                "not_answerable publication cannot carry investment ranges"
            )
        return self


class CompanyResearchFrozenRevisionResponse(UnderwritingModel):
    id: UUID
    project_id: UUID
    sequence: StrictInt = Field(ge=1)
    published_at: datetime
    boundary_id: UUID
    manifest_id: UUID
    manifest_hash: StrictStr = Field(pattern=SHA256_PATTERN)
    company: CompanyResearchIdentityResponse
    securities: tuple[CompanyResearchFrozenSecurityResponse, ...] = Field(min_length=1)
    cutoff_at: datetime
    historical_basis_id: UUID
    historical_basis_content_hash: StrictStr = Field(pattern=SHA256_PATTERN)
    strategy_version: StrictStr = Field(min_length=1)
    model_version: StrictStr = Field(min_length=1)
    assessment: CompanyResearchFrozenAssessmentResponse
    value_range: CompanyResearchValueRangeSummaryResponse | None
    return_range: CompanyResearchReturnRangeSummaryResponse | None
    blockers: tuple[StrictStr, ...]
    strongest_counterevidence: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    next_verification_events: tuple[StrictStr, ...]
    memo_markdown: StrictStr = Field(min_length=1, max_length=100_000)
    artifacts: tuple[CompanyResearchFrozenArtifactDescriptorResponse, ...] = Field(
        min_length=8, max_length=9
    )
    preparation_status: Literal["completed"]
    current_step: None
    progress: Literal[100]

    @field_validator("published_at", "cutoff_at")
    @classmethod
    def aware_publication_time(cls, value: datetime) -> datetime:
        return _require_aware(value, "publication datetime")

    @model_validator(mode="after")
    def closed_frozen_projection(self):
        CompanyResearchPublicationPreviewResponse(
            project_id=self.project_id,
            expected_lock_version=1,
            company=self.company,
            securities=self.securities,
            cutoff_at=self.cutoff_at,
            historical_basis_id=self.historical_basis_id,
            historical_basis_content_hash=self.historical_basis_content_hash,
            strategy_version=self.strategy_version,
            model_version=self.model_version,
            assessment=self.assessment,
            value_range=self.value_range,
            return_range=self.return_range,
            blockers=self.blockers,
            strongest_counterevidence=self.strongest_counterevidence,
            next_verification_events=self.next_verification_events,
            memo_markdown=self.memo_markdown,
            artifacts=self.artifacts,
            manifest_hash=self.manifest_hash,
        )
        return self


class CompanyResearchMarkdownExportResponse(UnderwritingModel):
    filename: StrictStr = Field(
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*-company-research-"
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.md$"
    )
    media_type: Literal["text/markdown"]
    content: StrictStr = Field(min_length=1)
    content_hash: StrictStr = Field(pattern=SHA256_PATTERN)
