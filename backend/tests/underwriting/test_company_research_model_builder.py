from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from uuid import UUID

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CapitalStructureReference,
    CompanyResearchValidationError,
    DriverInput,
    MarketBridgeArtifact,
    ModelInputState,
    ReverseDcfRequest,
    ScenarioDriverOverride,
    SecurityValuationReference,
    SourceLineageReference,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchDriverBinding,
    CompanyResearchBuildInput,
    CompanyResearchMetricClassification,
    CompanyResearchModelModule,
    CompanyResearchModelTemplate,
    CompanyResearchModelBuilder,
    CompanyResearchOperatingDriverBinding,
    CompanyResearchOperatingBaselineRequirement,
    CompanyResearchScenarioMechanism,
    FrozenMarketContext,
    FrozenMarketSnapshotBinding,
    FrozenMarketSnapshotRole,
    ScenarioAssumption,
    StrategyAssumptionSet,
)
from app.underwriting.services.company_research_sources import (
    CompanyResearchSourceCompiler,
)


CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)


def _lineage(key: str, role: str = "strategy_assumption") -> SourceLineageReference:
    return SourceLineageReference(
        fact_key=key,
        source_role=role,
        source_url=f"urn:company-research:{role}",
        source_locator=key,
        raw_hash=hashlib.sha256(key.encode()).hexdigest(),
    )


def _driver_paths() -> tuple[DriverInput, ...]:
    values = {
        "revenue": ("410000", "440000", "472000", "505000", "540000"),
        "operating_margin": ("0.31", "0.315", "0.32", "0.325", "0.33"),
        "cash_tax_rate": ("0.17", "0.17", "0.175", "0.175", "0.18"),
        "depreciation": ("26000", "30000", "34000", "38000", "42000"),
        "capex": ("180000", "165000", "145000", "125000", "110000"),
        "working_capital_change": ("5000", "5200", "5400", "5600", "5800"),
    }
    return tuple(
        DriverInput(
            driver_key=key,
            state=ModelInputState.ASSUMPTION,
            values=tuple(Decimal(item) for item in path),
            source_refs=(),
            assumption_key=f"alphabet-candidate.v1:{key}",
        )
        for key, path in values.items()
    )


def _overrides(**changes: str) -> tuple[ScenarioDriverOverride, ...]:
    return tuple(
        ScenarioDriverOverride(
            driver_key=key,
            value=Decimal(changes.get(key, "1")),
        )
        for key in (
            "revenue",
            "operating_margin",
            "cash_tax_rate",
            "depreciation",
            "capex",
            "working_capital_change",
        )
    )


def _strategy_assumptions() -> StrategyAssumptionSet:
    driver_paths = _driver_paths()
    scenarios = (
        ScenarioAssumption(
            "base", "steady_operations", _overrides()
        ),
        ScenarioAssumption(
            "bull",
            "capacity_upside",
            _overrides(
                revenue="1.10",
                operating_margin="1.05",
                capex="0.95",
                working_capital_change="0.90",
            ),
        ),
        ScenarioAssumption(
            "bear",
            "demand_stress",
            _overrides(
                revenue="0.90",
                operating_margin="0.90",
                capex="1.10",
                working_capital_change="1.20",
            ),
        ),
    )
    content_hash = StrategyAssumptionSet.calculate_content_hash(
        strategy_version="alphabet-candidate.v1",
        first_fiscal_year=2026,
        driver_paths=driver_paths,
        scenario_overrides=scenarios,
        terminal_growth=Decimal("0.03"),
    )
    return StrategyAssumptionSet(
        strategy_version="alphabet-candidate.v1",
        content_hash=content_hash,
        first_fiscal_year=2026,
        driver_paths=driver_paths,
        scenario_overrides=scenarios,
        terminal_growth=Decimal("0.03"),
    )


