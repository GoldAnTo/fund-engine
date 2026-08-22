"""Loader for the frozen CATL 2024 historical research basis.

This module is intentionally a fixture reader, not a current-data client.  It
turns the JSON artefacts into a small typed view and authenticates the source
manifest plus the *known-valued* observation batch using the same source-freeze
contracts used by the application.  ``unknown`` observations stay in the
fixture as evidence gaps; they must not be converted into zeroes or silently
fed to a numerical model.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.models.ledger import ValidationError
from app.underwriting.services.source_freeze import (
    FrozenObservationSet,
    FrozenSourceManifest,
    freeze_manifest,
    freeze_observations,
)


_ROOT = Path(__file__).resolve().parent
_CUTOFF = "2025-05-15T15:59:59+00:00"
_MANIFEST_SCHEMA = "underwriting.source-manifest.v1"
_OBSERVATION_SCHEMA = "underwriting.observations.v1"
_MECHANISM_SCHEMA = "underwriting.mechanisms.v1"
_REQUIRED_SOURCES = frozenset(
    {
        "catl-2024-annual-report-cninfo",
        "catl-2024-annual-report-official",
        "iea-global-ev-outlook-2025",
        "china-battery-alliance-2024-installations",
    }
)
_REQUIRED_MECHANISMS = frozenset(
    {
        "ev_storage_demand_to_shipments",
        "effective_capacity_to_utilization_and_price",
        "material_cost_pass_through_to_unit_margin",
        "certification_overseas_footprint_to_obtainable_share",
        "capex_working_capital_to_free_cash_flow",
        "counter_model_customer_bargaining_and_oversupply",
    }
)


def _read_json(name: str) -> dict[str, Any]:
    try:
        value = json.loads((_ROOT / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"CATL fixture {name} is unreadable") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"CATL fixture {name} must be an object")
    return value


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValidationError(f"CATL fixture {field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"CATL fixture {field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"CATL fixture {field} must be timezone-aware")
    return parsed.astimezone(UTC)


def _frozen_mapping(value: object, field: str) -> Mapping[str, str]:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValidationError(f"CATL fixture {field} must be a string object")
    return MappingProxyType(dict(sorted(value.items())))


@dataclass(frozen=True, slots=True)
class FixtureObservation:
    """One historical observation or explicitly documented evidence gap."""

    definition_key: str
    definition_version: int
    value: Decimal | None
    unit: str
    observed_start: datetime
    observed_end: datetime
    effective_at: datetime
    available_at: datetime
    source_id: str
    source_locator: str
    dimensions: Mapping[str, str]
    source_role: str
    research_object_kind: str
    derivation_parents: tuple[str, ...]
    observation_status: str


@dataclass(frozen=True, slots=True)
class CatlBaselineFixture:
    """The fixed, non-current CATL basis and its authenticated evidence batch."""

    cutoff: datetime
    sources: Mapping[str, Mapping[str, object]]
    observations: tuple[FixtureObservation, ...]
    mechanisms: tuple[Mapping[str, object], ...]
    source_manifest: FrozenSourceManifest
    frozen_observations: FrozenObservationSet

    def source(self, source_id: str) -> Mapping[str, object] | None:
        return self.sources.get(source_id)

    def observation(self, definition_key: str) -> FixtureObservation:
        matches = tuple(item for item in self.observations if item.definition_key == definition_key)
        if len(matches) != 1:
            raise KeyError(f"CATL fixture does not contain exactly one {definition_key} observation")
        return matches[0]


def _parse_observation(raw: object, *, cutoff: datetime, source_ids: frozenset[str]) -> FixtureObservation:
    if not isinstance(raw, dict):
        raise ValidationError("CATL fixture observation must be an object")
    key = raw.get("definition_key")
    source_id = raw.get("source_id")
    unit = raw.get("unit")
    locator = raw.get("source_locator")
    if not all(isinstance(value, str) and value.strip() for value in (key, source_id, unit, locator)):
        raise ValidationError("CATL fixture observation identity fields must not be empty")
    if source_id not in source_ids:
        raise ValidationError("CATL fixture observation references an unknown source")
    version = raw.get("definition_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValidationError("CATL fixture observation definition_version must be positive")
    status = raw.get("observation_status")
    if status not in {"reported", "official_industry", "unknown"}:
        raise ValidationError("CATL fixture observation status is invalid")
    raw_value = raw.get("value")
    if raw_value is None:
        if status != "unknown":
            raise ValidationError("CATL fixture only unknown observations may have null values")
        value = None
    else:
        if status == "unknown":
            raise ValidationError("CATL fixture unknown observation must have null value")
        try:
            value = Decimal(str(raw_value))
        except (InvalidOperation, ValueError) as exc:
            raise ValidationError("CATL fixture observation value must be a Decimal") from exc
        if not value.is_finite():
            raise ValidationError("CATL fixture observation value must be finite")
    observed_start = _timestamp(raw.get("observed_start"), "observed_start")
    observed_end = _timestamp(raw.get("observed_end"), "observed_end")
    effective_at = _timestamp(raw.get("effective_at"), "effective_at")
    available_at = _timestamp(raw.get("available_at"), "available_at")
    if observed_start > observed_end:
        raise ValidationError("CATL fixture observation period is invalid")
    if available_at > cutoff:
        raise ValidationError("CATL fixture observation leaks information after cutoff")
    derivation_parents = raw.get("derivation_parents")
    if not isinstance(derivation_parents, list) or not all(
        isinstance(item, str) and item for item in derivation_parents
    ):
        raise ValidationError("CATL fixture derivation_parents must be a string list")
    return FixtureObservation(
        definition_key=key.strip(),
        definition_version=version,
        value=value,
        unit=unit.strip(),
        observed_start=observed_start,
        observed_end=observed_end,
        effective_at=effective_at,
        available_at=available_at,
        source_id=source_id.strip(),
        source_locator=locator.strip(),
        dimensions=_frozen_mapping(raw.get("dimensions"), "dimensions"),
        source_role=str(raw.get("source_role", "")),
        research_object_kind=str(raw.get("research_object_kind", "")),
        derivation_parents=tuple(derivation_parents),
        observation_status=status,
    )


def _validate_mechanisms(raw: dict[str, Any]) -> tuple[Mapping[str, object], ...]:
    if raw.get("schema_version") != _MECHANISM_SCHEMA or not isinstance(raw.get("mechanisms"), list):
        raise ValidationError("CATL fixture mechanisms schema is invalid")
    mechanisms = raw["mechanisms"]
    keys: list[str] = []
    for mechanism in mechanisms:
        if not isinstance(mechanism, dict):
            raise ValidationError("CATL fixture mechanism must be an object")
        key = mechanism.get("key")
        if not isinstance(key, str):
            raise ValidationError("CATL fixture mechanism key is invalid")
        keys.append(key)
        for field in ("financial_mappings", "alternative_explanations", "falsifiers", "source_references"):
            if not isinstance(mechanism.get(field), list) or not mechanism[field]:
                raise ValidationError(f"CATL fixture mechanism {field} is required")
        review = mechanism.get("review_record")
        if not isinstance(review, dict) or review.get("promotion_path") != [
            "candidate", "adapted", "calibrated", "human_confirmed", "formal"
        ]:
            raise ValidationError("CATL fixture mechanism review_record is incomplete")
    if set(keys) != _REQUIRED_MECHANISMS or len(keys) != len(set(keys)):
        raise ValidationError("CATL fixture must contain the six governed mechanism keys")
    return tuple(MappingProxyType(item) for item in mechanisms)


def load_catl_fixture() -> CatlBaselineFixture:
    """Read and authenticate the immutable CATL historical-basis artefacts."""
    manifest_raw = _read_json("manifest.json")
    observations_raw = _read_json("observations.json")
    mechanisms_raw = _read_json("mechanisms.json")
    cutoff = _timestamp(manifest_raw.get("cutoff"), "manifest cutoff")
    if cutoff.isoformat() != _CUTOFF:
        raise ValidationError("CATL fixture cutoff must be fixed at 2025-05-15T15:59:59+00:00")
    if manifest_raw.get("schema_version") != _MANIFEST_SCHEMA or not isinstance(manifest_raw.get("sources"), list):
        raise ValidationError("CATL fixture manifest schema is invalid")
    source_manifest = freeze_manifest(manifest_raw, cutoff=cutoff)
    if set(source_manifest.source_ids) != _REQUIRED_SOURCES:
        raise ValidationError("CATL fixture must retain the four required authoritative sources")
    sources = MappingProxyType(
        {str(item["source_id"]): item for item in source_manifest.sources}
    )
    if observations_raw.get("schema_version") != _OBSERVATION_SCHEMA or not isinstance(observations_raw.get("observations"), list):
        raise ValidationError("CATL fixture observations schema is invalid")
    observations = tuple(
        _parse_observation(item, cutoff=cutoff, source_ids=frozenset(sources))
        for item in observations_raw["observations"]
    )
    if len({item.definition_key for item in observations}) != len(observations):
        raise ValidationError("CATL fixture observation keys must be unique")
    known_payload: list[dict[str, object]] = []
    for item, raw in zip(observations, observations_raw["observations"], strict=True):
        if item.value is not None:
            known_payload.append(dict(raw))
    frozen_observations = freeze_observations(known_payload, source_manifest=source_manifest)
    return CatlBaselineFixture(
        cutoff=cutoff,
        sources=sources,
        observations=observations,
        mechanisms=_validate_mechanisms(mechanisms_raw),
        source_manifest=source_manifest,
        frozen_observations=frozen_observations,
    )
