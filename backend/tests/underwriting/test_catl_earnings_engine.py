"""Contracts for the CATL segment earnings bridge.

These tests deliberately exercise financial identities rather than an annual
report fixture.  Task 8 supplies the frozen CATL observations; the engine
itself must be useful for any company whose authorized observations conform to
the same metric contract.
"""
from dataclasses import replace
from decimal import Context, Decimal, localcontext
from uuid import uuid4

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.earnings import (
    CompanyExposure,
    CoreContribution,
    SegmentEconomics,
    SegmentInputs,
)
from app.underwriting.domain.industry import (
    IndustryRange,
    IndustryScenario,
    ScenarioKind,
    ScenarioSpec,
)
from app.underwriting.services.earnings_engine import (
    build_company_engine,
    build_segment,
    validate_earnings_engine_integrity,
)
from app.underwriting.services.industry_state import (
    compile_industry_scenario,
    compile_industry_state,
)
from tests.underwriting.test_catl_industry_state import (
    complete_inputs,
    formal_industry_mechanisms,
)


def _segment(
    key: str,
    *,
    volume_gwh: str = "1",
    asp: str = "100",
    cost: str = "60",
    operating_expense: str = "0",
    depreciation: str = "0",
    cash_capex: str = "0",
    working_capital_change: str = "0",
    cash_tax: str = "0",
    normalized_cash_earning_power: str | None = None,
) -> SegmentInputs:
    return SegmentInputs(
        key=key,
        volume_gwh=Decimal(volume_gwh),
        asp_cny_per_kwh=Decimal(asp),
        unit_cash_cost_cny_per_kwh=Decimal(cost),
        operating_expense=Decimal(operating_expense),
        depreciation=Decimal(depreciation),
        cash_capex=Decimal(cash_capex),
        working_capital_change=Decimal(working_capital_change),
        cash_tax=Decimal(cash_tax),
        asp_derivation_metric_ids=(f"segment.{key}.revenue", f"segment.{key}.volume_gwh"),
        unit_cost_derivation_metric_ids=(f"segment.{key}.cost", f"segment.{key}.volume_gwh"),
        normalized_cash_earning_power=(
            Decimal(normalized_cash_earning_power)
            if normalized_cash_earning_power is not None
            else None
        ),
    )


def _scenario(parent_id):
    return IndustryScenario(
        id=uuid4(),
        kind=ScenarioKind.BASE,
        parent_industry_state_id=parent_id,
        overrides=tuple(),
        battery_demand_gwh=Decimal("1"),
        effective_capacity_gwh=Decimal("1"),
        utilization=Decimal("1"),
        inventory_change_gwh=Decimal("0"),
        price_range_cny_per_kwh=IndustryRange(Decimal("1"), Decimal("1")),
        unit_cost_range_cny_per_kwh=IndustryRange(Decimal("1"), Decimal("1")),
        industry_profit_pool_range_cny=IndustryRange(Decimal("1"), Decimal("1")),
        falsifier_keys=("falsifier",),
    )


def _verified_scenario_context():
    mechanisms = formal_industry_mechanisms()
    state = compile_industry_state(inputs=complete_inputs(), mechanisms=mechanisms)
    scenario = compile_industry_scenario(
        parent=state,
        spec=ScenarioSpec(ScenarioKind.BASE, tuple(), state.falsifier_keys),
        mechanisms=mechanisms,
    )
    return state, mechanisms, scenario