def _model_template() -> CompanyResearchModelTemplate:
    module_keys = (
        "search_and_other_ads",
        "youtube_ads_and_subscriptions",
        "google_cloud",
        "other_google_services",
        "other_bets",
        "corporate_capital_allocation",
    )
    driver_modules = {
        "revenue": "search_and_other_ads",
        "operating_margin": "google_cloud",
        "cash_tax_rate": "corporate_capital_allocation",
        "depreciation": "corporate_capital_allocation",
        "capex": "corporate_capital_allocation",
        "working_capital_change": "other_google_services",
    }
    return CompanyResearchModelTemplate(
        template_version="synthetic-company-model.v1",
        modules=tuple(
            CompanyResearchModelModule(
                module_key=key,
                revenue_sources=(f"{key} revenue descriptor",),
                cost_structure=(f"{key} cost descriptor",),
                capital_needs=(f"{key} capital descriptor",),
            )
            for key in module_keys
        ),
        metric_classifications=(
            *(
                CompanyResearchMetricClassification(key, "revenue", "revenue")
                for key in module_keys
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "capital_expenditures", "capital"
            ),
            CompanyResearchMetricClassification(
                "corporate_capital_allocation", "operating_expense", "cost"
            ),
        ),
        operating_driver_bindings=(
            CompanyResearchOperatingDriverBinding(
                driver_key="audience_intensity",
                module_key="search_and_other_ads",
                metric_key="revenue",
                input_state=ModelInputState.REPORTED,
                equation_id=None,
            ),
            CompanyResearchOperatingDriverBinding(
                driver_key="infrastructure_intensity",
                module_key="corporate_capital_allocation",
                metric_key="capital_expenditures",
                input_state=ModelInputState.REPORTED,
                equation_id=None,
            ),
        ),
        financial_driver_ownership=tuple(
            CompanyResearchDriverBinding(key, driver_modules[key])
            for key in (
                "revenue",
                "operating_margin",
                "cash_tax_rate",
                "depreciation",
                "capex",
                "working_capital_change",
            )
        ),
        operating_baseline_requirements=(
            CompanyResearchOperatingBaselineRequirement(
                requirement_key="consolidated_revenue",
                module_key="corporate_capital_allocation",
                metric_key="revenue",
            ),
        ),
        scenario_mechanisms=(
            CompanyResearchScenarioMechanism(
                "base", "steady_operations"
            ),
            CompanyResearchScenarioMechanism(
                "bull", "capacity_upside"
            ),
            CompanyResearchScenarioMechanism(
                "bear", "demand_stress"
            ),
        ),
    )
def _market_context() -> FrozenMarketContext:
    refs = {
        key: _lineage(key, "frozen_market_snapshot")
        for key in (
            "capital_structure_usd",
            "security_rights_nasdaq_goog",
            "market_price_usd_nasdaq_goog",
            "security_rights_nasdaq_googl",
            "market_price_usd_nasdaq_googl",
            "usd_cny_fx",
        )
    }
    market_bridge = MarketBridgeArtifact(
        capital_structure=CapitalStructureReference(
            cash=Decimal("126843"),
            debt=Decimal("46547"),
            minority_interest=Decimal("0"),
            investments=Decimal("0"),
            pension_liabilities=Decimal("0"),
            other_adjustments=Decimal("0"),
            source_ref=refs["capital_structure_usd"],
        ),
        securities=(
            SecurityValuationReference(
                "NASDAQ:GOOG",
                Decimal("5800"),
                Decimal("205"),
                Decimal("7.18"),
                refs["security_rights_nasdaq_goog"],
                refs["market_price_usd_nasdaq_goog"],
            ),
            SecurityValuationReference(
                "NASDAQ:GOOGL",
                Decimal("5800"),
                Decimal("204"),
                Decimal("7.18"),
                refs["security_rights_nasdaq_googl"],
                refs["market_price_usd_nasdaq_googl"],
            ),
        ),
        usd_cny_rate=Decimal("7.18"),
        fx_ref=refs["usd_cny_fx"],
    )
    ids = tuple(UUID(int=value) for value in range(1, 7))
    bindings = (
        FrozenMarketSnapshotBinding(
            ids[0], FrozenMarketSnapshotRole.PRICE, "NASDAQ:GOOG",
            refs["market_price_usd_nasdaq_goog"],
        ),
        FrozenMarketSnapshotBinding(
            ids[1], FrozenMarketSnapshotRole.PRICE, "NASDAQ:GOOGL",
            refs["market_price_usd_nasdaq_googl"],
        ),
        FrozenMarketSnapshotBinding(
            ids[2], FrozenMarketSnapshotRole.FX, None, refs["usd_cny_fx"],
        ),
        FrozenMarketSnapshotBinding(
            ids[3], FrozenMarketSnapshotRole.CAPITAL_STRUCTURE, None,
            refs["capital_structure_usd"],
        ),
        FrozenMarketSnapshotBinding(
            ids[4], FrozenMarketSnapshotRole.SECURITY_RIGHTS, "NASDAQ:GOOG",
            refs["security_rights_nasdaq_goog"],
        ),
        FrozenMarketSnapshotBinding(
            ids[5], FrozenMarketSnapshotRole.SECURITY_RIGHTS, "NASDAQ:GOOGL",
            refs["security_rights_nasdaq_googl"],
        ),
    )
    return FrozenMarketContext(
        price_snapshot_ids=(ids[0], ids[1]),
        fx_snapshot_ids=(ids[2],),
        capital_structure_snapshot_id=ids[3],
        security_rights_ids=(ids[4], ids[5]),
        snapshot_ids=ids,
        market_at=CUTOFF,
        market_bridge=market_bridge,
        snapshot_bindings=bindings,
        reverse_dcf_request=ReverseDcfRequest(
            driver_key="fcff_multiplier",
            target_enterprise_value=Decimal("1000000"),
            lower_bound=Decimal("0.01"),
            upper_bound=Decimal("10"),
            max_iterations=100,
        ),
    )


