from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, ROUND_DOWN, ROUND_UP, getcontext, localcontext
import hashlib

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    BusinessMapArtifact,
    BusinessModuleArtifact,
    CapitalStructureReference,
    ClassifiedBusinessEvidenceArtifact,
    CompanyResearchAssessment,
    CompanyResearchModelInput,
    CompanyResearchValidationError,
    canonical_decimal_string,
    DriverMapArtifact,
    DriverMetricArtifact,
    EvidenceGapContract,
    FinancialBridgeRow,
    JudgmentContextArtifact,
    MarketBridgeArtifact,
    ModelInputState,
    ResearchGap,
    ResearchGapSeverity,
    ReverseDcfRequest,
    ValuationSetArtifact,
    ScenarioArtifact,
    ScenarioDriverOverride,
    ScenarioFinancialDriverForecast,
    ScenarioFinancialBridge,
    ScenarioSetArtifact,
    SecurityValuationReference,
    SourceLineageReference,
    ValueRange,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_engine import CompanyResearchEngine


def _source(key: str) -> SourceLineageReference:
    return SourceLineageReference(
        fact_key=key,
        source_role="regulatory_filing",
        source_url="https://www.sec.gov/example",
        source_locator=f"Item 7 / {key}",
        raw_hash=hashlib.sha256(key.encode("utf-8")).hexdigest(),
    )


def _row(year: int, *, fcff: Decimal = Decimal("80")) -> FinancialBridgeRow:
    return FinancialBridgeRow(
        fiscal_year=year,
        revenue=Decimal("200"),
        operating_income=Decimal("100"),
        cash_tax_rate=Decimal("0.20"),
        depreciation=Decimal("20"),
        capex=Decimal("10"),
        working_capital_change=Decimal("10"),
        fcff=fcff,
        fact_refs=(_source("a"),),
        assumption_refs=(),
        input_states=(ModelInputState.REPORTED,),
    )


_FORECAST_DRIVER_KEYS = (
    "revenue",
    "operating_margin",
    "cash_tax_rate",
    "depreciation",
    "capex",
    "working_capital_change",
)


def _overrides(**values: Decimal) -> tuple[ScenarioDriverOverride, ...]:
    return tuple(
        ScenarioDriverOverride(driver_key, values.get(driver_key, Decimal("1")))
        for driver_key in _FORECAST_DRIVER_KEYS
    )


def _scenario_forecasts(source: SourceLineageReference) -> tuple[ScenarioFinancialDriverForecast, ...]:
    values = {
        "revenue": Decimal("200"),
        "operating_margin": Decimal("0.50"),
        "cash_tax_rate": Decimal("0.20"),
        "depreciation": Decimal("20"),
        "capex": Decimal("10"),
        "working_capital_change": Decimal("10"),
    }
    return tuple(
        ScenarioFinancialDriverForecast(
            driver_key=driver_key,
            values=(values[driver_key],) * 5,
            fact_refs=(),
            assumption_refs=(source,),
            input_state=ModelInputState.ASSUMPTION,
            assumption_key=f"engine-candidate.v1:{driver_key}",
            equation_id=None,
        )
        for driver_key in _FORECAST_DRIVER_KEYS
    )


