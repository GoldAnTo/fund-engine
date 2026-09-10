from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.company_research import (
    CapitalStructureReference,
    CompanyResearchCompany,
    CompanyResearchIdentitySet,
    CompanyResearchSecurity,
    CompanyResearchValidationError,
    DriverInput,
    MarketBridgeArtifact,
    ModelInputState,
    ResearchGapSeverity,
    ReverseDcfRequest,
    ScenarioDriverOverride,
    SecurityValuationReference,
    SourceLineageReference,
)
from app.underwriting.domain.company_research_contracts import StrategyAssumptionValue
from app.underwriting.domain.company_research_critical_inputs import (
    CRITICAL_DEPENDENCY_SURFACE_ORDER,
    CriticalDependencyGraph,
    CriticalInputKind,
    select_critical_inputs,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchBuildInput,
    CompanyResearchDriverBinding,
    CompanyResearchMetricClassification,
    CompanyResearchModelBuilder,
    CompanyResearchModelModule,
    CompanyResearchModelTemplate,
    CompanyResearchOperatingBaselineRequirement,
    CompanyResearchOperatingDriverBinding,
    CompanyResearchScenarioMechanism,
    EvidenceBuildMode,
    FrozenMarketContext,
    FrozenMarketEquityComponent,
    FrozenMarketSnapshotBinding,
    FrozenMarketSnapshotRole,
    ScenarioAssumption,
    StrategyAssumptionSet,
    validate_company_research_evidence_payload_for_read,
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
            assumption_rationale="Synthetic candidate rationale.",
            assumption_equation="candidate_input",
        )
        for key, path in values.items()
    )


def _overrides(scenario: str, **changes: str) -> tuple[ScenarioDriverOverride, ...]:
    return tuple(
        ScenarioDriverOverride(
            driver_key=key,
            value=Decimal(changes.get(key, "1")),
            state=ModelInputState.ASSUMPTION,
            assumption_key=f"alphabet-candidate.v1:{scenario}:{key}",
            rationale="Synthetic scenario candidate rationale.",
            equation="baseline * scenario_multiplier",
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
        ScenarioAssumption("base", "steady_operations", _overrides("base")),
        ScenarioAssumption(
            "bull",
            "capacity_upside",
            _overrides(
                "bull",
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
                "bear",
                revenue="0.90",
                operating_margin="0.90",
                capex="1.10",
                working_capital_change="1.20",
            ),
        ),
    )
    terminal_growth = StrategyAssumptionValue(
        value=Decimal("0.03"),
        state=ModelInputState.ASSUMPTION,
        assumption_key="alphabet-candidate.v1:terminal_growth",
        rationale="Synthetic terminal candidate rationale.",
        equation="terminal_growth_candidate",
    )
    content_hash = StrategyAssumptionSet.calculate_content_hash(
        strategy_version="alphabet-candidate.v1",
        first_fiscal_year=2026,
        driver_paths=driver_paths,
        scenario_overrides=scenarios,
        terminal_growth=terminal_growth,
    )
    return StrategyAssumptionSet(
        strategy_version="alphabet-candidate.v1",
        content_hash=content_hash,
        first_fiscal_year=2026,
        driver_paths=driver_paths,
        scenario_overrides=scenarios,
        terminal_growth=terminal_growth,
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
        company_external_key="US:ALPHABET:COMPANY",
        security_external_keys=("NASDAQ:GOOG", "NASDAQ:GOOGL"),
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
            CompanyResearchScenarioMechanism("base", "steady_operations"),
            CompanyResearchScenarioMechanism("bull", "capacity_upside"),
            CompanyResearchScenarioMechanism("bear", "demand_stress"),
        ),
    )


