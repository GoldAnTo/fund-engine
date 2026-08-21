"""Typed economic metric vocabulary and reconciliation semantics."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from app.models.ledger import ValidationError


class PeriodSemantics(StrEnum):
    POINT_IN_TIME = "point_in_time"
    FLOW = "flow"
    PERIOD_AVERAGE = "period_average"


class SourceRole(StrEnum):
    REPORTED = "reported"
    OFFICIAL_INDUSTRY = "official_industry"
    DERIVED = "derived"
    ASSUMPTION = "assumption"


class AggregationRule(StrEnum):
    SUM = "sum"
    WEIGHTED_AVERAGE = "weighted_average"
    LAST = "last"
    NONE = "none"


def _require_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must not be empty")


def _require_decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{name} must be a finite Decimal")
    return value


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    version: int
    label: str
    unit: str
    period_semantics: PeriodSemantics
    source_role: SourceRole
    aggregation: AggregationRule
    reconciliation_tolerance: Decimal

    def __post_init__(self) -> None:
        for value, name in ((self.key, "key"), (self.label, "label"), (self.unit, "unit")):
            _require_text(value, name)
        if self.version < 1:
            raise ValidationError("version must be at least 1")
        _require_decimal(self.reconciliation_tolerance, "reconciliation_tolerance")
        if self.reconciliation_tolerance < 0:
            raise ValidationError("reconciliation_tolerance must not be negative")


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MetricObservation:
    definition_key: str
    definition_version: int
    value: Decimal
    unit: str
    observed_start: datetime
    observed_end: datetime
    effective_at: datetime
    available_at: datetime
    source_id: str
    source_locator: str
    dimensions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _require_text(self.definition_key, "definition_key")
        if self.definition_version < 1:
            raise ValidationError("definition_version must be at least 1")
        _require_decimal(self.value, "value")
        _require_text(self.unit, "unit")
        for name in ("observed_start", "observed_end", "effective_at", "available_at"):
            normalized = _utc(getattr(self, name), name)
            object.__setattr__(self, name, normalized)
        if self.observed_start > self.observed_end:
            raise ValidationError("observed_start must not exceed observed_end")
        _require_text(self.source_id, "source_id")
        _require_text(self.source_locator, "source_locator")
        object.__setattr__(self, "dimensions", _canonical_dimensions(self.dimensions))

    @classmethod
    def create(
        cls,
        definition: MetricDefinition,
        value: Decimal,
        observed_start: datetime,
        observed_end: datetime,
        effective_at: datetime,
        available_at: datetime,
        cutoff: datetime,
        source_id: str,
        source_locator: str,
        unit: str,
        dimensions: Iterable[tuple[str, str]] | dict[str, str],
    ) -> "MetricObservation":
        if not isinstance(definition, MetricDefinition):
            raise ValidationError("definition is required")
        _require_text(source_id, "source_id")
        _require_text(source_locator, "source_locator")
        _require_text(unit, "unit")
        if unit != definition.unit:
            raise ValidationError("unit must match metric definition")
        normalized_start = _utc(observed_start, "observed_start")
        normalized_end = _utc(observed_end, "observed_end")
        normalized_effective = _utc(effective_at, "effective_at")
        normalized_available = _utc(available_at, "available_at")
        normalized_cutoff = _utc(cutoff, "cutoff")
        if normalized_available > normalized_cutoff:
            raise ValidationError("available_at must not exceed cutoff")
        if normalized_start > normalized_end:
            raise ValidationError("observed_start must not exceed observed_end")
        return cls(
            definition.key,
            definition.version,
            value,
            unit,
            normalized_start,
            normalized_end,
            normalized_effective,
            normalized_available,
            source_id,
            source_locator,
            dimensions,
        )


def _canonical_dimensions(
    dimensions: Iterable[tuple[str, str]] | dict[str, str],
) -> tuple[tuple[str, str], ...]:
    items = tuple(dimensions.items()) if isinstance(dimensions, dict) else tuple(dimensions)
    canonical: list[tuple[str, str]] = []
    keys: set[str] = set()
    for item in items:
        try:
            key, dimension_value = item
        except (TypeError, ValueError) as exc:
            raise ValidationError("dimensions must contain key/value pairs") from exc
        _require_text(key, "dimension key")
        _require_text(dimension_value, "dimension value")
        if key in keys:
            raise ValidationError("dimension keys must be unique")
        keys.add(key)
        canonical.append((key, dimension_value))
    return tuple(sorted(canonical))


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    total: Decimal
    parts_total: Decimal
    delta: Decimal
    tolerance: Decimal
    balanced: bool


def reconcile(total: Decimal, parts: tuple[Decimal, ...], tolerance: Decimal) -> ReconciliationResult:
    _require_decimal(total, "total")
    _require_decimal(tolerance, "tolerance")
    for part in parts:
        _require_decimal(part, "part")
    if tolerance < 0:
        raise ValidationError("tolerance must not be negative")
    parts_total = sum(parts, Decimal("0"))
    delta = total - parts_total
    return ReconciliationResult(total, parts_total, delta, tolerance, abs(delta) <= tolerance)