def test_segment_volume_price_cost_bridge() -> None:
    result = build_segment(
        SegmentInputs(
            key="power_battery",
            volume_gwh=Decimal("381"),
            asp_cny_per_kwh=Decimal("664.1504908136483"),
            unit_cash_cost_cny_per_kwh=Decimal("505.1477217847769"),
            operating_expense=Decimal("0"),
            depreciation=Decimal("0"),
            cash_capex=Decimal("0"),
            working_capital_change=Decimal("0"),
            cash_tax=Decimal("0"),
            asp_derivation_metric_ids=("segment.power_battery.revenue", "segment.power_battery.volume_gwh"),
            unit_cost_derivation_metric_ids=("segment.power_battery.cost", "segment.power_battery.volume_gwh"),
        )
    )

    assert result.revenue.quantize(Decimal("1")) == Decimal("253041337000")
    assert result.gross_profit.quantize(Decimal("1")) == Decimal("60580055000")
    assert result.operating_profit == result.gross_profit
    assert result.nopat == result.operating_profit
    assert result.free_cash_flow == result.nopat
    assert result.revenue_basis == "derived"
    assert result.cost_basis == "derived"


def test_company_bridge_refuses_unbalanced_revenue() -> None:
    with pytest.raises(ValidationError, match="revenue does not reconcile"):
        build_company_engine(
            company_total=Decimal("362012554000"),
            segments=(build_segment(_segment("power_battery")),),
        )


def test_company_bridge_retains_signed_reconciliations_and_fcf_identities() -> None:
    power = build_segment(
        _segment(
            "power_battery",
            volume_gwh="2",
            asp="100",
            cost="60",
            operating_expense="10",
            depreciation="7",
            cash_capex="6",
            working_capital_change="5",
            cash_tax="4",
        )
    )
    storage = build_segment(_segment("energy_storage", asp="80", cost="50"))
    total_revenue = power.revenue + storage.revenue
    total_cost = power.cost + storage.cost
    result = build_company_engine(
        company_total=total_revenue,
        company_total_cost=total_cost,
        segments=(power, storage),
        reported_operating_cash_flow=Decimal("90000000"),
        reported_cash_capex=Decimal("10000000"),
    )

    assert result.reconciliations.revenue.balanced is True
    assert result.reconciliations.cost is not None
    assert result.reconciliations.cost.balanced is True
    assert result.reported_fcf_proxy == Decimal("80000000")
    assert result.modeled_nopat == sum((power.nopat, storage.nopat), Decimal("0"))
    assert result.modeled_free_cash_flow == sum((power.free_cash_flow, storage.free_cash_flow), Decimal("0"))
    assert result.reconciliations.operating_profit_to_nopat.balanced is True
    assert result.reconciliations.nopat_to_free_cash_flow.balanced is True
    assert all(item.balanced for item in result.reconciliations.segment_revenue)
    assert all(item.balanced for item in result.reconciliations.segment_cost)


def test_unallocated_company_row_is_explicit_and_never_proportionally_spread() -> None:
    direct = build_segment(_segment("power_battery", operating_expense="0"))
    unallocated = build_segment(
        SegmentInputs.unallocated_company(
            operating_expense=Decimal("11"),
            depreciation=Decimal("3"),
            cash_capex=Decimal("2"),
            working_capital_change=Decimal("1"),
            cash_tax=Decimal("4"),
        )
    )
    result = build_company_engine(
        company_total=direct.revenue,
        company_total_cost=direct.cost,
        segments=(direct, unallocated),
    )

    assert result.segment("unallocated_company") == unallocated
    assert direct.operating_expense == Decimal("0")
    assert unallocated.revenue == Decimal("0")
    assert unallocated.operating_expense == Decimal("11")


