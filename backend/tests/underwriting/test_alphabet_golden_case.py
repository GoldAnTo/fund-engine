from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.ledger import ValidationError
from app.underwriting.fixtures.alphabet_golden_case import (
    BUNDLED_MANIFEST_CONTENT_SHA256,
    AlphabetGoldenCaseFixtureError,
    load_alphabet_golden_case_fixture,
)
from app.underwriting.adapters.company_research import AlphabetCompanyResearchAdapter
from app.underwriting.fixtures.product_foundation import load_product_foundation_fixture
from app.underwriting.persistence.company_research_models import CompanyResearchArtifactVersion
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
)
from app.underwriting.services.company_research_sources import (
    CompanyResearchSourceService,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)
from app.underwriting.services.company_research_model_builder import StrategyAssumptionSet


NOW = datetime(2026, 8, 25, 9, tzinfo=UTC)
_FIXTURE_ROOT = Path(__file__).parents[2] / "app" / "underwriting" / "fixtures" / "alphabet_golden_case"


def _copy_fixture(tmp_path: Path) -> Path:
    copied = tmp_path / "alphabet"
    copied.mkdir(parents=True)
    manifest = _read_json(_FIXTURE_ROOT / "manifest.json")
    files = manifest["files"]
    assert isinstance(files, list)
    for name in (
        "manifest.json",
        *(item["name"] for item in files if isinstance(item, dict)),
    ):
        assert isinstance(name, str)
        destination = copied / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((_FIXTURE_ROOT / name).read_bytes())
    return copied


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _refresh_manifest(root: Path) -> None:
    facts_path = root / "source_facts.json"
    facts = _read_json(facts_path)
    if "content_hash" in facts:
        from app.underwriting.hashing import canonical_hash

        facts["content_hash"] = canonical_hash(
            {key: value for key, value in facts.items() if key != "content_hash"}
        )
        _write_json(facts_path, facts)
    manifest = _read_json(root / "manifest.json")
    files = manifest["files"]
    assert isinstance(files, list)
    for item in files:
        assert isinstance(item, dict)
        name = item["name"]
        assert isinstance(name, str)
        item["content_hash"] = hashlib.sha256((root / name).read_bytes()).hexdigest()
    payload = {key: value for key, value in manifest.items() if key != "content_hash"}
    from app.underwriting.hashing import canonical_hash

    manifest["content_hash"] = canonical_hash(payload)
    _write_json(root / "manifest.json", manifest)


def _assumption_number(value: str, key: str) -> dict[str, object]:
    return {
        "value": value,
        "state": "assumption",
        "assumption_key": key,
        "rationale": "Synthetic test-only candidate assumption.",
        "equation": "candidate_input",
    }


