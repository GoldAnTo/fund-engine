"""Immutable value objects for power-battery industry research.

The model deliberately keeps the reported (nominal) nameplate capacity next
to the usable capacity.  A research consumer must never be able to mistake
the former for the latter.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from collections.abc import Iterator, Mapping
import re
from uuid import UUID, uuid4

from app.models.ledger import ValidationError
from app.underwriting.domain.types import BlockerCode


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must not be empty")
    return value.strip()


def _require_finite_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field_name} must be a finite Decimal")
    return value


class AnswerabilityBlocked(ValidationError):
    """A missing baseline or unidentified mechanism makes a state unusable."""

    def __init__(self, blocker_code: BlockerCode, detail: str) -> None:
        self.blocker_code = blocker_code
        self.detail = detail
        super().__init__(f"{blocker_code.value}: {detail}")


@dataclass(frozen=True, slots=True)
class IndustryInputs:
    """Observed industry baselines, deliberately nullable before compilation.

    Missing values are not quietly treated as zero.  The compiler maps them
    to a fail-closed answerability blocker with the affected metric key.
    """

    ev_sales_millions: Decimal | None
    average_battery_kwh: Decimal | None
    storage_demand_gwh: Decimal | None
    nominal_capacity_gwh: Decimal | None
    commissioned_share: Decimal | None
    certified_share: Decimal | None
    yield_rate: Decimal | None
    shipments_gwh: Decimal | None
    production_gwh: Decimal | None
    cell_asp_cny_per_kwh: Decimal | None
    unit_cash_cost_cny_per_kwh: Decimal | None


@dataclass(frozen=True, slots=True)
class IndustryRange:
    """Closed numeric range used for scenario outputs, never a probability."""

    low: Decimal
    high: Decimal

    def __post_init__(self) -> None:
        _require_finite_decimal(self.low, "range low")
        _require_finite_decimal(self.high, "range high")
        if self.low > self.high:
            raise ValidationError("range low must not exceed range high")


@dataclass(frozen=True, slots=True)
class IndustryMetric:
    """One immutable, metric-keyed industry output."""

    key: str
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _require_text(self.key, "industry metric key"))
        _require_finite_decimal(self.value, "industry metric value")


@dataclass(frozen=True, slots=True)
class IndustryMetricCollection(Mapping[str, Decimal]):
    """Read-only keyed output collection retained alongside named fields."""

    values: tuple[IndustryMetric, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.values, tuple) or not self.values:
            raise ValidationError("industry metrics must be a non-empty tuple")
        if not all(type(value) is IndustryMetric for value in self.values):
            raise ValidationError("industry metrics must contain IndustryMetric values")
        keys = tuple(value.key for value in self.values)
        if len(keys) != len(set(keys)):
            raise ValidationError("industry metric keys must be unique")

    def __getitem__(self, key: str) -> Decimal:
        for value in self.values:
            if value.key == key:
                return value.value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (value.key for value in self.values)

    def __len__(self) -> int:
        return len(self.values)


class ScenarioKind(StrEnum):
    BASE = "base"
    UPSIDE = "upside"
    DOWNSIDE = "downside"


@dataclass(frozen=True, slots=True)
class ScenarioDriverOverride:
    """One explicit change to a driver whose mechanism has declared it."""

    driver_key: str
    value: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "driver_key", _require_text(self.driver_key, "driver_key"))
        _require_finite_decimal(self.value, "scenario override value")


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    """A probability-free alternative to the state parent's input baseline."""

    kind: ScenarioKind
    overrides: tuple[ScenarioDriverOverride, ...]
    falsifiers: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not ScenarioKind:
            raise ValidationError("scenario kind is invalid")
        if not isinstance(self.overrides, tuple) or not all(
            type(item) is ScenarioDriverOverride for item in self.overrides
        ):
            raise ValidationError("scenario overrides must contain ScenarioDriverOverride values")
        driver_keys = tuple(item.driver_key for item in self.overrides)
        if len(driver_keys) != len(set(driver_keys)):
            raise ValidationError("scenario overrides must not contain duplicate driver keys")
        object.__setattr__(
            self,
            "overrides",
            tuple(sorted(self.overrides, key=lambda item: item.driver_key)),
        )
        if not isinstance(self.falsifiers, tuple):
            raise ValidationError("scenario falsifiers must be a tuple")
        normalized_falsifiers = tuple(
            _require_text(value, "scenario falsifier") for value in self.falsifiers
        )
        if not normalized_falsifiers:
            raise ValidationError("scenario falsifiers must not be empty")
        if len(normalized_falsifiers) != len(set(normalized_falsifiers)):
            raise ValidationError("scenario falsifiers must not contain duplicates")
        object.__setattr__(self, "falsifiers", tuple(sorted(normalized_falsifiers)))


