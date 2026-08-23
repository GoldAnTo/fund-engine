"""HTTP contract tests for immutable underwriting research revision reads."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.underwriting.domain.types import (
    BlockerCode,
    EligibleAction,
    HistoricalBasisInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.persistence.models import (
    UnderwritingLedgerEntry,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.repository import UnderwritingRepository
from app.underwriting.services.catl_baseline import CatlBaselineService
from app.underwriting.services.kernel import UnderwritingKernelService
from app.underwriting.services.revision_parent_seal import (
    CATL_PARENT_SET_ENTRY_TYPE,
    CATL_PARENT_SET_FAMILY,
    answerability_content_hash,
    catl_parent_set_seal_payload,
    catl_revision_content_hash,
    parent_set_semantic_hash,
)


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
    assert {
        key: history.json()[key]
        for key in ("object_kind", "canonical_name", "external_key")
    } == {
        "object_kind": "company",
        "canonical_name": "Revision API",
        "external_key": "company:revision-api",
    }
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


def test_research_archives_list_sorted_readable_and_unreadable_families(api_client, session) -> None:
    company, basis, first, second = _two_revision_chain(session)
    corrupt = UnderwritingRepository(session).append_research_version(
        object_id=company.id,
        basis_id=basis.id,
        version_kind="corrupt_model",
        content_hash="a" * 64,
        parent_ids=["not-a-parent"],
        expected_parent_id=None,
        created_at=NOW,
    )

    response = api_client.get(f"{BASE}/research-archives", params={"limit": 100})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "underwriting.v1"
    assert payload["next_cursor"] is None
    assert [(item["canonical_name"], item["version_kind"]) for item in payload["items"]] == [
        ("Revision API", "corrupt_model"),
        ("Revision API", "economic_model"),
    ]
    unreadable, readable = payload["items"]
    assert unreadable == {
        "schema_version": "underwriting.v1",
        "object_id": str(company.id),
        "object_kind": "company",
        "canonical_name": "Revision API",
        "external_key": "company:revision-api",
        "version_kind": "corrupt_model",
        "version_count": 1,
        "lineage_state": "unreadable",
        "latest_revision_id": None,
        "latest_sequence": None,
        "cutoff": None,
        "source_manifest_hash": None,
    }
    assert readable["lineage_state"] == "readable"
    assert readable["version_count"] == 2
    assert readable["latest_revision_id"] == str(second.id)
    assert readable["latest_sequence"] == second.sequence
    assert readable["cutoff"] == NOW.isoformat().replace("+00:00", "Z")
    assert readable["source_manifest_hash"] == basis.source_manifest_hash
    assert corrupt.id != readable["latest_revision_id"]
    assert first.id
    assert api_client.post(f"{BASE}/research-archives").status_code == 405


def test_research_archives_reject_a_malformed_cursor_with_underwriting_envelope(api_client, session) -> None:
    _two_revision_chain(session)

    response = api_client.get(f"{BASE}/research-archives", params={"cursor": "not-base64"})

    assert response.status_code == 422
    assert response.json()["schema_version"] == "underwriting.v1"
    assert response.json()["error"]["code"] == "validation_failed"


def test_catl_original_api_response_replays_byte_for_byte_after_a_test_only_successor(
    api_client, session,
) -> None:
    """Archive discovery can advance while the original CATL version stays frozen."""
    imported = CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())
    original_id = imported.research_version.id
    original_path = f"{BASE}/research-versions/{original_id}"
    original = api_client.get(original_path)
    original_directory = api_client.get(
        f"{BASE}/research-archives", params={"query": imported.company.canonical_name},
    )

    assert original.status_code == original_directory.status_code == 200, original.text
    original_refs = original.json()["parent_refs"]
    original_archive_item = next(
        item for item in original_directory.json()["items"]
        if item["object_id"] == str(imported.company.id)
        and item["version_kind"] == imported.research_version.version_kind
    )
    assert original_archive_item["version_count"] == 1
    assert original_archive_item["latest_revision_id"] == str(original_id)

    _append_test_only_catl_answerability_successor(session, imported)

    replayed = api_client.get(original_path)
    advanced_directory = api_client.get(
        f"{BASE}/research-archives", params={"query": imported.company.canonical_name},
    )
    successor = api_client.get(
        f"{BASE}/objects/{imported.company.id}/research-versions/"
        "catl_economic_model_evidence_only"
    )
    successor_id = successor.json()["revisions"][-1]["id"]
    diff = api_client.get(f"{BASE}/research-versions/{original_id}/diff/{successor_id}")

    assert replayed.status_code == advanced_directory.status_code == successor.status_code == diff.status_code == 200
    assert replayed.content == original.content
    assert replayed.json()["parent_refs"] == original_refs
    advanced_archive_item = next(
        item for item in advanced_directory.json()["items"]
        if item["object_id"] == str(imported.company.id)
        and item["version_kind"] == imported.research_version.version_kind
    )
    assert advanced_archive_item["version_count"] == 2
    assert advanced_archive_item["latest_revision_id"] == successor_id
    assert advanced_archive_item["cutoff"] == original_archive_item["cutoff"]
    assert advanced_archive_item["source_manifest_hash"] == original_archive_item["source_manifest_hash"]
    assert {ref["reference"] for ref in replayed.json()["parent_refs"]} == {
        ref["reference"] for ref in original_refs
    }
    assert [(entry["artifact_type"], entry["change_type"]) for entry in diff.json()["entries"]] == [
        ("ledger", "replaced"),
        ("answerability", "replaced"),
    ]
    changed_references = {
        ref["reference"]
        for entry in diff.json()["entries"]
        for ref in (entry["before"], entry["after"])
        if ref is not None
    }
    assert changed_references.isdisjoint(
        {
            ref["reference"]
            for ref in original_refs
            if ref["artifact_type"] not in {"ledger", "answerability"}
        }
    )


def _append_test_only_catl_answerability_successor(session, imported) -> None:
    """Publish the narrowest valid CATL successor using persisted fixture parents."""
    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    original_parent_refs = imported.research_version.parent_ids
    old_answerability_id = str(imported.answerability.id)
    old_seal = next(
        session.get(UnderwritingLedgerEntry, UUID(parent_id))
        for parent_id in original_parent_refs
        if (row := session.get(UnderwritingLedgerEntry, UUID(parent_id))) is not None
        and row.family_key == CATL_PARENT_SET_FAMILY
        and row.entry_type == CATL_PARENT_SET_ENTRY_TYPE
    )
    assert old_seal is not None
    successor_answerability = kernel.record_answerability(
        imported.company.id,
        imported.basis.id,
        (BlockerCode.MISSING_KEY_BASELINE, BlockerCode.MECHANISM_UNIDENTIFIED),
        ("industry.capacity_utilization_price_cost_baseline", "formal_mechanism_review"),
        True,
        EligibleAction.OBSERVE,
        (
            "collect comparable capacity, utilization, price, and cost evidence",
            "complete independent mechanism review before formalization",
        ),
        imported.answerability.id,
    )
    original_summary = _revision_parent_refs(session, imported.research_version.id)
    replacement_ref = {
        "reference": str(successor_answerability.id),
        "artifact_type": "answerability",
        "identity": "answerability",
        "content_hash": answerability_content_hash(
            object_id=successor_answerability.object_id,
            basis_id=successor_answerability.basis_id,
            version=successor_answerability.version,
            state=successor_answerability.state,
            blockers=successor_answerability.blockers,
            research_debt_keys=successor_answerability.research_debt_keys,
            resolvable_within_mandate=successor_answerability.resolvable_within_mandate,
            allowed_action=successor_answerability.allowed_action,
            resolution_requirements=successor_answerability.resolution_requirements,
        ),
    }
    sealed_refs = [
        {
            "reference": ref.reference,
            "artifact_type": ref.artifact_type,
            "identity": ref.identity,
            "content_hash": ref.content_hash,
        }
        for ref in original_summary
        if ref.artifact_type != "semantic_snapshot"
        and ref.reference not in {old_answerability_id, str(old_seal.id)}
    ] + [replacement_ref]
    token = next(ref.reference for ref in original_summary if ref.artifact_type == "semantic_snapshot")
    successor_seal = kernel.append_ledger_entry(
        imported.company.id,
        imported.basis.id,
        LedgerEntryInput(
            LedgerKind.CALIBRATION,
            CATL_PARENT_SET_FAMILY,
            CATL_PARENT_SET_ENTRY_TYPE,
            catl_parent_set_seal_payload(semantic_snapshot_token=token, refs=sealed_refs),
            NOW,
            NOW,
            "frozen_revision_parent_set",
        ),
        old_seal.id,
    )
    parent_ids = [
        parent_id
        for parent_id in original_parent_refs
        if parent_id not in {old_answerability_id, str(old_seal.id)}
    ] + [str(successor_answerability.id), str(successor_seal.id)]
    UnderwritingRepository(session).append_research_version(
        object_id=imported.company.id,
        basis_id=imported.basis.id,
        version_kind=imported.research_version.version_kind,
        content_hash=catl_revision_content_hash(
            semantic_snapshot_token=token,
            parent_set_semantic_hash=parent_set_semantic_hash(sealed_refs),
        ),
        parent_ids=parent_ids,
        expected_parent_id=imported.research_version.id,
        created_at=NOW,
    )


def _revision_parent_refs(session, revision_id):
    """Keep successor construction rooted in the immutable persisted parent set."""
    from app.underwriting.services.research_revision_diff import ResearchRevisionDiffService

    return ResearchRevisionDiffService(session).revision_summary(revision_id).parent_refs