def _input(*, market: bool = True, gaps: tuple[ResearchGap, ...] = ()) -> CompanyResearchModelInput:
    source_by_key = {
        key: _source(key)
        for key in (
            "a",
            "b",
            "c",
            "capital_structure_usd",
            "security_rights_nasdaq_googl",
            "market_price_usd_nasdaq_googl",
            "security_rights_nasdaq_goog",
            "market_price_usd_nasdaq_goog",
            "usd_cny_fx",
            "evidence_gap_contract",
        )
    }
    sources = tuple(source_by_key.values())
    business_map = BusinessMapArtifact(
        modules=(
            BusinessModuleArtifact(
                module_key="search_and_other_ads",
                revenue_sources=("query advertising",),
                cost_structure=("traffic acquisition",),
                capital_needs=("data centers",),
                fact_refs=(source_by_key["a"],),
                gap_refs=(),
                classified_evidence=(
                    ClassifiedBusinessEvidenceArtifact(
                        fact_ref=source_by_key["a"],
                        metric_key="revenue",
                        category="revenue",
                        value=Decimal("200"),
                        currency="USD",
                        unit="million",
                        period_start="2025-01-01",
                        period_end="2025-12-31",
                    ),
                ),
            ),
        )
    )
    equations = {
        "revenue": "revenue = volume * monetization",
        "operating_margin": "operating_income = revenue * operating_margin",
        "cash_tax_rate": "cash_tax_rate = reported_tax_rate",
        "depreciation": "depreciation = reported_depreciation",
        "capex": "capex = reported_capex",
        "working_capital_change": "working_capital_change = reported_working_capital_change",
    }
    drivers = DriverMapArtifact(
        drivers=tuple(
            DriverMetricArtifact(
                driver_key=driver_key,
                module_key="search_and_other_ads",
                fact_refs=(),
                assumption_refs=(source_by_key["c"],),
                equation=equations[driver_key],
                output_metric=driver_key,
                input_state=ModelInputState.ASSUMPTION,
                assumption_key=f"engine-candidate.v1:{driver_key}",
                equation_id=None,
                values=next(
                    item.values
                    for item in _scenario_forecasts(source_by_key["c"])
                    if item.driver_key == driver_key
                ),
            )
            for driver_key in _FORECAST_DRIVER_KEYS
        )
    )
    scenarios = ScenarioSetArtifact(
        scenarios=(
            ScenarioArtifact(
                scenario_id="base",
                mechanism_id="search_cloud_resilience",
                driver_overrides=_overrides(),
            ),
            ScenarioArtifact(
                scenario_id="bull",
                mechanism_id="ai_monetization_and_utilization",
                driver_overrides=_overrides(
                    revenue=Decimal("1.10"),
                    operating_margin=Decimal("1.05"),
                    capex=Decimal("0.95"),
                    working_capital_change=Decimal("0.90"),
                ),
            ),
            ScenarioArtifact(
                scenario_id="bear",
                mechanism_id="search_disruption_and_capital_drag",
                driver_overrides=_overrides(
                    revenue=Decimal("0.90"),
                    operating_margin=Decimal("0.90"),
                    capex=Decimal("1.10"),
                    working_capital_change=Decimal("1.20"),
                ),
            ),
        )
    )
    bridges = tuple(
        ScenarioFinancialBridge(
            scenario_id=scenario.scenario_id,
            first_fiscal_year=2026,
            driver_forecasts=_scenario_forecasts(source_by_key["c"]),
        )
        for scenario in scenarios.scenarios
    )
    return CompanyResearchModelInput(
        business_map=business_map,
        driver_map=drivers,
        scenario_set=scenarios,
        scenario_bridges=bridges,
        source_lineage=sources,
        research_gaps=gaps,
        evidence_gap_contract=EvidenceGapContract(
            source_ref=source_by_key["evidence_gap_contract"], gaps=gaps
        ),
        required_return=Decimal("0.12"),
        terminal_growth=Decimal("0.03"),
        market_bridge=(
            MarketBridgeArtifact(
                capital_structure=CapitalStructureReference(
                    cash=Decimal("10"), debt=Decimal("5"), minority_interest=Decimal("0"),
                    investments=Decimal("2"), pension_liabilities=Decimal("0"),
                    other_adjustments=Decimal("0"), source_ref=source_by_key["capital_structure_usd"],
                ),
                securities=(
                    SecurityValuationReference("NASDAQ:GOOGL", Decimal("10"), Decimal("100"), Decimal("7.20"), source_by_key["security_rights_nasdaq_googl"], source_by_key["market_price_usd_nasdaq_googl"]),
                    SecurityValuationReference("NASDAQ:GOOG", Decimal("10"), Decimal("100"), Decimal("7.20"), source_by_key["security_rights_nasdaq_goog"], source_by_key["market_price_usd_nasdaq_goog"]),
                ),
                usd_cny_rate=Decimal("7.20"), fx_ref=source_by_key["usd_cny_fx"],
            )
            if market
            else None
        ),
        judgment_context=JudgmentContextArtifact(
            operating_baseline_available=True,
            financial_bridge_closed=True,
            market_security_bridge_available=market,
            strongest_counterevidence=(source_by_key["a"],),
            next_verification_events=("next filing",),
        ),
    )