def _identity_set(
    *,
    company_external_key: str = "US:ALPHABET:COMPANY",
    security_external_keys: tuple[str, ...] = ("NASDAQ:GOOG", "NASDAQ:GOOGL"),
) -> CompanyResearchIdentitySet:
    company = CompanyResearchCompany(
        object_id=UUID(int=200),
        external_key=company_external_key,
        canonical_name="Synthetic Company",
    )
    return CompanyResearchIdentitySet(
        company=company,
        securities=tuple(
            CompanyResearchSecurity(
                object_id=UUID(int=201 + index),
                company_id=company.object_id,
                external_key=external_key,
                canonical_name=f"Synthetic Security {index}",
                symbol=external_key.rsplit(":", 1)[-1],
                exchange=external_key.split(":", 1)[0],
                share_class=f"Class {index}",
                trading_currency="USD",
            )
            for index, external_key in enumerate(security_external_keys)
        ),
    )


def _market_context() -> FrozenMarketContext:
    refs = {
        key: _lineage(key, "frozen_market_snapshot")
        for key in (
            "capital_structure_usd",
            "capital_bridge_policy",
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
            basic_shares=Decimal("11600"),
            diluted_shares=Decimal("12100"),
            source_ref=refs["capital_structure_usd"],
            capital_bridge_policy_version="synthetic-capital-bridge.v1",
            policy_ref=refs["capital_bridge_policy"],
            policy_excluded_adjustments=("pension_liabilities",),
        ),
        securities=(
            SecurityValuationReference(
                "NASDAQ:GOOG",
                Decimal("5800"),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("205"),
                Decimal("7.18"),
                refs["security_rights_nasdaq_goog"],
                refs["market_price_usd_nasdaq_goog"],
            ),
            SecurityValuationReference(
                "NASDAQ:GOOGL",
                Decimal("5800"),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
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

    def binding(index, role, security_key, source_ref):
        return FrozenMarketSnapshotBinding(
            snapshot_id=ids[index],
            role=role,
            security_external_key=security_key,
            source_ref=source_ref,
            snapshot_content_hash=f"{index + 1}" * 64,
            capture_envelope_id=UUID(int=100 + index),
            capture_content_hash="a" * 64,
            provenance_role="primary",
        )

    bindings = (
        binding(
            0,
            FrozenMarketSnapshotRole.PRICE,
            "NASDAQ:GOOG",
            refs["market_price_usd_nasdaq_goog"],
        ),
        binding(
            1,
            FrozenMarketSnapshotRole.PRICE,
            "NASDAQ:GOOGL",
            refs["market_price_usd_nasdaq_googl"],
        ),
        binding(2, FrozenMarketSnapshotRole.FX, None, refs["usd_cny_fx"]),
        binding(
            3,
            FrozenMarketSnapshotRole.CAPITAL_STRUCTURE,
            None,
            refs["capital_structure_usd"],
        ),
        binding(
            4,
            FrozenMarketSnapshotRole.SECURITY_RIGHTS,
            "NASDAQ:GOOG",
            refs["security_rights_nasdaq_goog"],
        ),
        binding(
            5,
            FrozenMarketSnapshotRole.SECURITY_RIGHTS,
            "NASDAQ:GOOGL",
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
        equity_components=(
            FrozenMarketEquityComponent(
                "class_a",
                Decimal("5800"),
                "NASDAQ:GOOGL",
                refs["security_rights_nasdaq_googl"],
                ids[1],
                refs["market_price_usd_nasdaq_googl"],
            ),
            FrozenMarketEquityComponent(
                "class_b",
                Decimal("0"),
                "NASDAQ:GOOGL",
                SourceLineageReference(
                    "economic_units_class_b",
                    "frozen_market_snapshot",
                    "https://example.com/sec",
                    "Class B units",
                    "2" * 64,
                ),
                ids[1],
                refs["market_price_usd_nasdaq_googl"],
                votes_per_unit=Decimal("10"),
                conversion_to_security_external_key="NASDAQ:GOOGL",
                conversion_ratio=Decimal("1"),
                dividend_rights_per_unit=Decimal("1"),
                economic_rights_per_unit=Decimal("1"),
                legal_rights_ref=SourceLineageReference(
                    "security_rights_class_b",
                    "frozen_market_snapshot",
                    "https://example.com/sec",
                    "Class B legal rights",
                    "3" * 64,
                ),
                price_proxy_ref=SourceLineageReference(
                    "market_price_proxy_class_b",
                    "frozen_market_snapshot",
                    "https://example.com/price",
                    "GOOGL proxy",
                    "1" * 64,
                ),
                price_proxy_policy_version="alphabet_class_b_googl_proxy.v1",
            ),
            FrozenMarketEquityComponent(
                "class_c",
                Decimal("5800"),
                "NASDAQ:GOOG",
                refs["security_rights_nasdaq_goog"],
                ids[0],
                refs["market_price_usd_nasdaq_goog"],
            ),
        ),
        reverse_dcf_request=ReverseDcfRequest(
            driver_key="fcff_multiplier",
            target_enterprise_value=Decimal("2291904"),
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
        identity_set=_identity_set(),
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


def test_build_input_requires_an_exact_company_identity_set() -> None:
    with pytest.raises(ValidationError, match="identity"):
        replace(_build_input(), identity_set=None)  # type: ignore[arg-type]


def test_builder_rejects_other_company_inputs_before_compiling_goog_market() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    evidence["company_external_key"] = "US:OTHER:COMPANY"
    evidence["security_external_keys"] = ["NYSE:OTHER"]
    for fact in evidence["facts"]:
        fact["company_external_key"] = "US:OTHER:COMPANY"
    gaps = deepcopy(value.gap_payload)
    gaps["company_external_key"] = "US:OTHER:COMPANY"
    other_template = replace(
        value.model_template,
        company_external_key="US:OTHER:COMPANY",
        security_external_keys=("NYSE:OTHER",),
    )

    with pytest.raises(ValidationError, match="market.*identity"):
        CompanyResearchModelBuilder().build(
            replace(
                value,
                identity_set=_identity_set(
                    company_external_key="US:OTHER:COMPANY",
                    security_external_keys=("NYSE:OTHER",),
                ),
                evidence_payload=evidence,
                evidence_content_hash=canonical_hash(evidence),
                gap_payload=gaps,
                model_template=other_template,
            )
        )


@pytest.mark.parametrize(
    ("target", "replacement"),
    (
        ("evidence_company", "US:OTHER:COMPANY"),
        ("evidence_security", ["NYSE:OTHER"]),
        ("gap_company", "US:OTHER:COMPANY"),
        ("template_company", "US:OTHER:COMPANY"),
        ("template_security", ("NYSE:OTHER",)),
    ),
)
def test_builder_rejects_each_cross_identity_boundary(
    target: str, replacement: object
) -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    gaps = deepcopy(value.gap_payload)
    template = value.model_template
    if target == "evidence_company":
        evidence["company_external_key"] = replacement
        for fact in evidence["facts"]:
            fact["company_external_key"] = replacement
    elif target == "evidence_security":
        evidence["security_external_keys"] = replacement
    elif target == "gap_company":
        gaps["company_external_key"] = replacement
    elif target == "template_company":
        template = replace(template, company_external_key=replacement)
    else:
        template = replace(template, security_external_keys=replacement)

    with pytest.raises(ValidationError, match="identity"):
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            gap_payload=gaps,
            model_template=template,
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


def test_authenticated_ai_draft_builds_directly_from_undecided_source_facts() -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    for fact in payload["facts"]:
        fact.pop("review_decision")

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=payload,
            evidence_content_hash=canonical_hash(payload),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )

    assert result.business_map.modules
    assert all("review_decision" not in fact for fact in payload["facts"])


def test_authenticated_ai_draft_excludes_explicitly_rejected_facts() -> None:
    value = _build_input()
    payload = deepcopy(value.evidence_payload)
    rejected_key = payload["facts"][0]["fact_key"]
    for fact in payload["facts"]:
        fact.pop("review_decision")
    payload["facts"][0]["review_decision"] = "rejected"

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=payload,
            evidence_content_hash=canonical_hash(payload),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )

    used_fact_keys = {
        ref.fact_key
        for module in result.business_map.modules
        for ref in module.fact_refs
    }
    assert rejected_key not in used_fact_keys


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


def test_builder_emits_a_critical_gap_for_each_missing_minimum_operating_driver() -> (
    None
):
    value = _build_input()
    result = CompanyResearchModelBuilder().build(value)
    available = {
        (str(item["business_module"]), str(item["metric_key"]))
        for item in value.evidence_payload["facts"]
        if item["review_decision"] == "confirmed"
    }
    missing = {
        item.driver_key
        for item in value.model_template.operating_driver_bindings
        if (item.module_key, item.metric_key) not in available
    }
    assert {
        gap.code.removeprefix("builder_generated_operating_driver_missing_")
        for gap in result.gaps
        if gap.code.startswith("builder_generated_operating_driver_missing_")
    } == missing
    assert all(
        gap.severity.value == "critical"
        for gap in result.gaps
        if gap.code.startswith("builder_generated_operating_driver_missing_")
    )


def test_builder_does_not_invent_market_gap_ownership_when_governed_gaps_are_missing() -> (
    None
):
    value = _build_input()
    gap_payload = deepcopy(value.gap_payload)
    gap_payload["gaps"] = [
        gap for gap in gap_payload["gaps"] if gap["gap_key"] != "market_price_missing"
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


def test_builder_emits_complete_explicit_critical_input_dependency_graph() -> None:
    value = _build_input()

    result = CompanyResearchModelBuilder().build(value)

    graph = result.critical_input_dependency_graph
    assert type(graph) is CriticalDependencyGraph
    assert tuple(node.surface for node in graph.surface_nodes) == (
        CRITICAL_DEPENDENCY_SURFACE_ORDER
    )
    consumers = {edge.consumer_key for edge in graph.edges}
    assert {node.key for node in graph.surface_nodes} <= consumers
    selected = select_critical_inputs(graph)
    selected_source_keys = {
        item.key
        for item in selected.inputs
        if item.key.startswith("fact:")
        if item.kind
        in {CriticalInputKind.SOURCE_FACT, CriticalInputKind.MANAGEMENT_GUIDANCE}
    }
    assert selected_source_keys == {
        f"fact:{fact['fact_key']}"
        for fact in value.evidence_payload["facts"]
        if fact["review_decision"] == "confirmed"
    }
    assert all(
        item.source_ref is not None
        for item in selected.inputs
        if item.kind
        in {CriticalInputKind.SOURCE_FACT, CriticalInputKind.MANAGEMENT_GUIDANCE}
    )


def test_dependency_graph_excludes_a_redundant_authenticated_fact() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    redundant = deepcopy(evidence["facts"][0])
    redundant["fact_key"] = "aaa_redundant_operating_expense"
    redundant["business_module"] = "corporate_capital_allocation"
    redundant["metric_key"] = "operating_expense"
    redundant["source_locator"] = "aaa_redundant_operating_expense"
    redundant["raw_hash"] = hashlib.sha256(
        b"aaa_redundant_operating_expense"
    ).hexdigest()
    evidence["facts"].append(redundant)
    source_refs = tuple(
        sorted(
            (
                *value.source_refs,
                {
                    key: redundant[key]
                    for key in (
                        "source_role",
                        "source_url",
                        "source_locator",
                        "raw_hash",
                    )
                },
            ),
            key=lambda item: tuple(item[key] for key in sorted(item)),
        )
    )

    selected = select_critical_inputs(
        CompanyResearchModelBuilder()
        .build(
            replace(
                value,
                evidence_payload=evidence,
                evidence_content_hash=canonical_hash(evidence),
                source_refs=source_refs,
            )
        )
        .critical_input_dependency_graph
    )

    assert "fact:aaa_redundant_operating_expense" not in {
        item.key for item in selected.inputs
    }


def test_dependency_graph_rejects_ambiguous_required_fact_matches() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    duplicate = deepcopy(evidence["facts"][0])
    duplicate["fact_key"] = "aaa_duplicate_search_revenue"
    duplicate["source_locator"] = "aaa_duplicate_search_revenue"
    duplicate["raw_hash"] = hashlib.sha256(
        b"aaa_duplicate_search_revenue"
    ).hexdigest()
    evidence["facts"].append(duplicate)
    source_refs = tuple(
        sorted(
            (
                *value.source_refs,
                {
                    key: duplicate[key]
                    for key in (
                        "source_role",
                        "source_url",
                        "source_locator",
                        "raw_hash",
                    )
                },
            ),
            key=lambda item: tuple(item[key] for key in sorted(item)),
        )
    )

    with pytest.raises(ValidationError, match="ambiguous critical fact dependency"):
        CompanyResearchModelBuilder().build(
            replace(
                value,
                evidence_payload=evidence,
                evidence_content_hash=canonical_hash(evidence),
                source_refs=source_refs,
            )
        )


def test_dependency_graph_maps_management_guidance_as_guidance() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    evidence["facts"][-1]["value_kind"] = "management_guidance"

    selected = select_critical_inputs(
        CompanyResearchModelBuilder()
        .build(
            replace(
                value,
                evidence_payload=evidence,
                evidence_content_hash=canonical_hash(evidence),
            )
        )
        .critical_input_dependency_graph
    )

    baseline = next(
        item
        for item in selected.inputs
        if item.key == "fact:q4_2025_consolidated_revenue"
    )
    assert baseline.kind is CriticalInputKind.MANAGEMENT_GUIDANCE


def test_dependency_graph_preserves_supported_derived_fact_equation_and_parents() -> (
    None
):
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    baseline = evidence["facts"][-1]
    parent_fact_keys = tuple(
        sorted(
            fact["fact_key"]
            for fact in evidence["facts"]
            if fact["metric_key"] == "revenue"
            and fact["business_module"] != "corporate_capital_allocation"
        )
    )
    baseline["value_kind"] = "derived"
    baseline["equation_id"] = "sum_segment_revenue.v1"
    baseline["parent_fact_keys"] = list(parent_fact_keys)

    graph = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    ).critical_input_dependency_graph
    derived = next(
        node.candidate
        for node in graph.calculation_nodes
        if node.key == "fact:q4_2025_consolidated_revenue"
    )

    assert derived.kind is CriticalInputKind.DERIVED_CALCULATION
    assert derived.equation_id == "sum_segment_revenue.v1"
    assert derived.parent_input_keys == tuple(
        f"fact:{fact_key}" for fact_key in parent_fact_keys
    )


def test_mainline_quarantines_incomplete_derived_fact_and_continues_draft() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    unsupported = evidence["facts"][-1]
    unsupported["fact_key"] = "unsupported_derived_revenue"
    unsupported["value"] = "987654321"
    unsupported["value_kind"] = "derived"

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )

    artifact_fact_keys = {
        ref.fact_key
        for module in result.business_map.modules
        for ref in module.fact_refs
    } | {
        ref.fact_key
        for driver in result.driver_map.drivers
        for ref in driver.fact_refs
    }
    artifact_values = {
        str(item.value)
        for module in result.business_map.modules
        for item in module.classified_evidence
    } | {
        str(item)
        for driver in result.driver_map.drivers
        for item in driver.values
    }
    gap_key = "builder_generated_unsupported_derived_unsupported_derived_revenue"
    graph = result.critical_input_dependency_graph

    assert "unsupported_derived_revenue" not in artifact_fact_keys
    assert "987654321" not in artifact_values
    assert all(
        "987654321"
        not in {
            str(row.revenue),
            str(row.operating_income),
            str(row.fcff),
        }
        for row in result.financial_bridge.rows
    )
    assert result.financial_bridge.rows
    assert not result.judgment_context.operating_baseline_available
    assert result.valuation_set is None
    assert result.memo.candidate_status == "machine_draft"
    assert gap_key in {gap.code for gap in result.gaps}
    assert f"unknown:{gap_key}" in {node.key for node in graph.unknown_nodes}
    assert "fact:unsupported_derived_revenue" not in {
        node.key for node in (*graph.input_nodes, *graph.calculation_nodes)
    }
    assert {
        edge.consumer_key
        for edge in graph.edges
        if edge.parent_key == f"unknown:{gap_key}"
    } == {
        "surface:answerability",
        "surface:security_value",
        "surface:security_return",
        "surface:direction",
    }


def test_mainline_quarantines_derived_fact_with_unavailable_parent() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    baseline = evidence["facts"][-1]
    baseline["value_kind"] = "derived"
    baseline["equation_id"] = "sum_segment_revenue.v1"
    baseline["parent_fact_keys"] = ["missing_parent_fact"]

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )

    assert "builder_generated_unsupported_derived_q4_2025_consolidated_revenue" in {
        gap.code for gap in result.gaps
    }


def test_mainline_redundant_unsupported_derived_fact_only_blocks_answerability() -> (
    None
):
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    redundant = deepcopy(evidence["facts"][0])
    redundant.update(
        {
            "fact_key": "redundant_unsupported_derived_revenue",
            "value": "987654321",
            "value_kind": "derived",
        }
    )
    evidence["facts"].append(redundant)

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )

    gap_key = (
        "builder_generated_unsupported_derived_"
        "redundant_unsupported_derived_revenue"
    )
    gap = next(item for item in result.gaps if item.code == gap_key)
    graph = result.critical_input_dependency_graph
    assert gap.severity is ResearchGapSeverity.HIGH
    assert result.judgment_context.operating_baseline_available
    assert result.valuation_set is not None
    assert {
        edge.consumer_key
        for edge in graph.edges
        if edge.parent_key == f"unknown:{gap_key}"
    } == {"surface:answerability"}


@pytest.mark.parametrize(
    ("field", "conflict"),
    (("currency", "CNY"), ("unit", "billions")),
)
def test_mainline_quarantine_precedes_cross_fact_currency_and_unit_consistency(
    field: str,
    conflict: str,
) -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    redundant = deepcopy(evidence["facts"][0])
    redundant.update(
        {
            "fact_key": f"inconsistent_unsupported_derived_{field}",
            "value": "987654321",
            "value_kind": "derived",
            field: conflict,
        }
    )
    evidence["facts"].append(redundant)

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )

    gap_key = (
        "builder_generated_unsupported_derived_"
        f"inconsistent_unsupported_derived_{field}"
    )
    assert gap_key in {gap.code for gap in result.gaps}
    assert result.valuation_set is not None


