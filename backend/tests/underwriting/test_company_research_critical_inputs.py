from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from app.underwriting.domain.company_research import (
    CompanyResearchValidationError,
    SourceLineageReference,
)
from app.underwriting.domain.company_research_critical_inputs import (
    CRITICAL_DEPENDENCY_SURFACE_ORDER,
    CriticalCalculationNode,
    CriticalDependencyEdge,
    CriticalDependencyGraph,
    CriticalDependencySurface,
    CriticalInput,
    CriticalInputCandidate,
    CriticalInputDecision,
    CriticalInputImpact,
    CriticalInputKind,
    CriticalInputNode,
    CriticalInputSet,
    CriticalSurfaceNode,
    CriticalUnknownNode,
    carry_forward_critical_input_decisions,
    select_critical_inputs,
)

SOURCE = SourceLineageReference(
    fact_key="reported_revenue",
    source_role="filing",
    source_url="https://example.test/filing",
    source_locator="page:12",
    raw_hash="a" * 64,
)
IMPACT = CriticalInputImpact(
    surfaces=(CriticalDependencySurface.REVENUE,),
    dependency_paths=(("revenue_bridge", "surface:revenue"),),
)


def _input(**changes: object) -> CriticalInput:
    values: dict[str, object] = {
        "key": "reported_revenue",
        "kind": CriticalInputKind.SOURCE_FACT,
        "value": Decimal(100),
        "period": "FY2025",
        "unit": "million",
        "currency": "CNY",
        "source_ref": SOURCE,
        "impact": IMPACT,
    }
    values.update(changes)
    return CriticalInput(**values)


def test_critical_input_vocabularies_are_exact_and_closed() -> None:
    assert tuple(item.value for item in CriticalInputKind) == (
        "source_fact",
        "management_guidance",
        "consensus",
        "ai_assumption",
        "user_assumption",
        "derived_calculation",
        "unknown",
    )
    assert tuple(item.value for item in CriticalInputDecision) == (
        "pending",
        "confirmed",
        "replaced_with_user_assumption",
        "marked_unknown",
        "accepted_gap",
    )
    assert CRITICAL_DEPENDENCY_SURFACE_ORDER == tuple(CriticalDependencySurface)


@pytest.mark.parametrize(
    "kind",
    (CriticalInputKind.SOURCE_FACT, CriticalInputKind.MANAGEMENT_GUIDANCE),
)
def test_sourced_inputs_require_exact_source_provenance(kind: CriticalInputKind) -> None:
    assert _input(kind=kind).source_ref == SOURCE

    with pytest.raises(CompanyResearchValidationError, match="source reference"):
        _input(kind=kind, source_ref=None)
    with pytest.raises(CompanyResearchValidationError, match="assumption provenance"):
        _input(
            kind=kind,
            assumption_key="reported_revenue.v1:override",
            rationale="manual override",
        )


def test_consensus_requires_provider_availability_coverage_and_source() -> None:
    consensus = _input(
        kind=CriticalInputKind.CONSENSUS,
        provider="Example Consensus",
        available_at=datetime(2026, 8, 31, tzinfo=UTC),
        coverage="12 analysts",
    )
    assert consensus.provider == "Example Consensus"

    for field in ("provider", "available_at", "coverage", "source_ref"):
        values = {
            "provider": "Example Consensus",
            "available_at": datetime(2026, 8, 31, tzinfo=UTC),
            "coverage": "12 analysts",
            "source_ref": SOURCE,
        }
        values[field] = None
        with pytest.raises(CompanyResearchValidationError, match="consensus"):
            _input(kind=CriticalInputKind.CONSENSUS, **values)