def _build_input() -> CompanyResearchBuildInput:
    fixture = load_alphabet_golden_case_fixture()
    evidence_payload = CompanyResearchSourceCompiler._evidence_payload(fixture)
    evidence_payload = deepcopy(evidence_payload)
    for fact in evidence_payload["facts"]:
        fact["review_decision"] = "confirmed"
    return CompanyResearchBuildInput(
        project_id=UUID(int=100),
        cutoff_at=CUTOFF,
        required_return=Decimal("0.12"),
        evidence_artifact_id=UUID(int=101),
        evidence_content_hash=canonical_hash(evidence_payload),
        evidence_payload=evidence_payload,
        gap_payload=CompanyResearchSourceCompiler._gaps_payload(fixture),
        source_refs=CompanyResearchSourceCompiler._source_refs(fixture),
        model_template=_model_template(),
        strategy_assumptions=_strategy_assumptions(),
        market_context=_market_context(),
    )


def test_builder_rejects_an_evidence_fact_without_review_decision() -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    payload["facts"][0].pop("review_decision")

    with pytest.raises(ValidationError, match="evidence must be reviewed"):
        CompanyResearchModelBuilder().build(
            replace(
                value,
                evidence_payload=payload,
                evidence_content_hash=canonical_hash(payload),
            )
        )


def test_builder_keeps_missing_market_inputs_as_blocking_gaps() -> None:
    result = CompanyResearchModelBuilder().build(
        replace(_build_input(), market_context=None)
    )

    assert result.assessment.status == "not_answerable"
    assert result.valuation_set is None
    assert {gap.code for gap in result.gaps} >= {
        "market_price_missing",
        "usd_cny_fx_missing",
    }
    assert result.financial_bridge.rows[0].revenue == Decimal("410000")


def test_builder_does_not_invent_market_gap_ownership_when_governed_gaps_are_missing() -> None:
    value = _build_input()
    gap_payload = deepcopy(value.gap_payload)
    gap_payload["gaps"] = [
        gap
        for gap in gap_payload["gaps"]
        if gap["gap_key"] != "market_price_missing"
    ]

    with pytest.raises(ValidationError, match="market gap contract"):
        CompanyResearchModelBuilder().build(
            replace(value, gap_payload=gap_payload, market_context=None)
        )


def test_builder_uses_exact_frozen_market_refs_for_both_alphabet_securities() -> None:
    value = _build_input()
    result = CompanyResearchModelBuilder().build(value)

    assert result.valuation_set is not None
    assert {
        item.security_external_key
        for item in result.valuation_set.security_value_ranges
    } == {"NASDAQ:GOOG", "NASDAQ:GOOGL"}
    assert result.market_snapshot_ids == tuple(
        sorted(value.market_context.snapshot_ids, key=str)  # type: ignore[union-attr]
    )


