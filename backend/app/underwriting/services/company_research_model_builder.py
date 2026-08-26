"""Build a closed company-research model from reviewed evidence and frozen inputs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
import re
from typing import Any
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    BusinessMapArtifact,
    BusinessModuleArtifact,
    CompanyResearchArtifactReference,
    CompanyResearchAssessment,
    CompanyResearchMemoArtifact,
    CompanyResearchModelInput,
    CompanyResearchValidationError,
    DriverInput,
    DriverMapArtifact,
    DriverMetricArtifact,
    EvidenceGapContract,
    FinancialBridgeArtifact,
    JudgmentContextArtifact,
    MarketBridgeArtifact,
    ModelInputState,
    ResearchGap,
    ResearchGapSeverity,
    SCENARIO_FINANCIAL_DRIVER_EQUATIONS,
    SCENARIO_FINANCIAL_DRIVER_KEYS,
    ScenarioArtifact,
    ScenarioDriverOverride,
    ScenarioFinancialBridge,
    ScenarioFinancialDriverForecast,
    ScenarioSetArtifact,
    SourceLineageReference,
    ValuationSetArtifact,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_engine import CompanyResearchEngine


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSIONED_STRATEGY = re.compile(r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*\Z")
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
_SOURCE_REF_FIELDS = frozenset(
    {"source_role", "source_url", "source_locator", "raw_hash"}
)
_SCENARIO_ORDER = ("base", "bull", "bear")
_CLOSED_BY_STRATEGY = frozenset({"forward_model_missing"})
_CLOSED_BY_MARKET = frozenset({"market_price_missing", "usd_cny_fx_missing"})


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
    if not isinstance(value, Mapping) or not all(
        isinstance(key, str) for key in value
    ):
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
class FrozenMarketContext:
    """An exact, already-resolved market boundary; the builder never fetches it."""

    price_snapshot_ids: tuple[UUID, ...]
    fx_snapshot_ids: tuple[UUID, ...]
    capital_structure_snapshot_id: UUID
    security_rights_ids: tuple[UUID, ...]
    snapshot_ids: tuple[UUID, ...]
    market_at: datetime
    market_bridge: MarketBridgeArtifact

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
            raise ValidationError("frozen market snapshot refs must not contain duplicates")
        if set(snapshot_ids) != set(expected) or len(snapshot_ids) != len(expected):
            raise ValidationError(
                "snapshot_ids must exactly cover the frozen market references"
            )
        object.__setattr__(self, "market_at", _aware_utc(self.market_at, "market_at"))
        if type(self.market_bridge) is not MarketBridgeArtifact:
            raise ValidationError("frozen market context requires a typed market bridge")
        security_count = len(self.market_bridge.securities)
        if (
            len(price_ids) != security_count
            or len(rights_ids) != security_count
            or len(fx_ids) != 1
        ):
            raise ValidationError(
                "frozen market context requires one price and rights ref per security and one FX ref"
            )


@dataclass(frozen=True, slots=True)
class ScenarioAssumption:
    """One mechanism-specific set of named driver multipliers."""

    scenario_id: str
    mechanism_id: str
    driver_overrides: tuple[ScenarioDriverOverride, ...]

    def __post_init__(self) -> None:
        if self.scenario_id not in set(_SCENARIO_ORDER):
            raise ValidationError("strategy scenario must be base, bull, or bear")
        _text(self.mechanism_id, "strategy scenario mechanism_id")
        if not isinstance(self.driver_overrides, tuple) or not all(
            type(item) is ScenarioDriverOverride for item in self.driver_overrides
        ):
            raise ValidationError("strategy scenario overrides must be typed")
        keys = tuple(item.driver_key for item in self.driver_overrides)
        if keys != SCENARIO_FINANCIAL_DRIVER_KEYS:
            raise ValidationError(
                "strategy scenario overrides must cover the six drivers in canonical order"
            )
        if self.scenario_id == "base" and any(
            item.value != Decimal("1") for item in self.driver_overrides
        ):
            raise ValidationError("base strategy scenario must preserve the baseline")
        if self.scenario_id != "base" and all(
            item.value == Decimal("1") for item in self.driver_overrides
        ):
            raise ValidationError("non-base strategy scenarios must change their mechanism")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "mechanism_id": self.mechanism_id,
            "driver_overrides": tuple(
                {
                    "driver_key": item.driver_key,
                    "value": str(item.value),
                }
                for item in self.driver_overrides
            ),
        }


@dataclass(frozen=True, slots=True)
class StrategyAssumptionSet:
    """A mandatory hash-addressed candidate forecast, distinct from evidence."""

    strategy_version: str
    content_hash: str
    first_fiscal_year: int
    driver_paths: tuple[DriverInput, ...]
    scenario_overrides: tuple[ScenarioAssumption, ...]
    terminal_growth: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.strategy_version, str)
            or _VERSIONED_STRATEGY.fullmatch(self.strategy_version) is None
        ):
            raise ValidationError("strategy assumptions require a versioned strategy")
        if type(self.first_fiscal_year) is not int or self.first_fiscal_year < 1900:
            raise ValidationError("strategy assumptions first fiscal year is invalid")
        if not isinstance(self.driver_paths, tuple) or not all(
            type(item) is DriverInput for item in self.driver_paths
        ):
            raise ValidationError("strategy assumptions driver paths must be typed")
        if tuple(item.driver_key for item in self.driver_paths) != (
            SCENARIO_FINANCIAL_DRIVER_KEYS
        ):
            raise ValidationError(
                "strategy assumptions must contain exactly six canonical driver paths"
            )
        for path in self.driver_paths:
            if path.state is not ModelInputState.ASSUMPTION:
                raise CompanyResearchValidationError(
                    "strategy assumption driver paths must use state=assumption"
                )
            if len(path.values) != 5:
                raise ValidationError(
                    "strategy assumption driver paths must contain five years"
                )
            if path.assumption_key is None or not path.assumption_key.startswith(
                f"{self.strategy_version}:"
            ):
                raise ValidationError(
                    "strategy assumption keys must be versioned by strategy_version"
                )
        if (
            not isinstance(self.scenario_overrides, tuple)
            or not all(
                type(item) is ScenarioAssumption for item in self.scenario_overrides
            )
            or tuple(item.scenario_id for item in self.scenario_overrides)
            != _SCENARIO_ORDER
        ):
            raise ValidationError(
                "strategy assumptions must contain canonical Base-Bull-Bear scenarios"
            )
        if len({item.mechanism_id for item in self.scenario_overrides}) != 3:
            raise ValidationError(
                "strategy assumptions require mechanism-specific scenario overrides"
            )
        terminal_growth = _decimal(
            self.terminal_growth, "strategy assumptions terminal_growth"
        )
        if terminal_growth < Decimal("0"):
            raise ValidationError("strategy assumptions terminal growth is invalid")
        _hash(self.content_hash, "strategy assumptions content hash")
        expected_hash = self.calculate_content_hash(
            strategy_version=self.strategy_version,
            first_fiscal_year=self.first_fiscal_year,
            driver_paths=self.driver_paths,
            scenario_overrides=self.scenario_overrides,
            terminal_growth=self.terminal_growth,
        )
        if self.content_hash != expected_hash:
            raise ValidationError("strategy assumptions content hash does not match")

    @classmethod
    def calculate_content_hash(
        cls,
        *,
        strategy_version: str,
        first_fiscal_year: int,
        driver_paths: tuple[DriverInput, ...],
        scenario_overrides: tuple[ScenarioAssumption, ...],
        terminal_growth: Decimal,
    ) -> str:
        return canonical_hash(
            {
                "schema_version": "company-research-strategy-assumptions.v1",
                "strategy_version": strategy_version,
                "first_fiscal_year": first_fiscal_year,
                "driver_paths": tuple(
                    item.canonical_payload() for item in driver_paths
                ),
                "scenario_overrides": tuple(
                    item.canonical_payload() for item in scenario_overrides
                ),
                "terminal_growth": str(terminal_growth),
            }
        )


@dataclass(frozen=True, slots=True)
class CompanyResearchBuildInput:
    project_id: UUID
    cutoff_at: datetime
    required_return: Decimal
    evidence_artifact_id: UUID
    evidence_content_hash: str
    evidence_payload: Mapping[str, object]
    gap_payload: Mapping[str, object]
    source_refs: tuple[dict[str, str], ...]
    strategy_assumptions: StrategyAssumptionSet
    market_context: FrozenMarketContext | None

    def __post_init__(self) -> None:
        _uuid(self.project_id, "project_id")
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
        if type(self.strategy_assumptions) is not StrategyAssumptionSet:
            raise ValidationError("strategy assumptions are required")
        if self.market_context is not None and type(self.market_context) is not FrozenMarketContext:
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

    @staticmethod
    def _validate_source_refs(value: object) -> None:
        if not isinstance(value, tuple) or not value:
            raise ValidationError("source_refs must be a non-empty tuple")
        canonical: list[tuple[tuple[str, str], ...]] = []
        for item in value:
            if not isinstance(item, dict) or set(item) != _SOURCE_REF_FIELDS:
                raise ValidationError("source_refs contain invalid fields")
            for field in _SOURCE_REF_FIELDS - {"raw_hash"}:
                _text(item.get(field), f"source_refs.{field}")
            _hash(item.get("raw_hash"), "source_refs.raw_hash")
            canonical.append(tuple(sorted(item.items())))
        if len(set(canonical)) != len(canonical):
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
            raise ValidationError("evidence fact company does not match evidence payload")
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
        _parse_timestamp(fact.get("published_at"), "evidence fact published_at")
        if available_at > cutoff_at:
            raise ValidationError("evidence fact is unavailable at model cutoff")
        review = fact.get("review_decision")
        if review == "rejected":
            raise ValidationError("rejected facts cannot be model inputs")
        if review != "confirmed":
            raise ValidationError("evidence must be reviewed before model input")
        reviewed.append(fact)
    return tuple(reviewed)


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

    def build(self, value: CompanyResearchBuildInput) -> CompanyResearchBuildResult:
        if type(value) is not CompanyResearchBuildInput:
            raise ValidationError("company research builder input must be typed")
        reviewed = _validate_evidence_payload(value.evidence_payload, value.cutoff_at)
        raw_gaps = _validate_gap_payload(value.gap_payload, value.evidence_payload)
        value._validate_source_refs(value.source_refs)
        if canonical_hash(value.evidence_payload) != value.evidence_content_hash:
            raise ValidationError("evidence content hash does not match payload")
        self._validate_authenticated_sources(value.source_refs, reviewed)

        evidence_refs = self._evidence_refs(reviewed)
        active_gaps = self._active_gaps(raw_gaps, value.market_context)
        business_map = self._business_map(reviewed, active_gaps, evidence_refs)
        assumption_refs = self._assumption_refs(value.strategy_assumptions)
        driver_map = self._driver_map(
            business_map, value.strategy_assumptions, assumption_refs
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
            operating_baseline_available=bool(reviewed),
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
            terminal_growth=value.strategy_assumptions.terminal_growth,
            market_bridge=(
                value.market_context.market_bridge
                if value.market_context is not None
                else None
            ),
            judgment_context=judgment,
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
    def _active_gaps(
        raw_gaps: tuple[Mapping[str, object], ...],
        market_context: FrozenMarketContext | None,
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
        return tuple(
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

    @staticmethod
    def _business_map(
        facts: tuple[Mapping[str, object], ...],
        gaps: tuple[ResearchGap, ...],
        refs: tuple[SourceLineageReference, ...],
    ) -> BusinessMapArtifact:
        refs_by_key = {item.fact_key: item for item in refs}
        modules = sorted({str(item["business_module"]) for item in facts})
        return BusinessMapArtifact(
            modules=tuple(
                BusinessModuleArtifact(
                    module_key=module,
                    revenue_sources=tuple(
                        sorted(
                            {
                                str(item["metric_key"])
                                for item in facts
                                if item["business_module"] == module
                            }
                        )
                    ),
                    cost_structure=(),
                    capital_needs=(),
                    fact_refs=tuple(
                        refs_by_key[str(item["fact_key"])]
                        for item in facts
                        if item["business_module"] == module
                    ),
                    gap_refs=tuple(
                        gap.code for gap in gaps if gap.module_key == module
                    ),
                )
                for module in modules
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
        business_map: BusinessMapArtifact,
        assumptions: StrategyAssumptionSet,
        assumption_refs: tuple[SourceLineageReference, ...],
    ) -> DriverMapArtifact:
        module_key = business_map.modules[0].module_key
        refs_by_driver = {
            path.driver_key: ref
            for path, ref in zip(
                assumptions.driver_paths, assumption_refs, strict=True
            )
        }
        return DriverMapArtifact(
            drivers=tuple(
                DriverMetricArtifact(
                    driver_key=path.driver_key,
                    module_key=module_key,
                    fact_refs=(),
                    assumption_refs=(refs_by_driver[path.driver_key],),
                    equation=SCENARIO_FINANCIAL_DRIVER_EQUATIONS[path.driver_key],
                    output_metric=path.driver_key,
                )
                for path in assumptions.driver_paths
            )
        )

    @staticmethod
    def _scenario_inputs(
        assumptions: StrategyAssumptionSet,
        assumption_refs: tuple[SourceLineageReference, ...],
    ) -> tuple[ScenarioSetArtifact, tuple[ScenarioFinancialBridge, ...]]:
        forecasts = tuple(
            ScenarioFinancialDriverForecast(
                driver_key=path.driver_key,
                values=path.values,
                fact_refs=(reference,),
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
            market_refs = (
                bridge.capital_structure.source_ref,
                bridge.fx_ref,
                *(
                    ref
                    for security in bridge.securities
                    for ref in (security.rights_ref, security.price_ref)
                ),
            )
        values = (*evidence_refs, *assumption_refs, gap_ref, *market_refs)
        if len({item.fact_key for item in values}) != len(values):
            raise ValidationError("model source lineage must not contain duplicate refs")
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
            return CompanyResearchArtifactReference(kind, canonical_hash(asdict(artifact)))

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
        )