def test_compiles_exact_lineage_closed_financials_and_ordered_mechanism_value_ranges() -> None:
    result = CompanyResearchEngine().compile(_input())

    assert result.assessment == CompanyResearchAssessment.answerable()
    assert set(result.scenario_enterprise_values) == {"base", "bull", "bear"}
    assert result.reverse_dcf is None
    assert all(isinstance(item.value_range, ValueRange) for item in result.security_values)
    assert [item.security_external_key for item in result.security_values] == ["NASDAQ:GOOG", "NASDAQ:GOOGL"]
    assert all(item.value_range.minimum <= item.value_range.maximum for item in result.security_values)
    assert not hasattr(result.security_values[0].value_range, "probability")


def test_rejects_market_references_reused_across_capital_rights_price_and_fx_roles() -> None:
    """Each market number must retain a distinct, role-specific source fact."""
    model = _input()
    market = model.market_bridge
    assert market is not None
    with pytest.raises(CompanyResearchValidationError, match="market bridge references.*role"):
        MarketBridgeArtifact(
            capital_structure=market.capital_structure,
            securities=tuple(
                replace(security, rights_ref=market.capital_structure.source_ref)
                for security in market.securities
            ),
            usd_cny_rate=market.usd_cny_rate,
            fx_ref=market.fx_ref,
        )


def test_mechanism_scenarios_transmit_named_financial_drivers_into_distinct_dcf_values() -> None:
    result = CompanyResearchEngine().compile(_input())

    # The input forecast is common.  Each mechanism changes named financial
    # drivers (revenue, margin, capex, and working capital), which the engine
    # compiles into a separate closed FCFF bridge before discounting it.
    assert result.scenario_enterprise_values["bull"] > result.scenario_enterprise_values["base"]
    assert result.scenario_enterprise_values["bear"] < result.scenario_enterprise_values["base"]


def test_derived_scenario_financial_bridges_close_each_year_without_generic_multiplier() -> None:
    bridges = CompanyResearchEngine._validate_model_links(_input())
    base = bridges["base"].rows[0]
    bull = bridges["bull"].rows[0]

    assert bull.revenue == Decimal("220")
    assert bull.operating_income == Decimal("115.5000")
    assert bull.capex == Decimal("9.50")
    assert bull.working_capital_change == Decimal("9.00")
    assert bull.fcff == Decimal("93.900000")
    assert bull.fcff != base.fcff * Decimal("1.10")
    for bridge in bridges.values():
        for row in bridge.rows:
            assert row.fcff == (
                row.operating_income * (Decimal("1") - row.cash_tax_rate)
                + row.depreciation
                - row.capex
                - row.working_capital_change
            )


