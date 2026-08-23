"""HTTP contract tests for immutable underwriting research revision reads."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.underwriting.domain.types import (
    HistoricalBasisInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.services.kernel import UnderwritingKernelService


BASE = "/api/underwriting/v1"
NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def _two_revision_chain(session):
    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    company = kernel.add_object(ResearchObjectKind.COMPANY, "company:revision-api", "Revision API")
    basis = kernel.add_basis(HistoricalBasisInput(NOW, None, "a" * 64))
    first_entry = kernel.append_ledger_entry(
        company.id,
        basis.id,
        LedgerEntryInput(
            LedgerKind.REALITY, "company.revenue", "reported",
            {"source_locator": "annual-report:p18", "unit": "CNY"},
            NOW, NOW, "annual-report",
        ),
        None,
    )
    first = kernel.publish_research_version(
        company.id, basis.id, "economic_model", [str(first_entry.id)], None,
    )
    second_entry = kernel.append_ledger_entry(
        company.id,
        basis.id,
        LedgerEntryInput(
            LedgerKind.REALITY, "company.revenue", "revised",
            {"source_locator": "annual-report:p20", "unit": "CNY"},
            NOW, NOW, "annual-report",
        ),
        first_entry.id,
    )
    second = kernel.publish_research_version(
        company.id, basis.id, "economic_model", [str(second_entry.id)], first.id,
    )
    return company, basis, first, second


def _entry_sort_key(entry: dict) -> tuple:
    before = entry["before"] or {}
    after = entry["after"] or {}
    return (
        {"evidence": 0, "mechanism": 1, "industry_model": 2, "answerability": 3}[entry["group"]],
        entry["artifact_type"], entry["identity"], entry["change_type"],
        before.get("reference", ""), after.get("reference", ""),
    )


def test_revision_history_detail_and_diff_are_read_only_and_historical(api_client, session) -> None:
    company, basis, first, second = _two_revision_chain(session)

    history = api_client.get(f"{BASE}/objects/{company.id}/research-versions/economic_model")
    detail = api_client.get(f"{BASE}/research-versions/{first.id}")
    diff = api_client.get(f"{BASE}/research-versions/{first.id}/diff/{second.id}")

    assert history.status_code == detail.status_code == diff.status_code == 200
    assert history.json()["schema_version"] == "underwriting.v1"
    assert [item["sequence"] for item in history.json()["revisions"]] == [1, 2]
    assert history.json()["revisions"][0]["id"] == str(first.id)
    assert detail.json()["cutoff"] == NOW.isoformat().replace("+00:00", "Z")
    assert detail.json()["source_manifest_hash"] == basis.source_manifest_hash
    assert detail.json()["parent_refs"][0]["source_locators"] == ["annual-report:p18"]
    assert len(diff.json()["diff_hash"]) == 64
    assert diff.json()["entries"] == sorted(diff.json()["entries"], key=_entry_sort_key)
    assert "valuation" not in diff.json()
    assert "price" not in diff.json()
    assert api_client.post(diff.request.url.path).status_code == 405


def test_revision_read_missing_and_non_ancestor_errors_use_underwriting_envelopes(api_client, session) -> None:
    company, _, first, second = _two_revision_chain(session)

    missing = api_client.get(f"{BASE}/research-versions/{uuid4()}")
    no_family = api_client.get(f"{BASE}/objects/{uuid4()}/research-versions/economic_model")
    reverse = api_client.get(f"{BASE}/research-versions/{second.id}/diff/{first.id}")

    assert missing.status_code == no_family.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"
    assert reverse.status_code == 422
    assert reverse.json()["schema_version"] == "underwriting.v1"
    assert reverse.json()["error"]["code"] == "validation_failed"
    assert company.id


def test_revision_reads_neither_flush_nor_commit_the_caller_session(api_client, session, monkeypatch) -> None:
    _, _, first, _ = _two_revision_chain(session)
    pending = UnderwritingResearchObject(
        kind="company",
        external_key="company:revision-api",
        canonical_name="would-conflict-if-flushed",
        created_at=NOW,
    )
    session.add(pending)

    def _unexpected_commit() -> None:
        raise AssertionError("read endpoint must not commit")

    monkeypatch.setattr(session, "commit", _unexpected_commit)
    response = api_client.get(f"{BASE}/research-versions/{first.id}")

    assert response.status_code == 200, response.text
    assert pending in session.new