def test_mainline_quarantines_cyclic_derived_fact_provenance() -> None:
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    first, second = evidence["facts"][:2]
    first["value_kind"] = "derived"
    first["equation_id"] = "cyclic_revenue.v1"
    first["parent_fact_keys"] = [second["fact_key"]]
    second["value_kind"] = "derived"
    second["equation_id"] = "cyclic_revenue.v1"
    second["parent_fact_keys"] = [first["fact_key"]]

    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            evidence_build_mode=EvidenceBuildMode.AUTHENTICATED_AI_DRAFT,
        )
    )

    expected = {
        f"builder_generated_unsupported_derived_{first['fact_key']}",
        f"builder_generated_unsupported_derived_{second['fact_key']}",
    }
    assert expected.issubset({gap.code for gap in result.gaps})


def test_legacy_human_reviewed_derived_fact_without_provenance_remains_readable() -> (
    None
):
    value = _build_input()
    evidence = deepcopy(value.evidence_payload)
    legacy = evidence["facts"][-1]
    legacy["value_kind"] = "derived"

    validate_company_research_evidence_payload_for_read(evidence)
    result = CompanyResearchModelBuilder().build(
        replace(
            value,
            evidence_payload=evidence,
            evidence_content_hash=canonical_hash(evidence),
            evidence_build_mode=EvidenceBuildMode.HUMAN_REVIEWED,
        )
    )

    assert any(
        item.fact_ref.fact_key == legacy["fact_key"]
        and str(item.value) == legacy["value"]
        for module in result.business_map.modules
        for item in module.classified_evidence
    )
    legacy_dependency = next(
        node.candidate
        for node in result.critical_input_dependency_graph.input_nodes
        if node.key == f"fact:{legacy['fact_key']}"
    )
    assert legacy_dependency.kind is CriticalInputKind.SOURCE_FACT
    assert legacy_dependency.source_ref is not None
    assert legacy_dependency.source_ref.fact_key == legacy["fact_key"]