def test_high_precision_scenario_and_dcf_results_ignore_caller_decimal_context() -> None:
    """The audited calculation precision is fixed, not inherited from callers."""
    model = _input()
    precise_values = {
        "revenue": Decimal("200.123456789012345678901234567890123456789012345678901234567890"),
        "operating_margin": Decimal("0.501234567890123456789012345678901234567890123456789012345678"),
        "cash_tax_rate": Decimal("0.201234567890123456789012345678901234567890123456789012345678"),
        "depreciation": Decimal("20.123456789012345678901234567890123456789012345678901234567890"),
        "capex": Decimal("10.123456789012345678901234567890123456789012345678901234567890"),
        "working_capital_change": Decimal("10.123456789012345678901234567890123456789012345678901234567890"),
    }
    precise_bridges = tuple(
        replace(
            bridge,
            driver_forecasts=tuple(
                replace(forecast, values=(precise_values[forecast.driver_key],) * 5)
                for forecast in bridge.driver_forecasts
            ),
        )
        for bridge in model.scenario_bridges
    )
    precise_model = replace(
        model,
        driver_map=replace(
            model.driver_map,
            drivers=tuple(
                replace(
                    driver,
                    values=(precise_values[driver.driver_key],) * 5,
                )
                for driver in model.driver_map.drivers
            ),
        ),
        scenario_bridges=precise_bridges,
    )

    global_context = getcontext().copy()
    with localcontext() as context:
        context.prec = 28
        low_precision = CompanyResearchEngine().compile(precise_model)
    with localcontext() as context:
        context.prec = 120
        high_precision = CompanyResearchEngine().compile(precise_model)

    assert low_precision.scenario_enterprise_values == high_precision.scenario_enterprise_values
    assert low_precision.valuation_set == high_precision.valuation_set
    assert getcontext().prec == global_context.prec
    assert getcontext().rounding == global_context.rounding


def test_rejects_counterevidence_outside_authenticated_source_lineage() -> None:
    model = _input()
    counterevidence = _source("counterevidence")

    with pytest.raises(ValidationError, match="source lineage"):
        CompanyResearchEngine().compile(
            replace(
                model,
                judgment_context=replace(
                    model.judgment_context,
                    strongest_counterevidence=(counterevidence,),
                ),
            )
        )


def test_rejects_a_non_base_scenario_with_a_no_op_override() -> None:
    model = _input()
    no_op_bull = ScenarioArtifact(
        scenario_id="bull",
        mechanism_id="ai_monetization_and_utilization",
        driver_overrides=_overrides(),
    )
    scenario_set = ScenarioSetArtifact(
        scenarios=tuple(
            no_op_bull if scenario.scenario_id == "bull" else scenario
            for scenario in model.scenario_set.scenarios
        )
    )

    with pytest.raises(ValidationError, match="distinct mechanism-specific financial forecasts"):
        CompanyResearchEngine().compile(replace(model, scenario_set=scenario_set))


def test_rejects_same_financial_forecast_plus_generic_fcff_multiplier() -> None:
    """A mechanism must transmit through financial drivers, never a DCF multiplier."""
    model = _input()
    generic_multiplier_bull = ScenarioArtifact(
        scenario_id="bull",
        mechanism_id="ai_monetization_and_utilization",
        driver_overrides=(ScenarioDriverOverride("fcff_multiplier", Decimal("1.10")),),
    )
    scenario_set = ScenarioSetArtifact(
        scenarios=tuple(
            generic_multiplier_bull if scenario.scenario_id == "bull" else scenario
            for scenario in model.scenario_set.scenarios
        )
    )

    with pytest.raises(ValidationError, match="named financial forecast driver"):
        CompanyResearchEngine().compile(replace(model, scenario_set=scenario_set))


def test_rejects_dropping_source_declared_evidence_gaps_from_model_input() -> None:
    source_declared_gaps = (
        ResearchGap("market_price_missing", "corporate_capital_allocation", ResearchGapSeverity.CRITICAL, "price"),
        ResearchGap("usd_cny_fx_missing", "corporate_capital_allocation", ResearchGapSeverity.CRITICAL, "FX"),
        ResearchGap("forward_model_missing", "corporate_capital_allocation", ResearchGapSeverity.CRITICAL, "forecast"),
    )
    model = _input(gaps=source_declared_gaps)

    with pytest.raises(CompanyResearchValidationError, match="evidence gap contract"):
        replace(model, research_gaps=())

    result = CompanyResearchEngine().compile(model)
    assert result.assessment == CompanyResearchAssessment.not_answerable()