def _add_synthetic_governed_inputs(root: Path) -> None:
    from app.underwriting.hashing import canonical_hash

    envelope = b"synthetic authenticated envelope"
    raw_hash = hashlib.sha256(envelope).hexdigest()
    raw_file = "raw/synthetic-envelope.txt.gz"
    raw_path = root / raw_file
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(gzip.compress(envelope, mtime=0))
    provenance = {
        "source_url": "https://example.test/source",
        "source_locator": "synthetic locator",
        "raw_hash": raw_hash,
        "provider_policy_version": "synthetic-provider.v1",
        "raw_components": [
            {
                "raw_file": raw_file,
                "raw_hash": raw_hash,
                "source_url": "https://example.test/source",
                "source_locator": "synthetic locator",
            }
        ],
    }
    market: dict[str, object] = {
        "schema_version": "alphabet.golden-case.market-inputs.v1",
        "content_hash": "",
        "company_external_key": "US:ALPHABET:COMPANY",
        "security_external_keys": ["NASDAQ:GOOG", "NASDAQ:GOOGL"],
        "prices": [
            {
                "kind": "price",
                "security_external_key": key,
                "value": value,
                "currency": "USD",
                "price_type": "official_close",
                "adjustment_basis": "unadjusted",
                "market_at": "2026-08-25T20:00:00+00:00",
                "available_at": "2026-08-25T23:00:00+00:00",
                **provenance,
            }
            for key, value in (
                ("NASDAQ:GOOG", "207.5"),
                ("NASDAQ:GOOGL", "206.25"),
            )
        ],
        "fx": {
            "kind": "fx",
            "base_currency": "USD",
            "quote_currency": "CNY",
            "rate": "7.18",
            "quote_direction": "quote_per_base",
            "market_at": "2026-08-24T16:00:00+00:00",
            "available_at": "2026-08-25T22:00:00+00:00",
            **provenance,
        },
        "capital_structure": {
            "kind": "capital_structure",
            "company_external_key": "US:ALPHABET:COMPANY",
            "currency": "USD",
            "cash": "30000",
            "debt": "46000",
            "minority_interest": "0",
            "investments": "96000",
            "pension_liabilities": "0",
            "other_adjustments": "0",
            "basic_shares": "12000",
            "diluted_shares": "12100",
            "potential_dilution_descriptors": ["unvested equity awards"],
            "report_period_start": "2026-04-01T00:00:00+00:00",
            "report_period_end": "2026-06-30T00:00:00+00:00",
            "market_at": "2026-06-30T00:00:00+00:00",
            "available_at": "2026-07-30T00:00:00+00:00",
            "capital_bridge_policy_version": "alphabet-capital-bridge.v1",
            "policy_excluded_adjustments": ["pension_liabilities"],
            **provenance,
        },
        "security_rights": [
            {
                "kind": "security_rights",
                "security_external_key": key,
                "economic_units": "5800",
                "votes_per_unit": votes,
                "conversion_ratio": "1",
                "adr_ratio": "1",
                "dividend_rights_per_unit": "1",
                "effective_from": "2026-01-01T00:00:00+00:00",
                "effective_to": None,
                **provenance,
            }
            for key, votes in (("NASDAQ:GOOG", "0"), ("NASDAQ:GOOGL", "1"))
        ],
    }
    market["content_hash"] = canonical_hash(
        {key: value for key, value in market.items() if key != "content_hash"}
    )
    _write_json(root / "market_inputs.json", market)

    driver_keys = (
        "revenue",
        "operating_margin",
        "cash_tax_rate",
        "depreciation",
        "capex",
        "working_capital_change",
    )
    strategy: dict[str, object] = {
        "schema_version": "alphabet.golden-case.strategy-assumptions.v1",
        "content_hash": "",
        "strategy_version": "alphabet-synthetic.v1",
        "first_fiscal_year": 2027,
        "driver_paths": [
            {
                "driver_key": key,
                **_assumption_number("1", f"alphabet-synthetic.v1:{key}"),
                "values": ["1", "1", "1", "1", "1"],
            }
            for key in driver_keys
        ],
        "scenario_overrides": [
            {
                "scenario_id": scenario,
                "mechanism_id": mechanism,
                "driver_overrides": [
                    {
                        "driver_key": key,
                        **_assumption_number(
                            "1" if scenario == "base" else ("1.1" if scenario == "bull" else "0.9"),
                            f"alphabet-synthetic.v1:{scenario}:{key}",
                        ),
                    }
                    for key in driver_keys
                ],
            }
            for scenario, mechanism in (
                ("base", "steady_operations"),
                ("bull", "capacity_upside"),
                ("bear", "demand_stress"),
            )
        ],
        "terminal_growth": _assumption_number(
            "0.03", "alphabet-synthetic.v1:terminal_growth"
        ),
    }
    # Driver paths carry their five values in addition to the shared metadata;
    # the single metadata value is not part of the path schema.
    for item in strategy["driver_paths"]:  # type: ignore[index]
        item.pop("value")  # type: ignore[union-attr]
    strategy["content_hash"] = canonical_hash(
        {key: value for key, value in strategy.items() if key != "content_hash"}
    )
    _write_json(root / "strategy_assumptions.json", strategy)

    manifest = _read_json(root / "manifest.json")
    files = manifest["files"]
    assert isinstance(files, list)
    files[:] = [
        item
        for item in files
        if not (isinstance(item, dict) and str(item.get("name", "")).startswith("raw/"))
    ]
    for name in ("market_inputs.json", "strategy_assumptions.json"):
        digest = hashlib.sha256((root / name).read_bytes()).hexdigest()
        existing = next(
            (item for item in files if isinstance(item, dict) and item.get("name") == name),
            None,
        )
        if existing is None:
            files.append({"name": name, "content_hash": digest})
        else:
            existing["content_hash"] = digest
    compressed = raw_path.read_bytes()
    files.append(
        {
            "name": raw_file,
            "content_hash": hashlib.sha256(compressed).hexdigest(),
            "raw_hash": raw_hash,
            "raw_size": len(envelope),
        }
    )
    manifest["content_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "content_hash"}
    )
    _write_json(root / "manifest.json", manifest)