def test_blocked_terminal_surfaces_end_at_exact_unknown_nodes() -> None:
    result = CompanyResearchModelBuilder().build(
        replace(_build_input(), market_context=None)
    )
    graph = result.critical_input_dependency_graph
    unknown_keys = {node.key for node in graph.unknown_nodes}

    blocked_surfaces = {
        "surface:capital_structure",
        "surface:security_value",
        "surface:security_return",
        "surface:direction",
    }
    assert {
        edge.parent_key
        for edge in graph.edges
        if edge.consumer_key in blocked_surfaces
    } == {"unknown:market_security_bridge_unavailable"}
    assert "unknown:market_security_bridge_unavailable" in unknown_keys


def test_market_dependency_edges_follow_exact_value_return_and_direction_equations() -> (
    None
):
    graph = CompanyResearchModelBuilder().build(
        _build_input()
    ).critical_input_dependency_graph
    edges = {(edge.parent_key, edge.consumer_key) for edge in graph.edges}

    assert (
        "market:fx:usd_cny",
        "calculation:security_return",
    ) in edges
    assert (
        "market:fx:usd_cny",
        "calculation:security_value",
    ) not in edges
    assert (
        "assumption:required_return",
        "calculation:direction",
    ) in edges
    assert (
        "calculation:security_return",
        "calculation:direction",
    ) in edges
    assert not any(
        parent.startswith("market:rights:")
        and consumer == "calculation:capital_structure"
        for parent, consumer in edges
    )
    scenario_assumptions = {
        node.candidate.key: node.candidate.assumption_key
        for node in graph.input_nodes
        if node.candidate.key.startswith("scenario:")
    }
    assert scenario_assumptions["scenario:bull:revenue"] == (
        "alphabet-candidate.v1:bull:revenue"
    )