def test_rejects_source_reference_not_in_evidence_lineage() -> None:
    model = _input()
    bad_ref = SourceLineageReference(
        "z",
        "regulatory_filing",
        "https://www.sec.gov/example",
        "Item 7 / z",
        "f" * 64,
    )
    bad_module = BusinessModuleArtifact(
        module_key="search_and_other_ads", revenue_sources=("query",), cost_structure=("tac",),
        capital_needs=("servers",), fact_refs=(bad_ref,), gap_refs=(),
        classified_evidence=(
            ClassifiedBusinessEvidenceArtifact(
                bad_ref,
                "revenue",
                "revenue",
                Decimal("1"),
                "USD",
                "million",
                "2025-01-01",
                "2025-12-31",
            ),
        ),
    )
    with pytest.raises(ValidationError, match="source lineage"):
        CompanyResearchEngine().compile(
            replace(model, business_map=BusinessMapArtifact(modules=(bad_module,)))
        )


def test_rejects_a_financial_bridge_that_does_not_close() -> None:
    with pytest.raises(CompanyResearchValidationError, match="financial bridge does not close"):
        _row(2026, fcff=Decimal("79"))


def test_rejects_non_distinct_alphabet_mechanisms_and_terminal_growth_boundary() -> None:
    model = _input()
    duplicate = ScenarioSetArtifact(
        scenarios=tuple(
            ScenarioArtifact(s.scenario_id, "search_cloud_resilience", s.driver_overrides)
            for s in model.scenario_set.scenarios
        )
    )
    with pytest.raises(ValidationError, match="distinct mechanisms"):
        CompanyResearchEngine().compile(replace(model, scenario_set=duplicate))
    with pytest.raises(ValidationError, match="terminal growth"):
        CompanyResearchEngine().compile(replace(model, terminal_growth=Decimal("0.12")))


def test_reverse_dcf_uses_deterministic_bisection_and_reports_residual() -> None:
    model = _input()
    first = CompanyResearchEngine().compile(
        replace(model, reverse_dcf=ReverseDcfRequest("fcff_multiplier", Decimal("500"), Decimal("0.5"), Decimal("2.0"), 80))
    )
    second = CompanyResearchEngine().compile(
        replace(model, reverse_dcf=ReverseDcfRequest("fcff_multiplier", Decimal("500"), Decimal("0.5"), Decimal("2.0"), 80))
    )
    assert first.reverse_dcf == second.reverse_dcf
    assert first.reverse_dcf is not None
    assert first.reverse_dcf.iteration_count <= 80
    assert abs(first.reverse_dcf.achieved_residual) <= Decimal("0.000001")


def test_reverse_dcf_rejects_non_integer_iteration_count_as_domain_validation() -> None:
    with pytest.raises(CompanyResearchValidationError, match="iteration count"):
        ReverseDcfRequest(
            "fcff_multiplier", Decimal("500"), Decimal("0.5"), Decimal("2.0"), "80"  # type: ignore[arg-type]
        )


def test_reverse_dcf_fails_closed_when_iteration_budget_does_not_converge() -> None:
    model = _input()
    with pytest.raises(ValidationError, match="did not converge"):
        CompanyResearchEngine().compile(
            replace(
                model,
                reverse_dcf=ReverseDcfRequest(
                    "fcff_multiplier",
                    Decimal("500"),
                    Decimal("0.5"),
                    Decimal("2.0"),
                    1,
                ),
            )
        )


def test_financial_bridge_row_requires_typed_separate_provenance() -> None:
    row = _row(2026)
    assumed = replace(
        row,
        fact_refs=(),
        assumption_refs=(_source("c"),),
        input_states=(ModelInputState.ASSUMPTION,),
    )
    assert assumed.fact_refs == ()
    assert assumed.assumption_refs == (_source("c"),)
    with pytest.raises(CompanyResearchValidationError, match="provenance"):
        replace(row, fact_refs=(), assumption_refs=())
    with pytest.raises(CompanyResearchValidationError, match="state"):
        replace(row, input_states=(ModelInputState.ASSUMPTION,))


