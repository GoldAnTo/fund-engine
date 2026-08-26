from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    BusinessMapArtifact,
    BusinessModuleArtifact,
    CapitalStructureReference,
    CompanyResearchAssessment,
    CompanyResearchModelInput,
    CompanyResearchValidationError,
    canonical_decimal_string,
    DriverMapArtifact,
    DriverMetricArtifact,
    FinancialBridgeArtifact,
    FinancialBridgeRow,
    JudgmentContextArtifact,
    MarketBridgeArtifact,
    ResearchGap,
    ResearchGapSeverity,
    ReverseDcfRequest,
    ScenarioArtifact,
    ScenarioDriverOverride,
    ScenarioFinancialBridge,
    ScenarioSetArtifact,
    SecurityValuationReference,
    SourceLineageReference,
    ValueRange,
)
from app.underwriting.services.company_research_engine import CompanyResearchEngine


def _source(key: str) -> SourceLineageReference:
    return SourceLineageReference(
        fact_key=key,
        source_role="regulatory_filing",
        source_url="https://www.sec.gov/example",
        source_locator=f"Item 7 / {key}",
        raw_hash=(key[0] * 64),
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
    )


def _input(*, market: bool = True, gaps: tuple[ResearchGap, ...] = ()) -> CompanyResearchModelInput:
    sources = (_source("a"), _source("b"), _source("c"), _source("d"))
    business_map = BusinessMapArtifact(
        modules=(
            BusinessModuleArtifact(
                module_key="search_and_other_ads",
                revenue_sources=("query advertising",),
                cost_structure=("traffic acquisition",),
                capital_needs=("data centers",),
                fact_refs=(sources[0],),
                gap_refs=(),
            ),
        )
    )
    drivers = DriverMapArtifact(
        drivers=(
            DriverMetricArtifact(
                driver_key="search_volume",
                module_key="search_and_other_ads",
                fact_refs=(sources[1],),
                assumption_refs=(sources[2],),
                equation="revenue = volume * monetization",
                output_metric="revenue",
            ),
        )
    )
    scenarios = ScenarioSetArtifact(
        scenarios=(
            ScenarioArtifact(
                scenario_id="base",
                mechanism_id="search_cloud_resilience",
                driver_overrides=(ScenarioDriverOverride("search_volume", Decimal("1.00")),),
            ),
            ScenarioArtifact(
                scenario_id="bull",
                mechanism_id="ai_monetization_and_utilization",
                driver_overrides=(ScenarioDriverOverride("search_volume", Decimal("1.10")),),
            ),
            ScenarioArtifact(
                scenario_id="bear",
                mechanism_id="search_disruption_and_capital_drag",
                driver_overrides=(ScenarioDriverOverride("search_volume", Decimal("0.90")),),
            ),
        )
    )
    bridges = tuple(
        ScenarioFinancialBridge(
            scenario_id=scenario.scenario_id,
            bridge=FinancialBridgeArtifact(rows=tuple(_row(2026 + index) for index in range(5))),
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
        required_return=Decimal("0.12"),
        terminal_growth=Decimal("0.03"),
        market_bridge=(
            MarketBridgeArtifact(
                capital_structure=CapitalStructureReference(
                    cash=Decimal("10"), debt=Decimal("5"), minority_interest=Decimal("0"),
                    investments=Decimal("2"), pension_liabilities=Decimal("0"),
                    other_adjustments=Decimal("0"), source_ref=sources[3],
                ),
                securities=(
                    SecurityValuationReference("NASDAQ:GOOGL", Decimal("10"), Decimal("100"), Decimal("7.20"), sources[3], sources[3]),
                    SecurityValuationReference("NASDAQ:GOOG", Decimal("10"), Decimal("100"), Decimal("7.20"), sources[3], sources[3]),
                ),
                usd_cny_rate=Decimal("7.20"), fx_ref=sources[3],
            )
            if market
            else None
        ),
        judgment_context=JudgmentContextArtifact(
            operating_baseline_available=True,
            financial_bridge_closed=True,
            market_security_bridge_available=market,
            strongest_counterevidence=(sources[0],),
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


def test_rejects_source_reference_not_in_evidence_lineage() -> None:
    model = _input()
    bad_module = BusinessModuleArtifact(
        module_key="search_and_other_ads", revenue_sources=("query",), cost_structure=("tac",),
        capital_needs=("servers",), fact_refs=(SourceLineageReference("z", "regulatory_filing", "https://www.sec.gov/example", "Item 7 / z", "f" * 64),), gap_refs=(),
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
