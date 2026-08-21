"""Contracts for the power-battery industry-state model."""

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest

from app.underwriting.domain.industry import (
    IndustryInputs,
    ScenarioDriverOverride,
    ScenarioKind,
    ScenarioSpec,
)
from app.underwriting.domain.mechanisms import (
    Falsifier,
    FinancialMapping,
    MechanismPack,
    MechanismStatus,
)
from app.underwriting.services.industry_state import (
    AnswerabilityBlocked,
    compile_industry_scenario,
    compile_industry_state,
)
from app.underwriting.services.mechanism_compiler import transition_mechanism


def formal_industry_mechanisms() -> tuple[MechanismPack, ...]:
    drivers = (
        "industry.ev_sales_millions",
        "industry.average_battery_kwh",
        "industry.storage_demand_gwh",
        "industry.nominal_capacity_gwh",
        "industry.commissioned_share",
        "industry.certified_share",
        "industry.yield_rate",
        "industry.shipments_gwh",
        "industry.production_gwh",
        "industry.cell_asp_cny_per_kwh",
        "industry.unit_cash_cost_cny_per_kwh",
    )
    candidate = MechanismPack(
        key="effective_capacity_to_utilization_and_price",
        version=1,
        status=MechanismStatus.CANDIDATE,
        scope_object_id=uuid4(),
        driver_keys=drivers,
        formula="effective capacity determines the industry utilization regime",
        applicability=("power-battery industry",),
        invalidation_conditions=("technology renders the capacity baseline obsolete",),
        financial_mappings=tuple(
            FinancialMapping(
                driver_key=key,
                target_metric_key="industry.utilization",
                direction="positive",
                lag_periods=0,
                magnitude_low=Decimal("0"),
                magnitude_high=Decimal("1"),
            )
            for key in drivers
        ),
        alternative_explanations=("customer inventory movements",),
        falsifiers=(
            Falsifier(
                key="utilization_outside_range",
                metric_key="industry.utilization",
                operator="outside",
                threshold_low=Decimal("0.50"),
                threshold_high=Decimal("1.00"),
                evaluation_periods=2,
                consequence="retire capacity mechanism",
            ),
        ),
        source_ids=("industry-source-2025",),
    )
    adapted = transition_mechanism(candidate, MechanismStatus.ADAPTED)
    calibrated = transition_mechanism(adapted, MechanismStatus.CALIBRATED)
    confirmed = transition_mechanism(
        calibrated,
        MechanismStatus.HUMAN_CONFIRMED,
        human_confirmation_identity="researcher-001",
        review_evidence_id=uuid4(),
    )
    return (transition_mechanism(confirmed, MechanismStatus.FORMAL),)


def complete_inputs() -> IndustryInputs:
    return IndustryInputs(
        ev_sales_millions=Decimal("30"),
        average_battery_kwh=Decimal("55"),
        storage_demand_gwh=Decimal("300"),
        nominal_capacity_gwh=Decimal("3000"),
        commissioned_share=Decimal("0.80"),
        certified_share=Decimal("0.75"),
        yield_rate=Decimal("0.95"),
        shipments_gwh=Decimal("1500"),
        production_gwh=Decimal("1600"),
        cell_asp_cny_per_kwh=Decimal("0.60"),
        unit_cash_cost_cny_per_kwh=Decimal("0.45"),
    )


def test_nominal_capacity_is_never_used_as_effective_capacity() -> None:
    result = compile_industry_state(
        inputs=complete_inputs(), mechanisms=formal_industry_mechanisms()
    )

    assert result.battery_demand_gwh == Decimal("1950.0000")
    assert result.nominal_capacity_gwh == Decimal("3000.0000")
    assert result.effective_capacity_gwh == Decimal("1710.0000")
    assert result.utilization == Decimal("1500") / Decimal("1710")
    assert result.effective_capacity_gwh != result.nominal_capacity_gwh
    assert result.inventory_change_gwh == Decimal("100.0000")
    assert result.unit_margin_cny_per_kwh == Decimal("0.1500")


def test_missing_certification_baseline_fails_closed() -> None:
    with pytest.raises(AnswerabilityBlocked, match="missing_key_baseline") as raised:
        compile_industry_state(
            inputs=replace(complete_inputs(), certified_share=None),
            mechanisms=formal_industry_mechanisms(),
        )

    assert raised.value.blocker_code.value == "missing_key_baseline"


@pytest.mark.parametrize(
    "inputs",
    (
        replace(complete_inputs(), shipments_gwh=Decimal("-1")),
        replace(complete_inputs(), certified_share=Decimal("1.01")),
        replace(complete_inputs(), yield_rate=Decimal("0")),
    ),
)
def test_physical_and_share_baselines_are_formally_validated(inputs: IndustryInputs) -> None:
    with pytest.raises(AnswerabilityBlocked, match="missing_key_baseline"):
        compile_industry_state(inputs=inputs, mechanisms=formal_industry_mechanisms())


def test_nonformal_mechanism_blocks_instead_of_becoming_an_industry_signal() -> None:
    nonformal = replace(formal_industry_mechanisms()[0], status=MechanismStatus.CALIBRATED)

    with pytest.raises(AnswerabilityBlocked, match="mechanism_unidentified") as raised:
        compile_industry_state(inputs=complete_inputs(), mechanisms=(nonformal,))

    assert raised.value.blocker_code.value == "mechanism_unidentified"


def test_scenario_retains_parent_and_overrides_only_declared_driver() -> None:
    mechanisms = formal_industry_mechanisms()
    state = compile_industry_state(
        inputs=complete_inputs(), mechanisms=mechanisms
    )
    scenario = compile_industry_scenario(
        parent=state,
        spec=ScenarioSpec(
            kind=ScenarioKind.UPSIDE,
            overrides=(
                ScenarioDriverOverride("industry.cell_asp_cny_per_kwh", Decimal("0.70")),
            ),
            falsifiers=("utilization_outside_range",),
        ),
        mechanisms=mechanisms,
    )

    assert scenario.parent_industry_state_id == state.id
    assert scenario.kind is ScenarioKind.UPSIDE
    assert scenario.price_range_cny_per_kwh.low == Decimal("0.7000")
    assert scenario.price_range_cny_per_kwh.high == Decimal("0.7000")
    assert scenario.probability is None
    assert scenario.falsifier_keys == ("utilization_outside_range",)


def test_scenario_rejects_override_outside_declared_mechanism_drivers() -> None:
    mechanisms = formal_industry_mechanisms()
    state = compile_industry_state(
        inputs=complete_inputs(), mechanisms=mechanisms
    )

    with pytest.raises(AnswerabilityBlocked, match="mechanism_unidentified"):
        compile_industry_scenario(
            parent=state,
            spec=ScenarioSpec(
                kind=ScenarioKind.DOWNSIDE,
                overrides=(ScenarioDriverOverride("industry.unsupported_driver", Decimal("0.60")),),
                falsifiers=("utilization_outside_range",),
            ),
            mechanisms=mechanisms,
        )
