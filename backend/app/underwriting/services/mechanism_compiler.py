"""Compile only review-governed, source-bound causal mechanisms."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import DecimalException
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from app.models.ledger import ValidationError
from app.underwriting.domain.mechanisms import (
    FinancialMapping,
    MechanismPack,
    MechanismStatus,
)
from app.underwriting.domain.metrics import MetricObservation
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.source_freeze import FrozenSourceManifest
from app.underwriting.services.source_policy import AuthorizationState


_NEXT_STATUS = {
    MechanismStatus.CANDIDATE: MechanismStatus.ADAPTED,
    MechanismStatus.ADAPTED: MechanismStatus.CALIBRATED,
    MechanismStatus.CALIBRATED: MechanismStatus.HUMAN_CONFIRMED,
    MechanismStatus.HUMAN_CONFIRMED: MechanismStatus.FORMAL,
    MechanismStatus.FORMAL: MechanismStatus.CHALLENGED,
    MechanismStatus.CHALLENGED: MechanismStatus.RETIRED_OR_REPLACED,
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must not be empty")
    return value.strip()


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValidationError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class MetricDefinitionDependency:
    """A metric-definition identity known at a particular historical cutoff."""

    metric_key: str
    definition_id: UUID | str
    definition_version: int
    content_hash: str
    source_manifest_hash: str
    available_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "metric_key", _require_text(self.metric_key, "metric_key"))
        if type(self.definition_id) is str:
            object.__setattr__(
                self, "definition_id", _require_text(self.definition_id, "definition_id")
            )
        elif type(self.definition_id) is not UUID:
            raise ValidationError("definition_id must be a UUID or non-empty string")
        if (
            isinstance(self.definition_version, bool)
            or not isinstance(self.definition_version, int)
            or self.definition_version < 1
        ):
            raise ValidationError("definition_version must be at least 1")
        if not isinstance(self.content_hash, str) or _SHA256.fullmatch(self.content_hash) is None:
            raise ValidationError("definition content_hash must be a lowercase SHA-256")
        if (
            not isinstance(self.source_manifest_hash, str)
            or _SHA256.fullmatch(self.source_manifest_hash) is None
        ):
            raise ValidationError("definition source_manifest_hash must be a lowercase SHA-256")
        object.__setattr__(self, "available_at", _utc(self.available_at, "available_at"))


@dataclass(frozen=True, slots=True)
class FrozenResolvedObservation:
    observation_id: UUID
    metric_key: str
    definition_id: UUID | str
    definition_version: int
    definition_content_hash: str
    source_id: str
    source_manifest_hash: str
    available_at: datetime


@dataclass(frozen=True, slots=True)
class MechanismDependencyContext:
    """Frozen, cutoff-bound dependencies permitted for mechanism compilation."""

    cutoff: datetime
    metric_definitions: tuple[MetricDefinitionDependency, ...]
    source_manifest: FrozenSourceManifest
    metric_observations: tuple[MetricObservation, ...] = tuple()

    def __post_init__(self) -> None:
        cutoff = _utc(self.cutoff, "cutoff")
        object.__setattr__(self, "cutoff", cutoff)
        if type(self.source_manifest) is not FrozenSourceManifest:
            raise ValidationError("source_manifest must be a FrozenSourceManifest")
        if self.source_manifest.cutoff != cutoff:
            raise ValidationError("source manifest cutoff must match dependency cutoff")
        if not isinstance(self.metric_definitions, tuple) or not self.metric_definitions:
            raise ValidationError("metric_definitions must be a non-empty tuple")
        if not isinstance(self.metric_observations, tuple) or not self.metric_observations:
            raise ValidationError("metric_observations must be a non-empty tuple")
        if not all(type(item) is MetricObservation for item in self.metric_observations):
            raise ValidationError("metric_observations must contain frozen MetricObservation values")
        observations = {item.definition_key: item for item in self.metric_observations}
        if not all(type(item) is MetricDefinitionDependency for item in self.metric_definitions):
            raise ValidationError("metric_definitions must contain MetricDefinitionDependency values")
        keys = tuple(item.metric_key for item in self.metric_definitions)
        ids = tuple(str(item.definition_id) for item in self.metric_definitions)
        if len(keys) != len(set(keys)):
            raise ValidationError("metric definition keys must be unique")
        if len(ids) != len(set(ids)):
            raise ValidationError("metric definition IDs must be unique")
        for item in self.metric_definitions:
            if item.available_at > cutoff:
                raise ValidationError("metric definition is unavailable at cutoff")
            if item.source_manifest_hash != self.source_manifest.manifest_hash:
                raise ValidationError("definition source manifest hash does not match dependency manifest")
            observation = observations.get(item.metric_key)
            if observation is None or observation.definition_version != item.definition_version:
                raise ValidationError("metric definition must bind an actual frozen observation")
            if observation is not None and (observation.source_id not in self.source_manifest.source_ids or observation.available_at > cutoff):
                raise ValidationError("frozen observation is unavailable at cutoff")
        for source in self.source_manifest.sources:
            _require_text(source.get("source_id"), "source_id")
            first_available_at = source.get("first_available_at")
            if not isinstance(first_available_at, str):
                raise ValidationError("source first_available_at is required")
            try:
                available_at = datetime.fromisoformat(first_available_at)
            except ValueError as exc:
                raise ValidationError(
                    "source first_available_at must be an ISO-8601 timestamp"
                ) from exc
            if _utc(available_at, "source first_available_at") > cutoff:
                raise ValidationError("source is unavailable at cutoff")
            try:
                authorization = AuthorizationState(source.get("authorization"))
            except ValueError as exc:
                raise ValidationError("source authorization is invalid") from exc
            if authorization is AuthorizationState.FORBIDDEN:
                raise ValidationError("source authorization is forbidden")

    @property
    def metric_definition_bindings(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((item.metric_key, str(item.definition_id)) for item in self.metric_definitions))

    @property
    def metric_definition_map(self) -> dict[str, str]:
        return dict(self.metric_definition_bindings)

    @property
    def metric_definition_by_key(self) -> dict[str, MetricDefinitionDependency]:
        return {item.metric_key: item for item in self.metric_definitions}

    @property
    def source_ids(self) -> tuple[str, ...]:
        return self.source_manifest.source_ids


@dataclass(frozen=True, slots=True)
class CompiledMechanisms:
    mechanisms: tuple[MechanismPack, ...]
    cutoff: datetime
    content_hash: str
    metric_definition_ids: tuple[str, ...]
    metric_definition_bindings: tuple[tuple[str, str], ...]
    metric_definition_provenance: tuple[MetricDefinitionDependency, ...]
    frozen_metric_definition_provenance: tuple[MetricDefinitionDependency, ...]
    frozen_metric_observations: tuple[FrozenResolvedObservation, ...]
    source_manifest_hash: str
    source_ids: tuple[str, ...]

    @property
    def dependency_ids(self) -> tuple[str, ...]:
        """Stable complete lineage, with metric definitions before sources."""
        return self.metric_definition_ids + self.source_ids


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
    if type(value) is not MechanismPack:
        raise ValidationError("mechanism is required")
    try:
        # Frozen dataclasses can still be altered with object.__setattr__ in a
        # malicious caller. Re-run every nested domain contract before using
        # attributes in set construction, sorting, or hash serialization.
        value.__post_init__()
        for mapping in value.financial_mappings:
            mapping.__post_init__()
        for falsifier in value.falsifiers:
            falsifier.__post_init__()
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
        if value.review_evidence_id is None:
            raise ValidationError("formal mechanism requires review_evidence_id")
        if (
            value.predecessor_status is not MechanismStatus.HUMAN_CONFIRMED
            or value.predecessor_version != value.version - 1
        ):
            raise ValidationError("formal mechanism requires a reviewed predecessor")
        mapped_drivers = {mapping.driver_key for mapping in value.financial_mappings}
        if mapped_drivers != set(value.driver_keys):
            raise ValidationError("formal mechanism mappings must cover every driver_key")
        if not _causal_chain_is_acyclic(value.financial_mappings):
            raise ValidationError("formal mechanism causal chain must be acyclic")
    except ValidationError:
        raise
    except (AttributeError, TypeError, ValueError, DecimalException) as exc:
        raise ValidationError("formal mechanism structure is invalid") from exc


def transition_mechanism(
    value: MechanismPack,
    next_status: MechanismStatus,
    *,
    human_confirmation_identity: str | None = None,
    review_evidence_id: UUID | None = None,
    revision_reason: str | None = None,
) -> MechanismPack:
    """Append a successor-shaped domain version through exactly one transition."""
    if type(value) is not MechanismPack:
        raise ValidationError("mechanism is required")
    if type(next_status) is not MechanismStatus:
        raise ValidationError("next mechanism status is invalid")
    if _NEXT_STATUS.get(value.status) is not next_status:
        raise ValidationError("mechanism lifecycle transition is not allowed")

    confirmation = human_confirmation_identity or value.human_confirmation_identity
    evidence = review_evidence_id or value.review_evidence_id
    if next_status is MechanismStatus.HUMAN_CONFIRMED and (
        confirmation is None or evidence is None
    ):
        raise ValidationError(
            "human_confirmation_identity and review_evidence_id are required before formal"
        )
    successor = replace(
        value,
        version=value.version + 1,
        status=next_status,
        human_confirmation_identity=confirmation,
        review_evidence_id=evidence,
        predecessor_status=value.status,
        predecessor_version=value.version,
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
        "review_evidence_id": (
            str(value.review_evidence_id) if value.review_evidence_id is not None else None
        ),
        "predecessor_status": (
            value.predecessor_status.value if value.predecessor_status is not None else None
        ),
        "predecessor_version": value.predecessor_version,
        "revision_reason": value.revision_reason,
    }


def _compiled_payload(
    *,
    mechanisms: tuple[MechanismPack, ...],
    cutoff: datetime,
    source_manifest_hash: str,
    metric_definition_bindings: tuple[tuple[str, str], ...],
    metric_definition_provenance: tuple[MetricDefinitionDependency, ...],
    frozen_metric_definition_provenance: tuple[MetricDefinitionDependency, ...],
    frozen_metric_observations: tuple[FrozenResolvedObservation, ...],
    source_ids: tuple[str, ...],
) -> dict[str, object]:
    """The complete integrity-covered representation of compiled mechanisms."""
    return {
        "cutoff": cutoff.isoformat(),
        "source_manifest_hash": source_manifest_hash,
        "mechanisms": tuple(_mechanism_payload(value) for value in mechanisms),
        "metric_definition_bindings": metric_definition_bindings,
        "metric_definition_provenance": tuple(
            {
                "metric_key": dependency.metric_key,
                "definition_id": str(dependency.definition_id),
                "definition_version": dependency.definition_version,
                "content_hash": dependency.content_hash,
                "source_manifest_hash": dependency.source_manifest_hash,
                "available_at": dependency.available_at.isoformat(),
            }
            for dependency in metric_definition_provenance
        ),
        "frozen_metric_definition_provenance": tuple(
            {
                "metric_key": dependency.metric_key,
                "definition_id": str(dependency.definition_id),
                "definition_version": dependency.definition_version,
                "content_hash": dependency.content_hash,
                "source_manifest_hash": dependency.source_manifest_hash,
                "available_at": dependency.available_at.isoformat(),
            }
            for dependency in frozen_metric_definition_provenance
        ),
        "frozen_metric_observations": tuple(
            {"observation_id": str(item.observation_id), "metric_key": item.metric_key, "definition_id": str(item.definition_id), "definition_version": item.definition_version, "definition_content_hash": item.definition_content_hash, "source_id": item.source_id, "source_manifest_hash": item.source_manifest_hash, "available_at": item.available_at.isoformat()}
            for item in frozen_metric_observations
        ),
        "source_ids": source_ids,
    }


def validate_compiled_mechanism_integrity(value: CompiledMechanisms) -> None:
    """Verify that a compiled mechanism still matches its frozen dependency hash.

    ``CompiledMechanisms`` is a frozen dataclass, but ``dataclasses.replace``
    is intentionally available for ordinary version construction.  Every
    public consumer must therefore re-establish this integrity boundary before
    trusting a supplied compiled object.
    """
    if type(value) is not CompiledMechanisms:
        raise ValidationError("compiled mechanisms are required")
    cutoff = _utc(value.cutoff, "compiled mechanism cutoff")
    if not isinstance(value.mechanisms, tuple) or not value.mechanisms:
        raise ValidationError("compiled mechanisms must be a non-empty tuple")
    if not all(type(mechanism) is MechanismPack for mechanism in value.mechanisms):
        raise ValidationError("compiled mechanisms must contain MechanismPack values")
    for mechanism in value.mechanisms:
        validate_formal_mechanism(mechanism)
    if len({mechanism.key for mechanism in value.mechanisms}) != len(value.mechanisms):
        raise ValidationError("compiled mechanism keys must be unique")
    if len({mechanism.scope_object_id for mechanism in value.mechanisms}) != 1:
        raise ValidationError("compiled mechanisms must share one scope_object_id")
    ordered_mechanisms = tuple(
        sorted(value.mechanisms, key=lambda mechanism: (mechanism.key, mechanism.version))
    )
    if value.mechanisms != ordered_mechanisms:
        raise ValidationError("compiled mechanisms must be canonically ordered")

    expected_metric_keys = {
        key
        for mechanism in ordered_mechanisms
        for key in (
            *mechanism.driver_keys,
            *(mapping.target_metric_key for mapping in mechanism.financial_mappings),
            *(falsifier.metric_key for falsifier in mechanism.falsifiers),
        )
    }
    if not isinstance(value.metric_definition_bindings, tuple):
        raise ValidationError("metric definition bindings must be a tuple")
    if not all(
        isinstance(binding, tuple)
        and len(binding) == 2
        and isinstance(binding[0], str)
        and binding[0].strip()
        and isinstance(binding[1], str)
        and binding[1].strip()
        for binding in value.metric_definition_bindings
    ):
        raise ValidationError("metric definition bindings are invalid")
    bindings = value.metric_definition_bindings
    if tuple(sorted(bindings)) != bindings or {key for key, _ in bindings} != expected_metric_keys:
        raise ValidationError("metric definition bindings do not match compiled mechanisms")
    if len({key for key, _ in bindings}) != len(bindings):
        raise ValidationError("metric definition bindings must be unique")
    expected_metric_ids = tuple(sorted(definition_id for _, definition_id in bindings))
    if value.metric_definition_ids != expected_metric_ids:
        raise ValidationError("metric definition IDs do not match compiled bindings")

    if not isinstance(value.metric_definition_provenance, tuple) or not all(
        type(dependency) is MetricDefinitionDependency
        for dependency in value.metric_definition_provenance
    ):
        raise ValidationError("metric definition provenance is invalid")
    provenance_bindings = tuple(
        (dependency.metric_key, str(dependency.definition_id))
        for dependency in value.metric_definition_provenance
    )
    if provenance_bindings != bindings:
        raise ValidationError("metric definition provenance does not match compiled bindings")
    for dependency in value.metric_definition_provenance:
        if dependency.available_at > cutoff:
            raise ValidationError("metric definition is unavailable at compiled cutoff")
        if dependency.source_manifest_hash != value.source_manifest_hash:
            raise ValidationError("metric definition provenance has the wrong source manifest")
    if not isinstance(value.frozen_metric_definition_provenance, tuple) or not all(
        type(dependency) is MetricDefinitionDependency
        for dependency in value.frozen_metric_definition_provenance
    ):
        raise ValidationError("frozen metric definition provenance is invalid")
    frozen_keys = tuple(item.metric_key for item in value.frozen_metric_definition_provenance)
    if frozen_keys != tuple(sorted(frozen_keys)) or len(frozen_keys) != len(set(frozen_keys)):
        raise ValidationError("frozen metric definition provenance must be canonically ordered")
    for dependency in value.frozen_metric_definition_provenance:
        if dependency.available_at > cutoff or dependency.source_manifest_hash != value.source_manifest_hash:
            raise ValidationError("frozen metric definition provenance is unavailable or foreign")

    expected_source_ids = tuple(
        sorted({source_id for mechanism in ordered_mechanisms for source_id in mechanism.source_ids})
    )
    if value.source_ids != expected_source_ids:
        raise ValidationError("compiled source IDs do not match mechanisms")
    if not isinstance(value.source_manifest_hash, str) or _SHA256.fullmatch(value.source_manifest_hash) is None:
        raise ValidationError("compiled source manifest hash must be a lowercase SHA-256")
    if not isinstance(value.content_hash, str) or _SHA256.fullmatch(value.content_hash) is None:
        raise ValidationError("compiled content hash must be a lowercase SHA-256")
    expected_hash = canonical_hash(
        _compiled_payload(
            mechanisms=ordered_mechanisms,
            cutoff=cutoff,
            source_manifest_hash=value.source_manifest_hash,
            metric_definition_bindings=bindings,
            metric_definition_provenance=value.metric_definition_provenance,
            frozen_metric_definition_provenance=value.frozen_metric_definition_provenance,
            frozen_metric_observations=value.frozen_metric_observations,
            source_ids=value.source_ids,
        )
    )
    if value.content_hash != expected_hash:
        raise ValidationError("compiled mechanism content hash does not match dependencies")


def compile_mechanisms(
    mechanisms: tuple[MechanismPack, ...],
    *,
    dependencies: MechanismDependencyContext,
) -> CompiledMechanisms:
    """Compile source-available formal packs into deterministic dependencies.

    The caller supplies one already-frozen, cutoff-bound dependency context.
    This pure compiler verifies that every causal input, output and falsifier
    metric, and every source reference, is among those cutoff-safe bindings.
    """
    if not isinstance(mechanisms, tuple) or not mechanisms:
        raise ValidationError("mechanisms must be a non-empty tuple")
    if type(dependencies) is not MechanismDependencyContext:
        raise ValidationError("dependencies must be a MechanismDependencyContext")
    definitions = dependencies.metric_definition_map
    definition_records = dependencies.metric_definition_by_key
    available_sources = set(dependencies.source_ids)
    mechanism_keys: set[str] = set()
    scope_object_ids: set[UUID] = set()
    dependency_metrics: set[str] = set()
    dependency_sources: set[str] = set()

    for value in mechanisms:
        if type(value) is not MechanismPack:
            raise ValidationError("mechanisms must contain MechanismPack values")
        validate_formal_mechanism(value)
        if value.key in mechanism_keys:
            raise ValidationError("mechanism keys must be unique")
        mechanism_keys.add(value.key)
        scope_object_ids.add(value.scope_object_id)
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

    if len(scope_object_ids) != 1:
        raise ValidationError("compiled mechanisms must share one scope_object_id")

    all_mappings = tuple(
        mapping for value in mechanisms for mapping in value.financial_mappings
    )
    if not _causal_chain_is_acyclic(all_mappings):
        raise ValidationError("formal mechanism causal chain must be acyclic")
    ordered_bindings = tuple(sorted((key, definitions[key]) for key in dependency_metrics))
    def resolve_observation(dependency: MetricDefinitionDependency) -> FrozenResolvedObservation:
        matches = tuple(
            observation for observation in dependencies.metric_observations
            if observation.definition_key == dependency.metric_key
            and observation.definition_version == dependency.definition_version
        )
        if len(matches) != 1:
            raise ValidationError("metric definition must resolve exactly one frozen observation")
        observation = matches[0]
        return FrozenResolvedObservation(
            observation.observation_id, observation.definition_key, dependency.definition_id,
            dependency.definition_version, dependency.content_hash, observation.source_id,
            dependency.source_manifest_hash, observation.available_at,
        )
    resolved_observations = tuple(resolve_observation(item) for item in sorted(dependencies.metric_definitions, key=lambda item: item.metric_key))
    ordered_provenance = tuple(
        definition_records[key] for key, _ in ordered_bindings
    )
    ordered_metrics = tuple(sorted(definition_id for _, definition_id in ordered_bindings))
    ordered_sources = tuple(sorted(dependency_sources))
    ordered_mechanisms = tuple(sorted(mechanisms, key=lambda value: (value.key, value.version)))
    return CompiledMechanisms(
        mechanisms=ordered_mechanisms,
        cutoff=dependencies.cutoff,
        content_hash=canonical_hash(
            _compiled_payload(
                mechanisms=ordered_mechanisms,
                cutoff=dependencies.cutoff,
                source_manifest_hash=dependencies.source_manifest.manifest_hash,
                metric_definition_bindings=ordered_bindings,
                metric_definition_provenance=ordered_provenance,
                frozen_metric_definition_provenance=tuple(sorted(dependencies.metric_definitions, key=lambda item: item.metric_key)),
                frozen_metric_observations=resolved_observations,
                source_ids=ordered_sources,
            )
        ),
        metric_definition_ids=ordered_metrics,
        metric_definition_bindings=ordered_bindings,
        metric_definition_provenance=ordered_provenance,
        frozen_metric_definition_provenance=tuple(sorted(dependencies.metric_definitions, key=lambda item: item.metric_key)),
        frozen_metric_observations=resolved_observations,
        source_manifest_hash=dependencies.source_manifest.manifest_hash,
        source_ids=ordered_sources,
    )