def test_bundled_manifest_is_pinned_by_its_exact_utf8_bytes() -> None:
    assert hashlib.sha256((_FIXTURE_ROOT / "manifest.json").read_bytes()).hexdigest() == (
        BUNDLED_MANIFEST_CONTENT_SHA256
    )


def test_custom_fixture_hashes_and_parses_each_file_from_the_same_read(
    tmp_path: Path, monkeypatch
) -> None:
    """A replacement between parse and hash must not affect one load."""
    root = _copy_fixture(tmp_path)
    facts_path = root / "source_facts.json"
    original_read_bytes = Path.read_bytes
    original = original_read_bytes(facts_path)
    calls = 0

    def replace_after_first_read(path: Path) -> bytes:
        nonlocal calls
        if path == facts_path:
            calls += 1
            if calls == 1:
                return original
            return b'{"replaced":"after-parse"}'
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", replace_after_first_read)

    fixture = load_alphabet_golden_case_fixture(root)

    assert calls == 1
    assert fixture.company_external_key == "US:ALPHABET:COMPANY"


def test_custom_fixture_read_failure_is_a_fixture_error(
    tmp_path: Path, monkeypatch
) -> None:
    root = _copy_fixture(tmp_path)
    facts_path = root / "source_facts.json"
    original_read_bytes = Path.read_bytes

    def deny_source_read(path: Path) -> bytes:
        if path == facts_path:
            raise PermissionError("fixture is not readable")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", deny_source_read)

    with pytest.raises(AlphabetGoldenCaseFixtureError, match="source_facts.json is unreadable"):
        load_alphabet_golden_case_fixture(root)


def test_custom_fixture_deep_json_is_a_recoverable_fixture_error(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path)
    facts_path = root / "source_facts.json"
    facts_path.write_bytes(b'{"nested":' * 2_000 + b"0" + b"}" * 2_000)
    manifest = _read_json(root / "manifest.json")
    files = manifest["files"]
    assert isinstance(files, list)
    for item in files:
        assert isinstance(item, dict)
        if item["name"] == "source_facts.json":
            item["content_hash"] = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    from app.underwriting.hashing import canonical_hash

    manifest["content_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "content_hash"}
    )
    _write_json(root / "manifest.json", manifest)

    with pytest.raises(AlphabetGoldenCaseFixtureError, match="unreadable"):
        load_alphabet_golden_case_fixture(root)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda root: (root / "source_facts.json").write_text(
                '{"schema_version":"alphabet.golden-case.source-facts.v1",'
                '"schema_version":"duplicate"}',
                encoding="utf-8",
            ),
            "duplicate key",
        ),
        (
            lambda root: _mutate_source_fact(root, "source_locator", " page 1"),
            "leading or trailing whitespace",
        ),
        (
            lambda root: _mutate_source_fact(root, "metric_key", "cafe\u0301"),
            "NFC",
        ),
        (
            lambda root: _mutate_source_fact(root, "period_start", "2025-1-01"),
            "round-trip",
        ),
        (
            lambda root: _mutate_source_fact(root, "currency", "EUR"),
            "currency",
        ),
        (
            lambda root: _mutate_source_fact(root, "unit", "billions"),
            "unit",
        ),
        (
            lambda root: _mutate_source_fact(root, "source_locator", ""),
            "source_locator",
        ),
        (
            lambda root: _mutate_source_fact(
                root, "available_at", "2026-08-26T23:59:59+00:00"
            ),
            "after fixture cutoff",
        ),
    ],
)
def test_custom_fixture_rejects_noncanonical_or_unavailable_source_facts(
    tmp_path: Path, mutate, message: str
) -> None:
    root = _copy_fixture(tmp_path)
    mutate(root)
    if message != "duplicate key":
        _refresh_manifest(root)

    with pytest.raises(ValidationError, match=message):
        load_alphabet_golden_case_fixture(root)