@dataclass(frozen=True, slots=True)
class IndustryState:
    """One computed, auditable industry baseline at a historical cutoff."""

    id: UUID
    inputs: IndustryInputs
    battery_demand_gwh: Decimal
    nominal_capacity_gwh: Decimal
    effective_capacity_gwh: Decimal
    utilization: Decimal
    inventory_change_gwh: Decimal
    unit_margin_cny_per_kwh: Decimal
    price_range_cny_per_kwh: IndustryRange
    unit_cost_range_cny_per_kwh: IndustryRange
    industry_profit_pool_range_cny: IndustryRange
    metrics: IndustryMetricCollection
    mechanism_lineage: tuple[tuple[str, UUID, int], ...]
    falsifier_keys: tuple[str, ...]
    compiled_mechanism_hash: str
    content_hash: str

    def __post_init__(self) -> None:
        if type(self.id) is not UUID:
            raise ValidationError("industry state id must be a UUID")
        if type(self.inputs) is not IndustryInputs:
            raise ValidationError("industry state inputs are invalid")
        for field_name in (
            "battery_demand_gwh",
            "nominal_capacity_gwh",
            "effective_capacity_gwh",
            "utilization",
            "inventory_change_gwh",
            "unit_margin_cny_per_kwh",
        ):
            _require_finite_decimal(getattr(self, field_name), field_name)
        if not all(
            type(value) is IndustryRange
            for value in (
                self.price_range_cny_per_kwh,
                self.unit_cost_range_cny_per_kwh,
                self.industry_profit_pool_range_cny,
            )
        ):
            raise ValidationError("industry state ranges are invalid")
        if type(self.metrics) is not IndustryMetricCollection:
            raise ValidationError("industry state metric collection is invalid")
        required_metrics = {
            "industry.nominal_capacity_gwh": self.nominal_capacity_gwh,
            "industry.effective_capacity_gwh": self.effective_capacity_gwh,
            "industry.utilization": self.utilization,
        }
        for key, expected_value in required_metrics.items():
            if self.metrics.get(key) != expected_value:
                raise ValidationError(f"industry state metric collection must retain {key}")
        if not isinstance(self.mechanism_lineage, tuple) or not self.mechanism_lineage:
            raise ValidationError("industry state mechanism lineage is required")
        if not all(
            isinstance(key, str)
            and type(scope_id) is UUID
            and isinstance(version, int)
            and not isinstance(version, bool)
            and version > 0
            for key, scope_id, version in self.mechanism_lineage
        ):
            raise ValidationError("industry state mechanism lineage is invalid")
        if not isinstance(self.falsifier_keys, tuple) or not self.falsifier_keys:
            raise ValidationError("industry state falsifier keys are required")
        if not isinstance(self.compiled_mechanism_hash, str) or _SHA256.fullmatch(
            self.compiled_mechanism_hash
        ) is None:
            raise ValidationError("industry state compiled_mechanism_hash must be a lowercase SHA-256")
        if not isinstance(self.content_hash, str) or _SHA256.fullmatch(self.content_hash) is None:
            raise ValidationError("industry state content_hash must be a lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class IndustryScenario:
    """A parent-linked, non-probabilistic scenario result."""

    id: UUID
    kind: ScenarioKind
    parent_industry_state_id: UUID
    overrides: tuple[ScenarioDriverOverride, ...]
    battery_demand_gwh: Decimal
    effective_capacity_gwh: Decimal
    utilization: Decimal
    inventory_change_gwh: Decimal
    price_range_cny_per_kwh: IndustryRange
    unit_cost_range_cny_per_kwh: IndustryRange
    industry_profit_pool_range_cny: IndustryRange
    falsifier_keys: tuple[str, ...]
    probability: None = None

    def __post_init__(self) -> None:
        if type(self.id) is not UUID or type(self.parent_industry_state_id) is not UUID:
            raise ValidationError("scenario IDs must be UUIDs")
        if type(self.kind) is not ScenarioKind:
            raise ValidationError("scenario kind is invalid")
        if self.probability is not None:
            raise ValidationError("industry scenarios must not attach probabilities in Wave 2")
        if not isinstance(self.overrides, tuple) or not all(
            type(item) is ScenarioDriverOverride for item in self.overrides
        ):
            raise ValidationError("scenario overrides are invalid")
        for field_name in (
            "battery_demand_gwh",
            "effective_capacity_gwh",
            "utilization",
            "inventory_change_gwh",
        ):
            _require_finite_decimal(getattr(self, field_name), field_name)
        if not all(
            type(value) is IndustryRange
            for value in (
                self.price_range_cny_per_kwh,
                self.unit_cost_range_cny_per_kwh,
                self.industry_profit_pool_range_cny,
            )
        ):
            raise ValidationError("scenario ranges are invalid")
        if not isinstance(self.falsifier_keys, tuple) or not self.falsifier_keys:
            raise ValidationError("scenario falsifier keys are required")


def new_industry_state_id() -> UUID:
    """Keep UUID creation in domain so the compiler remains deterministic in outputs."""
    return uuid4()


def new_industry_scenario_id() -> UUID:
    return uuid4()