def test_critical_input_selection_preserves_every_capital_structure_field() -> None:
    graph = CompanyResearchModelBuilder().build(
        _build_input()
    ).critical_input_dependency_graph

    selected = select_critical_inputs(graph)

    assert {
        item.key
        for item in selected.inputs
        if item.key.startswith("market:capital:")
    } == {
        "market:capital:cash",
        "market:capital:debt",
        "market:capital:minority_interest",
        "market:capital:investments",
        "market:capital:pension_liabilities",
        "market:capital:other_adjustments",
        "market:capital:basic_shares",
        "market:capital:diluted_shares",
        "market:capital:bridge_policy",
    }


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


def test_strategy_assumption_set_is_required_hash_addressed_and_never_reported() -> (
    None
):
    value = _build_input()
    with pytest.raises(ValidationError, match="strategy assumptions"):
        replace(value, strategy_assumptions=None)  # type: ignore[arg-type]

    assumptions = value.strategy_assumptions
    with pytest.raises(
        CompanyResearchValidationError, match="versioned assumption_key"
    ):
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


def test_strategy_assumption_hash_canonicalizes_all_decimal_scales() -> None:
    paths = _driver_paths()
    scaled_paths = tuple(
        replace(
            path,
            values=tuple(value.quantize(Decimal("0.0000")) for value in path.values),
        )
        for path in paths
    )
    scenarios = _strategy_assumptions().scenario_overrides
    scaled_scenarios = tuple(
        replace(
            scenario,
            driver_overrides=tuple(
                replace(override, value=override.value.quantize(Decimal("0.0000")))
                for override in scenario.driver_overrides
            ),
        )
        for scenario in scenarios
    )

    first = StrategyAssumptionSet.calculate_content_hash(
        strategy_version="alphabet-candidate.v1",
        first_fiscal_year=2026,
        driver_paths=paths,
        scenario_overrides=scenarios,
        terminal_growth=replace(
            _strategy_assumptions().terminal_growth, value=Decimal("0.03")
        ),
    )
    second = StrategyAssumptionSet.calculate_content_hash(
        strategy_version="alphabet-candidate.v1",
        first_fiscal_year=2026,
        driver_paths=scaled_paths,
        scenario_overrides=scaled_scenarios,
        terminal_growth=replace(
            _strategy_assumptions().terminal_growth, value=Decimal("0.030")
        ),
    )

    assert first == second