def test_consensus_availability_normalizes_equivalent_instants_to_utc() -> None:
    utc = _input(
        kind=CriticalInputKind.CONSENSUS,
        provider="Example Consensus",
        available_at=datetime(2026, 8, 31, tzinfo=UTC),
        coverage="12 analysts",
    )
    offset = _input(
        kind=CriticalInputKind.CONSENSUS,
        provider="Example Consensus",
        available_at=datetime(
            2026,
            8,
            31,
            8,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        coverage="12 analysts",
    )

    assert utc.available_at == offset.available_at == datetime(
        2026, 8, 31, tzinfo=UTC
    )
    assert offset.available_at is not None
    assert offset.available_at.tzinfo is UTC
    assert utc.input_fingerprint == offset.input_fingerprint


def test_consensus_availability_rejects_a_naive_datetime() -> None:
    with pytest.raises(CompanyResearchValidationError, match="timezone-aware"):
        _input(
            kind=CriticalInputKind.CONSENSUS,
            provider="Example Consensus",
            available_at=datetime(2026, 8, 31),  # noqa: DTZ001 - validation probe
            coverage="12 analysts",
        )


@pytest.mark.parametrize(
    "kind",
    (CriticalInputKind.AI_ASSUMPTION, CriticalInputKind.USER_ASSUMPTION),
)
def test_assumptions_require_versioned_key_and_rationale_without_source(
    kind: CriticalInputKind,
) -> None:
    assumption = _input(
        kind=kind,
        source_ref=None,
        assumption_key="revenue_growth.v1:base",
        rationale="Explicit five-year scenario assumption",
    )
    assert assumption.assumption_key == "revenue_growth.v1:base"

    with pytest.raises(CompanyResearchValidationError, match="versioned assumption"):
        replace(assumption, assumption_key="revenue_growth")
    with pytest.raises(CompanyResearchValidationError, match="rationale"):
        replace(assumption, rationale=None)
    with pytest.raises(CompanyResearchValidationError, match="source provenance"):
        replace(assumption, source_ref=SOURCE)


def test_derived_calculation_requires_equation_and_complete_canonical_parents() -> None:
    derived = _input(
        key="fcff",
        kind=CriticalInputKind.DERIVED_CALCULATION,
        source_ref=None,
        equation_id="fcff.v1",
        parent_input_keys=("capex", "operating_profit"),
    )
    assert derived.parent_input_keys == ("capex", "operating_profit")

    with pytest.raises(CompanyResearchValidationError, match="equation"):
        replace(derived, equation_id=None)
    with pytest.raises(CompanyResearchValidationError, match="parent input keys"):
        replace(derived, parent_input_keys=())
    with pytest.raises(CompanyResearchValidationError, match="canonical"):
        replace(derived, parent_input_keys=("operating_profit", "capex"))


def test_unknown_requires_reason_and_gap_key_and_cannot_carry_a_value() -> None:
    unknown = _input(
        key="terminal_growth_gap",
        kind=CriticalInputKind.UNKNOWN,
        value=None,
        period=None,
        unit=None,
        currency=None,
        source_ref=None,
        unknown_reason="No governed terminal-growth basis is available",
        gap_key="terminal_growth_missing",
    )
    assert unknown.value is None

    with pytest.raises(CompanyResearchValidationError, match="cannot carry a value"):
        replace(unknown, value=Decimal(0))
    with pytest.raises(CompanyResearchValidationError, match="reason"):
        replace(unknown, unknown_reason=None)
    with pytest.raises(CompanyResearchValidationError, match="gap key"):
        replace(unknown, gap_key=None)


def test_critical_input_scalar_rejects_boolean_values() -> None:
    with pytest.raises(CompanyResearchValidationError, match="value must be"):
        _input(value=True)


def test_fingerprint_uses_semantics_but_not_key_decision_or_replacement() -> None:
    original = _input()
    replacement = CriticalInputCandidate(
        key=original.key,
        kind=CriticalInputKind.USER_ASSUMPTION,
        value=Decimal(105),
        period=original.period,
        unit=original.unit,
        currency=original.currency,
        assumption_key="reported_revenue.v1:user_override",
        rationale="User-selected normalized revenue",
    )

    decided = replace(
        original,
        key="same_semantics_different_key",
        decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
        replacement=replace(replacement, key="same_semantics_different_key"),
    )

    assert decided.input_fingerprint == original.input_fingerprint
    assert replace(
        original, value=Decimal(101), input_fingerprint=""
    ).input_fingerprint != (
        original.input_fingerprint
    )
    assert replace(
        original,
        input_fingerprint="",
        impact=CriticalInputImpact(
            surfaces=(CriticalDependencySurface.FCFF,),
            dependency_paths=(("fcff_bridge", "surface:fcff"),),
        ),
    ).input_fingerprint != original.input_fingerprint


def test_fingerprint_distinguishes_decimal_from_numeric_text() -> None:
    decimal = _input(value=Decimal(123))
    text = _input(value="123")

    assert decimal.input_fingerprint != text.input_fingerprint


def test_replacement_is_a_user_assumption_and_derived_inputs_are_not_replaceable() -> None:
    replacement = CriticalInputCandidate(
        key="reported_revenue",
        kind=CriticalInputKind.USER_ASSUMPTION,
        value=Decimal(105),
        period="FY2025",
        unit="million",
        currency="CNY",
        assumption_key="reported_revenue.v1:user_override",
        rationale="User-selected normalized revenue",
    )
    replaced = _input(
        decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
        replacement=replacement,
    )
    assert replaced.kind is CriticalInputKind.SOURCE_FACT
    assert replaced.source_ref == SOURCE
    assert replaced.replacement == replacement

    with pytest.raises(CompanyResearchValidationError, match="user-assumption"):
        replace(replaced, replacement=replace(replacement, kind=CriticalInputKind.AI_ASSUMPTION))

    derived = _input(
        key="fcff",
        kind=CriticalInputKind.DERIVED_CALCULATION,
        source_ref=None,
        equation_id="fcff.v1",
        parent_input_keys=("capex", "operating_profit"),
    )
    with pytest.raises(CompanyResearchValidationError, match="not directly"):
        replace(
            derived,
            decision=CriticalInputDecision.REPLACED_WITH_USER_ASSUMPTION,
            replacement=replace(replacement, key="fcff"),
        )


def test_critical_input_set_requires_reproducible_surface_then_key_order() -> None:
    revenue = _input(key="z_revenue")
    fcff = _input(
        key="a_fcff",
        impact=CriticalInputImpact(
            surfaces=(CriticalDependencySurface.FCFF,),
            dependency_paths=(("fcff_bridge", "surface:fcff"),),
        ),
    )

    assert CriticalInputSet(inputs=(revenue, fcff)).inputs == (revenue, fcff)
    with pytest.raises(CompanyResearchValidationError, match="canonical order"):
        CriticalInputSet(inputs=(fcff, revenue))
    with pytest.raises(CompanyResearchValidationError, match="unique keys"):
        CriticalInputSet(inputs=(revenue, revenue))


def _surface_nodes() -> tuple[CriticalSurfaceNode, ...]:
    return tuple(CriticalSurfaceNode(surface=surface) for surface in CriticalDependencySurface)


def _selection_graph() -> CriticalDependencyGraph:
    reported = _candidate("reported_revenue")
    growth = _candidate(
        "revenue_growth",
        kind=CriticalInputKind.AI_ASSUMPTION,
        value=Decimal("0.10"),
        source_ref=None,
        unit="ratio",
        currency=None,
        assumption_key="revenue_growth.v1:base",
        rationale="Five-year base scenario",
    )
    unrelated = replace(reported, key="unrelated_headcount")
    revenue_bridge = _candidate(
        "revenue_bridge",
        kind=CriticalInputKind.DERIVED_CALCULATION,
        value=Decimal(110),
        source_ref=None,
        equation_id="revenue_bridge.v1",
        parent_input_keys=("reported_revenue", "revenue_growth"),
    )
    discount_gap = _candidate(
        "discount_rate_gap",
        kind=CriticalInputKind.UNKNOWN,
        value=None,
        source_ref=None,
        period=None,
        unit=None,
        currency=None,
        unknown_reason="No governed discount-rate basis",
        gap_key="discount_rate_missing",
    )
    return CriticalDependencyGraph(
        input_nodes=(
            CriticalInputNode(candidate=reported),
            CriticalInputNode(candidate=growth),
            CriticalInputNode(candidate=unrelated),
        ),
        calculation_nodes=(CriticalCalculationNode(candidate=revenue_bridge),),
        unknown_nodes=(CriticalUnknownNode(candidate=discount_gap),),
        surface_nodes=_surface_nodes(),
        edges=(
            CriticalDependencyEdge("discount_rate_gap", "surface:discount_terminal"),
            CriticalDependencyEdge("reported_revenue", "revenue_bridge"),
            CriticalDependencyEdge("revenue_bridge", "surface:fcff"),
            CriticalDependencyEdge("revenue_bridge", "surface:revenue"),
            CriticalDependencyEdge("revenue_growth", "revenue_bridge"),
        ),
    )


def _candidate(
    key: str,
    *,
    kind: CriticalInputKind = CriticalInputKind.SOURCE_FACT,
    value: Decimal | str | None = Decimal(100),
    source_ref: SourceLineageReference | None = SOURCE,
    **changes: object,
) -> CriticalInputCandidate:
    values: dict[str, object] = {
        "key": key,
        "kind": kind,
        "value": value,
        "period": "FY2025",
        "unit": "million",
        "currency": "CNY",
        "source_ref": source_ref,
    }
    values.update(changes)
    return CriticalInputCandidate(**values)


def test_selector_reverse_traverses_only_exact_reachable_dependencies() -> None:
    selected = select_critical_inputs(_selection_graph())

    assert tuple(item.key for item in selected.inputs) == (
        "reported_revenue",
        "revenue_growth",
        "discount_rate_gap",
    )
    assert "unrelated_headcount" not in {item.key for item in selected.inputs}
    reported = next(item for item in selected.inputs if item.key == "reported_revenue")
    assert reported.impact.surfaces == (
        CriticalDependencySurface.REVENUE,
        CriticalDependencySurface.FCFF,
    )
    assert reported.impact.dependency_paths == (
        ("revenue_bridge", "surface:fcff"),
        ("revenue_bridge", "surface:revenue"),
    )
    assert "revenue_bridge" not in {item.key for item in selected.inputs}


def test_reachable_calculation_connects_an_input_without_becoming_actionable() -> None:
    reported = _candidate("reported_revenue")
    calculation = _candidate(
        "revenue_bridge",
        kind=CriticalInputKind.DERIVED_CALCULATION,
        value=Decimal(100),
        source_ref=None,
        equation_id="revenue_bridge.v1",
        parent_input_keys=("reported_revenue",),
    )
    graph = CriticalDependencyGraph(
        input_nodes=(CriticalInputNode(candidate=reported),),
        calculation_nodes=(CriticalCalculationNode(candidate=calculation),),
        unknown_nodes=(),
        surface_nodes=_surface_nodes(),
        edges=(
            CriticalDependencyEdge("reported_revenue", "revenue_bridge"),
            CriticalDependencyEdge("revenue_bridge", "surface:revenue"),
        ),
    )

    selected = select_critical_inputs(graph)

    assert tuple(item.key for item in selected.inputs) == ("reported_revenue",)
    assert selected.inputs[0].impact.dependency_paths == (
        ("revenue_bridge", "surface:revenue"),
    )


def test_critical_input_set_rejects_an_actionable_calculation() -> None:
    calculation = _input(
        key="revenue_bridge",
        kind=CriticalInputKind.DERIVED_CALCULATION,
        source_ref=None,
        equation_id="revenue_bridge.v1",
        parent_input_keys=("reported_revenue",),
    )

    with pytest.raises(CompanyResearchValidationError, match="calculation"):
        CriticalInputSet(inputs=(calculation,))


def test_dependency_graph_fails_closed_on_invalid_shape() -> None:
    graph = _selection_graph()
    with pytest.raises(CompanyResearchValidationError, match="surface coverage"):
        replace(graph, surface_nodes=graph.surface_nodes[:-1])
    with pytest.raises(CompanyResearchValidationError, match="endpoint"):
        replace(
            graph,
            edges=tuple(
                sorted(
                    (*graph.edges, CriticalDependencyEdge("missing", "surface:revenue")),
                    key=lambda item: (item.parent_key, item.consumer_key),
                )
            ),
        )
    with pytest.raises(CompanyResearchValidationError, match="cycle"):
        replace(
            graph,
            edges=tuple(
                sorted(
                    (
                        *graph.edges,
                        CriticalDependencyEdge("revenue_bridge", "reported_revenue"),
                    ),
                    key=lambda item: (item.parent_key, item.consumer_key),
                )
            ),
        )
    with pytest.raises(CompanyResearchValidationError, match="complete parents"):
        replace(
            graph,
            edges=tuple(
                edge
                for edge in graph.edges
                if edge.parent_key != "revenue_growth"
            ),
        )


def test_selector_preserves_distinct_keys_with_identical_final_fingerprints() -> None:
    reported = _candidate("reported_revenue")
    duplicated = CriticalDependencyGraph(
        input_nodes=(
            CriticalInputNode(candidate=reported),
            CriticalInputNode(
                candidate=replace(reported, key="reported_revenue_copy")
            ),
        ),
        calculation_nodes=(),
        unknown_nodes=(),
        surface_nodes=_surface_nodes(),
        edges=tuple(
            sorted(
                (
                    CriticalDependencyEdge("reported_revenue", "surface:revenue"),
                    CriticalDependencyEdge(
                        "reported_revenue_copy", "surface:revenue"
                    ),
                ),
                key=lambda item: (item.parent_key, item.consumer_key),
            )
        ),
    )

    selected = select_critical_inputs(duplicated)

    assert tuple(
        item.key for item in selected.inputs if item.source_ref == SOURCE
    ) == ("reported_revenue", "reported_revenue_copy")


def test_rebuild_carries_only_unchanged_fingerprint_decisions() -> None:
    selected = select_critical_inputs(_selection_graph())
    decided = CriticalInputSet(
        inputs=tuple(
            replace(item, decision=CriticalInputDecision.CONFIRMED)
            if item.key in {"reported_revenue", "revenue_growth"}
            else item
            for item in selected.inputs
        )
    )
    graph = _selection_graph()
    changed_growth = replace(
        graph.input_nodes[1].candidate,
        value=Decimal("0.11"),
    )
    rebuilt_graph = replace(
        graph,
        input_nodes=tuple(
            sorted(
                (
                    graph.input_nodes[0],
                    CriticalInputNode(candidate=changed_growth),
                    graph.input_nodes[2],
                ),
                key=lambda node: node.key,
            )
        ),
    )

    rebuilt = carry_forward_critical_input_decisions(
        decided,
        select_critical_inputs(rebuilt_graph),
    )

    decisions = {item.key: item.decision for item in rebuilt.inputs}
    assert decisions["reported_revenue"] is CriticalInputDecision.CONFIRMED
    assert decisions["revenue_growth"] is CriticalInputDecision.PENDING


def test_rebuild_never_carries_a_decision_across_distinct_input_keys() -> None:
    cash = replace(_input(), key="market:capital:cash")
    previous = CriticalInputSet(
        inputs=(replace(cash, decision=CriticalInputDecision.CONFIRMED),)
    )
    rebuilt = CriticalInputSet(
        inputs=(replace(cash, key="market:capital:debt"),)
    )

    carried = carry_forward_critical_input_decisions(previous, rebuilt)

    assert carried.inputs[0].key == "market:capital:debt"
    assert carried.inputs[0].decision is CriticalInputDecision.PENDING


def test_dependency_graph_rejects_same_key_with_different_semantics() -> None:
    graph = _selection_graph()
    collision = CriticalInputNode(
        candidate=replace(
            graph.input_nodes[1].candidate,
            value=Decimal(999),
        )
    )
    with pytest.raises(CompanyResearchValidationError, match="key collision"):
        replace(
            graph,
            input_nodes=tuple(
                sorted((*graph.input_nodes, collision), key=lambda node: node.candidate.key)
            ),
        )
