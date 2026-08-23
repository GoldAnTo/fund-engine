"""HTTP contract tests for immutable underwriting research revision reads."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from uuid import UUID, uuid4

from app.underwriting.domain.types import (
    BlockerCode,
    EligibleAction,
    HistoricalBasisInput,
    LedgerEntryInput,
    LedgerKind,
    ResearchObjectKind,
)
from app.underwriting.domain.evidence_candidates import (
    CandidateEvidenceItem,
    CandidateEvidenceStatus,
)
from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.persistence.models import (
    UnderwritingLedgerEntry,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.repository import UnderwritingRepository
from app.underwriting.services.catl_baseline import CatlBaselineService
from app.underwriting.services.kernel import UnderwritingKernelService, canonical_hash
from app.underwriting.services.candidate_evidence import CandidateEvidenceService
from app.underwriting.services.source_freeze import freeze_manifest
from app.underwriting.persistence.research_repository import UnderwritingResearchRepository
from app.underwriting.services.revision_parent_seal import (
    CATL_PARENT_SET_ENTRY_TYPE,
    CATL_PARENT_SET_FAMILY,
    answerability_content_hash,
    catl_answerability_parent_content_hash,
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


def _published_candidate_revision(session):
    """Create a selected candidate revision without fixture/current-head readers."""
    kernel = UnderwritingKernelService(session, now=lambda: NOW)
    company = kernel.add_object(ResearchObjectKind.INDUSTRY, "industry:candidate-api", "Candidate API")
    manifest_payload = {
        "schema_version": "v1",
        "cutoff": NOW.isoformat(),
        "sources": [{
            "source_id": "candidate-source",
            "title": "Candidate source",
            "locator": "https://example.test/candidate-source",
            "published_at": NOW.isoformat(),
            "first_available_at": NOW.isoformat(),
            "retrieved_at": NOW.isoformat(),
            "content_sha256": "a" * 64,
            "authority": "issuer_filing",
            "authorization": "authorized",
            "display_policy": "derived_only",
            "provider_capability": "public_http",
            "retention": "hash_locator_and_derived_observations",
        }],
    }
    frozen = freeze_manifest(manifest_payload, NOW)
    basis = kernel.add_basis(HistoricalBasisInput(NOW, None, frozen.manifest_hash))
    repository = UnderwritingResearchRepository(session)
    manifest = repository.add_source_manifest(
        manifest_key="candidate-api", basis_id=basis.id, manifest=manifest_payload,
        manifest_hash=frozen.manifest_hash, content_hash=canonical_hash(manifest_payload),
        expected_parent_id=None, created_at=NOW,
    )
    item = CandidateEvidenceItem(
        metric_key="industry.capacity", status=CandidateEvidenceStatus.SOURCE_REPORTED,
        value=Decimal("100"), unit="GWh", observed_start=NOW - timedelta(days=365),
        observed_end=NOW, available_at=NOW, source_id="candidate-source",
        source_locator="https://example.test/candidate-source",
        scope_statement="Global disclosed capacity only.",
        exclusions=("No formal model input.",), methodology="Direct transcription.",
        prohibited_splicing_declaration="No source splicing.",
    )
    payload = {
        "object_id": str(company.id), "basis_id": str(basis.id),
        "source_manifest_id": str(manifest.id), "dossier_key": "industry-capacity",
        "version": 1, "scope_statement": "Candidate industry evidence only.",
        "status": "candidate", "purpose": "evidence_candidate",
        "items": [item.canonical_payload], "rejected_calculations": ["No valuation model."],
        "source_manifest_hash": manifest.manifest_hash, "created_at": NOW.isoformat(),
        "supersedes_id": None,
    }
    dossier = repository.append_candidate_dossier(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        dossier_key="industry-capacity", payload=payload, created_at=NOW,
        expected_parent_id=None,
    )
    for role, identity in (("provenance", "reviewer:provenance"), ("methodology", "reviewer:methodology")):
        review = {
            "dossier_id": str(dossier.id), "dossier_content_hash": dossier.content_hash,
            "reviewer_identity": identity, "reviewer_role": role, "decision": "approve",
            "rationale": "Evidence is bounded and traceable.", "reviewed_at": NOW.isoformat(),
        }
        repository.append_candidate_review(
            dossier_id=dossier.id, dossier_content_hash=dossier.content_hash,
            reviewer_identity=identity, reviewer_role=role, decision="approve",
            payload=review, created_at=NOW,
        )
    session.commit()
    published = CandidateEvidenceService(session, now=lambda: NOW).publish(dossier.id)
    session.commit()
    return company, basis, manifest, dossier, payload, published


def _entry_sort_key(entry: dict) -> tuple:
    before = entry["before"] or {}
    after = entry["after"] or {}
    return (
        {"evidence": 0, "mechanism": 1, "industry_model": 2, "answerability": 3}[entry["group"]],
        entry["artifact_type"], entry["identity"], entry["change_type"],
        before.get("reference", ""), after.get("reference", ""),
    )


def test_candidate_evidence_is_get_only_parent_sealed_and_deterministic(api_client, session) -> None:
    company, basis, manifest, dossier, payload, published = _published_candidate_revision(session)

    response = api_client.get(
        f"{BASE}/research-versions/{published.research_version.id}/candidate-evidence",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "schema_version": "underwriting.v1",
        "revision_id": str(published.research_version.id),
        "object_id": str(company.id),
        "basis_id": str(basis.id),
        "version_kind": "industry_evidence_candidate",
        "content_hash": published.research_version.content_hash,
        "cutoff": NOW.isoformat().replace("+00:00", "Z"),
        "source_manifest_hash": basis.source_manifest_hash,
        "dossier": {
            "schema_version": "underwriting.v1",
            "reference": str(dossier.id),
            "content_hash": dossier.content_hash,
            "dossier_key": "industry-capacity",
            "version": 1,
            "status": "candidate",
            "scope_statement": "Candidate industry evidence only.",
            "rejected_calculations": ["No valuation model."],
        },
        "items": [{
            "schema_version": "underwriting.v1",
            "metric_key": "industry.capacity",
            "status": "source_reported",
            "value": "100",
            "unit": "GWh",
            "observed_start": "2024-05-15T15:59:59Z",
            "observed_end": NOW.isoformat().replace("+00:00", "Z"),
            "available_at": NOW.isoformat().replace("+00:00", "Z"),
            "source_id": "candidate-source",
            "source_locator": "https://example.test/candidate-source",
            "scope_statement": "Global disclosed capacity only.",
            "exclusions": ["No formal model input."],
            "methodology": "Direct transcription.",
            "prohibited_splicing_declaration": "No source splicing.",
            "transcription_method": None,
            "error_bound": None,
            "scenario_use": None,
            "not_observed_declared": False,
            "unknown_reason": None,
        }],
        "reviews": [
            {
                "schema_version": "underwriting.v1",
                "reference": str(published.reviews[0].id),
                "content_hash": published.reviews[0].content_hash,
                "reviewer_identity": "reviewer:methodology",
                "reviewer_role": "methodology",
                "decision": "approve",
                "rationale": "Evidence is bounded and traceable.",
                "reviewed_at": NOW.isoformat().replace("+00:00", "Z"),
            },
            {
                "schema_version": "underwriting.v1",
                "reference": str(published.reviews[1].id),
                "content_hash": published.reviews[1].content_hash,
                "reviewer_identity": "reviewer:provenance",
                "reviewer_role": "provenance",
                "decision": "approve",
                "rationale": "Evidence is bounded and traceable.",
                "reviewed_at": NOW.isoformat().replace("+00:00", "Z"),
            },
        ],
        "answerability": {
            "schema_version": "underwriting.v1",
            "reference": str(published.answerability.id),
            "content_hash": body["answerability"]["content_hash"],
            "state": "not_answerable",
            "research_debt_keys": [f"candidate_evidence:{dossier.id}"],
            "resolution_requirements": [
                f"Formalize reviewed candidate dossier {dossier.id} before model use",
            ],
        },
    }
    assert api_client.post(response.request.url.path).status_code == 405
    assert payload["dossier_key"] == "industry-capacity"
    assert manifest.id


def test_candidate_evidence_reads_only_selected_parent_graph_and_fails_closed_on_tamper(
    api_client, session,
) -> None:
    company, basis, manifest, dossier, payload, published = _published_candidate_revision(session)
    successor_payload = dict(payload) | {
        "version": 2,
        "scope_statement": "Later candidate correction.",
        "rejected_calculations": [
            "No valuation model.",
            "No price target calculation.",
        ],
        "supersedes_id": str(dossier.id),
    }
    UnderwritingResearchRepository(session).append_candidate_dossier(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        dossier_key="industry-capacity", payload=successor_payload, created_at=NOW,
        expected_parent_id=dossier.id,
    )
    session.commit()

    selected = api_client.get(
        f"{BASE}/research-versions/{published.research_version.id}/candidate-evidence",
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["dossier"]["scope_statement"] == "Candidate industry evidence only."
    assert selected.json()["dossier"]["rejected_calculations"] == ["No valuation model."]

    tampered = dict(payload)
    tampered["scope_statement"] = "Tampered selected parent."
    cursor = session.connection().connection.cursor()
    cursor.execute(
        "UPDATE uw_evidence_candidate_dossier_versions SET payload = ? WHERE id = ?",
        (json.dumps(tampered), dossier.id.hex),
    )
    cursor.close()
    session.commit()
    session.expunge_all()

    invalid = api_client.get(
        f"{BASE}/research-versions/{published.research_version.id}/candidate-evidence",
    )
    assert invalid.status_code == 422
    assert invalid.json()["schema_version"] == "underwriting.v1"
    assert invalid.json()["error"]["code"] == "validation_failed"


def test_candidate_evidence_selected_successor_does_not_walk_unselected_predecessors(
    api_client, session,
) -> None:
    company, basis, manifest, dossier, payload, _ = _published_candidate_revision(session)
    successor_payload = dict(payload) | {
        "version": 2,
        "scope_statement": "Selected successor evidence only.",
        "supersedes_id": str(dossier.id),
    }
    repository = UnderwritingResearchRepository(session)
    successor = repository.append_candidate_dossier(
        object_id=company.id, basis_id=basis.id, source_manifest_id=manifest.id,
        dossier_key="industry-capacity", payload=successor_payload, created_at=NOW,
        expected_parent_id=dossier.id,
    )
    for role, identity in (("provenance", "reviewer:successor-provenance"), ("methodology", "reviewer:successor-methodology")):
        review = {
            "dossier_id": str(successor.id), "dossier_content_hash": successor.content_hash,
            "reviewer_identity": identity, "reviewer_role": role, "decision": "approve",
            "rationale": "Successor evidence is bounded and traceable.", "reviewed_at": NOW.isoformat(),
        }
        repository.append_candidate_review(
            dossier_id=successor.id, dossier_content_hash=successor.content_hash,
            reviewer_identity=identity, reviewer_role=role, decision="approve",
            payload=review, created_at=NOW,
        )
    session.commit()
    selected = CandidateEvidenceService(session, now=lambda: NOW).publish(successor.id)
    session.commit()

    before = api_client.get(
        f"{BASE}/research-versions/{selected.research_version.id}/candidate-evidence",
    )
    assert before.status_code == 200, before.text

    cursor = session.connection().connection.cursor()
    cursor.execute(
        "UPDATE uw_evidence_candidate_dossier_versions SET version = 9 WHERE id = ?",
        (dossier.id.hex,),
    )
    cursor.close()
    session.commit()
    session.expunge_all()

    response = api_client.get(
        f"{BASE}/research-versions/{selected.research_version.id}/candidate-evidence",
    )
    assert response.status_code == 200, response.text
    assert response.content == before.content
    assert response.json()["dossier"]["scope_statement"] == "Selected successor evidence only."


def test_candidate_evidence_uses_underwriting_not_found_and_validation_envelopes(
    api_client, session,
) -> None:
    _, _, ordinary_revision, _ = _two_revision_chain(session)

    missing = api_client.get(f"{BASE}/research-versions/{uuid4()}/candidate-evidence")
    wrong_kind = api_client.get(
        f"{BASE}/research-versions/{ordinary_revision.id}/candidate-evidence",
    )

    assert missing.status_code == 404
    assert missing.json()["schema_version"] == "underwriting.v1"
    assert missing.json()["error"]["code"] == "not_found"
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["schema_version"] == "underwriting.v1"
    assert wrong_kind.json()["error"]["code"] == "validation_failed"


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


def test_research_revision_boundary_returns_only_the_selected_frozen_catl_state(
    api_client, session,
) -> None:
    """The boundary binds the checked CATL revision, not a current-state lookup."""
    imported = CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())

    response = api_client.get(
        f"{BASE}/research-versions/{imported.research_version.id}/boundary",
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload == {
        "schema_version": "underwriting.v1",
        "revision_id": str(imported.research_version.id),
        "object_id": str(imported.company.id),
        "basis_id": str(imported.basis.id),
        "version_kind": "catl_economic_model_evidence_only",
        "content_hash": imported.research_version.content_hash,
        "cutoff": NOW.isoformat().replace("+00:00", "Z"),
        "source_manifest_hash": imported.basis.source_manifest_hash,
        "answerability": {
            "schema_version": "underwriting.v1",
            "reference": str(imported.answerability.id),
            "content_hash": answerability_content_hash(
                object_id=imported.answerability.object_id,
                basis_id=imported.answerability.basis_id,
                version=imported.answerability.version,
                state=imported.answerability.state,
                blockers=imported.answerability.blockers,
                research_debt_keys=imported.answerability.research_debt_keys,
                resolvable_within_mandate=imported.answerability.resolvable_within_mandate,
                allowed_action=imported.answerability.allowed_action,
                resolution_requirements=imported.answerability.resolution_requirements,
            ),
            "state": "not_answerable",
            "blockers": ["missing_key_baseline", "mechanism_unidentified"],
            "research_debt_keys": [
                "industry.capacity_utilization_price_cost_baseline",
                "formal_mechanism_review",
            ],
            "resolvable_within_mandate": True,
            "resolution_requirements": [
                "collect comparable capacity, utilization, price, and cost evidence",
                "complete independent mechanism review before formalization",
            ],
        },
        "unknown_evidence_gaps": [],
    }
    assert api_client.post(response.request.url.path).status_code == 405


def test_research_revision_boundary_allows_a_null_frozen_answerability(
    api_client, session,
) -> None:
    """No parent record means null—not an inferred answerability status."""
    _, _, first, _ = _two_revision_chain(session)

    response = api_client.get(f"{BASE}/research-versions/{first.id}/boundary")

    assert response.status_code == 200, response.text
    assert response.json()["revision_id"] == str(first.id)
    assert response.json()["answerability"] is None
    assert response.json()["unknown_evidence_gaps"] == []


def test_research_revision_boundary_uses_underwriting_error_envelopes(
    api_client, session,
) -> None:
    company, basis, _, _ = _two_revision_chain(session)
    corrupt = UnderwritingRepository(session).append_research_version(
        object_id=company.id,
        basis_id=basis.id,
        version_kind="boundary-corrupt",
        content_hash="a" * 64,
        parent_ids=["not-a-parent"],
        expected_parent_id=None,
        created_at=NOW,
    )

    missing = api_client.get(f"{BASE}/research-versions/{uuid4()}/boundary")
    invalid = api_client.get(f"{BASE}/research-versions/{corrupt.id}/boundary")

    assert missing.status_code == 404
    assert missing.json()["schema_version"] == "underwriting.v1"
    assert missing.json()["error"]["code"] == "not_found"
    assert invalid.status_code == 422
    assert invalid.json()["schema_version"] == "underwriting.v1"
    assert invalid.json()["error"]["code"] == "validation_failed"


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
        "content_hash": catl_answerability_parent_content_hash(
            object_id=successor_answerability.object_id,
            basis_id=successor_answerability.basis_id,
            version=successor_answerability.version,
            state=successor_answerability.state,
            blockers=successor_answerability.blockers,
            research_debt_keys=successor_answerability.research_debt_keys,
            resolvable_within_mandate=successor_answerability.resolvable_within_mandate,
            allowed_action=successor_answerability.allowed_action,
            resolution_requirements=successor_answerability.resolution_requirements,
            created_at=successor_answerability.created_at,
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
