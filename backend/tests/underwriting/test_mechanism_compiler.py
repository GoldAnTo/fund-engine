"""Contracts for governed economic mechanism compilation."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.mechanisms import (
    Falsifier,
    FinancialMapping,
    MechanismPack,
    MechanismStatus,
)
from app.underwriting.services.mechanism_compiler import (
    MechanismDependencyContext,
    MetricDefinitionDependency,
    compile_mechanisms,
    transition_mechanism,
    validate_formal_mechanism,
)
from app.underwriting.services.source_freeze import freeze_manifest


CUTOFF = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def dependency_context(
    bindings: dict[str, str] | None = None,
) -> MechanismDependencyContext:
    metric_bindings = bindings or {
        "industry.ev_demand_gwh": "metric-demand",
        "company.battery_shipments_gwh": "metric-shipment",
        "company.battery_shipments_growth": "metric-growth",
    }
    source_manifest = freeze_manifest(
        {
            "schema_version": "underwriting.source-manifest.v1",
            "sources": [
                {
                    "source_id": "iea-2025",
                    "title": "Global EV Outlook 2025",
                    "locator": "https://example.test/iea-2025",
                    "published_at": "2025-05-14T00:00:00+00:00",
                    "first_available_at": "2025-05-14T00:00:00+00:00",
                    "retrieved_at": "2025-05-14T00:00:00+00:00",
                    "content_sha256": "a" * 64,
                    "authority": "official_industry",
                    "authorization": "authorized",
                    "display_policy": "derived_only",
                    "provider_capability": "public_http",
                    "retention": "hash_locator_and_derived_observations",
                }
            ],
        },
        cutoff=CUTOFF,
    )
    return MechanismDependencyContext(
        cutoff=CUTOFF,
        metric_definitions=tuple(
            MetricDefinitionDependency(
                metric_key=key,
                definition_id=definition_id,
                available_at=datetime(2025, 5, 14, tzinfo=UTC),
            )
            for key, definition_id in metric_bindings.items()
        ),
        source_manifest=source_manifest,
    )


def formal_mechanism(**overrides: object) -> MechanismPack:
    value = MechanismPack(
        key="ev_storage_demand_to_shipments",
        version=1,
        status=MechanismStatus.FORMAL,
        scope_object_id=uuid4(),
        driver_keys=("industry.ev_demand_gwh",),
        formula="battery_shipments = end_demand * battery_intensity",
        applicability=("global power battery market",),
        invalidation_conditions=("battery intensity materially changes",),
        financial_mappings=(
            FinancialMapping(
                driver_key="industry.ev_demand_gwh",
                target_metric_key="company.battery_shipments_gwh",
                direction="positive",
                lag_periods=1,
                magnitude_low=Decimal("0.1"),
                magnitude_high=Decimal("0.9"),
            ),
        ),
        alternative_explanations=("inventory destocking",),
        falsifiers=(
            Falsifier(
                key="shipment_growth_fails",
                metric_key="company.battery_shipments_growth",
                operator="lt",
                threshold_low=Decimal("0"),
                threshold_high=None,
                evaluation_periods=2,
                consequence="challenge mechanism",
            ),
        ),
        source_ids=("iea-2025",),
        human_confirmation_identity="researcher-001",
    )
    return replace(value, **overrides)


def test_only_formal_mechanisms_can_compile() -> None:
    candidate = replace(
        formal_mechanism(), status=MechanismStatus.CANDIDATE, human_confirmation_identity=None
    )
    with pytest.raises(ValidationError, match="mechanism must be formal"):
        compile_mechanisms(
            (candidate,),
            dependencies=dependency_context(),
        )


def test_formal_mechanism_requires_mapping_alternative_and_falsifier() -> None:
    value = formal_mechanism(financial_mappings=(), alternative_explanations=(), falsifiers=())
    with pytest.raises(ValidationError, match="formal mechanism is incomplete"):
        validate_formal_mechanism(value)


def test_transition_requires_adjacent_lifecycle_and_human_confirmation() -> None:
    candidate = replace(
        formal_mechanism(), status=MechanismStatus.CANDIDATE, human_confirmation_identity=None
    )
    with pytest.raises(ValidationError, match="not allowed"):
        transition_mechanism(candidate, MechanismStatus.CALIBRATED)

    adapted = transition_mechanism(candidate, MechanismStatus.ADAPTED)
    calibrated = transition_mechanism(adapted, MechanismStatus.CALIBRATED)
    with pytest.raises(ValidationError, match="human_confirmation_identity"):
        transition_mechanism(calibrated, MechanismStatus.HUMAN_CONFIRMED)


def test_formal_transition_creates_successor_and_retains_named_reviewer() -> None:
    candidate = replace(
        formal_mechanism(), status=MechanismStatus.CANDIDATE, human_confirmation_identity=None
    )
    adapted = transition_mechanism(candidate, MechanismStatus.ADAPTED)
    calibrated = transition_mechanism(adapted, MechanismStatus.CALIBRATED)
    confirmed = transition_mechanism(
        calibrated,
        MechanismStatus.HUMAN_CONFIRMED,
        human_confirmation_identity="reviewer-001",
    )
    formal = transition_mechanism(confirmed, MechanismStatus.FORMAL)
    assert formal.version == 5
    assert formal.human_confirmation_identity == "reviewer-001"


def test_formal_mechanism_rejects_unmapped_driver_and_circular_chain() -> None:
    unmapped = formal_mechanism(driver_keys=("industry.ev_demand_gwh", "industry.storage_demand_gwh"))
    with pytest.raises(ValidationError, match="cover every driver_key"):
        validate_formal_mechanism(unmapped)

    cyclic = formal_mechanism(
        driver_keys=("industry.ev_demand_gwh", "company.battery_shipments_gwh"),
        financial_mappings=(
            FinancialMapping(
                "industry.ev_demand_gwh",
                "company.battery_shipments_gwh",
                "positive",
                1,
                Decimal("0.1"),
                Decimal("0.9"),
            ),
            FinancialMapping(
                "company.battery_shipments_gwh",
                "industry.ev_demand_gwh",
                "positive",
                0,
                Decimal("0.1"),
                Decimal("0.9"),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="causal chain must be acyclic"):
        validate_formal_mechanism(cyclic)


def test_compiler_requires_known_canonical_inputs_outputs_and_sources() -> None:
    value = formal_mechanism()
    with pytest.raises(ValidationError, match="target metric definition"):
        compile_mechanisms(
            (value,),
            dependencies=dependency_context({"industry.ev_demand_gwh": "metric-demand"}),
        )


def test_compiler_returns_deterministic_hash_and_dependency_ids() -> None:
    value = formal_mechanism()
    metric_definition_ids = {
        "industry.ev_demand_gwh": "metric-demand",
        "company.battery_shipments_gwh": "metric-shipment",
        "company.battery_shipments_growth": "metric-growth",
    }
    first = compile_mechanisms(
        (value,), dependencies=dependency_context(metric_definition_ids)
    )
    second = compile_mechanisms(
        (value,), dependencies=dependency_context(dict(reversed(tuple(metric_definition_ids.items()))))
    )
    assert first.content_hash == second.content_hash
    assert first.metric_definition_ids == (
        "metric-demand",
        "metric-growth",
        "metric-shipment",
    )
    assert first.source_ids == ("iea-2025",)
    assert first.cutoff == CUTOFF


def test_compiler_rejects_causal_cycle_across_formal_pack_union() -> None:
    forward = formal_mechanism(key="demand_to_shipments")
    reverse = formal_mechanism(
        key="shipments_to_demand",
        driver_keys=("company.battery_shipments_gwh",),
        financial_mappings=(
            FinancialMapping(
                "company.battery_shipments_gwh",
                "industry.ev_demand_gwh",
                "positive",
                0,
                Decimal("0.1"),
                Decimal("0.9"),
            ),
        ),
    )
    with pytest.raises(ValidationError, match="causal chain must be acyclic"):
        compile_mechanisms((forward, reverse), dependencies=dependency_context())


def test_dependency_context_rejects_metric_unavailable_at_cutoff() -> None:
    with pytest.raises(ValidationError, match="metric definition is unavailable at cutoff"):
        MechanismDependencyContext(
            cutoff=CUTOFF,
            metric_definitions=(
                MetricDefinitionDependency(
                    metric_key="industry.ev_demand_gwh",
                    definition_id="metric-demand",
                    available_at=datetime(2025, 5, 16, tzinfo=UTC),
                ),
            ),
            source_manifest=dependency_context().source_manifest,
        )


def test_compiled_hash_keeps_metric_key_to_definition_id_binding() -> None:
    value = formal_mechanism()
    first = compile_mechanisms(
        (value,),
        dependencies=dependency_context(
            {
                "industry.ev_demand_gwh": "metric-demand",
                "company.battery_shipments_gwh": "metric-shipment",
                "company.battery_shipments_growth": "metric-growth",
            }
        ),
    )
    second = compile_mechanisms(
        (value,),
        dependencies=dependency_context(
            {
                "industry.ev_demand_gwh": "metric-shipment",
                "company.battery_shipments_gwh": "metric-demand",
                "company.battery_shipments_growth": "metric-growth",
            }
        ),
    )
    assert first.content_hash != second.content_hash
    assert first.metric_definition_bindings == (
        ("company.battery_shipments_growth", "metric-growth"),
        ("company.battery_shipments_gwh", "metric-shipment"),
        ("industry.ev_demand_gwh", "metric-demand"),
    )


def test_falsifier_requires_complete_operator_bounds() -> None:
    with pytest.raises(ValidationError, match="outside"):
        Falsifier(
            key="range_break",
            metric_key="industry.utilization",
            operator="outside",
            threshold_low=Decimal("0.8"),
            threshold_high=None,
            evaluation_periods=1,
            consequence="challenge",
        )


@pytest.mark.parametrize(
    "key",
    (
        "ev_storage_demand_to_shipments",
        "effective_capacity_to_utilization_and_price",
        "material_cost_pass_through_to_unit_margin",
        "certification_overseas_footprint_to_obtainable_share",
        "capex_working_capital_to_free_cash_flow",
        "counter_model_customer_bargaining_and_oversupply",
    ),
)
def test_required_catl_mechanism_shapes_are_formalizable(key: str) -> None:
    value = formal_mechanism(key=key)
    validate_formal_mechanism(value)