def test_builder_rejects_unknown_evidence_fields() -> None:
    value = _build_input()
    unknown = deepcopy(value.evidence_payload)
    unknown["facts"][0]["invented"] = "no"
    with pytest.raises(ValidationError, match="evidence fields"):
        CompanyResearchModelBuilder().build(
            replace(
                value,
                evidence_payload=unknown,
                evidence_content_hash=canonical_hash(unknown),
            )
        )

def test_strict_contracts_reject_naive_datetimes_duplicates_and_uuid_order() -> None:
    value = _build_input()
    market = value.market_context
    assert market is not None

    with pytest.raises(ValidationError, match="timezone-aware"):
        replace(value, cutoff_at=CUTOFF.replace(tzinfo=None))
    with pytest.raises(ValidationError, match="duplicates"):
        replace(value, source_refs=value.source_refs + (value.source_refs[0],))
    with pytest.raises(ValidationError, match="canonical UUID order"):
        replace(market, price_snapshot_ids=tuple(reversed(market.price_snapshot_ids)))
    with pytest.raises(ValidationError, match="canonical UUID order"):
        replace(market, snapshot_ids=tuple(reversed(market.snapshot_ids)))
    with pytest.raises(ValidationError, match="timezone-aware"):
        replace(market, market_at=CUTOFF.replace(tzinfo=None))


def test_frozen_market_context_requires_one_price_and_rights_ref_per_security() -> None:
    market = _market_context()
    incomplete_ids = tuple(
        sorted(
            (
                market.price_snapshot_ids[0],
                *market.fx_snapshot_ids,
                market.capital_structure_snapshot_id,
                *market.security_rights_ids,
            ),
            key=str,
        )
    )

    with pytest.raises(ValidationError, match="per security"):
        replace(
            market,
            price_snapshot_ids=(market.price_snapshot_ids[0],),
            snapshot_ids=incomplete_ids,
        )


def test_strategy_assumption_set_is_required_hash_addressed_and_never_reported() -> None:
    value = _build_input()
    with pytest.raises(ValidationError, match="strategy assumptions"):
        replace(value, strategy_assumptions=None)  # type: ignore[arg-type]

    assumptions = value.strategy_assumptions
    with pytest.raises(CompanyResearchValidationError, match="versioned assumption_key"):
        replace(
            assumptions.driver_paths[0],
            assumption_key="unversioned-revenue",
        )
    with pytest.raises(CompanyResearchValidationError, match="state=assumption"):
        replace(
            assumptions,
            driver_paths=(
                replace(
                    assumptions.driver_paths[0],
                    state=ModelInputState.REPORTED,
                    source_refs=(_lineage("reported_revenue"),),
                    assumption_key=None,
                ),
                *assumptions.driver_paths[1:],
            ),
        )
    with pytest.raises(ValidationError, match="content hash"):
        replace(assumptions, content_hash="0" * 64)


def test_company_model_template_is_required() -> None:
    with pytest.raises(ValidationError, match="model template"):
        replace(_build_input(), model_template=None)  # type: ignore[arg-type]


def test_company_model_template_requires_revenue_cost_and_capital_classifications() -> None:
    template = _model_template()
    with pytest.raises(ValidationError, match="revenue, cost, and capital"):
        replace(
            template,
            metric_classifications=(template.metric_classifications[0],),
        )


def test_driver_input_states_are_closed_and_missing_values_are_rejected() -> None:
    source = _lineage("reported_revenue", "regulatory_filing")
    reported = DriverInput(
        driver_key="revenue",
        state=ModelInputState.REPORTED,
        values=(Decimal("1"),),
        source_refs=(source,),
        assumption_key=None,
    )
    assert reported.state is ModelInputState.REPORTED

    with pytest.raises(CompanyResearchValidationError, match="source refs"):
        replace(reported, source_refs=())
    with pytest.raises(CompanyResearchValidationError, match="equation"):
        DriverInput(
            driver_key="revenue",
            state=ModelInputState.DERIVED,
            values=(Decimal("1"),),
            source_refs=(source,),
            assumption_key=None,
        )
    with pytest.raises(CompanyResearchValidationError, match="missing values"):
        replace(_driver_paths()[0], values=())


