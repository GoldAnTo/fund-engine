"""Compile only review-governed, source-bound causal mechanisms."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.domain.mechanisms import (
    FinancialMapping,
    MechanismPack,
    MechanismStatus,
)
from app.underwriting.services.kernel import canonical_hash


_NEXT_STATUS = {
    MechanismStatus.CANDIDATE: MechanismStatus.ADAPTED,
    MechanismStatus.ADAPTED: MechanismStatus.CALIBRATED,
    MechanismStatus.CALIBRATED: MechanismStatus.HUMAN_CONFIRMED,
    MechanismStatus.HUMAN_CONFIRMED: MechanismStatus.FORMAL,
    MechanismStatus.FORMAL: MechanismStatus.CHALLENGED,
    MechanismStatus.CHALLENGED: MechanismStatus.RETIRED_OR_REPLACED,
}


@dataclass(frozen=True, slots=True)
class CompiledMechanisms:
    mechanisms: tuple[MechanismPack, ...]
    content_hash: str
    metric_definition_ids: tuple[str, ...]
    source_ids: tuple[str, ...]

    @property
    def dependency_ids(self) -> tuple[str, ...]:
        """Stable complete lineage, with metric definitions before sources."""
        return self.metric_definition_ids + self.source_ids


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must not be empty")
    return value.strip()


def _as_dependency_map(
    values: Mapping[str, UUID | str], field: str
) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise ValidationError(f"{field} must be a mapping")
    normalized: dict[str, str] = {}
    for key, value in values.items():
        canonical_key = _require_text(key, f"{field} key")
        dependency_id = str(value)
        _require_text(dependency_id, f"{field} dependency id")
        if canonical_key in normalized:
            raise ValidationError(f"{field} keys must be unique")
        normalized[canonical_key] = dependency_id
    return normalized


def _as_source_ids(values: Iterable[str]) -> set[str]:
    if isinstance(values, (str, bytes)):
        raise ValidationError("source_ids must be an iterable of source IDs")
    try:
        return {_require_text(value, "source_id") for value in values}
    except TypeError as exc:
        raise ValidationError("source_ids must be an iterable of source IDs") from exc


def _causal_chain_is_acyclic(mappings: tuple[FinancialMapping, ...]) -> bool:
    """Reject circular metric transmission paths within one mechanism pack."""
    graph: dict[str, set[str]] = {}
    for mapping in mappings:
        graph.setdefault(mapping.driver_key, set()).add(mapping.target_metric_key)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        for target in graph.get(node, ()):
            if not visit(target):
                return False
        visiting.remove(node)
        visited.add(node)
        return True

    return all(visit(node) for node in graph)


def validate_formal_mechanism(value: MechanismPack) -> None:
    """Fail closed unless a formal pack has a complete causal evidence shape."""
    if not isinstance(value, MechanismPack):
        raise ValidationError("mechanism is required")
    if value.status is not MechanismStatus.FORMAL:
        raise ValidationError("mechanism must be formal")
    if (
        not value.applicability
        or not value.invalidation_conditions
        or not value.financial_mappings
        or not value.alternative_explanations
        or not value.falsifiers
        or not value.source_ids
    ):
        raise ValidationError("formal mechanism is incomplete")
    if value.human_confirmation_identity is None:
        raise ValidationError("formal mechanism requires human_confirmation_identity")
    mapped_drivers = {mapping.driver_key for mapping in value.financial_mappings}
    if mapped_drivers != set(value.driver_keys):
        raise ValidationError("formal mechanism mappings must cover every driver_key")
    if not _causal_chain_is_acyclic(value.financial_mappings):
        raise ValidationError("formal mechanism causal chain must be acyclic")


def transition_mechanism(
    value: MechanismPack,
    next_status: MechanismStatus,
    *,
    human_confirmation_identity: str | None = None,
    revision_reason: str | None = None,
) -> MechanismPack:
    """Append a successor-shaped domain version through exactly one transition."""
    if not isinstance(value, MechanismPack):
        raise ValidationError("mechanism is required")
    if not isinstance(next_status, MechanismStatus):
        raise ValidationError("next mechanism status is invalid")
    if _NEXT_STATUS.get(value.status) is not next_status:
        raise ValidationError("mechanism lifecycle transition is not allowed")

    confirmation = human_confirmation_identity or value.human_confirmation_identity
    if next_status is MechanismStatus.HUMAN_CONFIRMED and confirmation is None:
        raise ValidationError("human_confirmation_identity is required before formal")
    successor = replace(
        value,
        version=value.version + 1,
        status=next_status,
        human_confirmation_identity=confirmation,
        revision_reason=revision_reason or value.revision_reason,
    )
    if next_status is MechanismStatus.FORMAL:
        validate_formal_mechanism(successor)
    return successor


def _mechanism_payload(value: MechanismPack) -> dict[str, object]:
    return {
        "key": value.key,
        "version": value.version,
        "status": value.status.value,
        "scope_object_id": str(value.scope_object_id),
        "driver_keys": value.driver_keys,
        "formula": value.formula,
        "applicability": value.applicability,
        "invalidation_conditions": value.invalidation_conditions,
        "financial_mappings": tuple(
            {
                "driver_key": mapping.driver_key,
                "target_metric_key": mapping.target_metric_key,
                "direction": mapping.direction,
                "lag_periods": mapping.lag_periods,
                "magnitude_low": str(mapping.magnitude_low),
                "magnitude_high": str(mapping.magnitude_high),
            }
            for mapping in value.financial_mappings
        ),
        "alternative_explanations": value.alternative_explanations,
        "falsifiers": tuple(
            {
                "key": falsifier.key,
                "metric_key": falsifier.metric_key,
                "operator": falsifier.operator,
                "threshold_low": (
                    str(falsifier.threshold_low)
                    if falsifier.threshold_low is not None
                    else None
                ),
                "threshold_high": (
                    str(falsifier.threshold_high)
                    if falsifier.threshold_high is not None
                    else None
                ),
                "evaluation_periods": falsifier.evaluation_periods,
                "consequence": falsifier.consequence,
            }
            for falsifier in value.falsifiers
        ),
        "source_ids": value.source_ids,
        "human_confirmation_identity": value.human_confirmation_identity,
        "revision_reason": value.revision_reason,
    }


def compile_mechanisms(
    mechanisms: tuple[MechanismPack, ...],
    *,
    metric_definition_ids: Mapping[str, UUID | str],
    source_ids: Iterable[str],
) -> CompiledMechanisms:
    """Compile source-available formal packs into deterministic dependencies.

    The caller supplies dependencies already resolved *at the selected
    historical basis cutoff*.  This pure compiler verifies that every causal
    input, output and falsifier metric, and every source reference, is among
    those cutoff-safe dependencies.
    """
    if not isinstance(mechanisms, tuple) or not mechanisms:
        raise ValidationError("mechanisms must be a non-empty tuple")
    definitions = _as_dependency_map(metric_definition_ids, "metric_definition_ids")
    available_sources = _as_source_ids(source_ids)
    mechanism_keys: set[str] = set()
    dependency_metrics: set[str] = set()
    dependency_sources: set[str] = set()

    for value in mechanisms:
        validate_formal_mechanism(value)
        if value.key in mechanism_keys:
            raise ValidationError("mechanism keys must be unique")
        mechanism_keys.add(value.key)
        for driver_key in value.driver_keys:
            if driver_key not in definitions:
                raise ValidationError("driver metric definition is unavailable at basis cutoff")
            dependency_metrics.add(driver_key)
        for mapping in value.financial_mappings:
            if mapping.target_metric_key not in definitions:
                raise ValidationError("target metric definition is unavailable at basis cutoff")
            dependency_metrics.add(mapping.target_metric_key)
        for falsifier in value.falsifiers:
            if falsifier.metric_key not in definitions:
                raise ValidationError("falsifier metric definition is unavailable at basis cutoff")
            dependency_metrics.add(falsifier.metric_key)
        for source_id in value.source_ids:
            if source_id not in available_sources:
                raise ValidationError("mechanism source is unavailable at basis cutoff")
            dependency_sources.add(source_id)

    ordered_metrics = tuple(sorted(definitions[key] for key in dependency_metrics))
    ordered_sources = tuple(sorted(dependency_sources))
    ordered_mechanisms = tuple(sorted(mechanisms, key=lambda value: (value.key, value.version)))
    return CompiledMechanisms(
        mechanisms=ordered_mechanisms,
        content_hash=canonical_hash(
            {
                "mechanisms": tuple(_mechanism_payload(value) for value in ordered_mechanisms),
                "metric_definition_ids": ordered_metrics,
                "source_ids": ordered_sources,
            }
        ),
        metric_definition_ids=ordered_metrics,
        source_ids=ordered_sources,
    )
