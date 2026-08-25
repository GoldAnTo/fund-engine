"""Canonical source-manifest and metric-observation freezing."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import math
import re
from types import MappingProxyType
import weakref

from app.models.ledger import ValidationError
from app.underwriting.domain.metrics import MetricObservation, SourceRole
from app.underwriting.domain.types import ResearchObjectKind
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.source_policy import (
    AuthorizationState,
    evaluate_source_policy,
)


_AUTHORITY_SOURCE_ROLES = {
    "issuer_filing": SourceRole.REPORTED,
    "issuer_publication": SourceRole.REPORTED,
    "issuer_website": SourceRole.REPORTED,
    "exchange_filing": SourceRole.REPORTED,
    "official_industry": SourceRole.OFFICIAL_INDUSTRY,
    "intergovernmental_agency": SourceRole.OFFICIAL_INDUSTRY,
    "government_archive": SourceRole.OFFICIAL_INDUSTRY,
    "government_preserved": SourceRole.OFFICIAL_INDUSTRY,
    "government_statistic": SourceRole.OFFICIAL_INDUSTRY,
    "derived": SourceRole.DERIVED,
    "assumption": SourceRole.ASSUMPTION,
}
_COMPANY_METRIC_ROLES = frozenset({SourceRole.REPORTED, SourceRole.DERIVED})
_INDUSTRY_METRIC_ROLES = frozenset(
    {SourceRole.OFFICIAL_INDUSTRY, SourceRole.DERIVED, SourceRole.ASSUMPTION}
)
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class FrozenSourceManifest:
    schema_version: str
    cutoff: datetime
    source_ids: tuple[str, ...]
    manifest_hash: str
    sources: tuple[Mapping[str, object], ...]

    def __init__(self) -> None:
        raise TypeError("FrozenSourceManifest must be created by freeze_manifest")


# A manifest is an authority boundary as well as a convenient immutable value.
# ``object.__new__`` can otherwise manufacture an exact-class lookalike that
# carries a copied manifest hash.  Keep both identity and a deep source-record
# snapshot for the manifests emitted by ``freeze_manifest``.
_MANIFEST_REGISTRATION_CAPABILITY = object()
_OBSERVATION_SET_REGISTRATION_CAPABILITY = object()


_TRUSTED_SOURCE_MANIFESTS: dict[
    int,
    tuple[
        weakref.ReferenceType[FrozenSourceManifest],
        str,
        datetime,
        tuple[str, ...],
        str,
        tuple[int, ...],
        str,
    ],
] = {}


def _thaw_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _manifest_sources_hash(sources: tuple[Mapping[str, object], ...]) -> str:
    return canonical_hash(tuple(_thaw_json_value(source) for source in sources))


def _is_deeply_immutable_json(value: object) -> bool:
    if type(value) is MappingProxyType:
        return all(_is_deeply_immutable_json(item) for item in value.values())
    if isinstance(value, Mapping):
        return False
    if isinstance(value, tuple):
        return all(_is_deeply_immutable_json(item) for item in value)
    return not isinstance(value, list)


def _register_source_manifest(
    value: FrozenSourceManifest,
    *,
    _capability: object | None = None,
) -> FrozenSourceManifest:
    if _capability is not _MANIFEST_REGISTRATION_CAPABILITY:
        raise ValidationError("frozen source manifest registration requires internal construction capability")
    _validate_source_manifest_payload(value)
    identity = id(value)

    def _discard(_: weakref.ReferenceType[FrozenSourceManifest]) -> None:
        _TRUSTED_SOURCE_MANIFESTS.pop(identity, None)

    _TRUSTED_SOURCE_MANIFESTS[identity] = (
        weakref.ref(value, _discard),
        value.schema_version,
        value.cutoff,
        value.source_ids,
        value.manifest_hash,
        tuple(id(source) for source in value.sources),
        _manifest_sources_hash(value.sources),
    )
    return value


def _is_trusted_source_manifest(value: FrozenSourceManifest) -> bool:
    trusted = _TRUSTED_SOURCE_MANIFESTS.get(id(value))
    return trusted is not None and trusted[0]() is value


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class FrozenObservationSet:
    """Authenticated, cutoff-bound immutable observation batch."""
    cutoff: datetime
    source_manifest_hash: str
    observation_set_hash: str
    observations: tuple[MetricObservation, ...]

    def __init__(self) -> None:
        raise TypeError("FrozenObservationSet must be created by freeze_observations")

    def __iter__(self):
        return iter(self.observations)

    def __len__(self) -> int:
        return len(self.observations)

    def __getitem__(self, index: int) -> MetricObservation:
        return self.observations[index]


# A frozen dataclass alone is not an authorization boundary: Python callers can
# still allocate one through ``object.__new__``.  Keep a process-local identity
# registry for batches created only after the source-policy freeze succeeded.
# The registry deliberately compares object identity rather than value equality,
# so a forged lookalike with a valid-looking hash is not trusted.
_TRUSTED_OBSERVATION_SETS: dict[
    int,
    tuple[
        weakref.ReferenceType[FrozenObservationSet],
        datetime,
        str,
        str,
        tuple[int, ...],
        tuple[str, ...],
    ],
] = {}


def _register_observation_set(
    value: FrozenObservationSet,
    *,
    _capability: object | None = None,
) -> FrozenObservationSet:
    if _capability is not _OBSERVATION_SET_REGISTRATION_CAPABILITY:
        raise ValidationError("frozen observation set registration requires internal construction capability")
    _validate_observation_set_payload(value)
    identity = id(value)

    def _discard(_: weakref.ReferenceType[FrozenObservationSet]) -> None:
        _TRUSTED_OBSERVATION_SETS.pop(identity, None)

    _TRUSTED_OBSERVATION_SETS[identity] = (
        weakref.ref(value, _discard),
        value.cutoff,
        value.source_manifest_hash,
        value.observation_set_hash,
        tuple(id(item) for item in value.observations),
        tuple(item.content_hash for item in value.observations),
    )
    return value


def _is_trusted_observation_set(value: FrozenObservationSet) -> bool:
    trusted = _TRUSTED_OBSERVATION_SETS.get(id(value))
    return trusted is not None and trusted[0]() is value


def _freeze_json_value(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


def _frozen_manifest(
    *,
    schema_version: str,
    cutoff: datetime,
    source_ids: tuple[str, ...],
    manifest_hash: str,
    sources: tuple[dict[str, object], ...],
) -> FrozenSourceManifest:
    """Build the immutable result only after policy and hash validation."""
    frozen_sources = tuple(_freeze_json_value(source) for source in sources)
    if not all(isinstance(source, Mapping) for source in frozen_sources):
        raise AssertionError("validated sources must be mappings")
    value = object.__new__(FrozenSourceManifest)
    object.__setattr__(value, "schema_version", schema_version)
    object.__setattr__(value, "cutoff", cutoff)
    object.__setattr__(value, "source_ids", source_ids)
    object.__setattr__(value, "manifest_hash", manifest_hash)
    object.__setattr__(value, "sources", frozen_sources)
    return value


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


def _canonical_json_value(value: object) -> object:
    """Copy JSON-compatible manifest content and reject unstable Python values."""
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("source manifest object keys must be strings")
            normalized[key] = _canonical_json_value(item)
        return normalized
    if isinstance(value, list):
        return [_canonical_json_value(item) for item in value]
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValidationError("source manifest Decimal must be finite")
        raise ValidationError("source manifest must be JSON-compatible")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError("source manifest float must be finite")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise ValidationError("source manifest must be JSON-compatible")


def normalize_source_record(raw: Mapping[str, object], *, cutoff: datetime) -> dict[str, object]:
    """Validate and UTC-normalize one manifest source without losing identity."""
    if not isinstance(raw, Mapping):
        raise ValidationError("source must be an object")
    canonical_raw = _canonical_json_value(raw)
    if not isinstance(canonical_raw, dict):  # Defensive: mappings normalize to dictionaries.
        raise ValidationError("source must be an object")
    normalized_cutoff = _utc(cutoff, "cutoff")
    decision = evaluate_source_policy(canonical_raw)
    first_available_at = _timestamp(
        canonical_raw.get("first_available_at"), "first_available_at"
    )
    if first_available_at > normalized_cutoff:
        raise ValidationError("source is unavailable at cutoff")

    normalized = canonical_raw
    normalized["source_id"] = decision.source_id
    normalized["locator"] = _require_text(canonical_raw.get("locator"), "locator")
    normalized["published_at"] = _timestamp(
        canonical_raw.get("published_at"), "published_at"
    ).isoformat()
    normalized["first_available_at"] = first_available_at.isoformat()
    normalized["retrieved_at"] = _timestamp(
        canonical_raw.get("retrieved_at"), "retrieved_at"
    ).isoformat()
    normalized["authorization"] = decision.authorization.value
    normalized["display_policy"] = decision.display_policy.value
    return normalized


def freeze_manifest(payload: Mapping[str, object], cutoff: datetime) -> FrozenSourceManifest:
    """Create an order-independent, historically valid source-manifest hash."""
    if not isinstance(payload, Mapping):
        raise ValidationError("source manifest must be an object")
    canonical_payload = _canonical_json_value(payload)
    if not isinstance(canonical_payload, dict):  # Defensive: mappings normalize to dictionaries.
        raise ValidationError("source manifest must be an object")
    schema_version = _require_text(canonical_payload.get("schema_version"), "schema_version")
    raw_sources = canonical_payload.get("sources")
    if not isinstance(raw_sources, list):
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
    return _register_source_manifest(
        _frozen_manifest(
            schema_version=schema_version,
            cutoff=normalized_cutoff,
            source_ids=source_ids,
            manifest_hash=canonical_hash(serialized),
            sources=normalized,
        ),
        _capability=_MANIFEST_REGISTRATION_CAPABILITY,
    )


def _validate_source_manifest_payload(value: object) -> FrozenSourceManifest:
    """Strictly recompute a frozen manifest from its actual source records."""
    if type(value) is not FrozenSourceManifest:
        raise ValidationError("frozen source manifest type is invalid")
    schema_version = _require_text(getattr(value, "schema_version", None), "schema_version")
    cutoff = _utc(getattr(value, "cutoff", None), "source manifest cutoff")
    if cutoff != getattr(value, "cutoff", None):
        raise ValidationError("frozen source manifest cutoff must be canonical UTC")
    source_ids_value = getattr(value, "source_ids", None)
    if not isinstance(source_ids_value, tuple) or not source_ids_value:
        raise ValidationError("frozen source manifest source_ids are invalid")
    if not all(isinstance(source_id, str) and source_id.strip() for source_id in source_ids_value):
        raise ValidationError("frozen source manifest source_ids are invalid")
    if tuple(sorted(source_ids_value)) != source_ids_value or len(set(source_ids_value)) != len(source_ids_value):
        raise ValidationError("frozen source manifest source_ids must be canonical")
    sources_value = getattr(value, "sources", None)
    if not isinstance(sources_value, tuple) or not sources_value:
        raise ValidationError("frozen source manifest sources are invalid")
    if not all(_is_deeply_immutable_json(source) for source in sources_value):
        raise ValidationError("frozen source manifest sources must be immutable")
    raw_sources = tuple(_thaw_json_value(source) for source in sources_value)
    normalized_sources = tuple(
        sorted(
            (normalize_source_record(source, cutoff=cutoff) for source in raw_sources),
            key=lambda item: str(item["source_id"]),
        )
    )
    source_ids = tuple(str(source["source_id"]) for source in normalized_sources)
    if source_ids != source_ids_value:
        raise ValidationError("frozen source manifest sources do not match source_ids")
    expected_hash = canonical_hash(
        {
            "schema_version": schema_version,
            "cutoff": cutoff.isoformat(),
            "sources": normalized_sources,
        }
    )
    manifest_hash = getattr(value, "manifest_hash", None)
    if not isinstance(manifest_hash, str) or _SHA256.fullmatch(manifest_hash) is None:
        raise ValidationError("frozen source manifest hash is invalid")
    if manifest_hash != expected_hash:
        raise ValidationError("frozen source manifest hash does not match sources")
    return value


def validate_frozen_source_manifest(
    value: object,
    *,
    cutoff: datetime | None = None,
) -> FrozenSourceManifest:
    """Validate the exact authenticated manifest and its frozen source records."""
    _validate_source_manifest_payload(value)
    if type(value) is not FrozenSourceManifest or not _is_trusted_source_manifest(value):
        raise ValidationError("frozen source manifest must be created by freeze_manifest")
    trusted = _TRUSTED_SOURCE_MANIFESTS[id(value)]
    normalized_cutoff = _utc(value.cutoff, "source manifest cutoff")
    if cutoff is not None and normalized_cutoff != _utc(cutoff, "cutoff"):
        raise ValidationError("source manifest cutoff does not match dependency cutoff")
    if (
        (value.schema_version, normalized_cutoff, value.source_ids, value.manifest_hash)
        != trusted[1:5]
        or tuple(id(source) for source in value.sources) != trusted[5]
        or _manifest_sources_hash(value.sources) != trusted[6]
    ):
        raise ValidationError("frozen source manifest no longer matches authenticated batch")
    return value


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


def _submitted_observation_classification(
    raw: Mapping[str, object],
) -> tuple[SourceRole, ResearchObjectKind]:
    source_role = raw.get("source_role")
    if source_role is None:
        raise ValidationError("source_role is required")
    research_object_kind = raw.get("research_object_kind")
    if research_object_kind is None:
        raise ValidationError("research_object_kind is required")
    try:
        return SourceRole(source_role), ResearchObjectKind(research_object_kind)
    except ValueError as exc:
        raise ValidationError("observation evidence classification is invalid") from exc


def _trusted_source_role(source: Mapping[str, object]) -> SourceRole:
    authority = _require_text(source.get("authority"), "authority")
    try:
        return _AUTHORITY_SOURCE_ROLES[authority]
    except KeyError as exc:
        raise ValidationError("source authority cannot classify observations") from exc


def _trusted_metric_context(
    definition_key: str,
) -> tuple[ResearchObjectKind, frozenset[SourceRole]]:
    if definition_key.startswith(("company.", "segment.")):
        return ResearchObjectKind.COMPANY, _COMPANY_METRIC_ROLES
    if definition_key.startswith("industry."):
        return ResearchObjectKind.INDUSTRY, _INDUSTRY_METRIC_ROLES
    raise ValidationError("metric context is unknown")


def _reported_company_evidence_key(value: MetricObservation) -> tuple[object, ...]:
    return (
        value.definition_key,
        value.definition_version,
        value.unit,
        value.value,
        value.observed_start,
        value.observed_end,
        value.effective_at,
        value.dimensions,
    )


def _validate_observation_set_payload(value: object) -> FrozenObservationSet:
    """Strictly recompute a batch hash before it is allowed into the registry."""
    if type(value) is not FrozenObservationSet:
        raise ValidationError("frozen observation set type is invalid")
    cutoff = _utc(value.cutoff, "frozen observation set cutoff")
    if cutoff != value.cutoff:
        raise ValidationError("frozen observation set cutoff must be canonical UTC")
    if not isinstance(value.source_manifest_hash, str) or _SHA256.fullmatch(value.source_manifest_hash) is None:
        raise ValidationError("frozen observation set manifest hash is invalid")
    if not isinstance(value.observations, tuple) or not value.observations:
        raise ValidationError("frozen observation set must contain observations")
    if not all(type(item) is MetricObservation for item in value.observations):
        raise ValidationError("frozen observation set observations are invalid")
    if tuple(sorted(value.observations, key=observation_sort_key)) != value.observations:
        raise ValidationError("frozen observation set observations must be canonically ordered")
    observation_ids: set[object] = set()
    content_hashes: list[str] = []
    for observation in value.observations:
        observation.__post_init__()
        if observation.available_at > cutoff:
            raise ValidationError("frozen observation is unavailable at set cutoff")
        if observation.observation_id in observation_ids:
            raise ValidationError("frozen observation set observation IDs must be unique")
        observation_ids.add(observation.observation_id)
        content_hashes.append(observation.content_hash)
    expected_hash = canonical_hash(tuple(content_hashes))
    if (
        not isinstance(value.observation_set_hash, str)
        or _SHA256.fullmatch(value.observation_set_hash) is None
        or value.observation_set_hash != expected_hash
    ):
        raise ValidationError("frozen observation set hash does not match observations")
    return value


def validate_frozen_observation_set(
    value: object,
    *,
    source_manifest: FrozenSourceManifest,
    cutoff: datetime,
) -> FrozenObservationSet:
    """Re-establish the authenticated batch boundary before it is consumed.

    Mechanism compilation is intentionally stricter than merely accepting a
    tuple of immutable observations.  It must receive the exact batch emitted
    by :func:`freeze_observations`, tied to the same source-manifest hash and
    historical cutoff, with a recomputed canonical observation-set hash.
    """
    if type(value) is not FrozenObservationSet or not _is_trusted_observation_set(value):
        raise ValidationError("frozen observation set must be created by freeze_observations")
    trusted = _TRUSTED_OBSERVATION_SETS[id(value)]
    _validate_observation_set_payload(value)
    validate_frozen_source_manifest(source_manifest, cutoff=cutoff)
    normalized_cutoff = _utc(cutoff, "cutoff")
    if value.cutoff != normalized_cutoff or source_manifest.cutoff != normalized_cutoff:
        raise ValidationError("frozen observation set cutoff must match dependency cutoff")
    if value.source_manifest_hash != source_manifest.manifest_hash:
        raise ValidationError("frozen observation set manifest hash does not match dependency manifest")
    known_sources = set(source_manifest.source_ids)
    content_hashes: list[str] = []
    observation_ids: set[object] = set()
    for observation in value.observations:
        # Re-run the domain contract so direct object mutation cannot slip into
        # a previously authenticated batch.
        observation.__post_init__()
        if observation.source_id not in known_sources or observation.available_at > normalized_cutoff:
            raise ValidationError("frozen observation is unavailable at dependency cutoff")
        if observation.observation_id in observation_ids:
            raise ValidationError("frozen observation set observation IDs must be unique")
        observation_ids.add(observation.observation_id)
        content_hashes.append(observation.content_hash)
    if (
        (value.cutoff, value.source_manifest_hash, value.observation_set_hash)
        != trusted[1:4]
        or tuple(id(item) for item in value.observations) != trusted[4]
        or tuple(content_hashes) != trusted[5]
    ):
        raise ValidationError("frozen observation set no longer matches authenticated batch")
    return value


def freeze_observations(
    payload: list[dict[str, object]],
    source_manifest: FrozenSourceManifest,
) -> FrozenObservationSet:
    """Freeze only known, resolved, source-authorized metric observations."""
    validate_frozen_source_manifest(source_manifest)
    if not isinstance(payload, list):
        raise ValidationError("observations must be a list")
    known_sources = set(source_manifest.source_ids)
    authorization_by_source = {
        str(source["source_id"]): evaluate_source_policy(source).authorization
        for source in source_manifest.sources
    }
    source_role_by_source = {
        str(source["source_id"]): _trusted_source_role(source)
        for source in source_manifest.sources
    }
    seen: set[tuple[object, ...]] = set()
    result: list[MetricObservation] = []
    reported_company_sources: dict[tuple[object, ...], set[str]] = {}

    def append_observation(raw: Mapping[str, object]) -> None:
        """Authenticate one raw record after any derived lineage is bound."""
        if not isinstance(raw, Mapping):
            raise ValidationError("observation must be an object")
        source_id = _require_text(raw.get("source_id"), "source_id")
        if source_id not in known_sources:
            raise ValidationError("observation references unknown source")
        if raw.get("conflict_group") and raw.get("resolution") != "resolved":
            raise ValidationError("unresolved source conflict")
        value = observation_from_record(raw, cutoff=source_manifest.cutoff)
        submitted_source_role, submitted_research_object_kind = (
            _submitted_observation_classification(raw)
        )
        trusted_source_role = source_role_by_source[source_id]
        trusted_research_object_kind, allowed_source_roles = _trusted_metric_context(
            value.definition_key
        )
        if (
            submitted_source_role is not SourceRole.DERIVED
            and submitted_source_role is not trusted_source_role
        ):
            raise ValidationError("source_role does not match source authority")
        if submitted_research_object_kind is not trusted_research_object_kind:
            raise ValidationError("research_object_kind does not match metric context")
        if submitted_source_role is SourceRole.DERIVED:
            parents = raw.get("derivation_parents")
            formula = raw.get("derivation_formula")
            if (
                not isinstance(parents, list)
                or not parents
                or not all(isinstance(parent, str) and parent.strip() for parent in parents)
                or not isinstance(formula, str)
                or not formula.strip()
            ):
                raise ValidationError("derived observation requires formula and derivation parents")
            if trusted_source_role not in {SourceRole.REPORTED, SourceRole.OFFICIAL_INDUSTRY}:
                raise ValidationError("source authority cannot support a derived observation")
        if submitted_source_role not in allowed_source_roles:
            raise ValidationError("source authority is not allowed for metric context")
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
        if (
            trusted_source_role is SourceRole.REPORTED
            and trusted_research_object_kind is ResearchObjectKind.COMPANY
        ):
            reported_company_sources.setdefault(
                _reported_company_evidence_key(value), set()
            ).add(value.source_id)
        result.append(value)

    raw_records: list[Mapping[str, object]] = []
    for raw in payload:
        if not isinstance(raw, Mapping):
            raise ValidationError("observation must be an object")
        raw_records.append(raw)

    pending: list[Mapping[str, object]] = []
    for raw in raw_records:
        if raw.get("source_role") is SourceRole.DERIVED or raw.get("source_role") == SourceRole.DERIVED.value:
            pending.append(raw)
        else:
            append_observation(raw)

    def derived_identity(raw: Mapping[str, object]) -> tuple[str, tuple[str, ...], str]:
        key = _require_text(raw.get("definition_key"), "definition_key")
        parents = raw.get("derivation_parents")
        formula = raw.get("derivation_formula")
        if (
            not isinstance(parents, list)
            or not parents
            or not all(isinstance(parent, str) and parent.strip() for parent in parents)
            or not isinstance(formula, str)
            or not formula.strip()
        ):
            raise ValidationError("derived observation requires formula and derivation parents")
        normalized_parents = tuple(parent.strip() for parent in parents)
        if len(normalized_parents) != len(set(normalized_parents)):
            raise ValidationError("derived observation parents must be unique")
        if key in normalized_parents:
            raise ValidationError("derived observation must not reference itself")
        return key, normalized_parents, " ".join(formula.split())

    def bind_derived_lineage(
        raw: Mapping[str, object],
        *,
        parents: tuple[tuple[str, MetricObservation], ...],
        formula: str,
    ) -> dict[str, object]:
        raw_dimensions = raw.get("dimensions")
        if not isinstance(raw_dimensions, Mapping):
            raise ValidationError("derived observation dimensions must be an object")
        dimensions = dict(raw_dimensions)
        reserved = {
            "_derivation_formula",
            "_derivation_parent_observation_ids",
            "_derivation_parent_content_hashes",
        }
        if reserved.intersection(dimensions):
            raise ValidationError("derived observation dimensions may not pre-populate lineage bindings")
        ordered_parents = tuple(sorted(parents, key=lambda pair: pair[0]))
        dimensions["_derivation_formula"] = formula
        dimensions["_derivation_parent_observation_ids"] = ",".join(
            str(parent.observation_id) for _, parent in ordered_parents
        )
        dimensions["_derivation_parent_content_hashes"] = ",".join(
            parent.content_hash for _, parent in ordered_parents
        )
        return dict(raw) | {"dimensions": dimensions, "derivation_formula": formula}

    while pending:
        progressed = False
        still_pending: list[Mapping[str, object]] = []
        pending_keys = {
            _require_text(raw.get("definition_key"), "definition_key") for raw in pending
        }
        for raw in pending:
            _, parent_keys, formula = derived_identity(raw)
            parent_matches = tuple(
                (
                    parent_key,
                    tuple(item for item in result if item.definition_key == parent_key),
                )
                for parent_key in parent_keys
            )
            unresolved = tuple(
                parent_key for parent_key, matches in parent_matches if not matches
            )
            if unresolved:
                if any(parent_key not in pending_keys for parent_key in unresolved):
                    raise ValidationError("derived observation parents must resolve in frozen batch")
                still_pending.append(raw)
                continue
            if any(len(matches) != 1 for _, matches in parent_matches):
                raise ValidationError("derived observation parent must resolve to exactly one observation")
            append_observation(
                bind_derived_lineage(
                    raw,
                    parents=tuple((key, matches[0]) for key, matches in parent_matches),
                    formula=formula,
                )
            )
            progressed = True
        if not progressed:
            raise ValidationError("derived observation parents must be acyclic")
        pending = still_pending

    for source_ids in reported_company_sources.values():
        if not any(
            authorization_by_source[source_id] is AuthorizationState.AUTHORIZED
            for source_id in source_ids
        ):
            raise ValidationError(
                "reference_only source cannot be the sole source of a reported company observation"
            )
    observations = tuple(sorted(result, key=observation_sort_key))
    value = object.__new__(FrozenObservationSet)
    object.__setattr__(value, "cutoff", source_manifest.cutoff)
    object.__setattr__(value, "source_manifest_hash", source_manifest.manifest_hash)
    object.__setattr__(value, "observations", observations)
    object.__setattr__(value, "observation_set_hash", canonical_hash(tuple(item.content_hash for item in observations)))
    return _register_observation_set(
        value,
        _capability=_OBSERVATION_SET_REGISTRATION_CAPABILITY,
    )