def _mutate_source_fact(root: Path, field: str, value: object) -> None:
    facts = _read_json(root / "source_facts.json")
    rows = facts["facts"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    rows[0][field] = value
    _write_json(root / "source_facts.json", facts)


def test_custom_fixture_rejects_unknown_keys_hash_mismatch_and_duplicate_fact_identity(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path)
    facts = _read_json(root / "source_facts.json")
    facts["unexpected"] = "no"
    _write_json(root / "source_facts.json", facts)
    _refresh_manifest(root)
    with pytest.raises(ValidationError, match="invalid fields"):
        load_alphabet_golden_case_fixture(root)

    root = _copy_fixture(tmp_path / "hash")
    manifest = _read_json(root / "manifest.json")
    manifest["content_hash"] = "0" * 64
    _write_json(root / "manifest.json", manifest)
    with pytest.raises(ValidationError, match="content hash mismatch"):
        load_alphabet_golden_case_fixture(root)

    root = _copy_fixture(tmp_path / "duplicate")
    facts = _read_json(root / "source_facts.json")
    rows = facts["facts"]
    assert isinstance(rows, list)
    rows.append(deepcopy(rows[0]))
    _write_json(root / "source_facts.json", facts)
    _refresh_manifest(root)
    with pytest.raises(ValidationError, match="fact identity"):
        load_alphabet_golden_case_fixture(root)


def test_custom_fixture_rejects_unhashable_security_external_key(tmp_path: Path) -> None:
    root = _copy_fixture(tmp_path)
    facts = _read_json(root / "source_facts.json")
    facts["security_external_keys"] = [["NASDAQ:GOOG"], "NASDAQ:GOOGL"]
    _write_json(root / "source_facts.json", facts)
    _refresh_manifest(root)

    with pytest.raises(AlphabetGoldenCaseFixtureError, match="securities"):
        load_alphabet_golden_case_fixture(root)


@pytest.mark.parametrize(
    "security_external_keys",
    (
        ["NASDAQ:GOOG", "NASDAQ:GOOGL", "NASDAQ:GOOG"],
        ["NASDAQ:GOOG", "NYSE:GOOGL"],
    ),
)
def test_custom_fixture_rejects_duplicate_or_wrong_security_external_keys(
    tmp_path: Path, security_external_keys: list[str]
) -> None:
    root = _copy_fixture(tmp_path)
    facts = _read_json(root / "source_facts.json")
    facts["security_external_keys"] = security_external_keys
    _write_json(root / "source_facts.json", facts)
    _refresh_manifest(root)

    with pytest.raises(AlphabetGoldenCaseFixtureError, match="securities"):
        load_alphabet_golden_case_fixture(root)


def test_loaded_fixture_keeps_official_lineage_modules_gaps_and_distinct_securities() -> None:
    fixture = load_alphabet_golden_case_fixture()

    assert {fact.source_role for fact in fixture.facts} == {
        "regulatory_filing",
        "company_material",
    }
    assert fixture.company_external_key == "US:ALPHABET:COMPANY"
    assert fixture.security_external_keys == ("NASDAQ:GOOG", "NASDAQ:GOOGL")
    assert {module.key for module in fixture.business_modules} == {
        "search_and_other_ads",
        "youtube_ads_and_subscriptions",
        "google_cloud",
        "other_google_services",
        "other_bets",
        "corporate_capital_allocation",
    }
    assert {gap.gap_key for gap in fixture.research_gaps} == {
        "market_price_missing",
        "usd_cny_fx_missing",
        "forward_model_missing",
    }
    assert all(fact.available_at <= fixture.cutoff for fact in fixture.facts)
    assert all(fact.published_at.time().isoformat() == "23:59:59" for fact in fixture.facts)


def test_adapter_accepts_only_the_fixture_business_module_vocabulary() -> None:
    fixture = load_alphabet_golden_case_fixture()

    assert AlphabetCompanyResearchAdapter().validate_source_modules(
        fixture.company_external_key, fixture.business_modules
    ) == fixture.business_modules


def test_bundled_strategy_is_an_explicit_machine_candidate_not_a_source_fact() -> None:
    fixture = load_alphabet_golden_case_fixture()
    assert fixture.strategy_assumptions is not None

    assumptions = AlphabetCompanyResearchAdapter().strategy_assumptions(
        fixture.strategy_assumptions
    )

    assert assumptions.strategy_version == "alphabet.machine-candidate.v1"
    assert assumptions.first_fiscal_year == 2026
    assert assumptions.driver_paths[0].values == (
        Decimal("480000"),
        Decimal("566400"),
        Decimal("651360"),
        Decimal("729523.2"),
        Decimal("802475.52"),
    )
    assert all(item.state.value == "assumption" for item in assumptions.driver_paths)
    assert all(not item.source_refs for item in assumptions.driver_paths)
    assert tuple(item.mechanism_id for item in assumptions.scenario_overrides) == (
        "search_cloud_resilience",
        "ai_monetization_and_utilization",
        "search_disruption_and_capital_drag",
    )


def test_bundled_market_inputs_pin_exact_abc_capital_and_raw_lineage() -> None:
    fixture = load_alphabet_golden_case_fixture()
    assert fixture.market_inputs is not None
    market = fixture.market_inputs

    assert tuple((item.security_external_key, item.value) for item in market.prices) == (
        ("NASDAQ:GOOG", Decimal("343.34")),
        ("NASDAQ:GOOGL", Decimal("346.96")),
    )
    assert market.fx.rate == Decimal("6.721")
    assert market.capital_structure.value("basic_shares") == Decimal("12230")
    assert market.capital_structure.value("diluted_shares") == Decimal("12309")
    assert tuple(
        (item.security_external_key, item.value("economic_units"))
        for item in market.security_rights
    ) == (
        ("NASDAQ:GOOG", Decimal("5527")),
        ("NASDAQ:GOOGL", Decimal("5868")),
    )
    assert len(market.capital_structure.provenance.raw_components) == 2
    assert {
        component.raw_file
        for capture in (*market.prices, market.fx, market.capital_structure, *market.security_rights)
        for component in capture.provenance.raw_components
    } == {
        "raw/nasdaq_goog_2026-08-25.json.gz",
        "raw/nasdaq_googl_2026-08-25.json.gz",
        "raw/federal_reserve_h10_usd_cny.csv.gz",
        "raw/alphabet_q2_2026_exhibit_99_1.html.gz",
        "raw/alphabet_q2_2026_10q.html.gz",
        "raw/alphabet_2025_10k.html.gz",
    }


def test_optional_governed_files_load_closed_market_and_strategy_contracts(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path)
    _add_synthetic_governed_inputs(root)

    fixture = load_alphabet_golden_case_fixture(root)

    assert fixture.market_inputs is not None
    assert fixture.market_inputs.security_external_keys == (
        "NASDAQ:GOOG",
        "NASDAQ:GOOGL",
    )
    assert fixture.strategy_assumptions is not None
    assumptions = AlphabetCompanyResearchAdapter().strategy_assumptions(
        fixture.strategy_assumptions
    )
    assert type(assumptions) is StrategyAssumptionSet
    assert assumptions.strategy_version == "alphabet-synthetic.v1"
    assert tuple(item.driver_key for item in assumptions.driver_paths) == (
        "revenue",
        "operating_margin",
        "cash_tax_rate",
        "depreciation",
        "capex",
        "working_capital_change",
    )


def test_governed_fixture_rejects_unknown_fields_noncanonical_decimals_and_bad_raw_sidecars(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path / "unknown")
    _add_synthetic_governed_inputs(root)
    market = _read_json(root / "market_inputs.json")
    market["unknown"] = True
    _write_json(root / "market_inputs.json", market)
    _refresh_manifest(root)
    with pytest.raises(ValidationError, match="market inputs.*invalid fields"):
        load_alphabet_golden_case_fixture(root)

    root = _copy_fixture(tmp_path / "decimal")
    _add_synthetic_governed_inputs(root)
    market = _read_json(root / "market_inputs.json")
    prices = market["prices"]
    assert isinstance(prices, list) and isinstance(prices[0], dict)
    prices[0]["value"] = "207.50"
    from app.underwriting.hashing import canonical_hash

    market["content_hash"] = canonical_hash(
        {key: value for key, value in market.items() if key != "content_hash"}
    )
    _write_json(root / "market_inputs.json", market)
    _refresh_manifest(root)
    with pytest.raises(ValidationError, match="canonical Decimal"):
        load_alphabet_golden_case_fixture(root)

    root = _copy_fixture(tmp_path / "envelope")
    _add_synthetic_governed_inputs(root)
    raw_path = root / "raw" / "synthetic-envelope.txt.gz"
    raw_path.write_bytes(gzip.compress(b"tampered", mtime=0))
    _refresh_manifest(root)
    with pytest.raises(ValidationError, match="decompressed (size|raw hash) mismatch"):
        load_alphabet_golden_case_fixture(root)


def test_governed_fixture_rejects_compressed_and_composite_raw_hash_drift(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path / "compressed")
    raw_path = root / "raw" / "nasdaq_goog_2026-08-25.json.gz"
    raw_path.write_bytes(raw_path.read_bytes() + b"tampered")
    with pytest.raises(ValidationError, match="content hash mismatch"):
        load_alphabet_golden_case_fixture(root)

    root = _copy_fixture(tmp_path / "composite")
    market = _read_json(root / "market_inputs.json")
    capital = market["capital_structure"]
    assert isinstance(capital, dict)
    capital["raw_hash"] = "0" * 64
    from app.underwriting.hashing import canonical_hash

    market["content_hash"] = canonical_hash(
        {key: value for key, value in market.items() if key != "content_hash"}
    )
    _write_json(root / "market_inputs.json", market)
    _refresh_manifest(root)
    with pytest.raises(ValidationError, match="composite raw hash mismatch"):
        load_alphabet_golden_case_fixture(root)


def test_strategy_fixture_fails_closed_when_numeric_metadata_is_missing(
    tmp_path: Path,
) -> None:
    root = _copy_fixture(tmp_path)
    _add_synthetic_governed_inputs(root)
    strategy = _read_json(root / "strategy_assumptions.json")
    paths = strategy["driver_paths"]
    assert isinstance(paths, list) and isinstance(paths[0], dict)
    paths[0].pop("rationale")
    from app.underwriting.hashing import canonical_hash

    strategy["content_hash"] = canonical_hash(
        {key: value for key, value in strategy.items() if key != "content_hash"}
    )
    _write_json(root / "strategy_assumptions.json", strategy)
    _refresh_manifest(root)

    with pytest.raises(ValidationError, match="strategy.*invalid fields"):
        load_alphabet_golden_case_fixture(root)


def test_alphabet_adapter_emits_six_module_model_template_without_forecasts() -> None:
    template = AlphabetCompanyResearchAdapter().model_template()

    assert tuple(item.module_key for item in template.modules) == (
        "search_and_other_ads",
        "youtube_ads_and_subscriptions",
        "google_cloud",
        "other_google_services",
        "other_bets",
        "corporate_capital_allocation",
    )
    assert {item.category for item in template.metric_classifications} == {
        "revenue",
        "cost",
        "capital",
    }
    assert tuple(item.driver_key for item in template.financial_driver_ownership) == (
        "revenue",
        "operating_margin",
        "cash_tax_rate",
        "depreciation",
        "capex",
        "working_capital_change",
    )
    assert not hasattr(template, "forecast_values")


def _preparation(session):
    from app.underwriting.persistence.models import UnderwritingResearchObject

    ProductFoundationFixtureService(session, now=lambda: NOW).load(
        load_product_foundation_fixture()
    )
    alphabet = session.scalar(
        select(UnderwritingResearchObject).where(
            UnderwritingResearchObject.external_key == "US:ALPHABET:COMPANY"
        )
    )
    assert alphabet is not None
    initializer = CompanyResearchInitializer(session, now=lambda: NOW)
    preview = initializer.preview(company_id=alphabet.id, cutoff_at=NOW)
    return initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=alphabet.id,
        cutoff_at=NOW,
        idempotency_key="task-seven-sources",
    )