def test_business_module_fact_refs_are_classified_exactly_once() -> None:
    source = _source("a")
    evidence = ClassifiedBusinessEvidenceArtifact(
        fact_ref=source,
        metric_key="revenue",
        category="revenue",
        value=Decimal("200"),
        currency="USD",
        unit="million",
        period_start="2025-01-01",
        period_end="2025-12-31",
    )
    module = BusinessModuleArtifact(
        module_key="synthetic_unit",
        revenue_sources=("revenue",),
        cost_structure=("cost",),
        capital_needs=("capital",),
        fact_refs=(source,),
        gap_refs=(),
        classified_evidence=(evidence,),
    )
    assert module.classified_evidence == (evidence,)
    with pytest.raises(CompanyResearchValidationError, match="exactly once"):
        replace(module, classified_evidence=())
    with pytest.raises(CompanyResearchValidationError, match="exactly once"):
        replace(module, classified_evidence=(evidence, evidence))


def test_rejects_scenario_baselines_with_equal_values_but_different_source_provenance() -> None:
    model = _input()
    alternate_source = _source("alternative_forecast_source")
    bull_bridge = next(
        bridge for bridge in model.scenario_bridges if bridge.scenario_id == "bull"
    )
    altered_bull = replace(
        bull_bridge,
        driver_forecasts=tuple(
            replace(forecast, assumption_refs=(alternate_source,))
            for forecast in bull_bridge.driver_forecasts
        ),
    )

    with pytest.raises(ValidationError, match="provenance"):
        CompanyResearchEngine().compile(
            replace(
                model,
                source_lineage=model.source_lineage + (alternate_source,),
                scenario_bridges=tuple(
                    altered_bull if bridge.scenario_id == "bull" else bridge
                    for bridge in model.scenario_bridges
                ),
            )
        )


def test_rejects_forecast_provenance_that_disagrees_with_driver_state() -> None:
    model = _input()
    reported_ref = _source("reported_forecast")
    bridges = tuple(
        replace(
            bridge,
            driver_forecasts=tuple(
                replace(
                    forecast,
                    fact_refs=(reported_ref,),
                    assumption_refs=(),
                    input_state=ModelInputState.REPORTED,
                    assumption_key=None,
                )
                if forecast.driver_key == "revenue"
                else forecast
                for forecast in bridge.driver_forecasts
            ),
        )
        for bridge in model.scenario_bridges
    )

    with pytest.raises(ValidationError, match="provenance"):
        CompanyResearchEngine().compile(
            replace(
                model,
                source_lineage=model.source_lineage + (reported_ref,),
                scenario_bridges=bridges,
            )
        )


def test_valuation_output_exposes_required_return_comparisons_without_probabilities() -> None:
    result = CompanyResearchEngine().compile(_input())
    valuation = result.valuation_set
    assert valuation is not None

    assert valuation.required_return == Decimal("0.12")
    comparisons = valuation.required_return_comparisons
    assert [comparison.security_external_key for comparison in comparisons] == [
        "NASDAQ:GOOG",
        "NASDAQ:GOOGL",
    ]
    assert all(
        comparison.achieved_return_range.minimum
        <= comparison.achieved_return_range.maximum
        for comparison in comparisons
    )
    assert all(type(comparison.meets_required_return) is bool for comparison in comparisons)
    assert comparisons[0].canonical_payload() == {
        "security_external_key": "NASDAQ:GOOG",
        "required_return": "0.12",
        "achieved_return_range": comparisons[0].achieved_return_range.canonical_payload(),
        "meets_required_return": comparisons[0].meets_required_return,
    }
    assert valuation.canonical_payload() == {
        "required_return": "0.12",
        "required_return_comparisons": tuple(
            comparison.canonical_payload() for comparison in comparisons
        ),
    }
    forbidden_probability_fields = {
        "probability",
        "odds",
        "expected_value",
        "scenario_weight",
        "weight",
    }
    assert forbidden_probability_fields.isdisjoint(comparisons[0].canonical_payload())
    assert forbidden_probability_fields.isdisjoint(valuation.canonical_payload())


