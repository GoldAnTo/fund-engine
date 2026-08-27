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
            "ready_to_freeze": "memo",
        }
        if self.status in pairs and self.current_step != pairs[self.status]:
            raise ValueError("preparation status and current_step are inconsistent")
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
_MODULE_ARTIFACT_KINDS: dict[str, str] = {
    "overview": "evidence_index",
    "business_map": "business_map",
    "operating_drivers": "driver_map",
    "evidence_and_gaps": "evidence_index",
    "industry_competition_regulation": "business_map",
    "financials_cash_flow_capital_allocation": "financial_bridge",
    "scenarios_valuation_implied_expectations": "valuation_set",
    "counterevidence_risks_next_checks": "research_gaps",
    "versions_changes_memo": "memo",
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
    provenance_role: Literal["primary", "fallback"]
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


class _CompanyResearchEvidenceFactBaseResponse(_ClosedCompanyResearchPayloadModel):
    fact_key: StrictStr = Field(min_length=1)
    company_external_key: StrictStr = Field(min_length=1)
    business_module: StrictStr = Field(min_length=1)
    metric_key: StrictStr = Field(min_length=1)
    value: CanonicalDecimalString
    value_kind: Literal["reported", "derived", "assumption"]
    currency: StrictStr = Field(min_length=1)
    unit: StrictStr = Field(min_length=1)
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
    value: CanonicalDecimalString
    currency: StrictStr = Field(min_length=1)
    unit: StrictStr = Field(min_length=1)
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
    input_state: Literal["reported", "derived", "assumption"]
    assumption_key: StrictStr | None
    equation_id: StrictStr | None
    values: tuple[CanonicalDecimalString, ...] = Field(min_length=1)
    assumption_rationale: StrictStr | None
    assumption_equation: StrictStr | None


class CompanyResearchDriverMapPayloadResponse(_ClosedCompanyResearchPayloadModel):
    drivers: tuple[CompanyResearchDriverResponse, ...] = Field(min_length=1)
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchFinancialBridgeRowResponse(_ClosedCompanyResearchPayloadModel):
    fiscal_year: StrictInt = Field(ge=1900)
    revenue: CanonicalDecimalString
    operating_income: CanonicalDecimalString
    cash_tax_rate: CanonicalDecimalString
    depreciation: CanonicalDecimalString
    capex: CanonicalDecimalString
    working_capital_change: CanonicalDecimalString
    fcff: CanonicalDecimalString
    fact_refs: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    assumption_refs: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    input_states: tuple[Literal["reported", "derived", "assumption"], ...] = Field(
        min_length=1
    )


class CompanyResearchFinancialBridgePayloadResponse(_ClosedCompanyResearchPayloadModel):
    rows: tuple[CompanyResearchFinancialBridgeRowResponse, ...] = Field(
        min_length=5, max_length=5
    )
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class CompanyResearchScenarioOverrideResponse(_ClosedCompanyResearchPayloadModel):
    driver_key: StrictStr = Field(min_length=1)
    value: CanonicalDecimalString
    state: Literal["reported", "derived", "assumption"] | None
    assumption_key: StrictStr | None
    rationale: StrictStr | None
    equation: StrictStr | None


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
    enterprise_value: CanonicalDecimalString


class CompanyResearchValueRangeResponse(_ClosedCompanyResearchPayloadModel):
    minimum: CanonicalDecimalString
    maximum: CanonicalDecimalString


class CompanyResearchSecurityValueRangeResponse(_ClosedCompanyResearchPayloadModel):
    security_external_key: StrictStr = Field(min_length=1)
    usd_per_share: CompanyResearchValueRangeResponse
    cny_return: CompanyResearchValueRangeResponse


class CompanyResearchReverseDcfResponse(_ClosedCompanyResearchPayloadModel):
    driver_key: Literal["fcff_multiplier"]
    implied_value: CanonicalDecimalString
    achieved_residual: CanonicalDecimalString
    iteration_count: StrictInt = Field(ge=1)


class CompanyResearchRequiredReturnComparisonResponse(
    _ClosedCompanyResearchPayloadModel
):
    security_external_key: StrictStr = Field(min_length=1)
    required_return: CanonicalDecimalString
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
    required_return: CanonicalDecimalString
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


class CompanyResearchMemoPayloadResponse(_ClosedCompanyResearchPayloadModel):
    assessment_status: Literal["not_answerable", "partially_answerable", "answerable"]
    business_map_ref: CompanyResearchMemoArtifactReferenceResponse
    driver_map_ref: CompanyResearchMemoArtifactReferenceResponse
    financial_bridge_ref: CompanyResearchMemoArtifactReferenceResponse
    scenario_set_ref: CompanyResearchMemoArtifactReferenceResponse
    valuation_set_ref: CompanyResearchMemoArtifactReferenceResponse | None
    gap_keys: tuple[StrictStr, ...]
    strongest_counterevidence: tuple[CompanyResearchLineageSourceReferenceResponse, ...]
    next_verification_events: tuple[StrictStr, ...]
    candidate_status: Literal["machine_draft"]
    lineage: CompanyResearchArtifactLineageResponse = Field(alias="_lineage")


class _CompanyResearchArtifactBaseResponse(UnderwritingModel):
    id: UUID
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
            (fact.source_role, fact.source_url, fact.source_locator, fact.raw_hash)
            not in parents
            for fact in self.payload.facts
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
    artifact: CompanyResearchArtifactResponse | None

    @model_validator(mode="after")
    def valid_state_artifact_pair(self):
        artifact = self.artifact.root if self.artifact is not None else None
        if artifact is not None and artifact.kind != _MODULE_ARTIFACT_KINDS[self.key]:
            raise ValueError("module artifact kind is impossible")
        if self.state in {"ready", "needs_review"}:
            if artifact is None:
                raise ValueError("ready and needs_review modules require an artifact")
            if self.state == "needs_review" and artifact.kind != "evidence_index":
                raise ValueError("only evidence can need review")
        elif artifact is not None:
            raise ValueError("non-ready modules cannot expose an artifact")
        return self


class CompanyResearchWorkspaceCompanyResponse(CompanyResearchIdentityResponse):
    id: UUID


class CompanyResearchWorkspacePreparationResponse(UnderwritingModel):
    id: UUID
    status: PreparationStatus
    current_step: str | None
    progress: StrictInt = Field(ge=0, le=100)

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
        return self


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
        for module in self.modules:
            artifact = module.artifact.root if module.artifact is not None else None
            if module.state == "needs_review" and not evidence_review:
                raise ValueError("needs_review requires the evidence review gate")
            if (
                evidence_review
                and artifact is not None
                and artifact.kind == "evidence_index"
                and module.state != "needs_review"
            ):
                raise ValueError("unreviewed evidence cannot be ready")
        return self


class ReviewCompanyEvidenceRequest(UnderwritingModel):
    evidence_artifact_id: UUID
    fact_key: StrictStr = Field(min_length=1, max_length=160)
    decision: Literal["confirmed", "rejected"]
    expected_head_id: UUID


class CompanyResearchEvidenceReviewResponse(UnderwritingModel):
    evidence_artifact: CompanyResearchEvidenceIndexArtifactResponse
