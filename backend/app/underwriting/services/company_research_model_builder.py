"""Build a closed company-research model from reviewed evidence and frozen inputs."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    SCENARIO_FINANCIAL_DRIVER_EQUATIONS,
    BusinessMapArtifact,
    BusinessModuleArtifact,
    ClassifiedBusinessEvidenceArtifact,
    CompanyResearchArtifactReference,
    CompanyResearchAssessment,
    CompanyResearchIdentitySet,
    CompanyResearchMemoArtifact,
    CompanyResearchModelInput,
    CompanyResearchProcessWarning,
    DriverMapArtifact,
    DriverMetricArtifact,
    EvidenceGapContract,
    FinancialBridgeArtifact,
    JudgmentContextArtifact,
    MarketBridgeArtifact,
    MarketEquityComponentReference,
    ModelSensitivityRequest,
    ModelInputState,
    ResearchGap,
    ResearchGapSeverity,
    ReverseDcfRequest,
    ScenarioArtifact,
    ScenarioFinancialBridge,
    ScenarioFinancialDriverForecast,
    ScenarioSetArtifact,
    SourceLineageReference,
    ValuationSetArtifact,
    canonical_decimal_string,
)
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchDriverBinding as CompanyResearchDriverBinding,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchMetricClassification as CompanyResearchMetricClassification,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchModelModule as CompanyResearchModelModule,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchModelTemplate,
    StrategyAssumptionSet,
    StrategyAssumptionValue,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchOperatingBaselineRequirement as CompanyResearchOperatingBaselineRequirement,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchOperatingDriverBinding as CompanyResearchOperatingDriverBinding,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchScenarioMechanism as CompanyResearchScenarioMechanism,
)
from app.underwriting.domain.company_research_contracts import (
    ScenarioAssumption as ScenarioAssumption,
)
from app.underwriting.domain.company_research_critical_inputs import (
    CRITICAL_DEPENDENCY_SURFACE_ORDER,
    CriticalCalculationNode,
    CriticalDependencyEdge,
    CriticalDependencyGraph,
    CriticalDependencySurface,
    CriticalInput,
    CriticalInputCandidate,
    CriticalInputKind,
    CriticalInputNode,
    CriticalInputSet,
    CriticalSurfaceNode,
    CriticalUnknownNode,
)
from app.underwriting.domain.company_research_market_contracts import (
    FrozenMarketSnapshotBinding,
    FrozenMarketSnapshotRole,
)
from app.underwriting.domain.company_research_market_contracts import (
    FrozenRawComponentReference as _FrozenRawComponentReferenceContract,
)
from app.underwriting.domain.company_research_provenance import canonical_source_refs
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_engine import CompanyResearchEngine

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_FIELDS = frozenset(
    {
        "fixture_content_hash",
        "cutoff",
        "company_external_key",
        "security_external_keys",
        "facts",
    }
)
_EVIDENCE_JUDGMENT_FIELDS = frozenset(
    {"counterevidence_fact_keys", "next_verification_events"}
)
_FACT_FIELDS = frozenset(
    {
        "fact_key",
        "company_external_key",
        "business_module",
        "metric_key",
        "value",
        "value_kind",
        "currency",
        "unit",
        "period_start",
        "period_end",
        "published_at",
        "available_at",
        "source_role",
        "source_url",
        "source_locator",
        "raw_hash",
        "review_decision",
    }
)
_DERIVED_PROVENANCE_FIELDS = frozenset({"equation_id", "parent_fact_keys"})
_GAP_PAYLOAD_FIELDS = frozenset(
    {"fixture_content_hash", "company_external_key", "gaps"}
)
_GAP_FIELDS = frozenset({"gap_key", "business_module", "reason"})
_SCENARIO_ORDER = ("base", "bull", "bear")
_CLOSED_BY_STRATEGY = frozenset({"forward_model_missing"})
_CLOSED_BY_MARKET = frozenset({"market_price_missing", "usd_cny_fx_missing"})
_BUILDER_GAP_PREFIX = "builder_generated_"
_MONETARY_DRIVER_KEYS = frozenset(
    {"revenue", "depreciation", "capex", "working_capital_change"}
)


class EvidenceBuildMode(StrEnum):
    HUMAN_REVIEWED = "human_reviewed"
    AUTHENTICATED_AI_DRAFT = "authenticated_ai_draft"


@dataclass(frozen=True, slots=True)
class CompanyResearchEvidencePartition:
    """Validated durable facts split by their eligibility for model use."""

    reviewed_facts: tuple[Mapping[str, object], ...]
    eligible_facts: tuple[Mapping[str, object], ...]
    unsupported_derived_facts: tuple[Mapping[str, object], ...]


# Compatibility export: new code imports the persistence-neutral contract seam.
FrozenRawComponentReference = _FrozenRawComponentReferenceContract


def _aware_utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _uuid(value: object, field_name: str) -> UUID:
    if type(value) is not UUID:
        raise ValidationError(f"{field_name} must be a UUID")
    return value


def _uuid_tuple(
    value: object, field_name: str, *, allow_empty: bool = False
) -> tuple[UUID, ...]:
    if (
        not isinstance(value, tuple)
        or (not value and not allow_empty)
        or not all(type(item) is UUID for item in value)
    ):
        raise ValidationError(f"{field_name} must contain UUID values")
    if len(set(value)) != len(value):
        raise ValidationError(f"{field_name} must not contain duplicates")
    if value != tuple(sorted(value, key=str)):
        raise ValidationError(f"{field_name} must use canonical UUID order")
    return value


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValidationError(f"{field_name} must be a string-keyed mapping")
    return value


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValidationError(f"{field_name} must be canonical non-empty text")
    return value


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValidationError(f"{field_name} must be a SHA-256 content hash")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValidationError(f"{field_name} must be a finite Decimal")
    return value


@dataclass(frozen=True, slots=True)
class FrozenMarketEquityComponent:
    """One market-cap component, including an explicit price-proxy policy."""

    component_key: str
    economic_units: Decimal
    price_proxy_security_external_key: str
    unit_source_ref: SourceLineageReference
    price_snapshot_id: UUID
    price_ref: SourceLineageReference
    votes_per_unit: Decimal | None = None
    conversion_to_security_external_key: str | None = None
    conversion_ratio: Decimal | None = None
    dividend_rights_per_unit: Decimal | None = None
    economic_rights_per_unit: Decimal | None = None
    legal_rights_ref: SourceLineageReference | None = None
    price_proxy_ref: SourceLineageReference | None = None
    price_proxy_policy_version: str | None = None

    def __post_init__(self) -> None:
        if self.component_key not in {
            "class_a",
            "class_b",
            "class_c",
            "listed_common",
        }:
            raise ValidationError("market equity component key is invalid")
        units = _decimal(self.economic_units, "market equity component economic_units")
        if units < Decimal("0"):
            raise ValidationError(
                "market equity component economic_units must be nonnegative"
            )
        _text(
            self.price_proxy_security_external_key,
            "market equity component price proxy",
        )
        if type(self.unit_source_ref) is not SourceLineageReference:
            raise ValidationError(
                "market equity component unit source ref must be typed"
            )
        _uuid(self.price_snapshot_id, "market equity component price_snapshot_id")
        if type(self.price_ref) is not SourceLineageReference:
            raise ValidationError("market equity component price ref must be typed")
        if self.component_key == "class_b":
            if (
                _decimal(self.votes_per_unit, "Class B votes_per_unit") != Decimal("10")
                or self.conversion_to_security_external_key != "NASDAQ:GOOGL"
                or _decimal(self.conversion_ratio, "Class B conversion_ratio")
                != Decimal("1")
                or _decimal(
                    self.dividend_rights_per_unit,
                    "Class B dividend_rights_per_unit",
                )
                != Decimal("1")
                or _decimal(
                    self.economic_rights_per_unit,
                    "Class B economic_rights_per_unit",
                )
                != Decimal("1")
                or type(self.legal_rights_ref) is not SourceLineageReference
                or type(self.price_proxy_ref) is not SourceLineageReference
                or self.price_proxy_policy_version != "alphabet_class_b_googl_proxy.v1"
            ):
                raise ValidationError(
                    "Class B legal rights and price proxy must be explicit"
                )


@dataclass(frozen=True, slots=True)
class FrozenMarketContext:
    """An exact, already-resolved market boundary; the builder never fetches it."""

    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    snapshot_ids: tuple[UUID, ...]
    market_at: datetime
    market_bridge: MarketBridgeArtifact
    snapshot_bindings: tuple[FrozenMarketSnapshotBinding, ...]
    equity_components: tuple[FrozenMarketEquityComponent, ...]
    reverse_dcf_request: ReverseDcfRequest

    @property
    def security_external_keys(self) -> tuple[str, ...]:
        return tuple(
            sorted(item.security_external_key for item in self.market_bridge.securities)
        )

    def __post_init__(self) -> None:
        price_ids = _uuid_tuple(self.price_snapshot_ids, "price_snapshot_ids")
        fx_ids = _uuid_tuple(self.fx_snapshot_ids, "fx_snapshot_ids", allow_empty=True)
        rights_ids = _uuid_tuple(self.security_rights_ids, "security_rights_ids")
        snapshot_ids = _uuid_tuple(self.snapshot_ids, "snapshot_ids")
        capital_id = _uuid(
            self.capital_structure_snapshot_id,
            "capital_structure_snapshot_id",
        )
        expected = (*price_ids, *fx_ids, capital_id, *rights_ids)
        if len(set(expected)) != len(expected):
            raise ValidationError(
                "frozen market snapshot refs must not contain duplicates"
            )
        if set(snapshot_ids) != set(expected) or len(snapshot_ids) != len(expected):
            raise ValidationError(
                "snapshot_ids must exactly cover the frozen market references"
            )
        object.__setattr__(self, "market_at", _aware_utc(self.market_at, "market_at"))
        if type(self.market_bridge) is not MarketBridgeArtifact:
            raise ValidationError(
                "frozen market context requires a typed market bridge"
            )
        security_count = len(self.market_bridge.securities)
        expected_fx_count = (
            0
            if self.market_bridge.financial_currency == self.market_bridge.base_currency
            else 1
        )
        if (
            len(price_ids) != security_count
            or len(rights_ids) != security_count
            or len(fx_ids) != expected_fx_count
        ):
            raise ValidationError(
                "frozen market context market-role counts do not match currencies"
            )
        if not isinstance(self.snapshot_bindings, tuple) or not all(
            type(item) is FrozenMarketSnapshotBinding for item in self.snapshot_bindings
        ):
            raise ValidationError("frozen market snapshot bindings must be typed")
        if tuple(item.snapshot_id for item in self.snapshot_bindings) != snapshot_ids:
            raise ValidationError(
                "snapshot bindings must exactly follow canonical snapshot_ids"
            )
        role_ids = {
            FrozenMarketSnapshotRole.PRICE: price_ids,
            FrozenMarketSnapshotRole.FX: fx_ids,
            FrozenMarketSnapshotRole.CAPITAL_STRUCTURE: (capital_id,),
            FrozenMarketSnapshotRole.SECURITY_RIGHTS: rights_ids,
        }
        if any(
            {item.snapshot_id for item in self.snapshot_bindings if item.role is role}
            != set(ids)
            for role, ids in role_ids.items()
        ):
            raise ValidationError(
                "snapshot bindings must exactly cover each market role"
            )
        bindings = {
            (item.role, item.security_external_key): item.source_ref
            for item in self.snapshot_bindings
        }
        bridge = self.market_bridge
        expected_refs = {
            (
                FrozenMarketSnapshotRole.CAPITAL_STRUCTURE,
                None,
            ): bridge.capital_structure.source_ref,
            **(
                {(FrozenMarketSnapshotRole.FX, None): bridge.fx_ref}
                if bridge.fx_ref is not None
                else {}
            ),
            **{
                (
                    FrozenMarketSnapshotRole.PRICE,
                    item.security_external_key,
                ): item.price_ref
                for item in bridge.securities
            },
            **{
                (
                    FrozenMarketSnapshotRole.SECURITY_RIGHTS,
                    item.security_external_key,
                ): item.rights_ref
                for item in bridge.securities
            },
        }
        if bindings != expected_refs:
            raise ValidationError(
                "frozen market snapshot binding bridge refs do not match"
            )
        if (
            not isinstance(self.equity_components, tuple)
            or not all(
                type(item) is FrozenMarketEquityComponent
                for item in self.equity_components
            )
            or not self.equity_components
            or len({item.component_key for item in self.equity_components})
            != len(self.equity_components)
        ):
            raise ValidationError(
                "frozen market context requires unique typed equity components"
            )
        securities = {
            item.security_external_key: item for item in self.market_bridge.securities
        }
        prices = {
            item.security_external_key: item
            for item in self.snapshot_bindings
            if item.role is FrozenMarketSnapshotRole.PRICE
        }
        if set(securities) == {"NASDAQ:GOOG", "NASDAQ:GOOGL"}:
            class_a, class_b, class_c = self.equity_components
            expected_proxy = {
                "class_a": "NASDAQ:GOOGL",
                "class_b": "NASDAQ:GOOGL",
                "class_c": "NASDAQ:GOOG",
            }
            if any(
                item.price_proxy_security_external_key
                != expected_proxy[item.component_key]
                for item in self.equity_components
            ):
                raise ValidationError(
                    "Class B price proxy policy must explicitly use GOOGL"
                )
            if (
                class_a.economic_units
                != securities["NASDAQ:GOOGL"].listed_class_economic_units
                or class_c.economic_units
                != securities["NASDAQ:GOOG"].listed_class_economic_units
                or class_a.unit_source_ref != securities["NASDAQ:GOOGL"].rights_ref
                or class_c.unit_source_ref != securities["NASDAQ:GOOG"].rights_ref
            ):
                raise ValidationError(
                    "Class A/C units must match listed rights and Class B must remain separate"
                )
            expected_b = (
                self.market_bridge.capital_structure.basic_shares
                - class_a.economic_units
                - class_c.economic_units
            )
            if expected_b < Decimal("0") or class_b.economic_units != expected_b:
                raise ValidationError(
                    "Class B units must equal basic shares less listed Class A and C units"
                )
        elif set(securities) == {"SZSE:300750"}:
            if (
                len(self.equity_components) != 1
                or self.equity_components[0].component_key != "listed_common"
                or self.equity_components[0].economic_units
                != self.market_bridge.capital_structure.basic_shares
                or self.equity_components[0].unit_source_ref
                != securities["SZSE:300750"].rights_ref
            ):
                raise ValidationError(
                    "CATL listed common units must match basic shares"
                )
        else:
            raise ValidationError("frozen market context company is unsupported")
        for component in self.equity_components:
            price_binding = prices.get(component.price_proxy_security_external_key)
            if (
                price_binding is None
                or component.price_snapshot_id != price_binding.snapshot_id
                or component.price_ref != price_binding.source_ref
            ):
                raise ValidationError(
                    "market equity component must use its exact frozen price proxy"
                )
        if type(self.reverse_dcf_request) is not ReverseDcfRequest:
            raise ValidationError("frozen market context requires governed reverse DCF")
        market_prices = {
            item.security_external_key: item.market_price
            for item in self.market_bridge.securities
        }
        capital = self.market_bridge.capital_structure
        with localcontext() as context:
            context.prec = 60
            expected_enterprise_value = +(
                sum(
                    (
                        item.economic_units
                        * market_prices[item.price_proxy_security_external_key]
                        for item in self.equity_components
                    ),
                    start=Decimal("0"),
                )
                + capital.debt
                + capital.minority_interest
                + capital.pension_liabilities
                + capital.other_adjustments
                - capital.cash
                - capital.investments
            )
        if (
            self.reverse_dcf_request.target_enterprise_value
            != expected_enterprise_value
        ):
            raise ValidationError(
                "reverse DCF target must close to exact Class A-B-C market equity"
            )


@dataclass(frozen=True, slots=True)
class CompanyResearchBuildInput:
    project_id: UUID
    identity_set: CompanyResearchIdentitySet
    cutoff_at: datetime
    required_return: Decimal
    evidence_artifact_id: UUID
    evidence_content_hash: str
    evidence_payload: Mapping[str, object]
    gap_payload: Mapping[str, object]
    source_refs: tuple[dict[str, str], ...]
    model_template: CompanyResearchModelTemplate
    strategy_assumptions: StrategyAssumptionSet
    market_context: FrozenMarketContext | None
    evidence_build_mode: EvidenceBuildMode = EvidenceBuildMode.HUMAN_REVIEWED
    critical_input_replacements: tuple[CriticalInputCandidate, ...] = ()
    critical_input_unknowns: tuple[CriticalInput, ...] = ()
    critical_inputs_artifact_id: UUID | None = None
    critical_inputs_content_hash: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.project_id, "project_id")
        if type(self.identity_set) is not CompanyResearchIdentitySet:
            raise ValidationError("company research identity set is required")
        _uuid(self.evidence_artifact_id, "evidence_artifact_id")
        object.__setattr__(self, "cutoff_at", _aware_utc(self.cutoff_at, "cutoff_at"))
        required_return = _decimal(self.required_return, "required_return")
        if required_return <= Decimal("0"):
            raise ValidationError("required_return must be positive")
        _hash(self.evidence_content_hash, "evidence_content_hash")
        if canonical_hash(self.evidence_payload) != self.evidence_content_hash:
            raise ValidationError("evidence content hash does not match payload")
        _mapping(self.evidence_payload, "evidence_payload")
        _mapping(self.gap_payload, "gap_payload")
        self._validate_source_refs(self.source_refs)
        if type(self.model_template) is not CompanyResearchModelTemplate:
            raise ValidationError("company model template is required")
        if type(self.strategy_assumptions) is not StrategyAssumptionSet:
            raise ValidationError("strategy assumptions are required")
        if (
            self.market_context is not None
            and type(self.market_context) is not FrozenMarketContext
        ):
            raise ValidationError("market_context must be a frozen market context")
        if type(self.evidence_build_mode) is not EvidenceBuildMode:
            raise ValidationError("evidence_build_mode must be controlled")
        if (
            not isinstance(self.critical_input_replacements, tuple)
            or not all(
                type(item) is CriticalInputCandidate
                and item.kind is CriticalInputKind.USER_ASSUMPTION
                for item in self.critical_input_replacements
            )
            or len({item.key for item in self.critical_input_replacements})
            != len(self.critical_input_replacements)
        ):
            raise ValidationError("critical input replacements must be canonical")
        if self.critical_input_replacements != tuple(
            sorted(self.critical_input_replacements, key=lambda item: item.key)
        ):
            raise ValidationError("critical input replacements must be canonical")
        if (
            not isinstance(self.critical_input_unknowns, tuple)
            or not all(
                type(item) is CriticalInput for item in self.critical_input_unknowns
            )
            or self.critical_input_unknowns
            != tuple(sorted(self.critical_input_unknowns, key=lambda item: item.key))
            or len({item.key for item in self.critical_input_unknowns})
            != len(self.critical_input_unknowns)
        ):
            raise ValidationError("critical input unknown overrides must be canonical")
        critical_boundary = (
            self.critical_inputs_artifact_id,
            self.critical_inputs_content_hash,
        )
        if any(value is not None for value in critical_boundary):
            if (
                type(self.critical_inputs_artifact_id) is not UUID
                or not isinstance(self.critical_inputs_content_hash, str)
                or _SHA256.fullmatch(self.critical_inputs_content_hash) is None
            ):
                raise ValidationError("critical input build boundary is invalid")
        if (
            self.critical_input_replacements or self.critical_input_unknowns
        ) and not all(value is not None for value in critical_boundary):
            raise ValidationError(
                "critical input overlays require an exact decision boundary"
            )
        if (
            self.market_context is not None
            and self.market_context.market_at > self.cutoff_at
        ):
            raise ValidationError("frozen market context must not be after cutoff_at")
        # Re-run the payload validation here and again in build(), because a
        # Mapping can be mutated by its owner despite this frozen outer value.
        _validate_evidence_payload(
            self.evidence_payload,
            self.cutoff_at,
            mode=self.evidence_build_mode,
        )
        _validate_gap_payload(self.gap_payload, self.evidence_payload)
        self._validate_identity_bindings()

    def _validate_identity_bindings(self) -> None:
        company_key = self.identity_set.company.external_key
        security_keys = tuple(
            security.external_key for security in self.identity_set.securities
        )
        if self.evidence_payload.get("company_external_key") != company_key:
            raise ValidationError(
                "evidence company identity does not match target identity"
            )
        if (
            tuple(self.evidence_payload.get("security_external_keys", ()))
            != security_keys
        ):
            raise ValidationError(
                "evidence security identity does not match target identity"
            )
        if self.gap_payload.get("company_external_key") != company_key:
            raise ValidationError("gap company identity does not match target identity")
        if (
            self.model_template.company_external_key != company_key
            or self.model_template.security_external_keys != security_keys
        ):
            raise ValidationError(
                "company model template identity does not match target identity"
            )
        if self.market_context is not None:
            market_keys = tuple(
                sorted(
                    security.security_external_key
                    for security in self.market_context.market_bridge.securities
                )
            )
            if market_keys != security_keys:
                raise ValidationError("market securities do not match target identity")

    @staticmethod
    def _validate_source_refs(value: object) -> None:
        if not isinstance(value, tuple) or not value:
            raise ValidationError("source_refs must be a non-empty tuple")
        if len(canonical_source_refs(value)) != len(value):
            raise ValidationError("source_refs must not contain duplicates")


@dataclass(frozen=True, slots=True)
class CompanyResearchBuildResult:
    business_map: BusinessMapArtifact
    driver_map: DriverMapArtifact
    financial_bridge: FinancialBridgeArtifact
    scenario_set: ScenarioSetArtifact
    valuation_set: ValuationSetArtifact | None
    judgment_context: JudgmentContextArtifact
    memo: CompanyResearchMemoArtifact
    assessment: CompanyResearchAssessment
    gaps: tuple[ResearchGap, ...]
    market_snapshot_ids: tuple[UUID, ...]
    critical_input_dependency_graph: CriticalDependencyGraph
    process_warnings: tuple[CompanyResearchProcessWarning, ...] = ()


def _parse_timestamp(value: object, field_name: str) -> datetime:
    text = _text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be an ISO-8601 timestamp") from exc
    return _aware_utc(parsed, field_name)


def _model_fact_decimal(fact: Mapping[str, object]) -> Decimal:
    raw = fact.get("value")
    if not isinstance(raw, str):
        raise ValidationError("model fact value must be a canonical Decimal string")
    try:
        parsed = Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError(
            "model fact value must be a canonical Decimal string"
        ) from exc
    if not parsed.is_finite() or canonical_decimal_string(parsed) != raw:
        raise ValidationError(
            "model fact value must be a canonical finite Decimal string"
        )
    return parsed


def _model_fact_period(fact: Mapping[str, object]) -> tuple[str, str]:
    values: list[str] = []
    parsed: list[date] = []
    for name in ("period_start", "period_end"):
        raw = fact.get(name)
        if not isinstance(raw, str) or not raw:
            raise ValidationError("model fact period must be complete")
        try:
            item = date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationError("model fact period must be valid") from exc
        if item.isoformat() != raw:
            raise ValidationError("model fact period must be canonical")
        values.append(raw)
        parsed.append(item)
    if parsed[0] > parsed[1]:
        raise ValidationError("model fact period must be ordered")
    return values[0], values[1]


def _validate_evidence_payload(
    payload_value: object,
    cutoff_at: datetime,
    *,
    mode: EvidenceBuildMode = EvidenceBuildMode.HUMAN_REVIEWED,
) -> CompanyResearchEvidencePartition:
    if type(mode) is not EvidenceBuildMode:
        raise ValidationError("evidence_build_mode must be controlled")
    payload = _mapping(payload_value, "evidence_payload")
    if set(payload) not in {
        _EVIDENCE_FIELDS,
        _EVIDENCE_FIELDS | _EVIDENCE_JUDGMENT_FIELDS,
    }:
        raise ValidationError("evidence fields are not recognized")
    _hash(payload.get("fixture_content_hash"), "evidence fixture_content_hash")
    evidence_cutoff = _parse_timestamp(payload.get("cutoff"), "evidence cutoff")
    if evidence_cutoff > cutoff_at:
        raise ValidationError("evidence cutoff must not be after the model cutoff")
    company_key = _text(payload.get("company_external_key"), "evidence company key")
    securities = payload.get("security_external_keys")
    if (
        not isinstance(securities, list)
        or not securities
        or not all(isinstance(item, str) and item for item in securities)
        or securities != sorted(set(securities))
    ):
        raise ValidationError("evidence security keys must be unique and canonical")
    facts = payload.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ValidationError("evidence facts must be a non-empty array")
    counterevidence: list[str] = []
    if _EVIDENCE_JUDGMENT_FIELDS.issubset(payload):
        counterevidence = payload.get("counterevidence_fact_keys")
        verification = payload.get("next_verification_events")
        if (
            not isinstance(counterevidence, list)
            or not counterevidence
            or not all(isinstance(item, str) and item for item in counterevidence)
            or len(set(counterevidence)) != len(counterevidence)
            or not isinstance(verification, list)
            or not verification
            or not all(
                isinstance(item, str) and item and item == item.strip()
                for item in verification
            )
        ):
            raise ValidationError("evidence judgment context is invalid")
    reviewed: list[Mapping[str, object]] = []
    keys: set[str] = set()
    for raw in facts:
        fact = _mapping(raw, "evidence fact")
        has_review_decision = "review_decision" in fact
        if mode is EvidenceBuildMode.HUMAN_REVIEWED and not has_review_decision:
            raise ValidationError("evidence must be reviewed before model input")
        base_fields = (
            _FACT_FIELDS if has_review_decision else _FACT_FIELDS - {"review_decision"}
        )
        fact_fields = set(fact)
        if fact.get("value_kind") == "derived":
            allowed_fields = base_fields | _DERIVED_PROVENANCE_FIELDS
            if not base_fields.issubset(fact_fields) or not fact_fields.issubset(
                allowed_fields
            ):
                raise ValidationError("evidence fields are not recognized")
            provenance_fields = fact_fields & _DERIVED_PROVENANCE_FIELDS
            if (
                mode is EvidenceBuildMode.HUMAN_REVIEWED
                and provenance_fields
                and provenance_fields != _DERIVED_PROVENANCE_FIELDS
            ):
                raise ValidationError("derived fact provenance must be complete")
        elif fact_fields != base_fields:
            raise ValidationError("evidence fields are not recognized")
        fact_key = _text(fact.get("fact_key"), "evidence fact_key")
        if fact_key in keys:
            raise ValidationError("evidence facts must not contain duplicate refs")
        keys.add(fact_key)
        if fact.get("company_external_key") != company_key:
            raise ValidationError(
                "evidence fact company does not match evidence payload"
            )
        for field in (
            "business_module",
            "metric_key",
            "value_kind",
            "source_role",
            "source_url",
            "source_locator",
        ):
            _text(fact.get(field), f"evidence fact {field}")
        _hash(fact.get("raw_hash"), "evidence fact raw_hash")
        available_at = _parse_timestamp(
            fact.get("available_at"), "evidence fact available_at"
        )
        published_at = _parse_timestamp(
            fact.get("published_at"), "evidence fact published_at"
        )
        if published_at > available_at:
            raise ValidationError(
                "evidence fact published_at must not be after available_at"
            )
        if available_at > cutoff_at:
            raise ValidationError("evidence fact is unavailable at model cutoff")
        review = fact.get("review_decision")
        if has_review_decision and (
            not isinstance(review, str) or review not in {"confirmed", "rejected"}
        ):
            raise ValidationError("evidence must be reviewed before model input")
        _model_fact_decimal(fact)
        if fact.get("value_kind") not in {
            "reported",
            "derived",
            "management_guidance",
        }:
            raise ValidationError("model fact value_kind is unsupported")
        if (
            mode is EvidenceBuildMode.HUMAN_REVIEWED
            and _DERIVED_PROVENANCE_FIELDS.issubset(fact)
        ):
            _text(fact.get("equation_id"), "derived fact equation_id")
            parent_fact_keys = fact.get("parent_fact_keys")
            if not _canonical_parent_fact_keys(parent_fact_keys):
                raise ValidationError(
                    "derived fact parent_fact_keys must be unique and canonical"
                )
        if fact.get("currency") is not None:
            _text(fact.get("currency"), "model fact currency")
        _text(fact.get("unit"), "model fact unit")
        _model_fact_period(fact)
        reviewed.append(fact)
    if not set(counterevidence).issubset(keys):
        raise ValidationError("evidence counterevidence facts are unavailable")
    accepted = tuple(
        fact
        for fact in reviewed
        if fact.get("review_decision") != "rejected"
        and (
            mode is EvidenceBuildMode.AUTHENTICATED_AI_DRAFT
            or fact.get("review_decision") == "confirmed"
        )
    )
    if mode is EvidenceBuildMode.HUMAN_REVIEWED:
        eligible = accepted
        unsupported_derived: tuple[Mapping[str, object], ...] = ()
    else:
        accepted_by_key = {str(fact["fact_key"]): fact for fact in accepted}
        eligibility: dict[str, bool] = {}
        visiting: set[str] = set()

        def is_eligible(fact: Mapping[str, object]) -> bool:
            fact_key = str(fact["fact_key"])
            if fact_key in eligibility:
                return eligibility[fact_key]
            if fact["value_kind"] != "derived":
                eligibility[fact_key] = True
                return True
            if fact_key in visiting:
                eligibility[fact_key] = False
                return False
            equation_id = fact.get("equation_id")
            parent_fact_keys = fact.get("parent_fact_keys")
            if (
                not isinstance(equation_id, str)
                or not equation_id
                or equation_id != equation_id.strip()
                or not _canonical_parent_fact_keys(parent_fact_keys)
            ):
                eligibility[fact_key] = False
                return False
            assert isinstance(parent_fact_keys, list)
            visiting.add(fact_key)
            supported = all(
                (parent := accepted_by_key.get(parent_key)) is not None
                and is_eligible(parent)
                for parent_key in parent_fact_keys
            )
            visiting.discard(fact_key)
            eligibility[fact_key] = supported
            return supported

        eligible = tuple(fact for fact in accepted if is_eligible(fact))
        unsupported_derived = tuple(
            fact
            for fact in accepted
            if fact["value_kind"] == "derived" and not is_eligible(fact)
        )
    for metric_key in {str(fact["metric_key"]) for fact in eligible}:
        currencies = {
            str(fact["currency"])
            for fact in eligible
            if fact["metric_key"] == metric_key
        }
        units = {
            str(fact["unit"]) for fact in eligible if fact["metric_key"] == metric_key
        }
        if len(currencies) != 1 or len(units) != 1:
            raise ValidationError(
                "model fact currency and unit must be consistent per metric"
            )
    return CompanyResearchEvidencePartition(
        reviewed_facts=tuple(reviewed),
        eligible_facts=eligible,
        unsupported_derived_facts=unsupported_derived,
    )


def _canonical_parent_fact_keys(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, str) and bool(item) and item == item.strip()
            for item in value
        )
        and value == sorted(set(value))
    )


def partition_company_research_evidence(
    payload_value: object,
    cutoff_at: datetime,
    *,
    mode: EvidenceBuildMode,
) -> CompanyResearchEvidencePartition:
    """Validate evidence and quarantine unsupported derived facts by build mode."""
    return _validate_evidence_payload(payload_value, cutoff_at, mode=mode)


def validate_company_research_evidence_payload_for_read(
    payload_value: object,
) -> None:
    """Validate a durable source/review payload without requiring all reviews."""
    payload = _mapping(payload_value, "evidence_payload")
    cutoff = _parse_timestamp(payload.get("cutoff"), "evidence cutoff")
    facts = payload.get("facts")
    if not isinstance(facts, list):
        raise ValidationError("evidence facts must be an array")
    normalized = dict(payload)
    normalized_facts: list[object] = []
    for value in facts:
        if not isinstance(value, Mapping):
            normalized_facts.append(value)
            continue
        fact = dict(value)
        fact.setdefault("review_decision", "confirmed")
        normalized_facts.append(fact)
    normalized["facts"] = normalized_facts
    _validate_evidence_payload(normalized, cutoff)


def _validate_gap_payload(
    payload_value: object, evidence_payload: Mapping[str, object]
) -> tuple[Mapping[str, object], ...]:
    payload = _mapping(payload_value, "gap_payload")
    if set(payload) != _GAP_PAYLOAD_FIELDS:
        raise ValidationError("gap payload fields are not recognized")
    if payload.get("fixture_content_hash") != evidence_payload.get(
        "fixture_content_hash"
    ) or payload.get("company_external_key") != evidence_payload.get(
        "company_external_key"
    ):
        raise ValidationError("gap payload does not match evidence identity")
    gaps = payload.get("gaps")
    if not isinstance(gaps, list):
        raise ValidationError("gap payload gaps must be an array")
    result: list[Mapping[str, object]] = []
    keys: set[str] = set()
    for raw in gaps:
        gap = _mapping(raw, "gap payload item")
        if set(gap) != _GAP_FIELDS:
            raise ValidationError("gap payload fields are not recognized")
        key = _text(gap.get("gap_key"), "gap payload gap_key")
        if key.startswith(_BUILDER_GAP_PREFIX):
            raise ValidationError(
                "gap payload cannot use the reserved builder-generated namespace"
            )
        if key in keys:
            raise ValidationError("gap payload must not contain duplicate refs")
        keys.add(key)
        _text(gap.get("business_module"), "gap payload business_module")
        _text(gap.get("reason"), "gap payload reason")
        result.append(gap)
    return tuple(result)


class CompanyResearchModelBuilder:
    """Convert reviewed payloads into typed artifacts and call the pure engine."""

    def __init__(self) -> None:
        self._engine = CompanyResearchEngine()

    @classmethod
    def validate_governed_gap_projection(
        cls,
        value: CompanyResearchBuildInput,
        gaps: tuple[ResearchGap, ...],
    ) -> None:
        """Require model-derived gaps to preserve every still-open source gap."""
        if type(value) is not CompanyResearchBuildInput:
            raise ValidationError("company research builder input must be typed")
        if not isinstance(gaps, tuple) or not all(
            type(gap) is ResearchGap for gap in gaps
        ):
            raise ValidationError("company research model gaps must be typed")
        _validate_evidence_payload(
            value.evidence_payload,
            value.cutoff_at,
            mode=value.evidence_build_mode,
        )
        raw_gaps = _validate_gap_payload(value.gap_payload, value.evidence_payload)
        expected_source_gaps = cls._active_gaps(
            raw_gaps,
            None if value.critical_input_unknowns else value.market_context,
            (),
        )
        source_codes = {str(item["gap_key"]) for item in raw_gaps}
        actual_source_gaps = tuple(
            sorted(
                (gap for gap in gaps if gap.code in source_codes),
                key=lambda gap: gap.code,
            )
        )
        if actual_source_gaps != expected_source_gaps:
            raise ValidationError(
                "company research model gaps conflict with governed source gaps"
            )

    def build(
        self,
        value: CompanyResearchBuildInput,
        *,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> CompanyResearchBuildResult:
        if type(value) is not CompanyResearchBuildInput:
            raise ValidationError("company research builder input must be typed")
        value = self.apply_critical_input_overlays(value)
        model_market_context = (
            None if value.critical_input_unknowns else value.market_context
        )
        evidence = _validate_evidence_payload(
            value.evidence_payload,
            value.cutoff_at,
            mode=value.evidence_build_mode,
        )
        raw_gaps = _validate_gap_payload(value.gap_payload, value.evidence_payload)
        value._validate_source_refs(value.source_refs)
        value._validate_identity_bindings()
        if canonical_hash(value.evidence_payload) != value.evidence_content_hash:
            raise ValidationError("evidence content hash does not match payload")
        self._validate_authenticated_sources(value.source_refs, evidence.reviewed_facts)
        self._validate_template_evidence(
            value.model_template, evidence.reviewed_facts, raw_gaps
        )
        if progress_callback is not None:
            progress_callback(35, "analyzing_company")
        eligible = self._evidence_with_overlays(
            evidence.eligible_facts,
            value.critical_input_replacements,
            value.critical_input_unknowns,
            evidence_artifact_id=value.evidence_artifact_id,
            critical_inputs_artifact_id=value.critical_inputs_artifact_id,
            critical_inputs_content_hash=value.critical_inputs_content_hash,
        )
        unsupported_derived = evidence.unsupported_derived_facts
        self._validate_scenario_mapping(
            value.model_template, value.strategy_assumptions
        )
        evidence_refs = self._evidence_refs(eligible)
        unsupported_gaps = self._unsupported_derived_gaps(
            value.model_template,
            eligible,
            unsupported_derived,
        )
        unsupported_pairs = {
            (str(fact["business_module"]), str(fact["metric_key"]))
            for fact in unsupported_derived
        }
        eligible_pairs = {
            (str(fact["business_module"]), str(fact["metric_key"])) for fact in eligible
        }
        required_baseline_pairs = {
            (item.module_key, item.metric_key)
            for item in value.model_template.operating_baseline_requirements
        }
        missing_baseline_pairs = required_baseline_pairs - eligible_pairs
        baseline_gaps = self._baseline_gaps(
            value.model_template,
            eligible,
            unavailable_pairs=unsupported_pairs,
        )
        active_gaps = self._active_gaps(
            raw_gaps,
            model_market_context,
            (*baseline_gaps, *unsupported_gaps),
        )
        fact_modules = {
            f"fact:{fact['fact_key']}": str(fact["business_module"])
            for fact in evidence.reviewed_facts
        }
        active_gaps = tuple(
            sorted(
                (
                    *active_gaps,
                    *(
                        ResearchGap(
                            code=self._marked_unknown_gap_key(item),
                            module_key=fact_modules.get(
                                item.key,
                                value.model_template.modules[0].module_key,
                            ),
                            severity=ResearchGapSeverity.CRITICAL,
                            message=(
                                f"User marked critical input {item.key} unknown; "
                                "a governed replacement or source is required."
                            ),
                        )
                        for item in value.critical_input_unknowns
                    ),
                ),
                key=lambda item: item.code,
            )
        )
        active_gaps = tuple(
            sorted(
                (
                    *active_gaps,
                    *self._operating_driver_gaps(
                        value.model_template,
                        eligible,
                        unavailable_pairs=unsupported_pairs,
                    ),
                ),
                key=lambda item: item.code,
            )
        )
        active_gaps = self._complete_module_gaps(
            value.model_template, eligible, active_gaps
        )
        business_map = self._business_map(
            value.model_template, eligible, active_gaps, evidence_refs
        )
        assumption_refs = self._assumption_refs(value.strategy_assumptions)
        driver_map = self._driver_map(
            value.model_template,
            eligible,
            evidence_refs,
            value.strategy_assumptions,
            assumption_refs,
        )
        scenario_set, scenario_bridges = self._scenario_inputs(
            value.strategy_assumptions, assumption_refs
        )
        gap_ref = SourceLineageReference(
            fact_key="evidence_gap_contract",
            source_role="reviewed_evidence_gap_contract",
            source_url=f"urn:company-research:evidence:{value.evidence_artifact_id}",
            source_locator="active_research_gaps",
            raw_hash=canonical_hash(value.gap_payload),
        )
        evidence_gap_contract = EvidenceGapContract(
            source_ref=gap_ref,
            gaps=active_gaps,
        )
        evidence_refs_by_key = {item.fact_key: item for item in evidence_refs}
        counterevidence_keys = value.evidence_payload.get(
            "counterevidence_fact_keys", []
        )
        verification_events = value.evidence_payload.get(
            "next_verification_events", []
        )
        judgment = JudgmentContextArtifact(
            operating_baseline_available=not missing_baseline_pairs,
            financial_bridge_closed=True,
            market_security_bridge_available=model_market_context is not None,
            strongest_counterevidence=tuple(
                evidence_refs_by_key[str(key)] for key in counterevidence_keys
            ),
            next_verification_events=(
                tuple(str(item) for item in verification_events)
                if verification_events
                else tuple(gap.message for gap in active_gaps)
            ),
        )
        source_lineage = self._source_lineage(
            evidence_refs,
            assumption_refs,
            gap_ref,
            value.market_context,
        )
        typed = CompanyResearchModelInput(
            business_map=business_map,
            driver_map=driver_map,
            scenario_set=scenario_set,
            scenario_bridges=scenario_bridges,
            source_lineage=source_lineage,
            research_gaps=active_gaps,
            evidence_gap_contract=evidence_gap_contract,
            required_return=value.required_return,
            terminal_growth=value.strategy_assumptions.terminal_growth.value,
            market_bridge=(
                model_market_context.market_bridge
                if model_market_context is not None
                else None
            ),
            judgment_context=judgment,
            reverse_dcf=(
                model_market_context.reverse_dcf_request
                if model_market_context is not None
                else None
            ),
            equity_components=(
                tuple(
                    MarketEquityComponentReference(
                        component_key=item.component_key,
                        economic_units=item.economic_units,
                        price_proxy_security_external_key=item.price_proxy_security_external_key,
                        unit_source_ref=item.unit_source_ref,
                        price_ref=item.price_ref,
                        votes_per_unit=item.votes_per_unit,
                        conversion_to_security_external_key=item.conversion_to_security_external_key,
                        conversion_ratio=item.conversion_ratio,
                        dividend_rights_per_unit=item.dividend_rights_per_unit,
                        economic_rights_per_unit=item.economic_rights_per_unit,
                        legal_rights_ref=item.legal_rights_ref,
                        price_proxy_ref=item.price_proxy_ref,
                        price_proxy_policy_version=item.price_proxy_policy_version,
                    )
                    for item in model_market_context.equity_components
                )
                if model_market_context is not None
                else ()
            ),
            sensitivity_requests=tuple(
                ModelSensitivityRequest(
                    variable_key=item.variable_key,
                    low_value=item.low.value,
                    high_value=item.high.value,
                    low_assumption_key=item.low.assumption_key,
                    high_assumption_key=item.high.assumption_key,
                )
                for item in value.strategy_assumptions.sensitivity_assumptions
            ),
        )
        compiled = self._engine.compile(typed)
        if progress_callback is not None:
            progress_callback(60, "building_forecast")
        financial_bridge = compiled.financial_bridges["base"]
        memo = self._memo(
            business_map=business_map,
            driver_map=driver_map,
            financial_bridge=financial_bridge,
            scenario_set=scenario_set,
            valuation_set=compiled.valuation_set,
            judgment=judgment,
            gaps=active_gaps,
            assessment=compiled.assessment,
        )
        dependency_graph = self._critical_dependency_graph(
            value=value,
            accepted_facts=eligible,
            financial_bridge=financial_bridge,
            valuation_set=compiled.valuation_set,
            gaps=active_gaps,
            judgment=judgment,
        )
        return CompanyResearchBuildResult(
            business_map=business_map,
            driver_map=driver_map,
            financial_bridge=financial_bridge,
            scenario_set=scenario_set,
            valuation_set=compiled.valuation_set,
            judgment_context=judgment,
            memo=memo,
            assessment=compiled.assessment,
            gaps=active_gaps,
            market_snapshot_ids=(
                value.market_context.snapshot_ids
                if value.market_context is not None
                else ()
            ),
            critical_input_dependency_graph=dependency_graph,
        )

    @staticmethod
    def _critical_dependency_graph(
        *,
        value: CompanyResearchBuildInput,
        accepted_facts: tuple[Mapping[str, object], ...],
        financial_bridge: FinancialBridgeArtifact,
        valuation_set: ValuationSetArtifact | None,
        gaps: tuple[ResearchGap, ...],
        judgment: JudgmentContextArtifact,
    ) -> CriticalDependencyGraph:
        """Record exact model dependencies while all typed inputs are still available."""

        input_nodes: list[CriticalInputNode] = []
        calculations: list[CriticalCalculationNode] = []
        unknowns: list[CriticalUnknownNode] = []
        edges: set[tuple[str, str]] = set()
        replacements = {item.key: item for item in value.critical_input_replacements}
        unknown_overrides = {item.key: item for item in value.critical_input_unknowns}

        def add_input(candidate: CriticalInputCandidate) -> str:
            if candidate.key in unknown_overrides:
                return candidate.key
            candidate = replacements.get(candidate.key, candidate)
            input_nodes.append(CriticalInputNode(candidate=candidate))
            return candidate.key

        def add_calculation(
            key: str,
            *,
            calculated_value: Decimal | str,
            equation_id: str,
            parents: tuple[str, ...],
        ) -> str:
            canonical_parents = tuple(sorted(parents))
            calculations.append(
                CriticalCalculationNode(
                    candidate=CriticalInputCandidate(
                        key=key,
                        kind=CriticalInputKind.DERIVED_CALCULATION,
                        value=calculated_value,
                        equation_id=equation_id,
                        parent_input_keys=canonical_parents,
                    )
                )
            )
            edges.update((parent, key) for parent in canonical_parents)
            return key

        def add_unknown(gap: ResearchGap, *surfaces: CriticalDependencySurface) -> str:
            key = f"unknown:{gap.code}"
            if key not in {node.key for node in unknowns}:
                unknowns.append(
                    CriticalUnknownNode(
                        candidate=CriticalInputCandidate(
                            key=key,
                            kind=CriticalInputKind.UNKNOWN,
                            value=None,
                            unknown_reason=gap.message,
                            gap_key=gap.code,
                        )
                    )
                )
            edges.update((key, f"surface:{surface.value}") for surface in surfaces)
            return key

        facts_by_key = {str(fact["fact_key"]): fact for fact in accepted_facts}
        fact_dependency_keys: dict[str, str] = {}
        resolving_fact_keys: set[str] = set()

        def unsupported_derived_fact(fact_key: str) -> str:
            gap = ResearchGap(
                code=f"derived_provenance_{fact_key}",
                module_key="historical_financial_baseline",
                severity=ResearchGapSeverity.CRITICAL,
                message=(
                    "Derived evidence cannot support this surface without an exact "
                    "equation and complete authenticated parent facts."
                ),
            )
            return add_unknown(gap)

        def fact_dependency(fact: Mapping[str, object]) -> str:
            raw_fact_key = str(fact["fact_key"])
            existing = fact_dependency_keys.get(raw_fact_key)
            if existing is not None:
                return existing
            if raw_fact_key in resolving_fact_keys:
                result = unsupported_derived_fact(raw_fact_key)
                fact_dependency_keys[raw_fact_key] = result
                return result
            resolving_fact_keys.add(raw_fact_key)
            key = f"fact:{raw_fact_key}"
            if fact["value_kind"] == "derived":
                equation_id = fact.get("equation_id")
                raw_parent_keys = fact.get("parent_fact_keys")
                if value.evidence_build_mode is EvidenceBuildMode.HUMAN_REVIEWED and (
                    not isinstance(equation_id, str)
                    or not isinstance(raw_parent_keys, list)
                ):
                    source_ref = SourceLineageReference(
                        fact_key=raw_fact_key,
                        source_role=str(fact["source_role"]),
                        source_url=str(fact["source_url"]),
                        source_locator=str(fact["source_locator"]),
                        raw_hash=str(fact["raw_hash"]),
                    )
                    result = add_input(
                        CriticalInputCandidate(
                            key=key,
                            kind=CriticalInputKind.SOURCE_FACT,
                            value=_model_fact_decimal(fact),
                            period=f"{fact['period_start']}/{fact['period_end']}",
                            unit=str(fact["unit"]),
                            currency=(
                                str(fact["currency"])
                                if fact["currency"] is not None
                                else None
                            ),
                            source_ref=source_ref,
                        )
                    )
                elif not isinstance(equation_id, str) or not isinstance(
                    raw_parent_keys, list
                ):
                    result = unsupported_derived_fact(raw_fact_key)
                else:
                    parent_facts = tuple(
                        facts_by_key.get(str(parent_key))
                        for parent_key in raw_parent_keys
                    )
                    if any(parent is None for parent in parent_facts):
                        result = unsupported_derived_fact(raw_fact_key)
                    else:
                        parent_dependencies = tuple(
                            fact_dependency(parent)
                            for parent in parent_facts
                            if parent is not None
                        )
                        if any(
                            parent.startswith("unknown:")
                            for parent in parent_dependencies
                        ):
                            result = unsupported_derived_fact(raw_fact_key)
                        else:
                            result = add_calculation(
                                key,
                                calculated_value=_model_fact_decimal(fact),
                                equation_id=equation_id,
                                parents=parent_dependencies,
                            )
            else:
                source_ref = SourceLineageReference(
                    fact_key=raw_fact_key,
                    source_role=str(fact["source_role"]),
                    source_url=str(fact["source_url"]),
                    source_locator=str(fact["source_locator"]),
                    raw_hash=str(fact["raw_hash"]),
                )
                result = add_input(
                    CriticalInputCandidate(
                        key=key,
                        kind=(
                            CriticalInputKind.MANAGEMENT_GUIDANCE
                            if fact["value_kind"] == "management_guidance"
                            else CriticalInputKind.SOURCE_FACT
                        ),
                        value=_model_fact_decimal(fact),
                        period=f"{fact['period_start']}/{fact['period_end']}",
                        unit=str(fact["unit"]),
                        currency=(
                            str(fact["currency"])
                            if fact["currency"] is not None
                            else None
                        ),
                        source_ref=source_ref,
                    )
                )
            resolving_fact_keys.discard(raw_fact_key)
            fact_dependency_keys[raw_fact_key] = result
            return result

        required_facts: dict[str, Mapping[str, object]] = {}

        def require_exact_fact(module_key: str, metric_key: str | None = None) -> None:
            matching = sorted(
                (
                    fact
                    for fact in accepted_facts
                    if fact["business_module"] == module_key
                    and (metric_key is None or fact["metric_key"] == metric_key)
                ),
                key=lambda fact: str(fact["fact_key"]),
            )
            if len(matching) > 1:
                raise ValidationError(
                    "ambiguous critical fact dependency for "
                    f"{module_key}/{metric_key or '*'}"
                )
            if len(matching) == 1:
                required_facts.setdefault(str(matching[0]["fact_key"]), matching[0])

        for requirement in value.model_template.operating_baseline_requirements:
            require_exact_fact(requirement.module_key, requirement.metric_key)
        for binding in value.model_template.operating_driver_bindings:
            require_exact_fact(binding.module_key, binding.metric_key)
        for module in value.model_template.modules:
            if any(
                fact["business_module"] == module.module_key
                for fact in required_facts.values()
            ):
                continue
            require_exact_fact(module.module_key)
        evidence_closure_parents = tuple(
            fact_dependency(fact) for fact in required_facts.values()
        )
        if evidence_closure_parents:
            evidence_closure = add_calculation(
                "calculation:authenticated_evidence_closure",
                calculated_value=(
                    "available" if judgment.operating_baseline_available else "blocked"
                ),
                equation_id="authenticated_evidence_closure.v1",
                parents=evidence_closure_parents,
            )
            edges.add((evidence_closure, "surface:answerability"))

        assumption_keys: dict[str, str] = {}
        assumption_currency = (
            value.market_context.market_bridge.financial_currency
            if value.identity_set.company.external_key == "CN:300750:COMPANY"
            and value.market_context is not None
            else None
        )
        for path in value.strategy_assumptions.driver_paths:
            key = f"assumption:{path.driver_key}"
            monetary = path.driver_key in _MONETARY_DRIVER_KEYS
            assumption_keys[path.driver_key] = add_input(
                CriticalInputCandidate(
                    key=key,
                    kind=CriticalInputKind.AI_ASSUMPTION,
                    value="|".join(
                        canonical_decimal_string(item) for item in path.values
                    ),
                    period=(
                        f"FY{value.strategy_assumptions.first_fiscal_year}-"
                        f"FY{value.strategy_assumptions.first_fiscal_year + 4}"
                    ),
                    unit=(
                        (f"{assumption_currency} million" if monetary else "ratio")
                        if assumption_currency is not None
                        else None
                    ),
                    currency=(assumption_currency if monetary else None),
                    assumption_key=path.assumption_key,
                    rationale=str(path.assumption_rationale),
                )
            )
        scenario_keys: list[str] = []
        for scenario in value.strategy_assumptions.scenario_overrides:
            for override in scenario.driver_overrides:
                key = f"scenario:{scenario.scenario_id}:{override.driver_key}"
                scenario_keys.append(
                    add_input(
                        CriticalInputCandidate(
                            key=key,
                            kind=CriticalInputKind.AI_ASSUMPTION,
                            value=override.value,
                            unit="multiplier",
                            assumption_key=override.assumption_key,
                            rationale=override.rationale,
                        )
                    )
                )
        terminal_growth_key = add_input(
            CriticalInputCandidate(
                key="assumption:terminal_growth",
                kind=CriticalInputKind.AI_ASSUMPTION,
                value=value.strategy_assumptions.terminal_growth.value,
                unit="ratio",
                assumption_key=value.strategy_assumptions.terminal_growth.assumption_key,
                rationale=value.strategy_assumptions.terminal_growth.rationale,
            )
        )
        required_return_key = add_input(
            CriticalInputCandidate(
                key="assumption:required_return",
                kind=CriticalInputKind.AI_ASSUMPTION,
                value=value.required_return,
                unit="ratio",
                assumption_key="company-research-mainline.v1:required_return",
                rationale="Frozen company-research policy required return.",
            )
        )
        sensitivity_keys: dict[str, tuple[str, str]] = {}
        for sensitivity in value.strategy_assumptions.sensitivity_assumptions:
            points: list[str] = []
            for label, assumption in (
                ("low", sensitivity.low),
                ("high", sensitivity.high),
            ):
                points.append(
                    add_input(
                        CriticalInputCandidate(
                            key=(
                                "assumption:sensitivity:"
                                f"{sensitivity.variable_key}:{label}"
                            ),
                            kind=CriticalInputKind.AI_ASSUMPTION,
                            value=assumption.value,
                            unit="ratio",
                            assumption_key=assumption.assumption_key,
                            rationale=assumption.rationale,
                        )
                    )
                )
            sensitivity_keys[sensitivity.variable_key] = (points[0], points[1])

        revenue = add_calculation(
            "calculation:revenue",
            calculated_value=financial_bridge.rows[0].revenue,
            equation_id="revenue.v1",
            parents=(assumption_keys["revenue"],),
        )
        operating_profit = add_calculation(
            "calculation:operating_profit",
            calculated_value=financial_bridge.rows[0].operating_income,
            equation_id="operating_profit.v1",
            parents=(revenue, assumption_keys["operating_margin"]),
        )
        fcff = add_calculation(
            "calculation:fcff",
            calculated_value=financial_bridge.rows[0].fcff,
            equation_id="fcff.v1",
            parents=(
                operating_profit,
                assumption_keys["cash_tax_rate"],
                assumption_keys["depreciation"],
                assumption_keys["capex"],
                assumption_keys["working_capital_change"],
            ),
        )
        scenario = add_calculation(
            "calculation:scenario",
            calculated_value="base|bull|bear",
            equation_id="scenario_set.v1",
            parents=tuple(scenario_keys),
        )
        discount_terminal = add_calculation(
            "calculation:discount_terminal",
            calculated_value=value.strategy_assumptions.terminal_growth.value,
            equation_id="discount_terminal.v1",
            parents=(required_return_key, terminal_growth_key),
        )
        edges.update(
            {
                (revenue, "surface:revenue"),
                (operating_profit, "surface:operating_profit"),
                (fcff, "surface:fcff"),
                (scenario, "surface:scenario"),
                (discount_terminal, "surface:discount_terminal"),
            }
        )

        security_value: str | None = None
        security_return: str | None = None
        market = value.market_context
        if market is not None:
            bridge = market.market_bridge
            capital_input_keys: list[str] = []
            for name in (
                "cash",
                "debt",
                "minority_interest",
                "investments",
                "pension_liabilities",
                "other_adjustments",
                "basic_shares",
                "diluted_shares",
            ):
                capital_input_keys.append(
                    add_input(
                        CriticalInputCandidate(
                            key=f"market:capital:{name}",
                            kind=CriticalInputKind.SOURCE_FACT,
                            value=getattr(bridge.capital_structure, name),
                            period=market.market_at.isoformat(),
                            unit="million",
                            currency=(
                                None if "shares" in name else bridge.financial_currency
                            ),
                            source_ref=bridge.capital_structure.source_ref,
                        )
                    )
                )
            capital_input_keys.append(
                add_input(
                    CriticalInputCandidate(
                        key="market:capital:bridge_policy",
                        kind=CriticalInputKind.SOURCE_FACT,
                        value=(
                            f"{bridge.capital_structure.capital_bridge_policy_version}|"
                            "excluded="
                            + ",".join(
                                bridge.capital_structure.policy_excluded_adjustments
                            )
                        ),
                        period=market.market_at.isoformat(),
                        unit="policy",
                        source_ref=bridge.capital_structure.policy_ref,
                    )
                )
            )
            market_price_keys: list[str] = []
            rights_keys: list[str] = []
            for security in bridge.securities:
                suffix = security.security_external_key.lower().replace(":", "_")
                market_price_keys.append(
                    add_input(
                        CriticalInputCandidate(
                            key=f"market:price:{suffix}",
                            kind=CriticalInputKind.SOURCE_FACT,
                            value=security.market_price,
                            period=market.market_at.isoformat(),
                            unit="per_share",
                            currency=security.quote_currency,
                            source_ref=security.price_ref,
                        )
                    )
                )
                rights_keys.append(
                    add_input(
                        CriticalInputCandidate(
                            key=f"market:rights:{suffix}",
                            kind=CriticalInputKind.SOURCE_FACT,
                            value=(
                                "conversion="
                                f"{canonical_decimal_string(security.conversion_ratio)}|"
                                "adr="
                                f"{canonical_decimal_string(security.adr_ratio)}|"
                                "dividend="
                                f"{canonical_decimal_string(security.dividend_rights_per_unit)}"
                            ),
                            period=market.market_at.isoformat(),
                            unit="rights_contract",
                            source_ref=security.rights_ref,
                        )
                    )
                )
            fx_keys: tuple[str, ...] = ()
            if bridge.fx_ref is not None:
                fx_keys = (
                    add_input(
                        CriticalInputCandidate(
                            key=(
                                "market:fx:"
                                f"{bridge.financial_currency.lower()}_"
                                f"{bridge.base_currency.lower()}"
                            ),
                            kind=CriticalInputKind.SOURCE_FACT,
                            value=bridge.financial_to_base_rate,
                            period=market.market_at.isoformat(),
                            unit="quote_per_base",
                            currency=(
                                f"{bridge.base_currency}/{bridge.financial_currency}"
                            ),
                            source_ref=bridge.fx_ref,
                        )
                    ),
                )
            capital_structure = add_calculation(
                "calculation:capital_structure",
                calculated_value=bridge.capital_structure.basic_shares,
                equation_id="capital_structure.v1",
                parents=tuple(capital_input_keys),
            )
            edges.add((capital_structure, "surface:capital_structure"))
            market_security_bridge = add_calculation(
                "calculation:market_security_bridge",
                calculated_value="available",
                equation_id="market_security_bridge.v1",
                parents=(
                    capital_structure,
                    *fx_keys,
                    *market_price_keys,
                    *rights_keys,
                ),
            )
            edges.add((market_security_bridge, "surface:answerability"))
            if valuation_set is not None:
                security_value = add_calculation(
                    "calculation:security_value",
                    calculated_value="|".join(
                        f"{item.security_external_key}:"
                        f"{canonical_decimal_string(item.value_per_share.minimum)}-"
                        f"{canonical_decimal_string(item.value_per_share.maximum)}"
                        for item in valuation_set.security_value_ranges
                    ),
                    equation_id="security_value.v1",
                    parents=(
                        fcff,
                        scenario,
                        discount_terminal,
                        capital_structure,
                        *rights_keys,
                    ),
                )
                security_return = add_calculation(
                    "calculation:security_return",
                    calculated_value="|".join(
                        f"{item.security_external_key}:"
                        f"{canonical_decimal_string(item.base_currency_return.minimum)}-"
                        f"{canonical_decimal_string(item.base_currency_return.maximum)}"
                        for item in valuation_set.security_value_ranges
                    ),
                    equation_id="security_return.v1",
                    parents=(security_value, *fx_keys, *market_price_keys),
                )
                for analysis in valuation_set.sensitivity_analyses:
                    sensitivity = add_calculation(
                        f"calculation:sensitivity:{analysis.variable_key}",
                        calculated_value="|".join(
                            f"{item.security_external_key}:"
                            f"{canonical_decimal_string(item.low_input_value_per_share)}-"
                            f"{canonical_decimal_string(item.high_input_value_per_share)}"
                            for item in analysis.security_values
                        ),
                        equation_id=analysis.equation_id,
                        parents=(
                            fcff,
                            capital_structure,
                            *rights_keys,
                            *sensitivity_keys[analysis.variable_key],
                        ),
                    )
                    edges.add((sensitivity, "surface:security_value"))
                direction = add_calculation(
                    "calculation:direction",
                    calculated_value="|".join(
                        f"{item.security_external_key}:"
                        f"{str(item.meets_required_return).lower()}"
                        for item in valuation_set.required_return_comparisons
                    ),
                    equation_id="required_return_comparison.v1",
                    parents=(required_return_key, security_return),
                )
                edges.update(
                    {
                        (security_value, "surface:security_value"),
                        (security_return, "surface:security_return"),
                        (direction, "surface:direction"),
                    }
                )

        gap_dependency_keys = {}
        for gap in gaps:
            if gap.code.startswith("critical_input_marked_unknown_"):
                continue
            gap_dependency_keys[gap.code] = (
                add_unknown(gap)
                if gap.severity is ResearchGapSeverity.LOW
                else add_unknown(gap, CriticalDependencySurface.ANSWERABILITY)
            )
        blocked_surfaces: tuple[CriticalDependencySurface, ...] = ()
        if market is None:
            blocked_surfaces = (
                CriticalDependencySurface.CAPITAL_STRUCTURE,
                CriticalDependencySurface.SECURITY_VALUE,
                CriticalDependencySurface.SECURITY_RETURN,
                CriticalDependencySurface.DIRECTION,
            )
        elif valuation_set is None:
            blocked_surfaces = (
                CriticalDependencySurface.SECURITY_VALUE,
                CriticalDependencySurface.SECURITY_RETURN,
                CriticalDependencySurface.DIRECTION,
            )
        if blocked_surfaces:
            if market is None:
                add_unknown(
                    ResearchGap(
                        code="market_security_bridge_unavailable",
                        module_key="scenarios_valuation_implied_expectations",
                        severity=ResearchGapSeverity.CRITICAL,
                        message=(
                            "The exact frozen market and security bridge required by "
                            "the valuation surfaces is unavailable."
                        ),
                    ),
                    *blocked_surfaces,
                    CriticalDependencySurface.ANSWERABILITY,
                )
            else:
                for gap in gaps:
                    if (
                        gap.severity is ResearchGapSeverity.CRITICAL
                        and gap.code in gap_dependency_keys
                    ):
                        edges.update(
                            (gap_dependency_keys[gap.code], f"surface:{surface.value}")
                            for surface in blocked_surfaces
                        )
        edges.add((fcff, "surface:answerability"))

        if judgment.strongest_counterevidence:
            for reference in judgment.strongest_counterevidence:
                fact = facts_by_key.get(reference.fact_key)
                key = (
                    fact_dependency(fact)
                    if fact is not None
                    else add_input(
                        CriticalInputCandidate(
                            key=f"fact:{reference.fact_key}",
                            kind=CriticalInputKind.SOURCE_FACT,
                            value=reference.fact_key,
                            source_ref=reference,
                        )
                    )
                )
                edges.add((key, "surface:strongest_counterevidence"))
        else:
            unknown = ResearchGap(
                code="strongest_counterevidence_missing",
                module_key="counterevidence_risks_next_checks",
                severity=ResearchGapSeverity.HIGH,
                message="No authenticated strongest counterevidence was identified.",
            )
            add_unknown(unknown, CriticalDependencySurface.STRONGEST_COUNTEREVIDENCE)

        for item in value.critical_input_unknowns:
            gap_key = CompanyResearchModelBuilder._marked_unknown_gap_key(item)
            if item.key not in {node.key for node in unknowns}:
                unknowns.append(
                    CriticalUnknownNode(
                        candidate=CriticalInputCandidate(
                            key=item.key,
                            kind=CriticalInputKind.UNKNOWN,
                            value=None,
                            unknown_reason=(
                                f"User marked critical input {item.key} unknown."
                            ),
                            gap_key=gap_key,
                        )
                    )
                )
            edges.update(
                (item.key, f"surface:{surface.value}")
                for surface in item.impact.surfaces
            )

        return CriticalDependencyGraph(
            input_nodes=tuple(sorted(input_nodes, key=lambda node: node.key)),
            calculation_nodes=tuple(sorted(calculations, key=lambda node: node.key)),
            unknown_nodes=tuple(sorted(unknowns, key=lambda node: node.key)),
            surface_nodes=tuple(
                CriticalSurfaceNode(surface=surface)
                for surface in CRITICAL_DEPENDENCY_SURFACE_ORDER
            ),
            edges=tuple(
                CriticalDependencyEdge(parent_key=parent, consumer_key=consumer)
                for parent, consumer in sorted(edges)
            ),
        )

    @staticmethod
    def _validate_authenticated_sources(
        source_refs: tuple[dict[str, str], ...],
        facts: tuple[Mapping[str, object], ...],
    ) -> None:
        authenticated = {tuple(sorted(item.items())) for item in source_refs}
        used = {
            tuple(
                sorted(
                    {
                        "source_role": str(fact["source_role"]),
                        "source_url": str(fact["source_url"]),
                        "source_locator": str(fact["source_locator"]),
                        "raw_hash": str(fact["raw_hash"]),
                    }.items()
                )
            )
            for fact in facts
        }
        if used != authenticated:
            raise ValidationError(
                "reviewed evidence refs must exactly match authenticated source_refs"
            )

    @staticmethod
    def _evidence_refs(
        facts: tuple[Mapping[str, object], ...],
    ) -> tuple[SourceLineageReference, ...]:
        return tuple(
            SourceLineageReference(
                fact_key=str(fact["fact_key"]),
                source_role=str(fact["source_role"]),
                source_url=str(fact["source_url"]),
                source_locator=str(fact["source_locator"]),
                raw_hash=str(fact["raw_hash"]),
            )
            for fact in facts
        )

    @staticmethod
    def _validate_template_evidence(
        template: CompanyResearchModelTemplate,
        facts: tuple[Mapping[str, object], ...],
        gaps: tuple[Mapping[str, object], ...],
    ) -> None:
        modules = {item.module_key for item in template.modules}
        metrics = {
            (item.module_key, item.metric_key)
            for item in template.metric_classifications
        }
        if any(str(item["business_module"]) not in modules for item in (*facts, *gaps)):
            raise ValidationError(
                "evidence module is outside the company model template"
            )
        if any(
            (str(item["business_module"]), str(item["metric_key"])) not in metrics
            for item in facts
        ):
            raise ValidationError(
                "evidence metric is outside the company model template"
            )

    @staticmethod
    def _validate_scenario_mapping(
        template: CompanyResearchModelTemplate,
        assumptions: StrategyAssumptionSet,
    ) -> None:
        expected = tuple(
            (item.scenario_id, item.mechanism_id)
            for item in template.scenario_mechanisms
        )
        actual = tuple(
            (item.scenario_id, item.mechanism_id)
            for item in assumptions.scenario_overrides
        )
        if actual != expected:
            raise ValidationError(
                "strategy assumptions must match the template mechanism mapping"
            )

    @staticmethod
    def _baseline_gaps(
        template: CompanyResearchModelTemplate,
        facts: tuple[Mapping[str, object], ...],
        *,
        unavailable_pairs: set[tuple[str, str]] | None = None,
    ) -> tuple[ResearchGap, ...]:
        unavailable_pairs = unavailable_pairs or set()
        available = {
            (str(item["business_module"]), str(item["metric_key"])) for item in facts
        }
        return tuple(
            ResearchGap(
                code=(
                    f"{_BUILDER_GAP_PREFIX}operating_baseline_missing_"
                    f"{item.requirement_key}"
                ),
                module_key=item.module_key,
                severity=ResearchGapSeverity.CRITICAL,
                message=f"Reviewed operating baseline is missing: {item.requirement_key}",
            )
            for item in template.operating_baseline_requirements
            if (item.module_key, item.metric_key) not in available
            and (item.module_key, item.metric_key) not in unavailable_pairs
        )

    @staticmethod
    def _unsupported_derived_gaps(
        template: CompanyResearchModelTemplate,
        eligible_facts: tuple[Mapping[str, object], ...],
        unsupported_facts: tuple[Mapping[str, object], ...],
    ) -> tuple[ResearchGap, ...]:
        available_pairs = {
            (str(fact["business_module"]), str(fact["metric_key"]))
            for fact in eligible_facts
        }
        available_modules = {str(fact["business_module"]) for fact in eligible_facts}
        required_pairs = {
            (item.module_key, item.metric_key)
            for item in template.operating_baseline_requirements
        } | {
            (item.module_key, item.metric_key)
            for item in template.operating_driver_bindings
        }

        def severity(fact: Mapping[str, object]) -> ResearchGapSeverity:
            pair = (str(fact["business_module"]), str(fact["metric_key"]))
            if (pair in required_pairs and pair not in available_pairs) or pair[
                0
            ] not in available_modules:
                return ResearchGapSeverity.CRITICAL
            return ResearchGapSeverity.HIGH

        return tuple(
            ResearchGap(
                code=(f"{_BUILDER_GAP_PREFIX}unsupported_derived_{fact['fact_key']}"),
                module_key=str(fact["business_module"]),
                severity=severity(fact),
                message=(
                    "Authenticated derived evidence has unsupported provenance: "
                    f"{fact['fact_key']}"
                ),
            )
            for fact in unsupported_facts
        )

    @staticmethod
    def _active_gaps(
        raw_gaps: tuple[Mapping[str, object], ...],
        market_context: FrozenMarketContext | None,
        baseline_gaps: tuple[ResearchGap, ...],
    ) -> tuple[ResearchGap, ...]:
        raw_keys = {str(item["gap_key"]) for item in raw_gaps}
        if market_context is None and not _CLOSED_BY_MARKET.issubset(raw_keys):
            raise ValidationError(
                "missing market inputs require a governed market gap contract"
            )
        closed = set(_CLOSED_BY_STRATEGY)
        if market_context is not None:
            closed.update(_CLOSED_BY_MARKET)
        source = {
            str(item["gap_key"]): item
            for item in raw_gaps
            if str(item["gap_key"]) not in closed
        }
        source_gaps = tuple(
            ResearchGap(
                code=key,
                module_key=str(item["business_module"]),
                severity=(
                    ResearchGapSeverity.CRITICAL
                    if key in _CLOSED_BY_MARKET
                    else (
                        ResearchGapSeverity.LOW
                        if key == "maintenance_vs_growth_capex_not_disaggregated"
                        else ResearchGapSeverity.HIGH
                    )
                ),
                message=str(item["reason"]),
            )
            for key, item in sorted(source.items())
        )
        return tuple(sorted((*source_gaps, *baseline_gaps), key=lambda item: item.code))

    @staticmethod
    def _complete_module_gaps(
        template: CompanyResearchModelTemplate,
        facts: tuple[Mapping[str, object], ...],
        gaps: tuple[ResearchGap, ...],
    ) -> tuple[ResearchGap, ...]:
        covered = {str(item["business_module"]) for item in facts} | {
            item.module_key for item in gaps
        }
        generated = tuple(
            ResearchGap(
                code=(
                    f"{_BUILDER_GAP_PREFIX}missing_module_evidence_{module.module_key}"
                ),
                module_key=module.module_key,
                severity=ResearchGapSeverity.CRITICAL,
                message=f"No confirmed evidence or governed gap covers {module.module_key}",
            )
            for module in template.modules
            if module.module_key not in covered
        )
        return tuple(sorted((*gaps, *generated), key=lambda item: item.code))

    @staticmethod
    def _operating_driver_gaps(
        template: CompanyResearchModelTemplate,
        facts: tuple[Mapping[str, object], ...],
        *,
        unavailable_pairs: set[tuple[str, str]] | None = None,
    ) -> tuple[ResearchGap, ...]:
        unavailable_pairs = unavailable_pairs or set()
        available = {
            (str(item["business_module"]), str(item["metric_key"])) for item in facts
        }
        return tuple(
            ResearchGap(
                code=(
                    f"{_BUILDER_GAP_PREFIX}operating_driver_missing_"
                    f"{binding.driver_key}"
                ),
                module_key=binding.module_key,
                severity=ResearchGapSeverity.CRITICAL,
                message=f"Confirmed numeric input is missing for {binding.driver_key}",
            )
            for binding in template.operating_driver_bindings
            if (binding.module_key, binding.metric_key) not in available
            and (binding.module_key, binding.metric_key) not in unavailable_pairs
        )

    @staticmethod
    def _business_map(
        template: CompanyResearchModelTemplate,
        facts: tuple[Mapping[str, object], ...],
        gaps: tuple[ResearchGap, ...],
        refs: tuple[SourceLineageReference, ...],
    ) -> BusinessMapArtifact:
        refs_by_key = {item.fact_key: item for item in refs}
        categories = {
            (item.module_key, item.metric_key): item.category
            for item in template.metric_classifications
        }
        return BusinessMapArtifact(
            modules=tuple(
                BusinessModuleArtifact(
                    module_key=module.module_key,
                    revenue_sources=module.revenue_sources,
                    cost_structure=module.cost_structure,
                    capital_needs=module.capital_needs,
                    fact_refs=tuple(
                        refs_by_key[str(item["fact_key"])]
                        for item in facts
                        if item["business_module"] == module.module_key
                    ),
                    gap_refs=tuple(
                        gap.code for gap in gaps if gap.module_key == module.module_key
                    ),
                    classified_evidence=tuple(
                        ClassifiedBusinessEvidenceArtifact(
                            fact_ref=refs_by_key[str(item["fact_key"])],
                            metric_key=str(item["metric_key"]),
                            category=categories[
                                (module.module_key, str(item["metric_key"]))
                            ],
                            value=_model_fact_decimal(item),
                            currency=(
                                str(item["currency"])
                                if item["currency"] is not None
                                else None
                            ),
                            unit=str(item["unit"]),
                            period_start=str(item["period_start"]),
                            period_end=str(item["period_end"]),
                        )
                        for item in facts
                        if item["business_module"] == module.module_key
                    ),
                )
                for module in template.modules
            )
        )

    @staticmethod
    def _assumption_refs(
        assumptions: StrategyAssumptionSet,
    ) -> tuple[SourceLineageReference, ...]:
        return tuple(
            SourceLineageReference(
                fact_key=f"strategy_assumption_{path.driver_key}",
                source_role="strategy_assumption",
                source_url=f"urn:company-research:strategy:{assumptions.strategy_version}",
                source_locator=str(path.assumption_key),
                raw_hash=assumptions.content_hash,
            )
            for path in assumptions.driver_paths
        )

    @staticmethod
    def _driver_map(
        template: CompanyResearchModelTemplate,
        facts: tuple[Mapping[str, object], ...],
        evidence_refs: tuple[SourceLineageReference, ...],
        assumptions: StrategyAssumptionSet,
        assumption_refs: tuple[SourceLineageReference, ...],
    ) -> DriverMapArtifact:
        modules_by_driver = {
            item.driver_key: item.module_key
            for item in template.financial_driver_ownership
        }
        evidence_by_key = {item.fact_key: item for item in evidence_refs}
        refs_by_driver = {
            path.driver_key: ref
            for path, ref in zip(assumptions.driver_paths, assumption_refs, strict=True)
        }
        operating = tuple(
            DriverMetricArtifact(
                driver_key=binding.driver_key,
                module_key=binding.module_key,
                fact_refs=tuple(
                    evidence_by_key[str(item["fact_key"])]
                    for item in facts
                    if item["business_module"] == binding.module_key
                    and item["metric_key"] == binding.metric_key
                ),
                assumption_refs=(),
                equation=(
                    "reported_value = reviewed_fact"
                    if binding.input_state is ModelInputState.REPORTED
                    else str(binding.equation_id)
                ),
                output_metric=binding.metric_key,
                input_state=binding.input_state,
                assumption_key=None,
                equation_id=binding.equation_id,
                values=tuple(
                    _model_fact_decimal(item)
                    for item in facts
                    if item["business_module"] == binding.module_key
                    and item["metric_key"] == binding.metric_key
                ),
            )
            for binding in template.operating_driver_bindings
            if any(
                item["business_module"] == binding.module_key
                and item["metric_key"] == binding.metric_key
                for item in facts
            )
        )
        financial = tuple(
            DriverMetricArtifact(
                driver_key=path.driver_key,
                module_key=modules_by_driver[path.driver_key],
                fact_refs=(),
                assumption_refs=(refs_by_driver[path.driver_key],),
                equation=SCENARIO_FINANCIAL_DRIVER_EQUATIONS[path.driver_key],
                output_metric=path.driver_key,
                input_state=path.state,
                assumption_key=path.assumption_key,
                equation_id=path.equation_id,
                values=path.values,
                assumption_rationale=path.assumption_rationale,
                assumption_equation=path.assumption_equation,
            )
            for path in assumptions.driver_paths
        )
        return DriverMapArtifact(drivers=(*operating, *financial))

    @staticmethod
    def _scenario_inputs(
        assumptions: StrategyAssumptionSet,
        assumption_refs: tuple[SourceLineageReference, ...],
    ) -> tuple[ScenarioSetArtifact, tuple[ScenarioFinancialBridge, ...]]:
        forecasts = tuple(
            ScenarioFinancialDriverForecast(
                driver_key=path.driver_key,
                values=path.values,
                fact_refs=(),
                assumption_refs=(reference,),
                input_state=path.state,
                assumption_key=path.assumption_key,
                equation_id=path.equation_id,
                assumption_rationale=path.assumption_rationale,
                assumption_equation=path.assumption_equation,
            )
            for path, reference in zip(
                assumptions.driver_paths, assumption_refs, strict=True
            )
        )
        scenarios = ScenarioSetArtifact(
            scenarios=tuple(
                ScenarioArtifact(
                    scenario_id=item.scenario_id,
                    mechanism_id=item.mechanism_id,
                    driver_overrides=item.driver_overrides,
                )
                for item in assumptions.scenario_overrides
            )
        )
        bridges = tuple(
            ScenarioFinancialBridge(
                scenario_id=scenario.scenario_id,
                first_fiscal_year=assumptions.first_fiscal_year,
                driver_forecasts=forecasts,
            )
            for scenario in scenarios.scenarios
        )
        return scenarios, bridges

    @staticmethod
    def _source_lineage(
        evidence_refs: tuple[SourceLineageReference, ...],
        assumption_refs: tuple[SourceLineageReference, ...],
        gap_ref: SourceLineageReference,
        market_context: FrozenMarketContext | None,
    ) -> tuple[SourceLineageReference, ...]:
        market_refs: tuple[SourceLineageReference, ...] = ()
        if market_context is not None:
            bridge = market_context.market_bridge
            values = [
                bridge.capital_structure.source_ref,
                bridge.capital_structure.policy_ref,
                *((bridge.fx_ref,) if bridge.fx_ref is not None else ()),
                *(
                    ref
                    for security in bridge.securities
                    for ref in (security.rights_ref, security.price_ref)
                ),
            ]
            for component in market_context.equity_components:
                for reference in (
                    component.legal_rights_ref,
                    component.unit_source_ref,
                    component.price_proxy_ref,
                ):
                    if reference is not None and reference.fact_key not in {
                        item.fact_key for item in values
                    }:
                        values.append(reference)
            market_refs = tuple(values)
        values = (*evidence_refs, *assumption_refs, gap_ref, *market_refs)
        if len({item.fact_key for item in values}) != len(values):
            raise ValidationError(
                "model source lineage must not contain duplicate refs"
            )
        return tuple(values)

    @staticmethod
    def _memo(
        *,
        business_map: BusinessMapArtifact,
        driver_map: DriverMapArtifact,
        financial_bridge: FinancialBridgeArtifact,
        scenario_set: ScenarioSetArtifact,
        valuation_set: ValuationSetArtifact | None,
        judgment: JudgmentContextArtifact,
        gaps: tuple[ResearchGap, ...],
        assessment: CompanyResearchAssessment,
    ) -> CompanyResearchMemoArtifact:
        def reference(kind: str, artifact: Any) -> CompanyResearchArtifactReference:
            return CompanyResearchArtifactReference(
                kind,
                canonical_hash(CompanyResearchArtifactCodec.encode(kind, artifact)),
            )

        return CompanyResearchMemoArtifact(
            assessment_status=assessment.status,
            business_map_ref=reference("business_map", business_map),
            driver_map_ref=reference("driver_map", driver_map),
            financial_bridge_ref=reference("financial_bridge", financial_bridge),
            scenario_set_ref=reference("scenario_set", scenario_set),
            valuation_set_ref=(
                reference("valuation_set", valuation_set)
                if valuation_set is not None
                else None
            ),
            gap_keys=tuple(sorted(gap.code for gap in gaps)),
            strongest_counterevidence=judgment.strongest_counterevidence,
            next_verification_events=judgment.next_verification_events,
            research_gaps=gaps,
        )

    @staticmethod
    def validate_critical_input_replacement(
        original: CriticalInputCandidate,
        replacement: CriticalInputCandidate,
    ) -> None:
        if (
            not isinstance(original, CriticalInputCandidate)
            or type(replacement) is not CriticalInputCandidate
            or replacement.kind is not CriticalInputKind.USER_ASSUMPTION
            or replacement.key != original.key
        ):
            raise ValidationError("critical input replacement is invalid")
        if replacement.value == original.value:
            raise ValidationError("critical input replacement must change the value")
        key = original.key
        safely_mapped = (
            key.startswith("fact:")
            or key.startswith("assumption:")
            or key.startswith("scenario:")
            or (
                key.startswith("market:capital:")
                and key != "market:capital:bridge_policy"
            )
            or key.startswith("market:price:")
            or key.startswith("market:fx:")
        )
        if not safely_mapped:
            raise ValidationError(
                "critical input replacement key is not safely mappable"
            )
        expected_unit = original.unit
        if expected_unit is None or replacement.unit != expected_unit:
            raise ValidationError(
                "critical input replacement unit must match the governed model unit"
            )
        if key.startswith("fact:"):
            if (
                type(original.value) is not Decimal
                or type(replacement.value) is not Decimal
            ):
                raise ValidationError("source fact replacement must be numeric")
            if replacement.value < Decimal(0):
                raise ValidationError(
                    "critical input replacement source value must be nonnegative"
                )
            return
        if key.startswith("assumption:"):
            if key in {"assumption:required_return", "assumption:terminal_growth"}:
                if type(replacement.value) is not Decimal:
                    raise ValidationError(
                        "scalar assumption replacement must be numeric"
                    )
                if key == "assumption:required_return" and replacement.value <= 0:
                    raise ValidationError(
                        "critical input replacement required return must be positive"
                    )
                if key == "assumption:terminal_growth" and replacement.value < 0:
                    raise ValidationError(
                        "critical input replacement terminal growth must be nonnegative"
                    )
                return
            if type(replacement.value) is not str:
                raise ValidationError(
                    "driver replacement must contain a five-year path"
                )
            try:
                values = tuple(Decimal(item) for item in replacement.value.split("|"))
            except InvalidOperation as exc:
                raise ValidationError(
                    "driver replacement must contain a five-year path"
                ) from exc
            if len(values) != 5 or any(not item.is_finite() for item in values):
                raise ValidationError(
                    "driver replacement must contain a five-year path"
                )
            driver_key = key.removeprefix("assumption:")
            if driver_key in {
                "revenue",
                "depreciation",
                "capex",
                "working_capital_change",
            } and any(item < 0 for item in values):
                raise ValidationError(
                    "critical input replacement driver values must be nonnegative"
                )
            if driver_key in {"operating_margin", "cash_tax_rate"} and any(
                item < 0 or item > 1 for item in values
            ):
                raise ValidationError(
                    "critical input replacement ratio values must be between zero and one"
                )
            return
        if key.startswith("scenario:"):
            parts = key.split(":")
            if (
                len(parts) != 3
                or parts[1] == "base"
                or type(replacement.value) is not Decimal
            ):
                raise ValidationError("scenario replacement is not safely mappable")
            if replacement.value <= 0:
                raise ValidationError(
                    "critical input replacement scenario multiplier must be positive"
                )
            return
        if key.startswith("market:capital:"):
            field = key.removeprefix("market:capital:")
            if field == "bridge_policy" or type(replacement.value) is not Decimal:
                raise ValidationError("market replacement is not safely mappable")
            if field in {"basic_shares", "diluted_shares"} and replacement.value <= 0:
                raise ValidationError(
                    "critical input replacement share count must be positive"
                )
            if field != "other_adjustments" and replacement.value < 0:
                raise ValidationError(
                    "critical input replacement capital value must be nonnegative"
                )
            return
        if key.startswith("market:price:") or key.startswith("market:fx:"):
            if type(replacement.value) is not Decimal:
                raise ValidationError("market replacement must be numeric")
            if replacement.value <= 0:
                raise ValidationError(
                    "critical input replacement market value must be positive"
                )
            return
        raise ValidationError("critical input replacement key is not safely mappable")

    @staticmethod
    def validate_critical_input_replacement_set(
        value: CriticalInputSet,
        original: CriticalInput,
        replacement: CriticalInputCandidate,
        *,
        minimum_listed_units: Decimal | None = None,
    ) -> None:
        if type(value) is not CriticalInputSet or type(original) is not CriticalInput:
            raise ValidationError("critical input replacement set is invalid")
        values = {item.key: item.value for item in value.inputs}
        values[original.key] = replacement.value
        required_return = values.get("assumption:required_return")
        terminal_growth = values.get("assumption:terminal_growth")
        if (
            type(required_return) is Decimal
            and type(terminal_growth) is Decimal
            and terminal_growth >= required_return
        ):
            raise ValidationError(
                "critical input replacement discount rates do not close"
            )
        basic_shares = values.get("market:capital:basic_shares")
        diluted_shares = values.get("market:capital:diluted_shares")
        if (
            type(basic_shares) is Decimal
            and type(diluted_shares) is Decimal
            and (
                basic_shares <= 0
                or diluted_shares < basic_shares
                or (
                    minimum_listed_units is not None
                    and basic_shares < minimum_listed_units
                )
            )
        ):
            raise ValidationError(
                "critical input replacement capital structure does not close"
            )
        bridge_policy = values.get("market:capital:bridge_policy")
        if isinstance(bridge_policy, str) and "|excluded=" in bridge_policy:
            excluded = tuple(
                field
                for field in bridge_policy.split("|excluded=", 1)[1].split(",")
                if field
            )
            if any(
                type(values.get(f"market:capital:{field}")) is not Decimal
                or values[f"market:capital:{field}"] != Decimal(0)
                for field in excluded
            ):
                raise ValidationError(
                    "critical input replacement capital policy does not close"
                )

    @staticmethod
    def _user_assumption_key(value: CriticalInputCandidate) -> str:
        assert value.assumption_key is not None
        return value.assumption_key

    @classmethod
    def _strategy_with_overlays(
        cls,
        value: StrategyAssumptionSet,
        replacements: Mapping[str, CriticalInputCandidate],
    ) -> StrategyAssumptionSet:
        paths = []
        for path in value.driver_paths:
            overlay = replacements.get(f"assumption:{path.driver_key}")
            if overlay is None:
                paths.append(path)
                continue
            assert isinstance(overlay.value, str)
            paths.append(
                replace(
                    path,
                    values=tuple(Decimal(item) for item in overlay.value.split("|")),
                    assumption_key=cls._user_assumption_key(overlay),
                    assumption_rationale=overlay.rationale,
                )
            )
        scenarios = []
        for scenario in value.scenario_overrides:
            overrides = []
            for item in scenario.driver_overrides:
                overlay = replacements.get(
                    f"scenario:{scenario.scenario_id}:{item.driver_key}"
                )
                overrides.append(
                    item
                    if overlay is None
                    else replace(
                        item,
                        value=overlay.value,
                        assumption_key=cls._user_assumption_key(overlay),
                        rationale=overlay.rationale,
                    )
                )
            scenarios.append(replace(scenario, driver_overrides=tuple(overrides)))
        terminal = value.terminal_growth
        if overlay := replacements.get("assumption:terminal_growth"):
            terminal = StrategyAssumptionValue(
                value=overlay.value,
                state=terminal.state,
                assumption_key=cls._user_assumption_key(overlay),
                rationale=str(overlay.rationale),
                equation=terminal.equation,
            )
        paths_tuple = tuple(paths)
        scenarios_tuple = tuple(scenarios)
        return StrategyAssumptionSet(
            strategy_version=value.strategy_version,
            content_hash=StrategyAssumptionSet.calculate_content_hash(
                strategy_version=value.strategy_version,
                first_fiscal_year=value.first_fiscal_year,
                driver_paths=paths_tuple,
                scenario_overrides=scenarios_tuple,
                terminal_growth=terminal,
                sensitivity_assumptions=value.sensitivity_assumptions,
            ),
            first_fiscal_year=value.first_fiscal_year,
            driver_paths=paths_tuple,
            scenario_overrides=scenarios_tuple,
            terminal_growth=terminal,
            sensitivity_assumptions=value.sensitivity_assumptions,
        )

    @staticmethod
    def _market_with_overlays(
        value: FrozenMarketContext,
        replacements: Mapping[str, CriticalInputCandidate],
        *,
        critical_inputs_artifact_id: UUID,
        critical_inputs_content_hash: str,
    ) -> FrozenMarketContext:
        def user_ref(
            *,
            key: str,
            fact_key: str,
            parent: SourceLineageReference,
        ) -> SourceLineageReference:
            return SourceLineageReference(
                fact_key=fact_key,
                source_role="user_assumption",
                source_url=(
                    "urn:company-research:critical-input-decision:"
                    f"{critical_inputs_artifact_id}"
                ),
                source_locator=(
                    f"parent_source_role={parent.source_role};"
                    f"parent_source_url={parent.source_url};"
                    f"parent_source_locator={parent.source_locator};"
                    f"parent_raw_hash={parent.raw_hash};critical_input_key={key}"
                ),
                raw_hash=critical_inputs_content_hash,
            )

        bridge = value.market_bridge
        capital = bridge.capital_structure
        capital_overlay_keys = []
        for field in (
            "cash",
            "debt",
            "minority_interest",
            "investments",
            "pension_liabilities",
            "other_adjustments",
            "basic_shares",
            "diluted_shares",
        ):
            overlay = replacements.get(f"market:capital:{field}")
            if overlay is not None:
                capital = replace(capital, **{field: overlay.value})
                capital_overlay_keys.append(overlay.key)
        capital_ref = capital.source_ref
        if capital_overlay_keys:
            capital_ref = user_ref(
                key="|".join(sorted(capital_overlay_keys)),
                fact_key=f"capital_structure_{bridge.financial_currency.lower()}",
                parent=capital.source_ref,
            )
            capital = replace(capital, source_ref=capital_ref)
        securities = []
        price_refs: dict[str, SourceLineageReference] = {}
        for security in bridge.securities:
            suffix = security.security_external_key.lower().replace(":", "_")
            overlay = replacements.get(f"market:price:{suffix}")
            price_ref = security.price_ref
            if overlay is not None:
                price_ref = user_ref(
                    key=overlay.key,
                    fact_key=(
                        f"market_price_{security.quote_currency.lower()}_{suffix}"
                    ),
                    parent=security.price_ref,
                )
            price_refs[security.security_external_key] = price_ref
            securities.append(
                security
                if overlay is None
                else replace(
                    security,
                    market_price=overlay.value,
                    price_ref=price_ref,
                )
            )
        fx_key = (
            "market:fx:"
            f"{bridge.financial_currency.lower()}_{bridge.base_currency.lower()}"
        )
        fx = replacements.get(fx_key)
        fx_value = bridge.financial_to_base_rate if fx is None else fx.value
        fx_ref = bridge.fx_ref
        if fx is not None:
            if fx_ref is None:
                raise ValidationError("same-currency market bridge cannot replace FX")
            fx_ref = user_ref(
                key=fx.key,
                fact_key=(
                    f"{bridge.financial_currency.lower()}_"
                    f"{bridge.base_currency.lower()}_fx"
                ),
                parent=fx_ref,
            )
            securities = [
                replace(item, quote_to_base_rate=fx_value) for item in securities
            ]
        bridge = replace(
            bridge,
            capital_structure=capital,
            securities=tuple(securities),
            financial_to_base_rate=fx_value,
            fx_ref=fx_ref,
        )
        components = list(value.equity_components)
        if replacements.get("market:capital:basic_shares") is not None:
            basic_overlay = replacements["market:capital:basic_shares"]
            components[1] = replace(
                components[1],
                economic_units=(
                    capital.basic_shares
                    - components[0].economic_units
                    - components[2].economic_units
                ),
                unit_source_ref=user_ref(
                    key=basic_overlay.key,
                    fact_key="class_b_economic_units_user_assumption",
                    parent=value.equity_components[1].unit_source_ref,
                ),
            )
        components = [
            replace(
                item,
                price_ref=price_refs[item.price_proxy_security_external_key],
            )
            for item in components
        ]
        bindings = tuple(
            replace(
                item,
                source_ref=(
                    capital_ref
                    if item.role is FrozenMarketSnapshotRole.CAPITAL_STRUCTURE
                    else (
                        fx_ref
                        if item.role is FrozenMarketSnapshotRole.FX
                        else (
                            price_refs[str(item.security_external_key)]
                            if item.role is FrozenMarketSnapshotRole.PRICE
                            else item.source_ref
                        )
                    )
                ),
            )
            for item in value.snapshot_bindings
        )
        prices = {
            item.security_external_key: item.market_price for item in bridge.securities
        }
        enterprise_value = sum(
            (
                item.economic_units * prices[item.price_proxy_security_external_key]
                for item in components
            ),
            start=Decimal(0),
        ) + (
            capital.debt
            + capital.minority_interest
            + capital.pension_liabilities
            + capital.other_adjustments
            - capital.cash
            - capital.investments
        )
        return replace(
            value,
            market_bridge=bridge,
            snapshot_bindings=bindings,
            equity_components=tuple(components),
            reverse_dcf_request=replace(
                value.reverse_dcf_request,
                target_enterprise_value=enterprise_value,
            ),
        )

    @classmethod
    def apply_critical_input_overlays(
        cls, value: CompanyResearchBuildInput
    ) -> CompanyResearchBuildInput:
        replacements = {item.key: item for item in value.critical_input_replacements}
        if not replacements and not value.critical_input_unknowns:
            return value
        facts = value.evidence_payload.get("facts")
        expected_units: dict[str, object] = (
            {
                f"fact:{item['fact_key']}": item.get("unit")
                for item in facts
                if isinstance(item, Mapping) and isinstance(item.get("fact_key"), str)
            }
            if isinstance(facts, list)
            else {}
        )
        financial_currency = (
            value.market_context.market_bridge.financial_currency
            if value.market_context is not None
            else "N/A"
        )
        driver_units = {
            "revenue": f"{financial_currency} million",
            "operating_margin": "ratio",
            "cash_tax_rate": "ratio",
            "depreciation": f"{financial_currency} million",
            "capex": f"{financial_currency} million",
            "working_capital_change": f"{financial_currency} million",
        }
        expected_units.update(
            {
                f"assumption:{item.driver_key}": driver_units[item.driver_key]
                for item in value.strategy_assumptions.driver_paths
            }
        )
        expected_units.update(
            {
                "assumption:required_return": "ratio",
                "assumption:terminal_growth": "ratio",
            }
        )
        expected_units.update(
            {
                f"scenario:{scenario.scenario_id}:{item.driver_key}": ("multiplier")
                for scenario in value.strategy_assumptions.scenario_overrides
                for item in scenario.driver_overrides
                if scenario.scenario_id != "base"
            }
        )
        if value.market_context is not None:
            expected_units.update(
                {
                    f"market:capital:{field}": "million"
                    for field in (
                        "cash",
                        "debt",
                        "minority_interest",
                        "investments",
                        "pension_liabilities",
                        "other_adjustments",
                        "basic_shares",
                        "diluted_shares",
                    )
                }
            )
            bridge = value.market_context.market_bridge
            if bridge.fx_ref is not None:
                expected_units[
                    "market:fx:"
                    f"{bridge.financial_currency.lower()}_"
                    f"{bridge.base_currency.lower()}"
                ] = "quote_per_base"
            expected_units.update(
                {
                    "market:price:"
                    + item.security_external_key.lower().replace(":", "_"): (
                        "per_share"
                    )
                    for item in value.market_context.market_bridge.securities
                }
            )
        for replacement in replacements.values():
            # The persisted decision service validates against the original
            # candidate. This closes unknown/unmapped keys for direct callers.
            key = replacement.key
            if key not in expected_units:
                raise ValidationError(
                    "critical input replacement key is not safely mappable"
                )
            if replacement.unit != expected_units[key]:
                raise ValidationError(
                    "critical input replacement unit must match the governed model unit"
                )
        strategy = cls._strategy_with_overlays(value.strategy_assumptions, replacements)
        required_return = value.required_return
        if overlay := replacements.get("assumption:required_return"):
            required_return = overlay.value
        market = value.market_context
        if market is not None and any(
            key.startswith("market:") for key in replacements
        ):
            assert type(value.critical_inputs_artifact_id) is UUID
            assert isinstance(value.critical_inputs_content_hash, str)
            market = cls._market_with_overlays(
                market,
                replacements,
                critical_inputs_artifact_id=value.critical_inputs_artifact_id,
                critical_inputs_content_hash=value.critical_inputs_content_hash,
            )
        return replace(
            value,
            required_return=required_return,
            strategy_assumptions=strategy,
            market_context=market,
        )

    @staticmethod
    def _evidence_with_overlays(
        facts: tuple[Mapping[str, object], ...],
        replacements: tuple[CriticalInputCandidate, ...],
        unknowns: tuple[CriticalInput, ...],
        *,
        evidence_artifact_id: UUID,
        critical_inputs_artifact_id: UUID | None,
        critical_inputs_content_hash: str | None,
    ) -> tuple[Mapping[str, object], ...]:
        overlays = {item.key: item for item in replacements}
        unknown_keys = {item.key for item in unknowns}
        values = []
        for fact in facts:
            if f"fact:{fact['fact_key']}" in unknown_keys:
                continue
            overlay = overlays.get(f"fact:{fact['fact_key']}")
            if overlay is None:
                values.append(fact)
                continue
            copied = dict(fact)
            copied["value"] = canonical_decimal_string(overlay.value)
            if type(critical_inputs_artifact_id) is not UUID or not isinstance(
                critical_inputs_content_hash, str
            ):
                raise ValidationError("critical input overlay provenance is incomplete")
            copied["source_role"] = "user_assumption"
            copied["source_url"] = (
                "urn:company-research:critical-input-decision:"
                f"{critical_inputs_artifact_id}"
            )
            copied["source_locator"] = (
                f"parent_evidence_artifact_id={evidence_artifact_id};"
                f"parent_raw_hash={fact['raw_hash']};"
                f"critical_input_key={overlay.key}"
            )
            copied["raw_hash"] = critical_inputs_content_hash
            values.append(copied)
        return tuple(values)

    @classmethod
    def effective_evidence_facts(
        cls, value: CompanyResearchBuildInput
    ) -> tuple[Mapping[str, object], ...]:
        evidence = partition_company_research_evidence(
            value.evidence_payload,
            value.cutoff_at,
            mode=value.evidence_build_mode,
        )
        return cls._evidence_with_overlays(
            evidence.eligible_facts,
            value.critical_input_replacements,
            value.critical_input_unknowns,
            evidence_artifact_id=value.evidence_artifact_id,
            critical_inputs_artifact_id=value.critical_inputs_artifact_id,
            critical_inputs_content_hash=value.critical_inputs_content_hash,
        )

    @staticmethod
    def _marked_unknown_gap_key(value: CriticalInput) -> str:
        if (
            value.kind is CriticalInputKind.UNKNOWN
            and value.gap_key is not None
            and value.gap_key.startswith("critical_input_marked_unknown_")
        ):
            return value.gap_key
        return f"critical_input_marked_unknown_{value.input_fingerprint[:16]}"
