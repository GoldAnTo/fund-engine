"""Shared company-model template and strategy-assumption contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CompanyResearchValidationError,
    DriverInput,
    ModelInputState,
    SCENARIO_FINANCIAL_DRIVER_KEYS,
    ScenarioDriverOverride,
    canonical_decimal_string,
)
from app.underwriting.hashing import canonical_hash


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSIONED_STRATEGY = re.compile(r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*\Z")
_VERSIONED_ASSUMPTION = re.compile(
    r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*:[a-z0-9_.:-]+\Z"
)
_SCENARIO_ORDER = ("base", "bull", "bear")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValidationError(f"{field_name} must be canonical non-empty text")
    return value


def _text_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or not all(
        isinstance(item, str) and item and item == item.strip() for item in value
    ):
        raise ValidationError(f"{field_name} must be non-empty canonical text")
    return value


def _decimal(value: object, field_name: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise ValidationError(f"{field_name} must be a finite Decimal")
    return value


@dataclass(frozen=True, slots=True)
class CompanyResearchModelModule:
    module_key: str
    revenue_sources: tuple[str, ...]
    cost_structure: tuple[str, ...]
    capital_needs: tuple[str, ...]

    def __post_init__(self) -> None:
        _text(self.module_key, "model template module_key")
        for name in ("revenue_sources", "cost_structure", "capital_needs"):
            _text_tuple(getattr(self, name), f"model template {name}")


@dataclass(frozen=True, slots=True)
class CompanyResearchMetricClassification:
    module_key: str
    metric_key: str
    category: str

    def __post_init__(self) -> None:
        _text(self.module_key, "model template metric module_key")
        _text(self.metric_key, "model template metric_key")
        if self.category not in {"revenue", "cost", "capital"}:
            raise ValidationError("model template metric category is invalid")


@dataclass(frozen=True, slots=True)
class CompanyResearchDriverBinding:
    driver_key: str
    module_key: str

    def __post_init__(self) -> None:
        _text(self.driver_key, "model template driver_key")
        _text(self.module_key, "model template driver module_key")


@dataclass(frozen=True, slots=True)
class CompanyResearchOperatingDriverBinding:
    driver_key: str
    module_key: str
    metric_key: str
    input_state: ModelInputState
    equation_id: str | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.driver_key, "driver_key"),
            (self.module_key, "module_key"),
            (self.metric_key, "metric_key"),
        ):
            _text(value, f"operating driver {name}")
        if self.driver_key in SCENARIO_FINANCIAL_DRIVER_KEYS:
            raise ValidationError("operating drivers must be separate from financial drivers")
        if self.input_state not in {ModelInputState.REPORTED, ModelInputState.DERIVED}:
            raise ValidationError("operating driver state must be reported or derived")
        if self.input_state is ModelInputState.REPORTED and self.equation_id is not None:
            raise ValidationError("reported operating driver cannot carry equation_id")
        if self.input_state is ModelInputState.DERIVED and not self.equation_id:
            raise ValidationError("derived operating driver requires equation_id")


@dataclass(frozen=True, slots=True)
class CompanyResearchOperatingBaselineRequirement:
    requirement_key: str
    module_key: str
    metric_key: str

    def __post_init__(self) -> None:
        _text(self.requirement_key, "operating baseline requirement_key")
        _text(self.module_key, "operating baseline module_key")
        _text(self.metric_key, "operating baseline metric_key")


@dataclass(frozen=True, slots=True)
class CompanyResearchScenarioMechanism:
    scenario_id: str
    mechanism_id: str

    def __post_init__(self) -> None:
        if self.scenario_id not in set(_SCENARIO_ORDER):
            raise ValidationError("model template scenario_id is invalid")
        _text(self.mechanism_id, "model template mechanism_id")


@dataclass(frozen=True, slots=True)
class CompanyResearchModelTemplate:
    """Adapter-owned vocabulary and mappings for one company model."""

    template_version: str
    company_external_key: str
    security_external_keys: tuple[str, ...]
    modules: tuple[CompanyResearchModelModule, ...]
    metric_classifications: tuple[CompanyResearchMetricClassification, ...]
    operating_driver_bindings: tuple[CompanyResearchOperatingDriverBinding, ...]
    financial_driver_ownership: tuple[CompanyResearchDriverBinding, ...]
    operating_baseline_requirements: tuple[CompanyResearchOperatingBaselineRequirement, ...]
    scenario_mechanisms: tuple[CompanyResearchScenarioMechanism, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.template_version, str) or _VERSIONED_STRATEGY.fullmatch(self.template_version) is None:
            raise ValidationError("company model template must be versioned")
        _text(self.company_external_key, "company model template company identity")
        security_keys = _text_tuple(self.security_external_keys, "company model template security identity")
        if security_keys != tuple(sorted(set(security_keys))):
            raise ValidationError("company model template security identity must be canonical")
        if not isinstance(self.modules, tuple) or not self.modules or not all(type(item) is CompanyResearchModelModule for item in self.modules):
            raise ValidationError("company model template modules must be typed")
        module_keys = tuple(item.module_key for item in self.modules)
        if len(set(module_keys)) != len(module_keys):
            raise ValidationError("company model template modules must be unique")
        if not isinstance(self.metric_classifications, tuple) or not self.metric_classifications or not all(type(item) is CompanyResearchMetricClassification for item in self.metric_classifications) or len({(item.module_key, item.metric_key) for item in self.metric_classifications}) != len(self.metric_classifications):
            raise ValidationError("company model template metric classifications must be unique")
        if any(item.module_key not in set(module_keys) for item in self.metric_classifications):
            raise ValidationError("metric classification references unknown module")
        if {item.category for item in self.metric_classifications} != {"revenue", "cost", "capital"}:
            raise ValidationError("company model template must classify revenue, cost, and capital metrics")
        if not isinstance(self.operating_driver_bindings, tuple) or not self.operating_driver_bindings or not all(type(item) is CompanyResearchOperatingDriverBinding for item in self.operating_driver_bindings) or len({item.driver_key for item in self.operating_driver_bindings}) != len(self.operating_driver_bindings):
            raise ValidationError("company model template operating drivers must be unique")
        if not isinstance(self.financial_driver_ownership, tuple) or not all(type(item) is CompanyResearchDriverBinding for item in self.financial_driver_ownership) or tuple(item.driver_key for item in self.financial_driver_ownership) != SCENARIO_FINANCIAL_DRIVER_KEYS:
            raise ValidationError("company model template must bind all six drivers canonically")
        if any(item.module_key not in set(module_keys) for item in (*self.operating_driver_bindings, *self.financial_driver_ownership)):
            raise ValidationError("company model template driver references unknown module")
        if not isinstance(self.operating_baseline_requirements, tuple) or not self.operating_baseline_requirements or not all(type(item) is CompanyResearchOperatingBaselineRequirement for item in self.operating_baseline_requirements):
            raise ValidationError("company model template requires an operating baseline")
        if len({item.requirement_key for item in self.operating_baseline_requirements}) != len(self.operating_baseline_requirements):
            raise ValidationError("operating baseline requirements must be unique")
        metrics = {(item.module_key, item.metric_key) for item in self.metric_classifications}
        if any((item.module_key, item.metric_key) not in metrics for item in self.operating_driver_bindings):
            raise ValidationError("operating driver metric is outside the template")
        if any(item.module_key not in set(module_keys) or (item.module_key, item.metric_key) not in metrics for item in self.operating_baseline_requirements):
            raise ValidationError("operating baseline requirement is outside the template")
        if not isinstance(self.scenario_mechanisms, tuple) or not all(type(item) is CompanyResearchScenarioMechanism for item in self.scenario_mechanisms) or tuple(item.scenario_id for item in self.scenario_mechanisms) != _SCENARIO_ORDER or len({item.mechanism_id for item in self.scenario_mechanisms}) != 3:
            raise ValidationError("company model template scenario mapping must be exact")


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
        if not isinstance(self.driver_overrides, tuple) or not all(type(item) is ScenarioDriverOverride for item in self.driver_overrides):
            raise ValidationError("strategy scenario overrides must be typed")
        keys = tuple(item.driver_key for item in self.driver_overrides)
        if keys != SCENARIO_FINANCIAL_DRIVER_KEYS:
            raise ValidationError("strategy scenario overrides must cover the six drivers in canonical order")
        if any(
            item.state is not ModelInputState.ASSUMPTION
            or not isinstance(item.assumption_key, str)
            or _VERSIONED_ASSUMPTION.fullmatch(item.assumption_key) is None
            or not item.rationale
            or not item.equation
            for item in self.driver_overrides
        ):
            raise ValidationError(
                "strategy scenario overrides require complete assumption metadata"
            )
        if self.scenario_id == "base" and any(item.value != Decimal("1") for item in self.driver_overrides):
            raise ValidationError("base strategy scenario must preserve the baseline")
        if self.scenario_id != "base" and all(item.value == Decimal("1") for item in self.driver_overrides):
            raise ValidationError("non-base strategy scenarios must change their mechanism")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "mechanism_id": self.mechanism_id,
            "driver_overrides": tuple(
                {"driver_key": item.driver_key, "value": canonical_decimal_string(item.value)}
                | {
                    "state": item.state.value,
                    "assumption_key": item.assumption_key,
                    "rationale": item.rationale,
                    "equation": item.equation,
                }
                for item in self.driver_overrides
            ),
        }


@dataclass(frozen=True, slots=True)
class StrategyAssumptionValue:
    value: Decimal
    state: ModelInputState
    assumption_key: str
    rationale: str
    equation: str

    def __post_init__(self) -> None:
        _decimal(self.value, "strategy assumption value")
        if (
            self.state is not ModelInputState.ASSUMPTION
            or _VERSIONED_ASSUMPTION.fullmatch(self.assumption_key) is None
        ):
            raise ValidationError("strategy value requires a versioned assumption")
        _text(self.rationale, "strategy assumption rationale")
        _text(self.equation, "strategy assumption equation")

    def canonical_payload(self) -> dict[str, str]:
        return {
            "value": canonical_decimal_string(self.value),
            "state": self.state.value,
            "assumption_key": self.assumption_key,
            "rationale": self.rationale,
            "equation": self.equation,
        }


@dataclass(frozen=True, slots=True)
class StrategyAssumptionSet:
    """A mandatory hash-addressed candidate forecast, distinct from evidence."""

    strategy_version: str
    content_hash: str
    first_fiscal_year: int
    driver_paths: tuple[DriverInput, ...]
    scenario_overrides: tuple[ScenarioAssumption, ...]
    terminal_growth: StrategyAssumptionValue

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_version, str) or _VERSIONED_STRATEGY.fullmatch(self.strategy_version) is None:
            raise ValidationError("strategy assumptions require a versioned strategy")
        if type(self.first_fiscal_year) is not int or self.first_fiscal_year < 1900:
            raise ValidationError("strategy assumptions first fiscal year is invalid")
        if not isinstance(self.driver_paths, tuple) or not all(type(item) is DriverInput for item in self.driver_paths):
            raise ValidationError("strategy assumptions driver paths must be typed")
        if tuple(item.driver_key for item in self.driver_paths) != SCENARIO_FINANCIAL_DRIVER_KEYS:
            raise ValidationError("strategy assumptions must contain exactly six canonical driver paths")
        for path in self.driver_paths:
            if path.state is not ModelInputState.ASSUMPTION:
                raise CompanyResearchValidationError("strategy assumption driver paths must use state=assumption")
            if len(path.values) != 5:
                raise ValidationError("strategy assumption driver paths must contain five years")
            if path.assumption_key is None or not path.assumption_key.startswith(f"{self.strategy_version}:"):
                raise ValidationError("strategy assumption keys must be versioned by strategy_version")
            if not path.assumption_rationale or not path.assumption_equation:
                raise ValidationError(
                    "strategy driver paths require rationale and equation metadata"
                )
        if not isinstance(self.scenario_overrides, tuple) or not all(type(item) is ScenarioAssumption for item in self.scenario_overrides) or tuple(item.scenario_id for item in self.scenario_overrides) != _SCENARIO_ORDER:
            raise ValidationError("strategy assumptions must contain canonical Base-Bull-Bear scenarios")
        if len({item.mechanism_id for item in self.scenario_overrides}) != 3:
            raise ValidationError("strategy assumptions require mechanism-specific scenario overrides")
        if type(self.terminal_growth) is not StrategyAssumptionValue:
            raise ValidationError("strategy terminal growth must be a typed assumption")
        if self.terminal_growth.value < Decimal("0"):
            raise ValidationError("strategy assumptions terminal growth is invalid")
        if not self.terminal_growth.assumption_key.startswith(f"{self.strategy_version}:"):
            raise ValidationError("strategy terminal growth key must be versioned")
        if not isinstance(self.content_hash, str) or _SHA256.fullmatch(self.content_hash) is None:
            raise ValidationError("strategy assumptions content hash must be a SHA-256 content hash")
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
        terminal_growth: StrategyAssumptionValue,
    ) -> str:
        return canonical_hash(
            {
                "schema_version": "alphabet.golden-case.strategy-assumptions.v1",
                "strategy_version": strategy_version,
                "first_fiscal_year": first_fiscal_year,
                "driver_paths": tuple(
                    {
                        "driver_key": item.driver_key,
                        "values": tuple(
                            canonical_decimal_string(value) for value in item.values
                        ),
                        "state": item.state.value,
                        "assumption_key": item.assumption_key,
                        "rationale": item.assumption_rationale,
                        "equation": item.assumption_equation,
                    }
                    for item in driver_paths
                ),
                "scenario_overrides": tuple(item.canonical_payload() for item in scenario_overrides),
                "terminal_growth": terminal_growth.canonical_payload(),
            }
        )
