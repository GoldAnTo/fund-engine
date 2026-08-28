"""Build a closed company-research model from reviewed evidence and frozen inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
import re
from typing import Any
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    BusinessMapArtifact,
    BusinessModuleArtifact,
    ClassifiedBusinessEvidenceArtifact,
    CompanyResearchArtifactReference,
    CompanyResearchAssessment,
    CompanyResearchIdentitySet,
    CompanyResearchMemoArtifact,
    CompanyResearchModelInput,
    canonical_decimal_string,
    DriverMapArtifact,
    DriverMetricArtifact,
    EvidenceGapContract,
    FinancialBridgeArtifact,
    JudgmentContextArtifact,
    MarketBridgeArtifact,
    MarketEquityComponentReference,
    ModelInputState,
    ResearchGap,
    ResearchGapSeverity,
    ReverseDcfRequest,
    SCENARIO_FINANCIAL_DRIVER_EQUATIONS,
    ScenarioArtifact,
    ScenarioFinancialBridge,
    ScenarioFinancialDriverForecast,
    ScenarioSetArtifact,
    SourceLineageReference,
    ValuationSetArtifact,
)
from app.underwriting.domain.company_research_contracts import (
    CompanyResearchDriverBinding as CompanyResearchDriverBinding,
    CompanyResearchMetricClassification as CompanyResearchMetricClassification,
    CompanyResearchModelModule as CompanyResearchModelModule,
    CompanyResearchModelTemplate,
    CompanyResearchOperatingBaselineRequirement as CompanyResearchOperatingBaselineRequirement,
    CompanyResearchOperatingDriverBinding as CompanyResearchOperatingDriverBinding,
    CompanyResearchScenarioMechanism as CompanyResearchScenarioMechanism,
    ScenarioAssumption as ScenarioAssumption,
    StrategyAssumptionSet,
)
from app.underwriting.domain.company_research_market_contracts import (
    FrozenMarketSnapshotBinding,
    FrozenMarketSnapshotRole,
    FrozenRawComponentReference as _FrozenRawComponentReferenceContract,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.domain.company_research_provenance import canonical_source_refs
from app.underwriting.domain.company_research_artifact_codec import (
    CompanyResearchArtifactCodec,
)
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
_GAP_PAYLOAD_FIELDS = frozenset(
    {"fixture_content_hash", "company_external_key", "gaps"}
)
_GAP_FIELDS = frozenset({"gap_key", "business_module", "reason"})
_SCENARIO_ORDER = ("base", "bull", "bear")
_CLOSED_BY_STRATEGY = frozenset({"forward_model_missing"})
_CLOSED_BY_MARKET = frozenset({"market_price_missing", "usd_cny_fx_missing"})
_BUILDER_GAP_PREFIX = "builder_generated_"

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
        if self.component_key not in {"class_a", "class_b", "class_c"}:
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
        fx_ids = _uuid_tuple(self.fx_snapshot_ids, "fx_snapshot_ids")
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
        if (
            len(price_ids) != security_count
            or len(rights_ids) != security_count
            or len(fx_ids) != 1
        ):
            raise ValidationError(
                "frozen market context requires one price and rights ref per security and one FX ref"
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
            (FrozenMarketSnapshotRole.FX, None): bridge.fx_ref,
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
            or tuple(item.component_key for item in self.equity_components)
            != ("class_a", "class_b", "class_c")
        ):
            raise ValidationError(
                "frozen market context requires exact Class A-B-C equity components"
            )
        securities = {
            item.security_external_key: item for item in self.market_bridge.securities
        }
        prices = {
            item.security_external_key: item
            for item in self.snapshot_bindings
            if item.role is FrozenMarketSnapshotRole.PRICE
        }
        class_a, class_b, class_c = self.equity_components
        expected_proxy = {
            "class_a": "NASDAQ:GOOGL",
            "class_b": "NASDAQ:GOOGL",
            "class_c": "NASDAQ:GOOG",
        }
        if any(
            item.price_proxy_security_external_key != expected_proxy[item.component_key]
            for item in self.equity_components
        ):
            raise ValidationError(
                "Class B price proxy policy must explicitly use GOOGL"
            )
        if (
            set(securities) != {"NASDAQ:GOOG", "NASDAQ:GOOGL"}
            or class_a.economic_units
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
            item.security_external_key: item.market_price_usd
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
        if (
            self.market_context is not None
            and self.market_context.market_at > self.cutoff_at
        ):
            raise ValidationError("frozen market context must not be after cutoff_at")
        # Re-run the payload validation here and again in build(), because a
        # Mapping can be mutated by its owner despite this frozen outer value.
        _validate_evidence_payload(self.evidence_payload, self.cutoff_at)
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
    payload_value: object, cutoff_at: datetime
) -> tuple[Mapping[str, object], ...]:
    payload = _mapping(payload_value, "evidence_payload")
    if set(payload) != _EVIDENCE_FIELDS:
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
    reviewed: list[Mapping[str, object]] = []
    keys: set[str] = set()
    for raw in facts:
        fact = _mapping(raw, "evidence fact")
        if "review_decision" not in fact:
            raise ValidationError("evidence must be reviewed before model input")
        if set(fact) != _FACT_FIELDS:
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
        if review not in {"confirmed", "rejected"}:
            raise ValidationError("evidence must be reviewed before model input")
        _model_fact_decimal(fact)
        if fact.get("value_kind") not in {
            "reported",
            "derived",
            "management_guidance",
        }:
            raise ValidationError("model fact value_kind is unsupported")
        _text(fact.get("currency"), "model fact currency")
        _text(fact.get("unit"), "model fact unit")
        _model_fact_period(fact)
        reviewed.append(fact)
    confirmed = tuple(
        fact for fact in reviewed if fact["review_decision"] == "confirmed"
    )
    for metric_key in {str(fact["metric_key"]) for fact in confirmed}:
        currencies = {
            str(fact["currency"])
            for fact in confirmed
            if fact["metric_key"] == metric_key
        }
        units = {
            str(fact["unit"]) for fact in confirmed if fact["metric_key"] == metric_key
        }
        if len(currencies) != 1 or len(units) != 1:
            raise ValidationError(
                "model fact currency and unit must be consistent per metric"
            )
    return tuple(reviewed)


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
        _validate_evidence_payload(value.evidence_payload, value.cutoff_at)
        raw_gaps = _validate_gap_payload(value.gap_payload, value.evidence_payload)
        expected_source_gaps = cls._active_gaps(raw_gaps, value.market_context, ())
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

    def build(self, value: CompanyResearchBuildInput) -> CompanyResearchBuildResult:
        if type(value) is not CompanyResearchBuildInput:
            raise ValidationError("company research builder input must be typed")
        reviewed = _validate_evidence_payload(value.evidence_payload, value.cutoff_at)
        raw_gaps = _validate_gap_payload(value.gap_payload, value.evidence_payload)
        value._validate_source_refs(value.source_refs)
        value._validate_identity_bindings()
        if canonical_hash(value.evidence_payload) != value.evidence_content_hash:
            raise ValidationError("evidence content hash does not match payload")
        self._validate_authenticated_sources(value.source_refs, reviewed)
        self._validate_template_evidence(value.model_template, reviewed, raw_gaps)
        confirmed = tuple(
            fact for fact in reviewed if fact["review_decision"] == "confirmed"
        )
        self._validate_scenario_mapping(
            value.model_template, value.strategy_assumptions
        )
        evidence_refs = self._evidence_refs(confirmed)
        baseline_gaps = self._baseline_gaps(value.model_template, confirmed)
        active_gaps = self._active_gaps(raw_gaps, value.market_context, baseline_gaps)
        active_gaps = tuple(
            sorted(
                (
                    *active_gaps,
                    *self._operating_driver_gaps(value.model_template, confirmed),
                ),
                key=lambda item: item.code,
            )
        )
        active_gaps = self._complete_module_gaps(
            value.model_template, confirmed, active_gaps
        )
        business_map = self._business_map(
            value.model_template, confirmed, active_gaps, evidence_refs
        )
        assumption_refs = self._assumption_refs(value.strategy_assumptions)
        driver_map = self._driver_map(
            value.model_template,
            confirmed,
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
        judgment = JudgmentContextArtifact(
            operating_baseline_available=not baseline_gaps,
            financial_bridge_closed=True,
            market_security_bridge_available=value.market_context is not None,
            strongest_counterevidence=(),
            next_verification_events=tuple(gap.message for gap in active_gaps),
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
                value.market_context.market_bridge
                if value.market_context is not None
                else None
            ),
            judgment_context=judgment,
            reverse_dcf=(
                value.market_context.reverse_dcf_request
                if value.market_context is not None
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
                    for item in value.market_context.equity_components
                )
                if value.market_context is not None
                else ()
            ),
        )
        compiled = self._engine.compile(typed)
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
    ) -> tuple[ResearchGap, ...]:
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
                    else ResearchGapSeverity.HIGH
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
    ) -> tuple[ResearchGap, ...]:
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
                            currency=str(item["currency"]),
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
            class_b = market_context.equity_components[1]
            assert class_b.legal_rights_ref is not None
            assert class_b.price_proxy_ref is not None
            market_refs = (
                bridge.capital_structure.source_ref,
                bridge.capital_structure.policy_ref,
                bridge.fx_ref,
                *(
                    ref
                    for security in bridge.securities
                    for ref in (security.rights_ref, security.price_ref)
                ),
                class_b.legal_rights_ref,
                class_b.unit_source_ref,
                class_b.price_proxy_ref,
            )
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