def test_memo_is_a_hash_referenced_machine_candidate() -> None:
    result = CompanyResearchModelBuilder().build(_build_input())

    assert result.memo.candidate_status == "machine_draft"
    assert result.memo.assessment_status == result.assessment.status
    assert result.memo.business_map_ref.artifact_kind == "business_map"
    assert result.memo.driver_map_ref.artifact_kind == "driver_map"
    assert result.memo.financial_bridge_ref.artifact_kind == "financial_bridge"
    assert result.memo.scenario_set_ref.artifact_kind == "scenario_set"
    assert result.memo.valuation_set_ref is not None
    assert all(
        len(reference.content_hash) == 64
        for reference in (
            result.memo.business_map_ref,
            result.memo.driver_map_ref,
            result.memo.financial_bridge_ref,
            result.memo.scenario_set_ref,
            result.memo.valuation_set_ref,
        )
    )


def test_template_preserves_gap_only_modules_and_classifies_capex_as_capital() -> None:
    value = _build_input()
    gap_only_module = CompanyResearchModelModule(
        module_key="distribution_risk",
        revenue_sources=("distribution revenue descriptor",),
        cost_structure=("distribution cost descriptor",),
        capital_needs=("distribution capital descriptor",),
    )
    gap_payload = deepcopy(value.gap_payload)
    gap_payload["gaps"].append(
        {
            "gap_key": "distribution_evidence_missing",
            "business_module": "distribution_risk",
            "reason": "No reviewed distribution evidence is available.",
        }
    )

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            model_template=replace(
                value.model_template,
                modules=(*value.model_template.modules, gap_only_module),
            ),
            gap_payload=gap_payload,
            market_context=None,
        )
    )

    modules = {item.module_key: item for item in result.business_map.modules}
    assert "distribution_risk" in modules
    assert modules["distribution_risk"].gap_refs == (
        "distribution_evidence_missing",
    )
    assert modules["distribution_risk"].fact_refs == ()
    corporate = modules["corporate_capital_allocation"]
    assert "capital_expenditures" not in corporate.revenue_sources
    assert corporate.revenue_sources
    assert corporate.cost_structure
    assert corporate.capital_needs


def test_generic_builder_consumes_fully_synthetic_module_vocabulary() -> None:
    value = _build_input()
    original = value.model_template
    module_map = {
        module.module_key: f"synthetic_unit_{index}"
        for index, module in enumerate(original.modules, start=1)
    }
    template = replace(
        original,
        modules=tuple(
            replace(module, module_key=module_map[module.module_key])
            for module in original.modules
        ),
        metric_classifications=tuple(
            replace(item, module_key=module_map[item.module_key])
            for item in original.metric_classifications
        ),
        operating_driver_bindings=tuple(
            replace(binding, module_key=module_map[binding.module_key])
            for binding in original.operating_driver_bindings
        ),
        financial_driver_ownership=tuple(
            replace(binding, module_key=module_map[binding.module_key])
            for binding in original.financial_driver_ownership
        ),
        operating_baseline_requirements=tuple(
            replace(requirement, module_key=module_map[requirement.module_key])
            for requirement in original.operating_baseline_requirements
        ),
    )
    evidence = deepcopy(value.evidence_payload)
    for fact in evidence["facts"]:
        fact["business_module"] = module_map[fact["business_module"]]
    gaps = deepcopy(value.gap_payload)
    for gap in gaps["gaps"]:
        gap["business_module"] = module_map[gap["business_module"]]

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            model_template=template,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            gap_payload=gaps,
        )
    )

    assert {module.module_key for module in result.business_map.modules} == set(
        module_map.values()
    )
    assert {
        driver.module_key for driver in result.driver_map.drivers
    } <= set(module_map.values())


def test_template_driver_bindings_and_candidate_provenance_survive_build() -> None:
    value = _build_input()
    result = CompanyResearchModelBuilder().build(value)
    expected_modules = {
        item.driver_key: item.module_key
        for item in value.model_template.financial_driver_ownership
    }

    assert {
        item.driver_key: item.module_key
        for item in result.driver_map.drivers
        if item.driver_key in expected_modules
    } == expected_modules
    assert all(
        item.input_state is ModelInputState.ASSUMPTION
        and item.assumption_key is not None
        and item.equation_id is None
        and item.fact_refs == ()
        and item.assumption_refs
        for item in result.driver_map.drivers
        if item.driver_key in expected_modules
    )


