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
    ScenarioDriverOverride,
    SecurityValuationReference,
    SourceLineageReference,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_model_builder import (
    CompanyResearchBuildInput,
    CompanyResearchModelBuilder,
    FrozenMarketContext,
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
            "base", "search_cloud_resilience", _overrides()
        ),
        ScenarioAssumption(
            "bull",
            "ai_monetization_and_utilization",
            _overrides(
                revenue="1.10",
                operating_margin="1.05",
                capex="0.95",
                working_capital_change="0.90",
            ),
        ),
        ScenarioAssumption(
            "bear",
            "search_disruption_and_capital_drag",
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
    return FrozenMarketContext(
        price_snapshot_ids=(ids[0], ids[1]),
        fx_snapshot_ids=(ids[2],),
        capital_structure_snapshot_id=ids[3],
        security_rights_ids=(ids[4], ids[5]),
        snapshot_ids=ids,
        market_at=CUTOFF,
        market_bridge=market_bridge,
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


def test_builder_rejects_unknown_evidence_fields_and_rejected_required_facts() -> None:
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

    rejected = deepcopy(value.evidence_payload)
    rejected["facts"][0]["review_decision"] = "rejected"
    with pytest.raises(ValidationError, match="rejected facts cannot be model inputs"):
        CompanyResearchModelBuilder().build(
            replace(
                value,
                evidence_payload=rejected,
                evidence_content_hash=canonical_hash(rejected),
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
