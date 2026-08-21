"""Immutable, review-governed causal mechanism contracts.

Mechanisms are deliberately more constrained than narratives.  A mechanism
cannot become formal merely because its prose sounds plausible: it must name
the driver-to-financial transmission, competing explanations, falsification
tests, and its frozen source lineage.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from app.models.ledger import ValidationError


class MechanismStatus(StrEnum):
    CANDIDATE = "candidate"
    ADAPTED = "adapted"
    CALIBRATED = "calibrated"
    HUMAN_CONFIRMED = "human_confirmed"
    FORMAL = "formal"
    CHALLENGED = "challenged"
    RETIRED_OR_REPLACED = "retired_or_replaced"


MappingDirection = Literal["positive", "negative", "nonlinear"]
FalsifierOperator = Literal["lt", "lte", "gt", "gte", "outside"]


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must not be empty")
    return value.strip()


def _require_finite_decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal")
    return value


def _require_text_tuple(values: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValidationError(f"{field} must be a tuple")
    normalized = tuple(_require_text(value, field) for value in values)
    if required and not normalized:
        raise ValidationError(f"{field} must not be empty")
    if len(normalized) != len(set(normalized)):
        raise ValidationError(f"{field} must not contain duplicates")
    return normalized


@dataclass(frozen=True, slots=True)
class FinancialMapping:
    """One quantified driver-to-financial-metric link in a causal chain."""

    driver_key: str
    target_metric_key: str
    direction: MappingDirection
    lag_periods: int
    magnitude_low: Decimal
    magnitude_high: Decimal

    def __post_init__(self) -> None:
        _require_text(self.driver_key, "driver_key")
        _require_text(self.target_metric_key, "target_metric_key")
        if self.driver_key == self.target_metric_key:
            raise ValidationError("financial mapping must not map a metric to itself")
        if self.direction not in ("positive", "negative", "nonlinear"):
            raise ValidationError("financial mapping direction is invalid")
        if isinstance(self.lag_periods, bool) or not isinstance(self.lag_periods, int):
            raise ValidationError("lag_periods must be an integer")
        if self.lag_periods < 0:
            raise ValidationError("lag_periods must not be negative")
        _require_finite_decimal(self.magnitude_low, "magnitude_low")
        _require_finite_decimal(self.magnitude_high, "magnitude_high")
        if self.magnitude_low > self.magnitude_high:
            raise ValidationError("magnitude_low must not exceed magnitude_high")


@dataclass(frozen=True, slots=True)
class Falsifier:
    """A measurable condition that challenges a mechanism rather than its prose."""

    key: str
    metric_key: str
    operator: FalsifierOperator
    threshold_low: Decimal | None
    threshold_high: Decimal | None
    evaluation_periods: int
    consequence: str

    def __post_init__(self) -> None:
        _require_text(self.key, "falsifier key")
        _require_text(self.metric_key, "falsifier metric_key")
        if self.operator not in ("lt", "lte", "gt", "gte", "outside"):
            raise ValidationError("falsifier operator is invalid")
        if isinstance(self.evaluation_periods, bool) or not isinstance(self.evaluation_periods, int):
            raise ValidationError("evaluation_periods must be an integer")
        if self.evaluation_periods < 1:
            raise ValidationError("evaluation_periods must be at least 1")
        _require_text(self.consequence, "falsifier consequence")
        if self.operator == "outside":
            if self.threshold_low is None or self.threshold_high is None:
                raise ValidationError("outside falsifier requires lower and upper thresholds")
            _require_finite_decimal(self.threshold_low, "threshold_low")
            _require_finite_decimal(self.threshold_high, "threshold_high")
            if self.threshold_low >= self.threshold_high:
                raise ValidationError("outside falsifier lower threshold must be below upper threshold")
        else:
            if self.threshold_low is None:
                raise ValidationError("falsifier threshold_low is required")
            _require_finite_decimal(self.threshold_low, "threshold_low")
            if self.threshold_high is not None:
                raise ValidationError("falsifier threshold_high is only valid for outside")


@dataclass(frozen=True, slots=True)
class MechanismPack:
    """A versioned causal model scoped to one research object.

    ``human_confirmation_identity`` records the accountable reviewer.  It is
    optional while a pack is provisional, but is retained on its successor
    versions and mandatory before the pack may be formalized.
    """

    key: str
    version: int
    status: MechanismStatus
    scope_object_id: UUID
    driver_keys: tuple[str, ...]
    formula: str
    applicability: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    financial_mappings: tuple[FinancialMapping, ...]
    alternative_explanations: tuple[str, ...]
    falsifiers: tuple[Falsifier, ...]
    source_ids: tuple[str, ...]
    human_confirmation_identity: str | None = None
    revision_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.key, "mechanism key")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValidationError("mechanism version must be at least 1")
        if not isinstance(self.status, MechanismStatus):
            raise ValidationError("mechanism status is invalid")
        if not isinstance(self.scope_object_id, UUID):
            raise ValidationError("scope_object_id must be a UUID")
        _require_text_tuple(self.driver_keys, "driver_keys", required=True)
        _require_text(self.formula, "formula")
        _require_text_tuple(self.applicability, "applicability")
        _require_text_tuple(self.invalidation_conditions, "invalidation_conditions")
        _require_text_tuple(self.alternative_explanations, "alternative_explanations")
        _require_text_tuple(self.source_ids, "source_ids")
        if not isinstance(self.financial_mappings, tuple) or not all(
            isinstance(mapping, FinancialMapping) for mapping in self.financial_mappings
        ):
            raise ValidationError("financial_mappings must contain FinancialMapping values")
        if not isinstance(self.falsifiers, tuple) or not all(
            isinstance(falsifier, Falsifier) for falsifier in self.falsifiers
        ):
            raise ValidationError("falsifiers must contain Falsifier values")
        mapping_identities = tuple(
            (mapping.driver_key, mapping.target_metric_key) for mapping in self.financial_mappings
        )
        if len(mapping_identities) != len(set(mapping_identities)):
            raise ValidationError("financial mappings must not contain duplicates")
        falsifier_keys = tuple(falsifier.key for falsifier in self.falsifiers)
        if len(falsifier_keys) != len(set(falsifier_keys)):
            raise ValidationError("falsifiers must not contain duplicate keys")
        if self.human_confirmation_identity is not None:
            _require_text(self.human_confirmation_identity, "human_confirmation_identity")
        if self.revision_reason is not None:
            _require_text(self.revision_reason, "revision_reason")

