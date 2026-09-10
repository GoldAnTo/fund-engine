"""Authenticated, non-current CATL Company Research answerable case.

Only normalized values, locators, and digests are retained.  The annual report
itself is not bundled; its digest is the already-governed CNINFO digest used by
the older evidence-only CATL fixture.  The exchange row is retained as a small
normalized record and authenticated independently.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CompanyResearchModule,
    DriverInput,
    ModelInputState,
    ScenarioDriverOverride,
)
from app.underwriting.domain.company_research_contracts import (
    ScenarioAssumption,
    SensitivityAssumption,
    StrategyAssumptionSet,
    StrategyAssumptionValue,
)
from app.underwriting.hashing import canonical_hash

_ROOT = Path(__file__).resolve().parent
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMPANY_KEY = "CN:300750:COMPANY"
_SECURITY_KEYS = ("SZSE:300750",)
_CUTOFF = datetime(2025, 11, 6, 15, 59, 59, tzinfo=UTC)
_ANNUAL_REPORT_HASH = "b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad"
BUNDLED_MANIFEST_CONTENT_SHA256 = (
    "81bbc607f480ecc196590fd69d9dddcd0d86612b587527755bf8bca96b0f539b"
)

REQUIRED_INPUT_KEYS = frozenset(
    {
        "revenue_2024",
        "operating_profit_2024",
        "income_tax_expense_2024",
        "depreciation_2024",
        "capex_2024",
        "working_capital_change_2024",
        "basic_shares_2024",
        "cash_2024",
        "debt_2024",
    }
)


class CatlAnswerableCaseFixtureError(ValidationError):
    """The frozen answerable fixture failed authentication or validation."""


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CatlAnswerableCaseFixtureError(
                f"CATL answerable fixture contains duplicate key {key}"
            )
        result[key] = value
    return result


def _read_json(path: Path) -> tuple[bytes, dict[str, object]]:
    try:
        contents = path.read_bytes()
        value = json.loads(contents, object_pairs_hook=_strict_object)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {path.name} is unreadable"
        ) from exc
    if not isinstance(value, dict):
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {path.name} must be an object"
        )
    return contents, value


def _exact(value: object, keys: frozenset[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} has invalid fields"
        )
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be canonical text"
        )
    return value


def _digest(value: object, field: str) -> str:
    result = _text(value, field)
    if _SHA256.fullmatch(result) is None:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be a SHA-256 digest"
        )
    return result


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, str):
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be a Decimal string"
        )
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be a Decimal string"
        ) from exc
    if not result.is_finite() or format(result, "f") != value:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be canonical Decimal text"
        )
    return result


def _timestamp(value: object, field: str) -> datetime:
    text = _text(value, field)
    try:
        result = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be ISO-8601"
        ) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be timezone-aware"
        )
    result = result.astimezone(UTC)
    if result.isoformat() != text:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must round-trip canonically"
        )
    return result


def _date(value: object, field: str) -> date:
    text = _text(value, field)
    try:
        result = date.fromisoformat(text)
    except ValueError as exc:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must be an ISO date"
        ) from exc
    if result.isoformat() != text:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} must round-trip canonically"
        )
    return result


def _content_hash(raw: Mapping[str, object], field: str) -> str:
    expected = _digest(raw.get("content_hash"), f"{field}.content_hash")
    actual = canonical_hash(
        {key: value for key, value in raw.items() if key != "content_hash"}
    )
    if expected != actual:
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} content hash mismatch"
        )
    return expected


@dataclass(frozen=True, slots=True)
class CatlSourceFact:
    fact_key: str
    company_external_key: str
    business_module: str
    metric_key: str
    value: Decimal
    value_kind: Literal["reported", "derived"]
    currency: str | None
    unit: str
    period_start: date
    period_end: date
    published_at: datetime
    available_at: datetime
    source_role: Literal["regulatory_filing"]
    source_url: str
    source_locator: str
    raw_hash: str
    equation_id: str | None
    parent_fact_keys: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        value = {
            "fact_key": self.fact_key,
            "company_external_key": self.company_external_key,
            "business_module": self.business_module,
            "metric_key": self.metric_key,
            "value": format(self.value, "f"),
            "value_kind": self.value_kind,
            "currency": self.currency,
            "unit": self.unit,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "published_at": self.published_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "source_role": self.source_role,
            "source_url": self.source_url,
            "source_locator": self.source_locator,
            "raw_hash": self.raw_hash,
        }
        if self.value_kind == "derived":
            value["equation_id"] = self.equation_id
            value["parent_fact_keys"] = list(self.parent_fact_keys)
        return value


@dataclass(frozen=True, slots=True)
class CatlResearchGap:
    gap_key: str
    business_module: str
    reason: str

    def payload(self) -> dict[str, str]:
        return {
            "gap_key": self.gap_key,
            "business_module": self.business_module,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CatlMetricContract:
    business_module: str
    metric_key: str
    unit: str
    currency: str | None
    value_kind: str


@dataclass(frozen=True, slots=True)
class CatlCapturedAssumptionNumber:
    value: Decimal
    state: str
    assumption_key: str
    rationale: str
    equation: str


@dataclass(frozen=True, slots=True)
class CatlCapturedStrategyDriverPath:
    driver_key: str
    values: tuple[Decimal, ...]
    state: str
    assumption_key: str
    rationale: str
    equation: str


@dataclass(frozen=True, slots=True)
class CatlCapturedStrategyScenario:
    scenario_id: str
    mechanism_id: str
    driver_overrides: tuple[tuple[str, CatlCapturedAssumptionNumber], ...]


@dataclass(frozen=True, slots=True)
class CatlCapturedSensitivityAssumption:
    variable_key: str
    low: CatlCapturedAssumptionNumber
    high: CatlCapturedAssumptionNumber


@dataclass(frozen=True, slots=True)
class CatlStrategyAssumptionBundle:
    content_hash: str
    strategy_version: str
    first_fiscal_year: int
    driver_paths: tuple[CatlCapturedStrategyDriverPath, ...]
    scenario_overrides: tuple[CatlCapturedStrategyScenario, ...]
    required_return: CatlCapturedAssumptionNumber
    terminal_growth: CatlCapturedAssumptionNumber
    sensitivity_assumptions: tuple[CatlCapturedSensitivityAssumption, ...]


@dataclass(frozen=True, slots=True)
class CatlCapturedPrice:
    security_external_key: str
    value: Decimal
    currency: str
    unit: str
    price_type: str
    adjustment_basis: str
    market_at: datetime
    available_at: datetime
    source_role: str
    source_url: str
    source_locator: str
    raw_hash: str
    normalized_record: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class CatlCapitalStructure:
    cash_fact_key: str
    debt_fact_key: str
    share_count_fact_key: str
    cash: Decimal
    debt: Decimal
    shares: Decimal
    currency: str
    monetary_unit: str
    share_unit: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class CatlMarketInputBundle:
    content_hash: str
    company_external_key: str
    security_external_keys: tuple[str, ...]
    base_currency: str
    price: CatlCapturedPrice
    capital_structure: CatlCapitalStructure

    def verify_price_record_hash(self) -> str:
        actual = canonical_hash(dict(self.price.normalized_record))
        if actual != self.price.raw_hash:
            raise CatlAnswerableCaseFixtureError(
                "CATL exchange normalized record hash mismatch"
            )
        return actual


@dataclass(frozen=True, slots=True)
class CatlAnswerableCaseFixture:
    cutoff: datetime
    content_hash: str
    company_external_key: str
    security_external_keys: tuple[str, ...]
    base_currency: str
    business_modules: tuple[CompanyResearchModule, ...]
    facts: tuple[CatlSourceFact, ...]
    metric_contracts: tuple[CatlMetricContract, ...]
    research_gaps: tuple[CatlResearchGap, ...]
    counterevidence_fact_keys: tuple[str, ...]
    next_verification_events: tuple[str, ...]
    strategy_assumptions: CatlStrategyAssumptionBundle
    market_inputs: CatlMarketInputBundle

    def fact(self, key: str) -> CatlSourceFact:
        matches = tuple(item for item in self.facts if item.fact_key == key)
        if len(matches) != 1:
            raise KeyError(
                f"CATL answerable fixture does not contain exactly one {key}"
            )
        return matches[0]


_FACT_KEYS = frozenset(
    {
        "fact_key",
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
        "equation_id",
        "parent_fact_keys",
    }
)


def _parse_fact(
    value: object,
    *,
    cutoff: datetime,
    modules: frozenset[str],
) -> CatlSourceFact:
    raw = _exact(value, _FACT_KEYS, "source fact")
    kind = _text(raw.get("value_kind"), "fact.value_kind")
    if kind not in {"reported", "derived"}:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture fact value_kind is unsupported"
        )
    module = _text(raw.get("business_module"), "fact.business_module")
    if module not in modules:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture fact business module is unsupported"
        )
    published_at = _timestamp(raw.get("published_at"), "fact.published_at")
    available_at = _timestamp(raw.get("available_at"), "fact.available_at")
    if published_at > available_at or available_at > cutoff:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture fact is outside its availability boundary"
        )
    period_start = _date(raw.get("period_start"), "fact.period_start")
    period_end = _date(raw.get("period_end"), "fact.period_end")
    if period_start > period_end:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture fact period is invalid"
        )
    currency = raw.get("currency")
    if currency is not None and currency != "CNY":
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture fact currency must be CNY or null"
        )
    equation = raw.get("equation_id")
    parents = raw.get("parent_fact_keys")
    if not isinstance(parents, list) or not all(
        isinstance(item, str) and item and item == item.strip() for item in parents
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture parent facts must be canonical"
        )
    if kind == "derived":
        if (
            not isinstance(equation, str)
            or not equation
            or tuple(parents) != tuple(sorted(set(parents)))
            or not parents
        ):
            raise CatlAnswerableCaseFixtureError(
                "CATL answerable fixture derived fact provenance is incomplete"
            )
    elif equation is not None or parents:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture reported fact cannot carry derived provenance"
        )
    source_url = _text(raw.get("source_url"), "fact.source_url")
    if not source_url.startswith("https://static.cninfo.com.cn/"):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture historical fact must use the governed filing"
        )
    raw_hash = _digest(raw.get("raw_hash"), "fact.raw_hash")
    if raw_hash != _ANNUAL_REPORT_HASH:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture filing digest is not governed"
        )
    return CatlSourceFact(
        fact_key=_text(raw.get("fact_key"), "fact.fact_key"),
        company_external_key=_COMPANY_KEY,
        business_module=module,
        metric_key=_text(raw.get("metric_key"), "fact.metric_key"),
        value=_decimal(raw.get("value"), "fact.value"),
        value_kind=kind,  # type: ignore[arg-type]
        currency=currency,  # type: ignore[arg-type]
        unit=_text(raw.get("unit"), "fact.unit"),
        period_start=period_start,
        period_end=period_end,
        published_at=published_at,
        available_at=available_at,
        source_role="regulatory_filing",
        source_url=source_url,
        source_locator=_text(raw.get("source_locator"), "fact.source_locator"),
        raw_hash=raw_hash,
        equation_id=equation if isinstance(equation, str) else None,
        parent_fact_keys=tuple(parents),
    )


def _assumption_number(value: object, field: str) -> CatlCapturedAssumptionNumber:
    raw = _exact(
        value,
        frozenset({"value", "state", "assumption_key", "rationale", "equation"}),
        field,
    )
    state = _text(raw.get("state"), f"{field}.state")
    key = _text(raw.get("assumption_key"), f"{field}.assumption_key")
    if state != "assumption" or not key.startswith("catl-machine-candidate.v1:"):
        raise CatlAnswerableCaseFixtureError(
            f"CATL answerable fixture {field} is not a governed assumption"
        )
    return CatlCapturedAssumptionNumber(
        value=_decimal(raw.get("value"), f"{field}.value"),
        state=state,
        assumption_key=key,
        rationale=_text(raw.get("rationale"), f"{field}.rationale"),
        equation=_text(raw.get("equation"), f"{field}.equation"),
    )


def _strategy(raw: dict[str, object]) -> CatlStrategyAssumptionBundle:
    _exact(
        raw,
        frozenset(
            {
                "schema_version",
                "content_hash",
                "strategy_version",
                "first_fiscal_year",
                "driver_paths",
                "scenario_overrides",
                "required_return",
                "terminal_growth",
                "sensitivity_assumptions",
            }
        ),
        "strategy assumptions",
    )
    if raw.get("schema_version") != "catl.answerable-case.strategy-assumptions.v1":
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture strategy schema is unsupported"
        )
    strategy_version = _text(raw.get("strategy_version"), "strategy_version")
    if strategy_version != "catl-machine-candidate.v1":
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture strategy version is unsupported"
        )
    first_year = raw.get("first_fiscal_year")
    if type(first_year) is not int or first_year != 2025:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture first fiscal year is unsupported"
        )
    path_values = raw.get("driver_paths")
    if not isinstance(path_values, list):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture driver paths must be an array"
        )
    paths: list[CatlCapturedStrategyDriverPath] = []
    for index, value in enumerate(path_values):
        item = _exact(
            value,
            frozenset(
                {
                    "driver_key",
                    "values",
                    "state",
                    "assumption_key",
                    "rationale",
                    "equation",
                }
            ),
            f"strategy driver_paths[{index}]",
        )
        values = item.get("values")
        if not isinstance(values, list) or len(values) != 5:
            raise CatlAnswerableCaseFixtureError(
                "CATL answerable fixture strategy paths require five values"
            )
        metadata = _assumption_number(
            {
                "value": values[0],
                "state": item.get("state"),
                "assumption_key": item.get("assumption_key"),
                "rationale": item.get("rationale"),
                "equation": item.get("equation"),
            },
            f"strategy driver_paths[{index}]",
        )
        paths.append(
            CatlCapturedStrategyDriverPath(
                driver_key=_text(item.get("driver_key"), "strategy driver_key"),
                values=tuple(
                    _decimal(number, "strategy driver value") for number in values
                ),
                state=metadata.state,
                assumption_key=metadata.assumption_key,
                rationale=metadata.rationale,
                equation=metadata.equation,
            )
        )
    expected_drivers = (
        "revenue",
        "operating_margin",
        "cash_tax_rate",
        "depreciation",
        "capex",
        "working_capital_change",
    )
    if tuple(item.driver_key for item in paths) != expected_drivers:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture strategy driver paths are incomplete"
        )
    scenario_values = raw.get("scenario_overrides")
    if not isinstance(scenario_values, list):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture scenarios must be an array"
        )
    scenarios: list[CatlCapturedStrategyScenario] = []
    for index, value in enumerate(scenario_values):
        item = _exact(
            value,
            frozenset({"scenario_id", "mechanism_id", "driver_overrides"}),
            f"strategy scenario[{index}]",
        )
        overrides = item.get("driver_overrides")
        if not isinstance(overrides, list):
            raise CatlAnswerableCaseFixtureError(
                "CATL answerable fixture scenario overrides must be an array"
            )
        parsed_overrides: list[tuple[str, CatlCapturedAssumptionNumber]] = []
        for override in overrides:
            if not isinstance(override, dict):
                raise CatlAnswerableCaseFixtureError(
                    "CATL answerable fixture scenario override must be an object"
                )
            driver_key = _text(override.get("driver_key"), "scenario driver_key")
            parsed_overrides.append(
                (
                    driver_key,
                    _assumption_number(
                        {
                            key: item
                            for key, item in override.items()
                            if key != "driver_key"
                        },
                        "scenario override",
                    ),
                )
            )
        if tuple(key for key, _ in parsed_overrides) != expected_drivers:
            raise CatlAnswerableCaseFixtureError(
                "CATL answerable fixture scenario drivers are incomplete"
            )
        scenarios.append(
            CatlCapturedStrategyScenario(
                scenario_id=_text(item.get("scenario_id"), "scenario_id"),
                mechanism_id=_text(item.get("mechanism_id"), "mechanism_id"),
                driver_overrides=tuple(parsed_overrides),
            )
        )
    if tuple(item.scenario_id for item in scenarios) != ("base", "bull", "bear"):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture scenarios must be Base/Bull/Bear"
        )
    required_return = _assumption_number(raw.get("required_return"), "required_return")
    terminal = _assumption_number(raw.get("terminal_growth"), "terminal_growth")
    sensitivity_values = raw.get("sensitivity_assumptions")
    if not isinstance(sensitivity_values, list):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture sensitivity assumptions must be an array"
        )
    sensitivity = tuple(
        CatlCapturedSensitivityAssumption(
            variable_key=_text(
                _exact(
                    item,
                    frozenset({"variable_key", "low", "high"}),
                    "sensitivity assumption",
                ).get("variable_key"),
                "sensitivity variable_key",
            ),
            low=_assumption_number(item.get("low"), "sensitivity low"),
            high=_assumption_number(item.get("high"), "sensitivity high"),
        )
        for item in sensitivity_values
    )
    typed_sensitivity = tuple(
        SensitivityAssumption(
            variable_key=item.variable_key,
            low=StrategyAssumptionValue(
                value=item.low.value,
                state=ModelInputState.ASSUMPTION,
                assumption_key=item.low.assumption_key,
                rationale=item.low.rationale,
                equation=item.low.equation,
            ),
            high=StrategyAssumptionValue(
                value=item.high.value,
                state=ModelInputState.ASSUMPTION,
                assumption_key=item.high.assumption_key,
                rationale=item.high.rationale,
                equation=item.high.equation,
            ),
        )
        for item in sensitivity
    )
    if tuple(item.variable_key for item in sensitivity) != (
        "required_return",
        "terminal_growth",
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture sensitivities are incomplete"
        )
    typed = StrategyAssumptionSet(
        strategy_version=strategy_version,
        content_hash=StrategyAssumptionSet.calculate_content_hash(
            strategy_version=strategy_version,
            first_fiscal_year=first_year,
            driver_paths=tuple(
                DriverInput(
                    driver_key=item.driver_key,
                    state=ModelInputState.ASSUMPTION,
                    values=item.values,
                    source_refs=(),
                    assumption_key=item.assumption_key,
                    assumption_rationale=item.rationale,
                    assumption_equation=item.equation,
                )
                for item in paths
            ),
            scenario_overrides=tuple(
                ScenarioAssumption(
                    scenario_id=item.scenario_id,
                    mechanism_id=item.mechanism_id,
                    driver_overrides=tuple(
                        ScenarioDriverOverride(
                            driver_key=driver_key,
                            value=number.value,
                            state=ModelInputState.ASSUMPTION,
                            assumption_key=number.assumption_key,
                            rationale=number.rationale,
                            equation=number.equation,
                        )
                        for driver_key, number in item.driver_overrides
                    ),
                )
                for item in scenarios
            ),
            terminal_growth=StrategyAssumptionValue(
                value=terminal.value,
                state=ModelInputState.ASSUMPTION,
                assumption_key=terminal.assumption_key,
                rationale=terminal.rationale,
                equation=terminal.equation,
            ),
            sensitivity_assumptions=typed_sensitivity,
        ),
        first_fiscal_year=first_year,
        driver_paths=tuple(
            DriverInput(
                driver_key=item.driver_key,
                state=ModelInputState.ASSUMPTION,
                values=item.values,
                source_refs=(),
                assumption_key=item.assumption_key,
                assumption_rationale=item.rationale,
                assumption_equation=item.equation,
            )
            for item in paths
        ),
        scenario_overrides=tuple(
            ScenarioAssumption(
                scenario_id=item.scenario_id,
                mechanism_id=item.mechanism_id,
                driver_overrides=tuple(
                    ScenarioDriverOverride(
                        driver_key=driver_key,
                        value=number.value,
                        state=ModelInputState.ASSUMPTION,
                        assumption_key=number.assumption_key,
                        rationale=number.rationale,
                        equation=number.equation,
                    )
                    for driver_key, number in item.driver_overrides
                ),
            )
            for item in scenarios
        ),
        terminal_growth=StrategyAssumptionValue(
            value=terminal.value,
            state=ModelInputState.ASSUMPTION,
            assumption_key=terminal.assumption_key,
            rationale=terminal.rationale,
            equation=terminal.equation,
        ),
        sensitivity_assumptions=typed_sensitivity,
    )
    stored_hash = _digest(raw.get("content_hash"), "strategy content_hash")
    if stored_hash != typed.content_hash:
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture strategy content hash mismatch"
        )
    return CatlStrategyAssumptionBundle(
        content_hash=typed.content_hash,
        strategy_version=strategy_version,
        first_fiscal_year=first_year,
        driver_paths=tuple(paths),
        scenario_overrides=tuple(scenarios),
        required_return=required_return,
        terminal_growth=terminal,
        sensitivity_assumptions=sensitivity,
    )


def _market(raw: dict[str, object], cutoff: datetime) -> CatlMarketInputBundle:
    _exact(
        raw,
        frozenset(
            {
                "schema_version",
                "content_hash",
                "company_external_key",
                "security_external_keys",
                "base_currency",
                "price",
                "capital_structure",
            }
        ),
        "market inputs",
    )
    if raw.get("schema_version") != "catl.answerable-case.market-inputs.v1":
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture market schema is unsupported"
        )
    content_hash = _content_hash(raw, "market inputs")
    price_raw = _exact(
        raw.get("price"),
        frozenset(
            {
                "security_external_key",
                "value",
                "currency",
                "unit",
                "price_type",
                "adjustment_basis",
                "market_at",
                "available_at",
                "source_role",
                "source_url",
                "source_locator",
                "raw_hash",
                "normalized_record",
            }
        ),
        "market price",
    )
    record = price_raw.get("normalized_record")
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "date",
            "open",
            "close",
            "low",
            "high",
            "change",
            "percent_change",
            "volume_lots",
            "turnover_cny",
        }
        or not all(
            isinstance(key, str) and isinstance(item, str)
            for key, item in record.items()
        )
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture exchange record is invalid"
        )
    price = CatlCapturedPrice(
        security_external_key=_text(
            price_raw.get("security_external_key"), "price.security_external_key"
        ),
        value=_decimal(price_raw.get("value"), "price.value"),
        currency=_text(price_raw.get("currency"), "price.currency"),
        unit=_text(price_raw.get("unit"), "price.unit"),
        price_type=_text(price_raw.get("price_type"), "price.price_type"),
        adjustment_basis=_text(
            price_raw.get("adjustment_basis"), "price.adjustment_basis"
        ),
        market_at=_timestamp(price_raw.get("market_at"), "price.market_at"),
        available_at=_timestamp(price_raw.get("available_at"), "price.available_at"),
        source_role=_text(price_raw.get("source_role"), "price.source_role"),
        source_url=_text(price_raw.get("source_url"), "price.source_url"),
        source_locator=_text(price_raw.get("source_locator"), "price.source_locator"),
        raw_hash=_digest(price_raw.get("raw_hash"), "price.raw_hash"),
        normalized_record=MappingProxyType(dict(record)),
    )
    if (
        price.security_external_key != _SECURITY_KEYS[0]
        or price.currency != "CNY"
        or price.unit != "per_share"
        or price.price_type != "official_close"
        or price.adjustment_basis != "unadjusted"
        or price.source_role != "official_exchange"
        or not price.source_url.startswith("https://www.szse.cn/")
        or price.market_at > price.available_at
        or price.available_at > cutoff
        or record["close"] != format(price.value, "f")
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture exchange price is invalid"
        )
    capital_raw = _exact(
        raw.get("capital_structure"),
        frozenset(
            {
                "cash_fact_key",
                "debt_fact_key",
                "share_count_fact_key",
                "cash",
                "debt",
                "shares",
                "currency",
                "monetary_unit",
                "share_unit",
                "policy_version",
            }
        ),
        "capital structure",
    )
    capital = CatlCapitalStructure(
        cash_fact_key=_text(capital_raw.get("cash_fact_key"), "capital.cash_fact_key"),
        debt_fact_key=_text(capital_raw.get("debt_fact_key"), "capital.debt_fact_key"),
        share_count_fact_key=_text(
            capital_raw.get("share_count_fact_key"), "capital.share_count_fact_key"
        ),
        cash=_decimal(capital_raw.get("cash"), "capital.cash"),
        debt=_decimal(capital_raw.get("debt"), "capital.debt"),
        shares=_decimal(capital_raw.get("shares"), "capital.shares"),
        currency=_text(capital_raw.get("currency"), "capital.currency"),
        monetary_unit=_text(capital_raw.get("monetary_unit"), "capital.monetary_unit"),
        share_unit=_text(capital_raw.get("share_unit"), "capital.share_unit"),
        policy_version=_text(
            capital_raw.get("policy_version"), "capital.policy_version"
        ),
    )
    if (
        raw.get("company_external_key") != _COMPANY_KEY
        or raw.get("security_external_keys") != list(_SECURITY_KEYS)
        or raw.get("base_currency") != "CNY"
        or capital.cash_fact_key != "cash_2024"
        or capital.debt_fact_key != "debt_2024"
        or capital.share_count_fact_key != "basic_shares_2024"
        or capital.currency != "CNY"
        or capital.monetary_unit != "CNY million"
        or capital.share_unit != "million shares"
        or capital.policy_version != "catl-equity-bridge.v1"
        or min(capital.cash, capital.debt, capital.shares, price.value) <= 0
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture capital structure is invalid"
        )
    result = CatlMarketInputBundle(
        content_hash=content_hash,
        company_external_key=_COMPANY_KEY,
        security_external_keys=_SECURITY_KEYS,
        base_currency="CNY",
        price=price,
        capital_structure=capital,
    )
    result.verify_price_record_hash()
    return result


def load_catl_answerable_case_fixture(
    root: Path | None = None,
) -> CatlAnswerableCaseFixture:
    """Load the complete CATL case and fail closed on any byte or graph drift."""
    fixture_root = root or _ROOT
    manifest_bytes, manifest = _read_json(fixture_root / "manifest.json")
    if (
        root is None
        and hashlib.sha256(manifest_bytes).hexdigest()
        != BUNDLED_MANIFEST_CONTENT_SHA256
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture bundled manifest digest mismatch"
        )
    _exact(
        manifest,
        frozenset(
            {
                "schema_version",
                "content_hash",
                "cutoff",
                "company_external_key",
                "security_external_keys",
                "base_currency",
                "files",
            }
        ),
        "manifest",
    )
    if manifest.get("schema_version") != "catl.answerable-case.manifest.v1":
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture manifest schema is unsupported"
        )
    manifest_hash = _content_hash(manifest, "manifest")
    cutoff = _timestamp(manifest.get("cutoff"), "manifest.cutoff")
    if (
        cutoff != _CUTOFF
        or manifest.get("company_external_key") != _COMPANY_KEY
        or manifest.get("security_external_keys") != list(_SECURITY_KEYS)
        or manifest.get("base_currency") != "CNY"
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture manifest identity is invalid"
        )
    files = manifest.get("files")
    expected_names = (
        "business_map.json",
        "source_facts.json",
        "strategy_assumptions.json",
        "market_inputs.json",
    )
    if (
        not isinstance(files, list)
        or tuple(item.get("name") if isinstance(item, dict) else None for item in files)
        != expected_names
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture manifest files are invalid"
        )
    parsed: dict[str, dict[str, object]] = {}
    for item in files:
        entry = _exact(item, frozenset({"name", "content_hash"}), "manifest file")
        name = _text(entry.get("name"), "manifest file.name")
        contents, value = _read_json(fixture_root / name)
        if hashlib.sha256(contents).hexdigest() != _digest(
            entry.get("content_hash"), "manifest file.content_hash"
        ):
            raise CatlAnswerableCaseFixtureError(
                f"CATL answerable fixture {name} file hash mismatch"
            )
        parsed[name] = value
    business = _exact(
        parsed["business_map.json"],
        frozenset(
            {"schema_version", "content_hash", "company_external_key", "modules"}
        ),
        "business map",
    )
    if (
        business.get("schema_version") != "catl.answerable-case.business-map.v1"
        or business.get("company_external_key") != _COMPANY_KEY
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture business map is invalid"
        )
    _content_hash(business, "business map")
    module_values = business.get("modules")
    if not isinstance(module_values, list):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture modules must be an array"
        )
    modules = tuple(
        CompanyResearchModule(
            key=_text(
                _exact(item, frozenset({"key", "label"}), "module").get("key"),
                "module.key",
            ),
            label=_text(item.get("label"), "module.label"),
        )
        for item in module_values
    )
    module_keys = frozenset(item.key for item in modules)
    if len(module_keys) != len(modules):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture modules must be unique"
        )
    source = _exact(
        parsed["source_facts.json"],
        frozenset(
            {
                "schema_version",
                "content_hash",
                "company_external_key",
                "security_external_keys",
                "base_currency",
                "facts",
                "metric_contracts",
                "research_gaps",
                "counterevidence_fact_keys",
                "next_verification_events",
            }
        ),
        "source facts",
    )
    if (
        source.get("schema_version") != "catl.answerable-case.source-facts.v1"
        or source.get("company_external_key") != _COMPANY_KEY
        or source.get("security_external_keys") != list(_SECURITY_KEYS)
        or source.get("base_currency") != "CNY"
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture source identity is invalid"
        )
    _content_hash(source, "source facts")
    fact_values = source.get("facts")
    if not isinstance(fact_values, list):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture facts must be an array"
        )
    facts = tuple(
        _parse_fact(item, cutoff=cutoff, modules=module_keys) for item in fact_values
    )
    facts_by_key = {item.fact_key: item for item in facts}
    if len(facts_by_key) != len(facts) or not REQUIRED_INPUT_KEYS.issubset(
        facts_by_key
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture required inputs are incomplete"
        )
    if any(facts_by_key[key].value == 0 for key in REQUIRED_INPUT_KEYS):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture required input cannot be zero"
        )
    metric_values = source.get("metric_contracts")
    if not isinstance(metric_values, list):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture governed metric contracts must be an array"
        )
    metric_contracts = tuple(
        CatlMetricContract(
            business_module=_text(
                _exact(
                    item,
                    frozenset(
                        {
                            "business_module",
                            "metric_key",
                            "unit",
                            "currency",
                            "value_kind",
                        }
                    ),
                    "governed metric contract",
                ).get("business_module"),
                "governed metric contract business_module",
            ),
            metric_key=_text(
                item.get("metric_key"), "governed metric contract metric_key"
            ),
            unit=_text(item.get("unit"), "governed metric contract unit"),
            currency=(
                _text(item.get("currency"), "governed metric contract currency")
                if item.get("currency") is not None
                else None
            ),
            value_kind=_text(
                item.get("value_kind"), "governed metric contract value_kind"
            ),
        )
        for item in metric_values
    )
    contract_by_metric = {
        (item.business_module, item.metric_key): item for item in metric_contracts
    }
    fact_metric_keys = {(item.business_module, item.metric_key) for item in facts}
    if (
        len(contract_by_metric) != len(metric_contracts)
        or set(contract_by_metric) != fact_metric_keys
        or any(
            (
                fact.unit,
                fact.currency,
                fact.value_kind,
            )
            != (
                contract_by_metric[(fact.business_module, fact.metric_key)].unit,
                contract_by_metric[(fact.business_module, fact.metric_key)].currency,
                contract_by_metric[(fact.business_module, fact.metric_key)].value_kind,
            )
            for fact in facts
        )
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture governed metric contract does not match facts"
        )
    if any(
        item.value_kind == "derived"
        and any(parent not in facts_by_key for parent in item.parent_fact_keys)
        for item in facts
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture derived fact ancestry is incomplete"
        )
    derived_equations = {
        "catl.effective-tax-rate.v1": (
            frozenset({"income_tax_expense_2024", "profit_before_tax_2024"}),
            lambda: (
                facts_by_key["income_tax_expense_2024"].value
                / facts_by_key["profit_before_tax_2024"].value
            ),
        ),
        "catl.change-in-net-working-capital.v1": (
            frozenset(
                {
                    "inventory_cash_flow_effect_2024",
                    "operating_payables_cash_flow_effect_2024",
                    "operating_receivables_cash_flow_effect_2024",
                }
            ),
            lambda: (
                -(
                    facts_by_key["inventory_cash_flow_effect_2024"].value
                    + facts_by_key["operating_payables_cash_flow_effect_2024"].value
                    + facts_by_key["operating_receivables_cash_flow_effect_2024"].value
                )
            ),
        ),
        "catl.gross-debt.v1": (
            frozenset(
                {
                    "bonds_payable_2024",
                    "current_portion_long_term_debt_2024",
                    "lease_liabilities_2024",
                    "long_term_borrowings_2024",
                    "short_term_borrowings_2024",
                }
            ),
            lambda: sum(
                (
                    facts_by_key["bonds_payable_2024"].value,
                    facts_by_key["current_portion_long_term_debt_2024"].value,
                    facts_by_key["lease_liabilities_2024"].value,
                    facts_by_key["long_term_borrowings_2024"].value,
                    facts_by_key["short_term_borrowings_2024"].value,
                ),
                start=Decimal(0),
            ),
        ),
    }
    with localcontext() as context:
        context.prec = 60
        for item in facts:
            if item.value_kind != "derived":
                continue
            equation = derived_equations.get(item.equation_id or "")
            if (
                equation is None
                or frozenset(item.parent_fact_keys) != equation[0]
                or item.value != equation[1]()
            ):
                raise CatlAnswerableCaseFixtureError(
                    "CATL answerable fixture derived fact does not reconcile"
                )
    gap_values = source.get("research_gaps")
    if not isinstance(gap_values, list):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture research gaps must be an array"
        )
    gaps = tuple(
        CatlResearchGap(
            gap_key=_text(
                _exact(
                    item, frozenset({"gap_key", "business_module", "reason"}), "gap"
                ).get("gap_key"),
                "gap.gap_key",
            ),
            business_module=_text(item.get("business_module"), "gap.business_module"),
            reason=_text(item.get("reason"), "gap.reason"),
        )
        for item in gap_values
    )
    if len({item.gap_key for item in gaps}) != len(gaps):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture gap keys must be unique"
        )
    if any(item.business_module not in module_keys for item in gaps):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture gap references an unknown module"
        )
    counterevidence_values = source.get("counterevidence_fact_keys")
    if (
        not isinstance(counterevidence_values, list)
        or not counterevidence_values
        or not all(isinstance(item, str) and item for item in counterevidence_values)
        or len(set(counterevidence_values)) != len(counterevidence_values)
        or any(item not in facts_by_key for item in counterevidence_values)
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture counterevidence facts are invalid"
        )
    verification_values = source.get("next_verification_events")
    if (
        not isinstance(verification_values, list)
        or not verification_values
        or not all(
            isinstance(item, str) and item and item == item.strip()
            for item in verification_values
        )
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture verification events are invalid"
        )
    strategy = _strategy(parsed["strategy_assumptions.json"])
    market = _market(parsed["market_inputs.json"], cutoff)
    capital = market.capital_structure
    if (
        capital.cash != facts_by_key[capital.cash_fact_key].value
        or capital.debt != facts_by_key[capital.debt_fact_key].value
        or capital.shares != facts_by_key[capital.share_count_fact_key].value
    ):
        raise CatlAnswerableCaseFixtureError(
            "CATL answerable fixture capital structure does not match source facts"
        )
    return CatlAnswerableCaseFixture(
        cutoff=cutoff,
        content_hash=manifest_hash,
        company_external_key=_COMPANY_KEY,
        security_external_keys=_SECURITY_KEYS,
        base_currency="CNY",
        business_modules=modules,
        facts=facts,
        metric_contracts=metric_contracts,
        research_gaps=gaps,
        counterevidence_fact_keys=tuple(counterevidence_values),
        next_verification_events=tuple(verification_values),
        strategy_assumptions=strategy,
        market_inputs=market,
    )


__all__ = [
    "BUNDLED_MANIFEST_CONTENT_SHA256",
    "REQUIRED_INPUT_KEYS",
    "CatlAnswerableCaseFixture",
    "CatlAnswerableCaseFixtureError",
    "CatlStrategyAssumptionBundle",
    "load_catl_answerable_case_fixture",
]