def test_prepare_evidence_index_writes_only_index_and_gaps_then_waits_for_review(session) -> None:
    initialized = _preparation(session)

    outcome = CompanyResearchSourceService(session, now=lambda: NOW).prepare_evidence_index(
        preparation_id=initialized.preparation.id
    )

    assert outcome.status == "awaiting_evidence_review"
    assert outcome.evidence_index.kind == "evidence_index"
    assert outcome.research_gaps.kind == "research_gaps"
    assert {
        row.kind
        for row in session.scalars(
            select(CompanyResearchArtifactVersion).where(
                CompanyResearchArtifactVersion.project_id == initialized.project.id
            )
        )
    } == {"evidence_index", "research_gaps"}
    assert outcome.job.status == "waiting_for_review"
    assert "assessment" not in {event.event_type for event in outcome.events}


@pytest.mark.parametrize("failure", ("unavailable", "parser"))
def test_prepare_evidence_index_records_recoverable_failure_without_artifact_head(
    session, monkeypatch, failure: str
) -> None:
    initialized = _preparation(session)
    service = CompanyResearchSourceService(session, now=lambda: NOW)

    def unavailable():
        raise AlphabetGoldenCaseFixtureError("fixture source unavailable")

    def parser():
        raise ValidationError("fixture source parser failure")

    monkeypatch.setattr(service, "_load_fixture", unavailable if failure == "unavailable" else parser)
    outcome = service.prepare_evidence_index(preparation_id=initialized.preparation.id)

    assert outcome.status == "recoverable_failure"
    assert outcome.error["code"] == "alphabet_source_unavailable"
    assert session.scalar(
        select(func.count()).select_from(CompanyResearchArtifactVersion).where(
            CompanyResearchArtifactVersion.project_id == initialized.project.id
        )
    ) == 0
    assert outcome.job.status == "failed"
    assert outcome.events[-1].event_type == "source_preparation_failed"
    assert outcome.events[-1].payload == {
        "code": "alphabet_source_unavailable",
        "recoverable": True,
    }