def test_missing_market_or_critical_gap_is_not_answerable_without_direction_or_confidence() -> None:
    result = CompanyResearchEngine().compile(_input(market=False))
    assert result.assessment.status == "not_answerable"
    assert result.assessment.direction is None
    assert result.assessment.confidence is None

    result = CompanyResearchEngine().compile(
        _input(gaps=(ResearchGap("critical_gap", "business_map", ResearchGapSeverity.CRITICAL, "verify"),))
    )
    assert result.assessment.status == "not_answerable"


def test_missing_operating_baseline_is_not_answerable_even_when_market_is_present() -> None:
    model = _input()
    result = CompanyResearchEngine().compile(
        replace(
            model,
            judgment_context=replace(
                model.judgment_context, operating_baseline_available=False
            ),
        )
    )
    assert result.assessment == CompanyResearchAssessment.not_answerable()


def test_noncritical_gap_makes_an_otherwise_closed_model_partially_answerable() -> None:
    result = CompanyResearchEngine().compile(
        _input(gaps=(ResearchGap("review_gap", "business_map", ResearchGapSeverity.HIGH, "review"),))
    )
    assert result.assessment.status == "partially_answerable"
    assert result.assessment.direction == "provisional_neutral"


def test_decimal_boundaries_are_decimal_only_and_serialize_canonically() -> None:
    assert canonical_decimal_string(Decimal("1.230000000000")) == "1.23"
    assert canonical_decimal_string(Decimal("-0.000000000001")) == "-0.000000000001"
    with pytest.raises(CompanyResearchValidationError, match="Decimal"):
        FinancialBridgeRow(
            fiscal_year=2026, revenue=Decimal("1"), operating_income=1.0,
            cash_tax_rate=Decimal("0"), depreciation=Decimal("0"), capex=Decimal("0"),
            working_capital_change=Decimal("0"), fcff=Decimal("0"), fact_refs=(_source("a"),),
            assumption_refs=(),
            input_states=(ModelInputState.REPORTED,),
        )


def test_valuation_canonical_payload_and_hash_ignore_caller_decimal_context() -> None:
    valuation = CompanyResearchEngine().compile(_input()).valuation_set
    assert valuation is not None
    high_precision_return = ValueRange(
        Decimal("0.123456789012345678901234567890123456789012345678901234567890"),
        Decimal("0.223456789012345678901234567890123456789012345678901234567890"),
    )
    first_range = valuation.security_value_ranges[0]
    first_comparison = valuation.required_return_comparisons[0]
    valuation = replace(
        valuation,
        security_value_ranges=(
            replace(first_range, cny_return=high_precision_return),
            *valuation.security_value_ranges[1:],
        ),
        required_return_comparisons=(
            replace(
                first_comparison,
                achieved_return_range=high_precision_return,
                meets_required_return=True,
            ),
            *valuation.required_return_comparisons[1:],
        ),
    )

    global_context = getcontext().copy()
    with localcontext() as context:
        context.prec = 5
        low_precision_payload = valuation.canonical_payload()
        low_precision_hash = canonical_hash(low_precision_payload)
    with localcontext() as context:
        context.prec = 120
        high_precision_payload = valuation.canonical_payload()
        high_precision_hash = canonical_hash(high_precision_payload)

    assert low_precision_payload == high_precision_payload
    assert low_precision_hash == high_precision_hash
    assert getcontext().prec == global_context.prec
    assert getcontext().rounding == global_context.rounding


