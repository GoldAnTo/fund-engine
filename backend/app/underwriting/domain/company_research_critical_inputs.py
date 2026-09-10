"""Deterministic critical-input contracts and their explicit dependency graph.

The graph is emitted while the model is constructed.  Reconstructing exact
dependencies after the fact from persisted artifacts with pooled provenance is
forbidden because it would manufacture edges that the artifacts do not retain.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from app.underwriting.domain.company_research import (
    CompanyResearchValidationError,
    SourceLineageReference,
    canonical_decimal_string,
)

_KEY = re.compile(r"[a-z][a-z0-9_.:-]*\Z")
_ASSUMPTION_KEY = re.compile(
    r"[a-z][a-z0-9_.-]*\.v[1-9][0-9]*:[a-z][a-z0-9_]*"
    r"(?::[a-z][a-z0-9_]*)*\Z"
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class CriticalInputKind(StrEnum):
    SOURCE_FACT = "source_fact"
    MANAGEMENT_GUIDANCE = "management_guidance"
    CONSENSUS = "consensus"
    AI_ASSUMPTION = "ai_assumption"
    USER_ASSUMPTION = "user_assumption"
    DERIVED_CALCULATION = "derived_calculation"
    UNKNOWN = "unknown"


class CriticalInputDecision(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REPLACED_WITH_USER_ASSUMPTION = "replaced_with_user_assumption"
    MARKED_UNKNOWN = "marked_unknown"
    ACCEPTED_GAP = "accepted_gap"


class CriticalDependencySurface(StrEnum):
    REVENUE = "revenue"
    OPERATING_PROFIT = "operating_profit"
    FCFF = "fcff"
    CAPITAL_STRUCTURE = "capital_structure"
    DISCOUNT_TERMINAL = "discount_terminal"
    SCENARIO = "scenario"
    SECURITY_VALUE = "security_value"
    SECURITY_RETURN = "security_return"
    ANSWERABILITY = "answerability"
    DIRECTION = "direction"
    STRONGEST_COUNTEREVIDENCE = "strongest_counterevidence"


CRITICAL_DEPENDENCY_SURFACE_ORDER = tuple(CriticalDependencySurface)
_SURFACE_POSITION = {
    surface: position
    for position, surface in enumerate(CRITICAL_DEPENDENCY_SURFACE_ORDER)
}


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CompanyResearchValidationError(
            f"critical input {field_name} must be canonical non-empty text"
        )
    return value


def _optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _text(value, field_name)


def _key(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if _KEY.fullmatch(text) is None:
        raise CompanyResearchValidationError(
            f"critical input {field_name} must be a stable key"
        )
    return text


def _value(value: object) -> Decimal | str | None:
    if value is None or type(value) is str:
        if isinstance(value, str):
            _text(value, "value")
        return value
    if type(value) is Decimal and value.is_finite():
        return value
    raise CompanyResearchValidationError(
        "critical input value must be a finite Decimal, canonical text, or null"
    )


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise CompanyResearchValidationError(
            f"critical input {field_name} must be timezone-aware"
        )
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class CriticalInputImpact:
    """Exact terminal surfaces and dependency paths reached by one input."""

    surfaces: tuple[CriticalDependencySurface, ...]
    dependency_paths: tuple[tuple[str, ...], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.surfaces, tuple)
            or not self.surfaces
            or not all(type(item) is CriticalDependencySurface for item in self.surfaces)
            or len(set(self.surfaces)) != len(self.surfaces)
            or self.surfaces
            != tuple(sorted(self.surfaces, key=_SURFACE_POSITION.__getitem__))
        ):
            raise CompanyResearchValidationError(
                "critical input impact surfaces must be unique and canonical"
            )
        if (
            not isinstance(self.dependency_paths, tuple)
            or not self.dependency_paths
            or not all(
                isinstance(path, tuple)
                and bool(path)
                and all(isinstance(item, str) and item for item in path)
                for path in self.dependency_paths
            )
            or len(set(self.dependency_paths)) != len(self.dependency_paths)
            or self.dependency_paths != tuple(sorted(self.dependency_paths))
        ):
            raise CompanyResearchValidationError(
                "critical input dependency paths must be unique and canonical"
            )
        reached: set[CriticalDependencySurface] = set()
        for path in self.dependency_paths:
            marker = path[-1]
            if not marker.startswith("surface:"):
                raise CompanyResearchValidationError(
                    "critical input dependency path must terminate at a surface"
                )
            try:
                reached.add(CriticalDependencySurface(marker.removeprefix("surface:")))
            except ValueError as exc:
                raise CompanyResearchValidationError(
                    "critical input dependency path has an invalid surface"
                ) from exc
        if reached != set(self.surfaces):
            raise CompanyResearchValidationError(
                "critical input dependency paths must exactly cover impact surfaces"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class CriticalInputCandidate:
    """One typed semantic input before graph reachability adds its impact."""

    key: str
    kind: CriticalInputKind
    value: Decimal | str | None
    period: str | None = None
    unit: str | None = None
    currency: str | None = None
    source_ref: SourceLineageReference | None = None
    provider: str | None = None
    available_at: datetime | None = None
    coverage: str | None = None
    rationale: str | None = None
    assumption_key: str | None = None
    equation_id: str | None = None
    parent_input_keys: tuple[str, ...] = ()
    unknown_reason: str | None = None
    gap_key: str | None = None

    def __post_init__(self) -> None:
        if self.available_at is not None:
            object.__setattr__(
                self,
                "available_at",
                _utc(self.available_at, "available_at"),
            )
        _validate_semantics(self)


@dataclass(frozen=True, slots=True, kw_only=True)
class CriticalInput(CriticalInputCandidate):
    """One selected immutable input plus its review decision."""

    impact: CriticalInputImpact
    decision: CriticalInputDecision = CriticalInputDecision.PENDING
    replacement: CriticalInputCandidate | None = None
    input_fingerprint: str = field(default="")

    def __post_init__(self) -> None:
        super(CriticalInput, self).__post_init__()
        if type(self.impact) is not CriticalInputImpact:
            raise CompanyResearchValidationError(
                "critical input impact must be typed"
            )
        if type(self.decision) is not CriticalInputDecision:
            raise CompanyResearchValidationError(
                "critical input decision must be controlled"
            )
        if self.decision is CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION:
            replacement = self.replacement
            if (
                type(replacement) is not CriticalInputCandidate
                or replacement.kind is not CriticalInputKind.USER_ASSUMPTION
                or replacement.key != self.key
            ):
                raise CompanyResearchValidationError(
                    "critical input replacement requires a same-key user-assumption payload"
                )
        elif self.replacement is not None:
            raise CompanyResearchValidationError(
                "critical input replacement is invalid for its decision"
            )
        if (
            self.kind is CriticalInputKind.DERIVED_CALCULATION
            and self.decision is CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION
        ):
            raise CompanyResearchValidationError(
                "derived critical input is not directly user-replaceable"
            )
        calculated = critical_input_fingerprint(self)
        if not isinstance(self.input_fingerprint, str):
            raise CompanyResearchValidationError(
                "critical input fingerprint must be a string"
            )
        if self.input_fingerprint and (
            _HASH.fullmatch(self.input_fingerprint) is None
            or self.input_fingerprint != calculated
        ):
            raise CompanyResearchValidationError(
                "critical input fingerprint does not match semantic fields"
            )
        object.__setattr__(self, "input_fingerprint", calculated)


def _validate_semantics(value: CriticalInputCandidate) -> None:
    _key(value.key, "key")
    if type(value.kind) is not CriticalInputKind:
        raise CompanyResearchValidationError("critical input kind must be controlled")
    _value(value.value)
    for field_name in ("period", "unit", "currency"):
        _optional_text(getattr(value, field_name), field_name)
    if value.source_ref is not None and type(value.source_ref) is not SourceLineageReference:
        raise CompanyResearchValidationError(
            "critical input source reference must be exact source lineage"
        )
    for field_name in (
        "provider",
        "coverage",
        "rationale",
        "assumption_key",
        "equation_id",
        "unknown_reason",
        "gap_key",
    ):
        _optional_text(getattr(value, field_name), field_name)
    if value.available_at is not None:
        _utc(value.available_at, "available_at")
    if (
        not isinstance(value.parent_input_keys, tuple)
        or not all(isinstance(item, str) for item in value.parent_input_keys)
    ):
        raise CompanyResearchValidationError(
            "critical input parent input keys must be a tuple"
        )
    for item in value.parent_input_keys:
        _key(item, "parent input key")
    if (
        len(set(value.parent_input_keys)) != len(value.parent_input_keys)
        or value.parent_input_keys != tuple(sorted(value.parent_input_keys))
    ):
        raise CompanyResearchValidationError(
            "critical input parent input keys must be unique and canonical"
        )

    source_kinds = {
        CriticalInputKind.SOURCE_FACT,
        CriticalInputKind.MANAGEMENT_GUIDANCE,
    }
    assumption_fields = (
        value.assumption_key,
        value.rationale,
    )
    equation_fields = (value.equation_id, value.parent_input_keys)
    consensus_fields = (value.provider, value.available_at, value.coverage)
    gap_fields = (value.unknown_reason, value.gap_key)
    if value.kind in source_kinds:
        if type(value.source_ref) is not SourceLineageReference:
            raise CompanyResearchValidationError(
                "sourced critical input requires an exact source reference"
            )
        if any(item is not None for item in assumption_fields):
            raise CompanyResearchValidationError(
                "sourced critical input cannot carry assumption provenance"
            )
        if any(equation_fields) or any(item is not None for item in consensus_fields + gap_fields):
            raise CompanyResearchValidationError(
                "sourced critical input contains incompatible provenance"
            )
    elif value.kind is CriticalInputKind.CONSENSUS:
        if (
            type(value.source_ref) is not SourceLineageReference
            or value.provider is None
            or value.available_at is None
            or value.coverage is None
        ):
            raise CompanyResearchValidationError(
                "consensus critical input requires provider, availability, coverage, and source"
            )
        if any(item is not None for item in assumption_fields + gap_fields) or any(equation_fields):
            raise CompanyResearchValidationError(
                "consensus critical input contains incompatible provenance"
            )
    elif value.kind in {
        CriticalInputKind.AI_ASSUMPTION,
        CriticalInputKind.USER_ASSUMPTION,
    }:
        if value.source_ref is not None:
            raise CompanyResearchValidationError(
                "assumption critical input cannot carry source provenance"
            )
        if (
            value.assumption_key is None
            or _ASSUMPTION_KEY.fullmatch(value.assumption_key) is None
        ):
            raise CompanyResearchValidationError(
                "assumption critical input requires a versioned assumption key"
            )
        if value.rationale is None:
            raise CompanyResearchValidationError(
                "assumption critical input requires explicit rationale"
            )
        if any(equation_fields) or any(item is not None for item in consensus_fields + gap_fields):
            raise CompanyResearchValidationError(
                "assumption critical input contains incompatible provenance"
            )
    elif value.kind is CriticalInputKind.DERIVED_CALCULATION:
        if value.source_ref is not None or any(
            item is not None for item in assumption_fields + consensus_fields + gap_fields
        ):
            raise CompanyResearchValidationError(
                "derived critical input contains incompatible provenance"
            )
        if value.equation_id is None:
            raise CompanyResearchValidationError(
                "derived critical input requires an equation identifier"
            )
        if not value.parent_input_keys:
            raise CompanyResearchValidationError(
                "derived critical input requires complete parent input keys"
            )
    else:
        if value.value is not None:
            raise CompanyResearchValidationError(
                "unknown critical input cannot carry a value"
            )
        if value.source_ref is not None or any(
            item is not None
            for item in assumption_fields + consensus_fields + (value.equation_id,)
        ) or value.parent_input_keys:
            raise CompanyResearchValidationError(
                "unknown critical input contains incompatible provenance"
            )
        if value.unknown_reason is None:
            raise CompanyResearchValidationError(
                "unknown critical input requires a non-empty reason"
            )
        if value.gap_key is None:
            raise CompanyResearchValidationError(
                "unknown critical input requires a non-empty gap key"
            )

    if value.kind is not CriticalInputKind.UNKNOWN and value.value is None:
        raise CompanyResearchValidationError(
            "non-unknown critical input requires an explicit value"
        )


def _canonical_value(value: Decimal | str | None) -> dict[str, str | None]:
    if type(value) is Decimal:
        return {"type": "decimal", "value": canonical_decimal_string(value)}
    if type(value) is str:
        return {"type": "text", "value": value}
    return {"type": "none", "value": None}


def critical_input_fingerprint(value: CriticalInput) -> str:
    """Hash semantic provenance and dependency paths, never review state."""

    source = value.source_ref.canonical_payload() if value.source_ref else None
    payload = {
        "schema_version": "company-research-critical-input-fingerprint.v1",
        "kind": value.kind.value,
        "value": _canonical_value(value.value),
        "period": value.period,
        "unit": value.unit,
        "currency": value.currency,
        "source": source,
        "consensus": (
            {
                "provider": value.provider,
                "available_at": value.available_at.isoformat()
                if value.available_at is not None
                else None,
                "coverage": value.coverage,
            }
            if value.kind is CriticalInputKind.CONSENSUS
            else None
        ),
        "assumption_key": value.assumption_key,
        "equation_id": value.equation_id,
        "parent_input_keys": list(value.parent_input_keys),
        "gap_key": value.gap_key,
        "dependency_paths": [list(path) for path in value.impact.dependency_paths],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def critical_input_order_key(value: CriticalInput) -> tuple[int, str]:
    return min(_SURFACE_POSITION[item] for item in value.impact.surfaces), value.key


@dataclass(frozen=True, slots=True)
class CriticalInputSet:
    """One canonical, immutable review set suitable for artifact persistence."""

    inputs: tuple[CriticalInput, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.inputs, tuple) or not all(
            type(item) is CriticalInput for item in self.inputs
        ):
            raise CompanyResearchValidationError(
                "critical input set must contain typed inputs"
            )
        if any(
            item.kind is CriticalInputKind.DERIVED_CALCULATION
            for item in self.inputs
        ):
            raise CompanyResearchValidationError(
                "critical input set cannot contain an actionable calculation"
            )
        keys = tuple(item.key for item in self.inputs)
        if len(set(keys)) != len(keys):
            raise CompanyResearchValidationError(
                "critical input set requires unique keys"
            )
        if self.inputs != tuple(sorted(self.inputs, key=critical_input_order_key)):
            raise CompanyResearchValidationError(
                "critical input set must use canonical order"
            )


def _candidate_identity(value: CriticalInput) -> CriticalInputCandidate:
    return CriticalInputCandidate(
        key=value.key,
        kind=value.kind,
        value=value.value,
        period=value.period,
        unit=value.unit,
        currency=value.currency,
        source_ref=value.source_ref,
        provider=value.provider,
        available_at=value.available_at,
        coverage=value.coverage,
        rationale=value.rationale,
        assumption_key=value.assumption_key,
        equation_id=value.equation_id,
        parent_input_keys=value.parent_input_keys,
        unknown_reason=value.unknown_reason,
        gap_key=value.gap_key,
    )


def validate_initial_critical_input_set(value: CriticalInputSet) -> None:
    if type(value) is not CriticalInputSet or any(
        item.decision is not CriticalInputDecision.PENDING
        or item.replacement is not None
        for item in value.inputs
    ):
        raise CompanyResearchValidationError(
            "initial critical inputs must have pending decisions"
        )


def critical_input_successor_change(
    parent: CriticalInputSet,
    successor: CriticalInputSet,
) -> CriticalInput:
    """Return the one authenticated decision change or fail closed."""

    if type(parent) is not CriticalInputSet or type(successor) is not CriticalInputSet:
        raise CompanyResearchValidationError(
            "critical input successor sets must be typed"
        )
    if tuple(item.key for item in parent.inputs) != tuple(
        item.key for item in successor.inputs
    ):
        raise CompanyResearchValidationError(
            "critical input successor must preserve original items"
        )
    changes: list[tuple[CriticalInput, CriticalInput]] = []
    for previous, candidate in zip(parent.inputs, successor.inputs, strict=True):
        if previous == candidate:
            continue
        if (
            _candidate_identity(previous) != _candidate_identity(candidate)
            or previous.impact != candidate.impact
            or previous.input_fingerprint != candidate.input_fingerprint
        ):
            raise CompanyResearchValidationError(
                "critical input successor must preserve original items and fingerprints"
            )
        changes.append((previous, candidate))
    if len(changes) != 1:
        raise CompanyResearchValidationError(
            "critical input successor must change exactly one decision"
        )
    previous, changed = changes[0]
    if (
        previous.decision is not CriticalInputDecision.PENDING
        or previous.replacement is not None
        or changed.decision is CriticalInputDecision.PENDING
    ):
        raise CompanyResearchValidationError(
            "critical input successor must resolve one pending decision"
        )
    return changed


def carry_forward_critical_input_decisions(
    previous: CriticalInputSet,
    rebuilt: CriticalInputSet,
) -> CriticalInputSet:
    """Carry review state only across identical semantic fingerprints."""

    if type(previous) is not CriticalInputSet or type(rebuilt) is not CriticalInputSet:
        raise CompanyResearchValidationError(
            "critical input rebuild sets must be typed"
        )
    previous_by_identity = {
        (item.key, item.input_fingerprint): item for item in previous.inputs
    }
    carried: list[CriticalInput] = []
    for candidate in rebuilt.inputs:
        prior = previous_by_identity.get(
            (candidate.key, candidate.input_fingerprint)
        )
        if prior is None or prior.decision is CriticalInputDecision.PENDING:
            carried.append(candidate)
            continue
        replacement = prior.replacement
        if replacement is not None and replacement.key != candidate.key:
            replacement = replace(replacement, key=candidate.key)
        carried.append(
            replace(
                candidate,
                decision=prior.decision,
                replacement=replacement,
            )
        )
    return CriticalInputSet(inputs=tuple(carried))


@dataclass(frozen=True, slots=True)
class CriticalInputNode:
    candidate: CriticalInputCandidate

    def __post_init__(self) -> None:
        if type(self.candidate) is not CriticalInputCandidate or self.candidate.kind in {
            CriticalInputKind.DERIVED_CALCULATION,
            CriticalInputKind.UNKNOWN,
        }:
            raise CompanyResearchValidationError(
                "critical dependency input node must contain a confirmable input"
            )

    @property
    def key(self) -> str:
        return self.candidate.key


@dataclass(frozen=True, slots=True)
class CriticalCalculationNode:
    candidate: CriticalInputCandidate

    def __post_init__(self) -> None:
        if (
            type(self.candidate) is not CriticalInputCandidate
            or self.candidate.kind is not CriticalInputKind.DERIVED_CALCULATION
        ):
            raise CompanyResearchValidationError(
                "critical calculation node must contain a derived calculation"
            )

    @property
    def key(self) -> str:
        return self.candidate.key


@dataclass(frozen=True, slots=True)
class CriticalUnknownNode:
    candidate: CriticalInputCandidate

    def __post_init__(self) -> None:
        if (
            type(self.candidate) is not CriticalInputCandidate
            or self.candidate.kind is not CriticalInputKind.UNKNOWN
        ):
            raise CompanyResearchValidationError(
                "critical unknown node must contain an unknown input"
            )

    @property
    def key(self) -> str:
        return self.candidate.key


@dataclass(frozen=True, slots=True)
class CriticalSurfaceNode:
    surface: CriticalDependencySurface

    def __post_init__(self) -> None:
        if type(self.surface) is not CriticalDependencySurface:
            raise CompanyResearchValidationError(
                "critical dependency surface node must be controlled"
            )

    @property
    def key(self) -> str:
        return f"surface:{self.surface.value}"


@dataclass(frozen=True, slots=True)
class CriticalDependencyEdge:
    parent_key: str
    consumer_key: str

    def __post_init__(self) -> None:
        _key(self.parent_key, "dependency edge parent key")
        _key(self.consumer_key, "dependency edge consumer key")
        if self.parent_key == self.consumer_key:
            raise CompanyResearchValidationError(
                "critical dependency edge cannot be a self edge"
            )


@dataclass(frozen=True, slots=True)
class CriticalDependencyGraph:
    """A complete DAG emitted at model-construction time."""

    input_nodes: tuple[CriticalInputNode, ...]
    calculation_nodes: tuple[CriticalCalculationNode, ...]
    unknown_nodes: tuple[CriticalUnknownNode, ...]
    surface_nodes: tuple[CriticalSurfaceNode, ...]
    edges: tuple[CriticalDependencyEdge, ...]

    def __post_init__(self) -> None:
        typed_collections = (
            (self.input_nodes, CriticalInputNode, "input"),
            (self.calculation_nodes, CriticalCalculationNode, "calculation"),
            (self.unknown_nodes, CriticalUnknownNode, "unknown"),
        )
        for values, expected_type, name in typed_collections:
            if not isinstance(values, tuple) or not all(
                type(item) is expected_type for item in values
            ):
                raise CompanyResearchValidationError(
                    f"critical dependency {name} nodes must be a typed tuple"
                )
            if values != tuple(sorted(values, key=lambda item: item.key)):
                raise CompanyResearchValidationError(
                    f"critical dependency {name} nodes must be canonical"
                )
        if not isinstance(self.surface_nodes, tuple) or not all(
            type(item) is CriticalSurfaceNode for item in self.surface_nodes
        ):
            raise CompanyResearchValidationError(
                "critical dependency surface nodes must be a typed tuple"
            )
        if tuple(node.surface for node in self.surface_nodes) != (
            CRITICAL_DEPENDENCY_SURFACE_ORDER
        ):
            raise CompanyResearchValidationError(
                "critical dependency graph must have exact surface coverage"
            )

        all_nodes = (
            *self.input_nodes,
            *self.calculation_nodes,
            *self.unknown_nodes,
            *self.surface_nodes,
        )
        keys = tuple(node.key for node in all_nodes)
        if len(set(keys)) != len(keys):
            raise CompanyResearchValidationError(
                "critical dependency graph contains a key collision"
            )
        if not isinstance(self.edges, tuple) or not all(
            type(edge) is CriticalDependencyEdge for edge in self.edges
        ):
            raise CompanyResearchValidationError(
                "critical dependency edges must be a typed tuple"
            )
        edge_keys = tuple((edge.parent_key, edge.consumer_key) for edge in self.edges)
        if len(set(edge_keys)) != len(edge_keys) or edge_keys != tuple(
            sorted(edge_keys)
        ):
            raise CompanyResearchValidationError(
                "critical dependency edges must be unique and canonical"
            )
        known = set(keys)
        if any(
            edge.parent_key not in known or edge.consumer_key not in known
            for edge in self.edges
        ):
            raise CompanyResearchValidationError(
                "critical dependency edge endpoint is missing"
            )
        surface_keys = {node.key for node in self.surface_nodes}
        if any(edge.parent_key in surface_keys for edge in self.edges):
            raise CompanyResearchValidationError(
                "critical dependency surfaces must be terminal"
            )

        parents_by_consumer: dict[str, set[str]] = {}
        consumers_by_parent: dict[str, set[str]] = {key: set() for key in keys}
        for edge in self.edges:
            parents_by_consumer.setdefault(edge.consumer_key, set()).add(
                edge.parent_key
            )
            consumers_by_parent[edge.parent_key].add(edge.consumer_key)
        for node in self.calculation_nodes:
            if parents_by_consumer.get(node.key, set()) != set(
                node.candidate.parent_input_keys
            ):
                raise CompanyResearchValidationError(
                    "critical calculation requires complete parents"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(key: str) -> None:
            if key in visiting:
                raise CompanyResearchValidationError(
                    "critical dependency graph contains a cycle"
                )
            if key in visited:
                return
            visiting.add(key)
            for consumer in consumers_by_parent[key]:
                visit(consumer)
            visiting.remove(key)
            visited.add(key)

        for key in keys:
            visit(key)


def _selected_input(
    candidate: CriticalInputCandidate,
    impact: CriticalInputImpact,
) -> CriticalInput:
    return CriticalInput(
        key=candidate.key,
        kind=candidate.kind,
        value=candidate.value,
        period=candidate.period,
        unit=candidate.unit,
        currency=candidate.currency,
        source_ref=candidate.source_ref,
        provider=candidate.provider,
        available_at=candidate.available_at,
        coverage=candidate.coverage,
        rationale=candidate.rationale,
        assumption_key=candidate.assumption_key,
        equation_id=candidate.equation_id,
        parent_input_keys=candidate.parent_input_keys,
        unknown_reason=candidate.unknown_reason,
        gap_key=candidate.gap_key,
        impact=impact,
    )


def select_critical_inputs(graph: CriticalDependencyGraph) -> CriticalInputSet:
    """Reverse-traverse exact graph edges from the fixed terminal surfaces."""

    if type(graph) is not CriticalDependencyGraph:
        raise CompanyResearchValidationError(
            "critical input selection requires an explicit dependency graph"
        )
    candidates = {
        node.key: node.candidate
        for node in (
            *graph.input_nodes,
            *graph.unknown_nodes,
        )
    }
    surface_by_key = {node.key: node.surface for node in graph.surface_nodes}
    consumers_by_parent: dict[str, tuple[str, ...]] = {}
    for edge in graph.edges:
        consumers_by_parent[edge.parent_key] = (
            *consumers_by_parent.get(edge.parent_key, ()),
            edge.consumer_key,
        )

    path_cache: dict[str, tuple[tuple[str, ...], ...]] = {}

    def paths_from(key: str) -> tuple[tuple[str, ...], ...]:
        cached = path_cache.get(key)
        if cached is not None:
            return cached
        paths: list[tuple[str, ...]] = []
        for consumer in consumers_by_parent.get(key, ()):
            if consumer in surface_by_key:
                paths.append((consumer,))
            else:
                paths.extend((consumer, *tail) for tail in paths_from(consumer))
        result = tuple(sorted(set(paths)))
        path_cache[key] = result
        return result

    selected: list[CriticalInput] = []
    for key, candidate in candidates.items():
        paths = paths_from(key)
        if not paths:
            continue
        reached = {
            surface_by_key[path[-1]]
            for path in paths
        }
        impact = CriticalInputImpact(
            surfaces=tuple(sorted(reached, key=_SURFACE_POSITION.__getitem__)),
            dependency_paths=paths,
        )
        selected.append(_selected_input(candidate, impact))

    return CriticalInputSet(
        inputs=tuple(sorted(selected, key=critical_input_order_key))
    )
