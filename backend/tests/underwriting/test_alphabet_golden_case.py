from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
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


NOW = datetime(2026, 8, 25, 9, tzinfo=UTC)
_FIXTURE_ROOT = Path(__file__).parents[2] / "app" / "underwriting" / "fixtures" / "alphabet_golden_case"


def _copy_fixture(tmp_path: Path) -> Path:
    copied = tmp_path / "alphabet"
    copied.mkdir(parents=True)
    for name in ("manifest.json", "business_map.json", "source_facts.json"):
        (copied / name).write_bytes((_FIXTURE_ROOT / name).read_bytes())
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


def test_bundled_manifest_is_pinned_by_its_exact_utf8_bytes() -> None:
    assert hashlib.sha256((_FIXTURE_ROOT / "manifest.json").read_bytes()).hexdigest() == (
        BUNDLED_MANIFEST_CONTENT_SHA256
    )


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
    assert {gap.gap_key for gap in fixture.research_gaps} >= {
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