@pytest.mark.parametrize("rounding", (ROUND_UP, ROUND_DOWN))
def test_canonical_decimal_serialization_ignores_restricted_caller_context(
    rounding: str,
) -> None:
    """Canonical artifacts retain every legal Decimal digit and exponent."""
    values = (
        Decimal("1234567890123456789012345678901234567890123456789012345678901234567890"),
        Decimal("1.2300E+999999"),
        Decimal("-1.2300E-999999"),
    )
    expected = (
        "1234567890123456789012345678901234567890123456789012345678901234567890",
        "123" + "0" * 999997,
        "-0." + "0" * 999998 + "123",
    )

    with localcontext() as caller_context:
        caller_context.prec = 2
        caller_context.rounding = rounding
        caller_context.Emin = -2
        caller_context.Emax = 2
        caller_context.clamp = 1
        for signal in caller_context.traps:
            caller_context.traps[signal] = True
        before = caller_context.copy()

        serialized = tuple(canonical_decimal_string(value) for value in values)
        payload_hash = canonical_hash({"values": serialized})

        assert serialized == expected
        assert payload_hash == canonical_hash({"values": expected})
        assert getcontext().prec == before.prec
        assert getcontext().rounding == before.rounding
        assert getcontext().Emin == before.Emin
        assert getcontext().Emax == before.Emax
        assert getcontext().clamp == before.clamp
        assert getcontext().flags == before.flags
        assert getcontext().traps == before.traps


def test_valuation_rejects_comparison_with_nonmatching_security_return_range() -> None:
    valuation = CompanyResearchEngine().compile(_input()).valuation_set
    assert valuation is not None
    first_comparison = valuation.required_return_comparisons[0]
    mismatched_comparison = replace(
        first_comparison,
        achieved_return_range=ValueRange(Decimal("0.13"), Decimal("0.23")),
        meets_required_return=True,
    )

    with pytest.raises(CompanyResearchValidationError, match="match security return ranges"):
        ValuationSetArtifact(
            scenario_dcf_values=valuation.scenario_dcf_values,
            reverse_dcf=valuation.reverse_dcf,
            security_value_ranges=valuation.security_value_ranges,
            required_return=valuation.required_return,
            required_return_comparisons=(
                mismatched_comparison,
                *valuation.required_return_comparisons[1:],
            ),
        )


def test_valuation_rejects_duplicate_required_return_comparison_for_a_security() -> None:
    valuation = CompanyResearchEngine().compile(_input()).valuation_set
    assert valuation is not None

    with pytest.raises(CompanyResearchValidationError, match="cover each security exactly once"):
        ValuationSetArtifact(
            scenario_dcf_values=valuation.scenario_dcf_values,
            reverse_dcf=valuation.reverse_dcf,
            security_value_ranges=valuation.security_value_ranges,
            required_return=valuation.required_return,
            required_return_comparisons=(
                *valuation.required_return_comparisons,
                valuation.required_return_comparisons[0],
            ),
        )


def test_negative_cash_and_debt_adjustments_remain_exact_decimal_inputs() -> None:
    model = _input()
    market = model.market_bridge
    assert market is not None
    result = CompanyResearchEngine().compile(
        replace(
            model,
            market_bridge=replace(
                market,
                capital_structure=replace(
                    market.capital_structure, cash=Decimal("-1.25"), debt=Decimal("-2.50")
                ),
            ),
        )
    )
    assert result.valuation_set is not None


@pytest.mark.parametrize("shares", (Decimal("0"), Decimal("-1")))
def test_rejects_zero_or_negative_security_shares(shares: Decimal) -> None:
    model = _input()
    market = model.market_bridge
    assert market is not None
    first = market.securities[0]
    with pytest.raises(CompanyResearchValidationError, match="share_count"):
        SecurityValuationReference(first.security_external_key, shares, first.market_price_usd, first.usd_cny_rate, first.rights_ref, first.price_ref)