def test_reported_rows_stay_reported_and_derived_unit_economics_keep_parent_metric_ids() -> None:
    result = build_segment(
        SegmentInputs(
            key="materials_recycling",
            volume_gwh=None,
            asp_cny_per_kwh=None,
            unit_cash_cost_cny_per_kwh=None,
            operating_expense=Decimal("0"),
            depreciation=Decimal("0"),
            cash_capex=Decimal("0"),
            working_capital_change=Decimal("0"),
            cash_tax=Decimal("0"),
            reported_revenue=Decimal("100"),
            reported_cost=Decimal("70"),
        )
    )
    derived = _segment("power_battery", volume_gwh="2", asp="100", cost="60")
    derived = replace(
        derived,
        asp_derivation_metric_ids=("segment.power_battery.revenue", "segment.power_battery.volume_gwh"),
        unit_cost_derivation_metric_ids=("segment.power_battery.cost", "segment.power_battery.volume_gwh"),
    )
    derived_result = build_segment(derived)

    assert result.revenue_basis == "reported"
    assert result.cost_basis == "reported"
    assert derived_result.asp_derivation_metric_ids == (
        "segment.power_battery.revenue",
        "segment.power_battery.volume_gwh",
    )
    assert derived_result.unit_cost_derivation_metric_ids == (
        "segment.power_battery.cost",
        "segment.power_battery.volume_gwh",
    )


def test_largest_revenue_segment_need_not_be_largest_cash_or_value_core() -> None:
    revenue_leader = build_segment(
        _segment("power_battery", asp="200", cost="180", normalized_cash_earning_power="5")
    )
    cash_leader = build_segment(
        _segment(
            "energy_storage",
            asp="100",
            cost="20",
            cash_capex="1",
            normalized_cash_earning_power="99",
        )
    )
    state, mechanisms, scenario = _verified_scenario_context()
    result = build_company_engine(
        company_total=revenue_leader.revenue + cash_leader.revenue,
        company_total_cost=revenue_leader.cost + cash_leader.cost,
        segments=(revenue_leader, cash_leader),
        industry_state=state,
        mechanisms=mechanisms,
        scenario=scenario,
        exposures=(
            CompanyExposure("power_battery", state.id, Decimal("1")),
            CompanyExposure("energy_storage", state.id, Decimal("1")),
        ),
    )

    assert result.four_core_views.revenue_core[0].segment_key == "power_battery"
    assert result.four_core_views.cash_core[0].segment_key == "energy_storage"
    assert result.four_core_views.value_core[0].segment_key == "energy_storage"
    assert result.four_core_views.value_core[0].basis == "normalized_cash_earning_power:base"
    assert not hasattr(result.four_core_views.value_core[0], "price")


def test_value_core_requires_explicit_bound_industry_scenario_and_exposure() -> None:
    segment = build_segment(_segment("power_battery", normalized_cash_earning_power="10"))
    state, mechanisms, _ = _verified_scenario_context()
    with pytest.raises(ValidationError, match="industry scenario and exposures"):
        build_company_engine(
            company_total=segment.revenue,
            company_total_cost=segment.cost,
            segments=(segment,),
        )
    with pytest.raises(ValidationError, match="scenario must belong to industry state"):
        build_company_engine(
            company_total=segment.revenue,
            company_total_cost=segment.cost,
            segments=(segment,),
            industry_state=state,
            mechanisms=mechanisms,
            scenario=_scenario(uuid4()),
            exposures=(CompanyExposure("power_battery", state.id, Decimal("1")),),
        )


def test_exposure_must_match_each_scenario_valued_segment_once() -> None:
    segment = build_segment(_segment("power_battery", normalized_cash_earning_power="10"))
    state, mechanisms, scenario = _verified_scenario_context()
    with pytest.raises(ValidationError, match="exactly one company exposure"):
        build_company_engine(
            company_total=segment.revenue,
            company_total_cost=segment.cost,
            segments=(segment,),
            industry_state=state,
            mechanisms=mechanisms,
            scenario=scenario,
            exposures=tuple(),
        )


def test_derived_bridge_defaults_to_stable_numerator_and_denominator_metric_ids() -> None:
    result = build_segment(_segment("power_battery"))

    assert result.asp_derivation_metric_ids == (
        "segment.power_battery.revenue",
        "segment.power_battery.volume_gwh",
    )
    assert result.unit_cost_derivation_metric_ids == (
        "segment.power_battery.cost",
        "segment.power_battery.volume_gwh",
    )