def test_confirmed_numeric_facts_build_nonfinancial_operating_drivers() -> None:
    result = CompanyResearchModelBuilder().build(_build_input())
    drivers = {item.driver_key: item for item in result.driver_map.drivers}

    audience = drivers["audience_intensity"]
    infrastructure = drivers["infrastructure_intensity"]
    assert audience.module_key == "search_and_other_ads"
    assert infrastructure.module_key == "corporate_capital_allocation"
    assert audience.input_state is ModelInputState.REPORTED
    assert infrastructure.input_state is ModelInputState.REPORTED
    assert audience.fact_refs[0].fact_key == "fy2025_search_other_revenue"
    assert infrastructure.fact_refs[0].fact_key == "fy2025_capital_expenditures"
    assert audience.values == (Decimal("224532"),)
    assert infrastructure.values == (Decimal("91447"),)


def test_metric_classification_routes_numeric_evidence_and_swaps_executably() -> None:
    value = _build_input()
    result = CompanyResearchModelBuilder().build(value)
    corporate = next(
        item
        for item in result.business_map.modules
        if item.module_key == "corporate_capital_allocation"
    )
    capex = next(
        item
        for item in corporate.classified_evidence
        if item.metric_key == "capital_expenditures"
    )
    assert capex.category == "capital"

    classifications = tuple(
        replace(item, category="cost")
        if item.metric_key == "capital_expenditures"
        else replace(item, category="capital")
        if item.metric_key == "operating_expense"
        else item
        for item in value.model_template.metric_classifications
    )
    swapped = CompanyResearchModelBuilder().build(
        replace(
            value,
            model_template=replace(
                value.model_template,
                metric_classifications=classifications,
            ),
        )
    )
    swapped_corporate = next(
        item
        for item in swapped.business_map.modules
        if item.module_key == "corporate_capital_allocation"
    )
    assert next(
        item
        for item in swapped_corporate.classified_evidence
        if item.metric_key == "capital_expenditures"
    ).category == "cost"


def test_template_only_empty_module_gets_explicit_blocking_gap() -> None:
    value = _build_input()
    orphan = CompanyResearchModelModule(
        module_key="orphan_unit",
        revenue_sources=("orphan revenue",),
        cost_structure=("orphan cost",),
        capital_needs=("orphan capital",),
    )
    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            model_template=replace(
                value.model_template,
                modules=(*value.model_template.modules, orphan),
            ),
        )
    )

    orphan_artifact = next(
        item for item in result.business_map.modules if item.module_key == "orphan_unit"
    )
    assert orphan_artifact.fact_refs == ()
    assert orphan_artifact.gap_refs == ("missing_module_evidence_orphan_unit",)
    assert result.assessment.status == "not_answerable"


def test_missing_bound_operating_driver_creates_critical_gap() -> None:
    value = _build_input()
    missing = CompanyResearchOperatingDriverBinding(
        driver_key="cost_signal",
        module_key="corporate_capital_allocation",
        metric_key="operating_expense",
        input_state=ModelInputState.REPORTED,
        equation_id=None,
    )
    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            model_template=replace(
                value.model_template,
                operating_driver_bindings=(
                    *value.model_template.operating_driver_bindings,
                    missing,
                ),
            ),
        )
    )

    assert "operating_driver_missing_cost_signal" in {
        gap.code for gap in result.gaps
    }
    assert result.assessment.status == "not_answerable"


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("value", "NaN"),
        ("value", "01"),
        ("value_kind", "estimate"),
        ("currency", None),
        ("unit", ""),
        ("period_start", "2025-13-01"),
    ),
)
def test_confirmed_model_facts_require_canonical_numeric_contract(
    field: str, invalid: object
) -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    payload["facts"][0][field] = invalid

    with pytest.raises(ValidationError, match="model fact"):
        CompanyResearchModelBuilder().build(
            replace(
                value,
                evidence_payload=payload,
                evidence_content_hash=canonical_hash(payload),
            )
        )