def test_company_model_template_is_required() -> None:
    with pytest.raises(ValidationError, match="model template"):
        replace(_build_input(), model_template=None)  # type: ignore[arg-type]


def test_company_model_template_requires_revenue_cost_and_capital_classifications() -> (
    None
):
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
    assert modules["distribution_risk"].gap_refs == ("distribution_evidence_missing",)
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
    assert {driver.module_key for driver in result.driver_map.drivers} <= set(
        module_map.values()
    )


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
    assert (
        next(
            item
            for item in swapped_corporate.classified_evidence
            if item.metric_key == "capital_expenditures"
        ).category
        == "cost"
    )


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
    assert orphan_artifact.gap_refs == (
        "builder_generated_missing_module_evidence_orphan_unit",
    )
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

    assert "builder_generated_operating_driver_missing_cost_signal" in {
        gap.code for gap in result.gaps
    }
    assert result.assessment.status == "not_answerable"


def test_raw_governed_gaps_cannot_use_the_builder_generated_namespace() -> None:
    value = _build_input()
    gaps = deepcopy(value.gap_payload)
    gaps["gaps"].append(
        {
            "gap_key": "builder_generated_missing_module_evidence_orphan_unit",
            "business_module": "corporate_capital_allocation",
            "reason": "Attempted collision with a compiled gap.",
        }
    )

    with pytest.raises(ValidationError, match="reserved.*namespace"):
        replace(value, gap_payload=gaps)


def test_compiled_financial_bridge_keeps_assumption_refs_out_of_fact_refs() -> None:
    result = CompanyResearchModelBuilder().build(_build_input())

    assert all(not row.fact_refs for row in result.financial_bridge.rows)
    assert all(row.assumption_refs for row in result.financial_bridge.rows)
    assert {
        ref.fact_key for ref in result.financial_bridge.rows[0].assumption_refs
    } == {
        f"strategy_assumption_{key}"
        for key in (
            "revenue",
            "operating_margin",
            "cash_tax_rate",
            "depreciation",
            "capex",
            "working_capital_change",
        )
    }


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
    assert "builder_generated_missing_module_evidence_other_bets" in {
        gap.code for gap in result.gaps
    }
    assert all(
        ref.fact_key != rejected_key
        for module in result.business_map.modules
        for ref in module.fact_refs
    )


def test_missing_required_reviewed_baseline_creates_gap_and_blocks_answerability() -> (
    None
):
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
    assert "builder_generated_operating_baseline_missing_consolidated_revenue" in {
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


def test_frozen_market_bindings_reject_arbitrary_ids_and_mismatched_bridge_refs() -> (
    None
):
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