def test_segment_economics_cannot_be_directly_constructed_with_broken_financial_identity() -> None:
    good = build_segment(_segment("power_battery"))
    with pytest.raises(ValidationError, match="gross profit does not reconcile"):
        SegmentEconomics(
            **{
                field: (Decimal("999") if field == "gross_profit" else getattr(good, field))
                for field in good.__dataclass_fields__
            }
        )


def test_modeled_nopat_per_share_uses_stable_decimal_math() -> None:
    segment = build_segment(_segment("power_battery", asp="101", cost="100"))
    result = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
        diluted_shares=Decimal("3"),
    )

    with localcontext(Context(prec=128)):
        assert result.modeled_nopat_per_share == segment.nopat / Decimal("3")


def test_earnings_engine_direct_construction_cannot_bypass_company_reconciliation() -> None:
    segment = build_segment(_segment("power_battery"))
    good = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
    )

    with pytest.raises(ValidationError, match="company revenue does not reconcile"):
        replace(good, company_revenue=Decimal("0"))


def test_decimal_results_do_not_depend_on_ambient_worker_precision() -> None:
    inputs = _segment(
        "power_battery",
        volume_gwh="381",
        asp="664.1504908136483",
        cost="505.1477217847769",
    )
    with localcontext(Context(prec=3)):
        constrained = build_segment(inputs)
        constrained_engine = build_company_engine(
            company_total=constrained.revenue,
            company_total_cost=constrained.cost,
            segments=(constrained,),
            diluted_shares=Decimal("3"),
        )
    unrestricted = build_segment(inputs)
    unrestricted_engine = build_company_engine(
        company_total=unrestricted.revenue,
        company_total_cost=unrestricted.cost,
        segments=(unrestricted,),
        diluted_shares=Decimal("3"),
    )

    assert constrained == unrestricted
    assert constrained_engine == unrestricted_engine


def test_partial_normalized_value_core_is_rejected_instead_of_dropping_a_segment() -> None:
    with_value = build_segment(_segment("power_battery", normalized_cash_earning_power="10"))
    without_value = build_segment(_segment("energy_storage"))
    state, mechanisms, scenario = _verified_scenario_context()
    with pytest.raises(ValidationError, match="normalized cash earning power is required"):
        build_company_engine(
            company_total=with_value.revenue + without_value.revenue,
            company_total_cost=with_value.cost + without_value.cost,
            segments=(with_value, without_value),
            industry_state=state,
            mechanisms=mechanisms,
            scenario=scenario,
            exposures=(CompanyExposure("power_battery", state.id, Decimal("1")),),
        )


def test_company_reconciliation_tolerance_is_fixed_at_cny_1000() -> None:
    segment = build_segment(_segment("power_battery"))
    with pytest.raises(ValidationError, match="exactly CNY 1000"):
        build_company_engine(
            company_total=segment.revenue,
            company_total_cost=segment.cost,
            segments=(segment,),
            tolerance=Decimal("1001"),
        )


def test_direct_engine_cannot_raise_the_company_reconciliation_tolerance() -> None:
    segment = build_segment(_segment("power_battery"))
    good = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
    )
    widened = replace(
        good.reconciliations,
        revenue=replace(good.reconciliations.revenue, tolerance=Decimal("1001")),
    )

    with pytest.raises(ValidationError, match="exactly CNY 1000"):
        replace(good, reconciliations=widened)


def test_direct_engine_cannot_accept_a_forged_four_core_view() -> None:
    segment = build_segment(_segment("power_battery"))
    good = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
    )
    forged = replace(
        good.four_core_views,
        revenue_core=(CoreContribution("invented_segment", Decimal("1"), Decimal("1"), "revenue"),),
    )

    with pytest.raises(ValidationError, match="four core views do not reconcile"):
        replace(good, four_core_views=forged)


def test_core_contribution_rejects_valuation_language() -> None:
    with pytest.raises(ValidationError, match="valuation terms"):
        CoreContribution("power_battery", Decimal("1"), Decimal("1"), "target price")