def test_prepare_evidence_index_recovers_from_unhashable_fixture_security_key(
    session, monkeypatch, tmp_path: Path
) -> None:
    initialized = _preparation(session)
    root = _copy_fixture(tmp_path)
    facts = _read_json(root / "source_facts.json")
    facts["security_external_keys"] = [["NASDAQ:GOOG"], "NASDAQ:GOOGL"]
    _write_json(root / "source_facts.json", facts)
    _refresh_manifest(root)
    service = CompanyResearchSourceService(session, now=lambda: NOW)
    monkeypatch.setattr(service, "_load_fixture", lambda: load_alphabet_golden_case_fixture(root))

    outcome = service.prepare_evidence_index(preparation_id=initialized.preparation.id)

    assert outcome.status == "recoverable_failure"
    assert outcome.error == {"code": "alphabet_source_unavailable", "recoverable": True}
    assert session.scalar(
        select(func.count()).select_from(CompanyResearchArtifactVersion).where(
            CompanyResearchArtifactVersion.project_id == initialized.project.id
        )
    ) == 0
    assert outcome.events[-1].event_type == "source_preparation_failed"


def test_prepare_evidence_index_recovers_from_deep_fixture_json(
    session, monkeypatch, tmp_path: Path
) -> None:
    initialized = _preparation(session)
    root = _copy_fixture(tmp_path)
    facts_path = root / "source_facts.json"
    facts_path.write_bytes(b'{"nested":' * 2_000 + b"0" + b"}" * 2_000)
    manifest = _read_json(root / "manifest.json")
    files = manifest["files"]
    assert isinstance(files, list)
    for item in files:
        assert isinstance(item, dict)
        if item["name"] == "source_facts.json":
            item["content_hash"] = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    from app.underwriting.hashing import canonical_hash

    manifest["content_hash"] = canonical_hash(
        {key: value for key, value in manifest.items() if key != "content_hash"}
    )
    _write_json(root / "manifest.json", manifest)
    service = CompanyResearchSourceService(session, now=lambda: NOW)
    monkeypatch.setattr(
        service, "_load_fixture", lambda: load_alphabet_golden_case_fixture(root)
    )

    outcome = service.prepare_evidence_index(preparation_id=initialized.preparation.id)

    assert outcome.status == "recoverable_failure"
    assert outcome.error == {"code": "alphabet_source_unavailable", "recoverable": True}
    assert session.scalar(
        select(func.count()).select_from(CompanyResearchArtifactVersion).where(
            CompanyResearchArtifactVersion.project_id == initialized.project.id
        )
    ) == 0
    assert outcome.events[-1].event_type == "source_preparation_failed"
