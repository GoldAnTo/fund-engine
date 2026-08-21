"""Contracts for governed economic mechanism compilation."""

from dataclasses import replace
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
    compile_mechanisms,
    transition_mechanism,
    validate_formal_mechanism,
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
            metric_definition_ids={
                "industry.ev_demand_gwh": "metric-demand",
                "company.battery_shipments_gwh": "metric-shipment",
                "company.battery_shipments_growth": "metric-growth",
            },
            source_ids={"iea-2025"},
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
            metric_definition_ids={"industry.ev_demand_gwh": "metric-demand"},
            source_ids={"iea-2025"},
        )


def test_compiler_returns_deterministic_hash_and_dependency_ids() -> None:
    value = formal_mechanism()
    metric_definition_ids = {
        "industry.ev_demand_gwh": "metric-demand",
        "company.battery_shipments_gwh": "metric-shipment",
        "company.battery_shipments_growth": "metric-growth",
    }
    first = compile_mechanisms(
        (value,), metric_definition_ids=metric_definition_ids, source_ids={"iea-2025"}
    )
    second = compile_mechanisms(
        (value,), metric_definition_ids=dict(reversed(tuple(metric_definition_ids.items()))), source_ids={"iea-2025"}
    )
    assert first.content_hash == second.content_hash
    assert first.metric_definition_ids == (
        "metric-demand",
        "metric-growth",
        "metric-shipment",
    )
    assert first.source_ids == ("iea-2025",)


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