@pytest.mark.parametrize("basis", ("DCF", "fair value", "enterprise value"))
def test_core_contribution_rejects_additional_valuation_language(basis: str) -> None:
    with pytest.raises(ValidationError, match="valuation terms"):
        CoreContribution("power_battery", Decimal("1"), Decimal("1"), basis)


def test_direct_engine_rejects_modeled_nopat_per_share_without_diluted_shares() -> None:
    segment = build_segment(_segment("power_battery"))
    good = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
    )

    with pytest.raises(ValidationError, match="diluted shares and modeled NOPAT per share"):
        replace(good, modeled_nopat_per_share=Decimal("777"))


def test_direct_engine_requires_eps_when_diluted_shares_are_present() -> None:
    segment = build_segment(_segment("power_battery"))
    good = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
    )

    with pytest.raises(ValidationError, match="diluted shares and modeled NOPAT per share"):
        replace(good, diluted_shares=Decimal("3"))


def test_direct_engine_rejects_mismatched_modeled_nopat_per_share() -> None:
    segment = build_segment(_segment("power_battery"))
    good = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
        diluted_shares=Decimal("3"),
    )

    with pytest.raises(ValidationError, match="modeled NOPAT per share does not reconcile"):
        replace(good, modeled_nopat_per_share=Decimal("777"))


def test_physical_segment_economics_revalidates_volume_price_and_cost_bridge() -> None:
    good = build_segment(_segment("power_battery"))
    with pytest.raises(ValidationError, match="physical revenue does not reconcile"):
        replace(
            good,
            revenue=good.revenue + Decimal("1"),
            gross_profit=good.gross_profit + Decimal("1"),
            operating_profit=good.operating_profit + Decimal("1"),
            nopat=good.nopat + Decimal("1"),
            free_cash_flow=good.free_cash_flow + Decimal("1"),
        )


def test_physical_inputs_require_metric_parent_ids() -> None:
    value = _segment("power_battery")
    with pytest.raises(ValidationError, match="derived physical inputs require parent metric IDs"):
        replace(
            value,
            asp_derivation_metric_ids=None,
            unit_cost_derivation_metric_ids=None,
        )


def test_public_integrity_validator_detects_post_construction_tampering() -> None:
    segment = build_segment(_segment("power_battery"))
    engine = build_company_engine(
        company_total=segment.revenue,
        company_total_cost=segment.cost,
        segments=(segment,),
    )
    object.__setattr__(engine, "modeled_free_cash_flow", Decimal("777"))

    with pytest.raises(ValidationError, match="content hash"):
        validate_earnings_engine_integrity(engine)


def test_engine_rejects_scenario_that_does_not_replay_from_verified_industry_inputs() -> None:
    mechanisms = formal_industry_mechanisms()
    state = compile_industry_state(inputs=complete_inputs(), mechanisms=mechanisms)
    scenario = _scenario(state.id)
    segment = build_segment(_segment("power_battery", normalized_cash_earning_power="10"))
    with pytest.raises(ValidationError, match="scenario does not match verified"):
        build_company_engine(
            company_total=segment.revenue,
            company_total_cost=segment.cost,
            segments=(segment,),
            industry_state=state,
            mechanisms=mechanisms,
            scenario=scenario,
            exposures=(CompanyExposure("power_battery", state.id, Decimal("1")),),
        )


def test_legacy_scenario_without_verified_state_and_mechanisms_is_rejected() -> None:
    segment = build_segment(_segment("power_battery", normalized_cash_earning_power="10"))
    state_id = uuid4()
    with pytest.raises(ValidationError, match="verified industry state, scenario, and mechanisms"):
        build_company_engine(
            company_total=segment.revenue,
            company_total_cost=segment.cost,
            segments=(segment,),
            scenario=_scenario(state_id),
            exposures=(CompanyExposure("power_battery", state_id, Decimal("1")),),
        )