def test_confirmed_model_fact_currency_and_units_are_consistent_per_metric() -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    payload["facts"][0]["currency"] = "CNY"

    with pytest.raises(ValidationError, match="model fact currency and unit"):
        CompanyResearchModelBuilder().build(
            replace(
                value,
                evidence_payload=payload,
                evidence_content_hash=canonical_hash(payload),
            )
        )


def test_mixed_review_decisions_filter_rejected_nonrequired_facts() -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    rejected_key = "fy2025_other_bets_revenue"
    for fact in payload["facts"]:
        if fact["fact_key"] == rejected_key:
            fact["review_decision"] = "rejected"

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=payload,
            evidence_content_hash=canonical_hash(payload),
        )
    )

    assert result.assessment.status == "not_answerable"
    assert "missing_module_evidence_other_bets" in {
        gap.code for gap in result.gaps
    }
    assert all(
        ref.fact_key != rejected_key
        for module in result.business_map.modules
        for ref in module.fact_refs
    )


def test_missing_required_reviewed_baseline_creates_gap_and_blocks_answerability() -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    for fact in payload["facts"]:
        if fact["fact_key"] == "q4_2025_consolidated_revenue":
            fact["review_decision"] = "rejected"

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=payload,
            evidence_content_hash=canonical_hash(payload),
        )
    )

    assert result.assessment.status == "not_answerable"
    assert result.judgment_context.operating_baseline_available is False
    assert result.valuation_set is None
    assert "operating_baseline_missing_consolidated_revenue" in {
        gap.code for gap in result.gaps
    }


def test_absent_required_baseline_fact_also_creates_a_gap() -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    payload["facts"] = [
        fact
        for fact in payload["facts"]
        if fact["fact_key"] != "q4_2025_consolidated_revenue"
    ]
    source_refs = tuple(
        ref for ref in value.source_refs if ref["source_role"] != "company_material"
    )

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=payload,
            evidence_content_hash=canonical_hash(payload),
            source_refs=source_refs,
        )
    )

    assert result.judgment_context.operating_baseline_available is False
    assert result.assessment.status == "not_answerable"


def test_strategy_mechanisms_must_match_template_scenario_mapping_exactly() -> None:
    value = _build_input()
    assumptions = value.strategy_assumptions
    base, bull, bear = assumptions.scenario_overrides
    swapped = (
        replace(base, mechanism_id=bull.mechanism_id),
        replace(bull, mechanism_id=base.mechanism_id),
        bear,
    )
    swapped_assumptions = StrategyAssumptionSet(
        strategy_version=assumptions.strategy_version,
        content_hash=StrategyAssumptionSet.calculate_content_hash(
            strategy_version=assumptions.strategy_version,
            first_fiscal_year=assumptions.first_fiscal_year,
            driver_paths=assumptions.driver_paths,
            scenario_overrides=swapped,
            terminal_growth=assumptions.terminal_growth,
        ),
        first_fiscal_year=assumptions.first_fiscal_year,
        driver_paths=assumptions.driver_paths,
        scenario_overrides=swapped,
        terminal_growth=assumptions.terminal_growth,
    )

    with pytest.raises(ValidationError, match="template mechanism mapping"):
        CompanyResearchModelBuilder().build(
            replace(value, strategy_assumptions=swapped_assumptions)
        )


def test_frozen_market_bindings_reject_arbitrary_ids_and_mismatched_bridge_refs() -> None:
    market = _market_context()
    with pytest.raises(ValidationError, match="snapshot bindings"):
        replace(
            market,
            snapshot_bindings=(
                replace(market.snapshot_bindings[0], snapshot_id=UUID(int=999)),
                *market.snapshot_bindings[1:],
            ),
        )
    with pytest.raises(ValidationError, match="bridge refs"):
        replace(
            market,
            snapshot_bindings=(
                replace(
                    market.snapshot_bindings[0],
                    source_ref=_lineage("arbitrary_price", "frozen_market_snapshot"),
                ),
                *market.snapshot_bindings[1:],
            ),
        )


def test_governed_reverse_dcf_request_survives_the_frozen_market_boundary() -> None:
    result = CompanyResearchModelBuilder().build(_build_input())

    assert result.valuation_set is not None
    assert result.valuation_set.reverse_dcf is not None
    assert result.valuation_set.reverse_dcf.driver_key == "fcff_multiplier"
