"""Canonical source-manifest and metric-observation freezing."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.ledger import ValidationError
from app.underwriting.domain.metrics import MetricObservation
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.source_policy import (
    AuthorizationState,
    evaluate_source_policy,
)


@dataclass(frozen=True, slots=True)
class FrozenSourceManifest:
    cutoff: datetime
    source_ids: tuple[str, ...]
    manifest_hash: str
    sources: tuple[dict[str, object], ...]


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValidationError(f"{field} must not be empty")
    return normalized


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} is required")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{field} must be an ISO-8601 timestamp") from exc
    return _utc(parsed, field)


def _canonical_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    return value


def normalize_source_record(raw: Mapping[str, object], *, cutoff: datetime) -> dict[str, object]:
    """Validate and UTC-normalize one manifest source without losing identity."""
    if not isinstance(raw, Mapping):
        raise ValidationError("source must be an object")
    normalized_cutoff = _utc(cutoff, "cutoff")
    decision = evaluate_source_policy(raw)
    first_available_at = _timestamp(raw.get("first_available_at"), "first_available_at")
    if first_available_at > normalized_cutoff:
        raise ValidationError("source is unavailable at cutoff")

    normalized = dict(_canonical_value(raw))
    normalized["source_id"] = decision.source_id
    normalized["locator"] = _require_text(raw.get("locator"), "locator")
    normalized["published_at"] = _timestamp(raw.get("published_at"), "published_at").isoformat()
    normalized["first_available_at"] = first_available_at.isoformat()
    normalized["retrieved_at"] = _timestamp(raw.get("retrieved_at"), "retrieved_at").isoformat()
    normalized["authorization"] = decision.authorization.value
    normalized["display_policy"] = decision.display_policy.value
    return normalized


def freeze_manifest(payload: Mapping[str, object], cutoff: datetime) -> FrozenSourceManifest:
    """Create an order-independent, historically valid source-manifest hash."""
    if not isinstance(payload, Mapping):
        raise ValidationError("source manifest must be an object")
    schema_version = _require_text(payload.get("schema_version"), "schema_version")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, (list, tuple)):
        raise ValidationError("sources must be a list")
    normalized_cutoff = _utc(cutoff, "cutoff")
    normalized = tuple(
        sorted(
            (normalize_source_record(raw, cutoff=normalized_cutoff) for raw in raw_sources),
            key=lambda item: str(item["source_id"]),
        )
    )
    source_ids = tuple(str(item["source_id"]) for item in normalized)
    if len(source_ids) != len(set(source_ids)):
        raise ValidationError("source_id must be unique")
    serialized = {
        "schema_version": schema_version,
        "cutoff": normalized_cutoff.isoformat(),
        "sources": normalized,
    }
    return FrozenSourceManifest(
        cutoff=normalized_cutoff,
        source_ids=source_ids,
        manifest_hash=canonical_hash(serialized),
        sources=normalized,
    )


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValidationError(f"{field} must be a Decimal")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{field} must be a Decimal") from exc
    if not decimal.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal")
    return decimal


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError(f"{field} must be at least 1")
    return value


def observation_from_record(raw: Mapping[str, object], *, cutoff: datetime) -> MetricObservation:
    """Construct one source-traceable observation and enforce the cutoff."""
    if not isinstance(raw, Mapping):
        raise ValidationError("observation must be an object")
    available_at = _timestamp(raw.get("available_at"), "available_at")
    if available_at > _utc(cutoff, "cutoff"):
        raise ValidationError("available_at must not exceed cutoff")
    dimensions = raw.get("dimensions", ())
    return MetricObservation(
        definition_key=_require_text(raw.get("definition_key"), "definition_key"),
        definition_version=_positive_int(raw.get("definition_version"), "definition_version"),
        value=_decimal(raw.get("value"), "value"),
        unit=_require_text(raw.get("unit"), "unit"),
        observed_start=_timestamp(raw.get("observed_start"), "observed_start"),
        observed_end=_timestamp(raw.get("observed_end"), "observed_end"),
        effective_at=_timestamp(raw.get("effective_at"), "effective_at"),
        available_at=available_at,
        source_id=_require_text(raw.get("source_id"), "source_id"),
        source_locator=_require_text(raw.get("source_locator"), "source_locator"),
        dimensions=dimensions,  # type: ignore[arg-type]
    )


def observation_sort_key(value: MetricObservation) -> tuple[object, ...]:
    return (
        value.definition_key,
        value.definition_version,
        value.observed_end,
        value.dimensions,
        value.source_id,
        value.source_locator,
    )


def _reported_company_identity(
    raw: Mapping[str, object], value: MetricObservation,
) -> tuple[object, ...] | None:
    if raw.get("source_role") != "reported":
        return None
    if raw.get("research_object_kind", raw.get("entity_kind")) != "company":
        return None
    return (
        value.definition_key,
        value.definition_version,
        value.observed_end,
        value.dimensions,
    )


def _supporting_source_ids(raw: Mapping[str, object], source_id: str) -> tuple[str, ...]:
    support = raw.get("supporting_source_ids", ())
    if isinstance(support, str) or not isinstance(support, (list, tuple, set, frozenset)):
        raise ValidationError("supporting_source_ids must be a list")
    return (source_id,) + tuple(_require_text(value, "supporting_source_id") for value in support)


def freeze_observations(
    payload: list[dict[str, object]],
    source_manifest: FrozenSourceManifest,
) -> tuple[MetricObservation, ...]:
    """Freeze only known, resolved, source-authorized metric observations."""
    if not isinstance(source_manifest, FrozenSourceManifest):
        raise ValidationError("source_manifest is required")
    if not isinstance(payload, list):
        raise ValidationError("observations must be a list")
    known_sources = set(source_manifest.source_ids)
    authorization_by_source = {
        str(source["source_id"]): evaluate_source_policy(source).authorization
        for source in source_manifest.sources
    }
    seen: set[tuple[object, ...]] = set()
    result: list[MetricObservation] = []
    reported_company_sources: dict[tuple[object, ...], set[str]] = {}
    for raw in payload:
        if not isinstance(raw, Mapping):
            raise ValidationError("observation must be an object")
        source_id = _require_text(raw.get("source_id"), "source_id")
        if source_id not in known_sources:
            raise ValidationError("observation references unknown source")
        if raw.get("conflict_group") and raw.get("resolution") != "resolved":
            raise ValidationError("unresolved source conflict")
        value = observation_from_record(raw, cutoff=source_manifest.cutoff)
        identity = (
            value.definition_key,
            value.definition_version,
            value.observed_end,
            value.dimensions,
            value.source_id,
        )
        if identity in seen:
            raise ValidationError("observation identity must be unique")
        seen.add(identity)
        reported_identity = _reported_company_identity(raw, value)
        if reported_identity is not None:
            support_ids = _supporting_source_ids(raw, value.source_id)
            if unknown := set(support_ids) - known_sources:
                raise ValidationError("observation references unknown source")
            reported_company_sources.setdefault(reported_identity, set()).update(support_ids)
        result.append(value)

    for source_ids in reported_company_sources.values():
        if not any(
            authorization_by_source[source_id] is AuthorizationState.AUTHORIZED
            for source_id in source_ids
        ):
            raise ValidationError(
                "reference_only source cannot be the sole source of a reported company observation"
            )
    return tuple(sorted(result, key=observation_sort_key))
