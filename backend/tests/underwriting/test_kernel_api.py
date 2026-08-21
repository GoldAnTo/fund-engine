"""HTTP contract tests for the independent underwriting kernel API."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

BASE = "/api/underwriting/v1"
TIME = datetime(2026, 8, 20, 9, 30, tzinfo=UTC).isoformat()


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _create_object(api_client, *, kind: str, external_key: str, name: str) -> dict:
    response = api_client.post(
        f"{BASE}/objects",
        json={"kind": kind, "external_key": external_key, "canonical_name": name},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["schema_version"] == "underwriting.v1"
    return body


def _create_basis(api_client) -> dict:
    response = api_client.post(
        f"{BASE}/historical-bases",
        json={
            "cutoff": TIME,
            "price_as_of": TIME,
            "source_manifest_hash": "a" * 64,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["schema_version"] == "underwriting.v1"
    return body


def _entry_payload(basis_id: str, kind: str, family_key: str) -> dict:
    return {
        "basis_id": basis_id,
        "ledger_kind": kind,
        "family_key": family_key,
        "entry_type": "reported",
        "payload": {"source": kind},
        "effective_at": TIME,
        "available_at": TIME,
        "source_boundary": "public",
        "expected_parent_id": None,
    }


def test_underwriting_api_creates_object_relation_and_basis(api_client):
    industry = _create_object(
        api_client, kind="industry", external_key="industry:ai", name="AI"
    )
    company = _create_object(
        api_client, kind="company", external_key="company:acme", name="Acme"
    )
    security = _create_object(
        api_client, kind="security", external_key="security:acme", name="Acme A"
    )

    relation = api_client.post(
        f"{BASE}/object-relations",
        json={
            "parent_id": industry["id"],
            "child_id": company["id"],
            "relation_type": "industry_exposes_company",
        },
    )
    assert relation.status_code == 201, relation.text
    assert relation.json() == {
        "schema_version": "underwriting.v1",
        "id": relation.json()["id"],
        "parent_id": industry["id"],
        "child_id": company["id"],
        "relation_type": "industry_exposes_company",
        "created_at": relation.json()["created_at"],
    }

    security_relation = api_client.post(
        f"{BASE}/object-relations",
        json={
            "parent_id": company["id"],
            "child_id": security["id"],
            "relation_type": "company_has_security",
        },
    )
    assert security_relation.status_code == 201, security_relation.text
    assert _create_basis(api_client)["source_manifest_hash"] == "a" * 64


def test_underwriting_api_records_mandate_four_ledger_kinds_and_snapshot(api_client):
    company = _create_object(
        api_client, kind="company", external_key="company:acme", name="Acme"
    )
    basis = _create_basis(api_client)

    mandate = api_client.post(
        f"{BASE}/mandates",
        json={
            "mandate_key": "core",
            "horizon_years": 5,
            "base_currency": "USD",
            "required_return": "0.15",
            "permanent_loss_limit": "0.30",
            "comparison_set": ["SPX", "NDX"],
            "expected_parent_id": None,
        },
    )
    assert mandate.status_code == 201, mandate.text
    assert mandate.json()["version"] == 1
    assert mandate.json()["supersedes_id"] is None
    assert mandate.json()["schema_version"] == "underwriting.v1"

    entries = []
    for kind in ("reality", "belief", "decision", "calibration"):
        response = api_client.post(
            f"{BASE}/objects/{company['id']}/ledger-entries",
            json=_entry_payload(basis["id"], kind, f"{kind}:core"),
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["ledger_kind"] == kind
        assert body["version"] == 1
        assert body["schema_version"] == "underwriting.v1"
        entries.append(body)

    snapshot = api_client.get(f"{BASE}/objects/{company['id']}/snapshots/{basis['id']}")
    assert snapshot.status_code == 200, snapshot.text
    body = snapshot.json()
    assert body["schema_version"] == "underwriting.v1"
    assert body["object_id"] == company["id"]
    assert body["basis_id"] == basis["id"]
    assert {entry["id"] for entry in body["entries"]} == {entry["id"] for entry in entries}
    assert len(body["snapshot_hash"]) == 64


def test_underwriting_api_records_answerability(api_client):
    company = _create_object(
        api_client, kind="company", external_key="company:acme", name="Acme"
    )
    basis = _create_basis(api_client)
    response = api_client.post(
        f"{BASE}/objects/{company['id']}/answerability",
        json={
            "basis_id": basis["id"],
            "hard_blockers": [],
            "research_debt_keys": [],
            "resolvable_within_mandate": True,
            "requested_action": "eligible_for_staged_entry",
            "resolution_requirements": [],
            "expected_parent_id": None,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "answerable"
    assert response.json()["allowed_action"] == "eligible_for_staged_entry"
    assert response.json()["version"] == 1
    assert response.json()["schema_version"] == "underwriting.v1"


def test_underwriting_api_rejects_unknown_fields_with_422(api_client):
    response = api_client.post(
        f"{BASE}/objects",
        json={
            "kind": "company",
            "external_key": "company:acme",
            "canonical_name": "Acme",
            "unexpected": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["schema_version"] == "underwriting.v1"
    assert _error_code(response) == "validation_failed"


def test_underwriting_api_type_errors_use_underwriting_envelope(api_client):
    response = api_client.post(
        f"{BASE}/objects",
        json={"kind": 1, "external_key": "company:acme", "canonical_name": "Acme"},
    )
    assert response.status_code == 422
    assert response.json()["schema_version"] == "underwriting.v1"
    assert _error_code(response) == "validation_failed"


def test_existing_v1_validation_envelope_is_unchanged(api_client):
    response = api_client.get("/api/v1/companies?limit=not-an-integer")
    assert response.status_code == 422
    assert response.json()["schema_version"] == "v1"
    assert _error_code(response) == "validation_failed"


@pytest.mark.parametrize(
    ("field", "value"),
    [("family_key", "f" * 161), ("entry_type", "e" * 81)],
)
def test_underwriting_api_rejects_overlong_ledger_fields(api_client, field, value):
    company = _create_object(
        api_client, kind="company", external_key="company:acme", name="Acme"
    )
    basis = _create_basis(api_client)
    payload = _entry_payload(basis["id"], "reality", "revenue")
    payload[field] = value

    response = api_client.post(
        f"{BASE}/objects/{company['id']}/ledger-entries", json=payload
    )
    assert response.status_code == 422
    assert response.json()["schema_version"] == "underwriting.v1"
    assert _error_code(response) == "validation_failed"


def test_underwriting_api_commit_integrity_conflict_returns_envelope(
    api_client, session, monkeypatch
):
    def _raise_integrity_error():
        raise IntegrityError("COMMIT", {}, RuntimeError("forced conflict"))

    monkeypatch.setattr(session, "commit", _raise_integrity_error)
    response = api_client.post(
        f"{BASE}/objects",
        json={"kind": "company", "external_key": "company:acme", "canonical_name": "Acme"},
    )
    assert response.status_code == 409
    assert response.json()["schema_version"] == "underwriting.v1"
    assert _error_code(response) == "conflict"


def test_underwriting_api_conflicts_use_error_envelope(api_client):
    company = _create_object(
        api_client, kind="company", external_key="company:acme", name="Acme"
    )
    duplicate = api_client.post(
        f"{BASE}/objects",
        json={"kind": "company", "external_key": "company:acme", "canonical_name": "Again"},
    )
    assert duplicate.status_code == 409
    assert _error_code(duplicate) == "conflict"

    basis = _create_basis(api_client)
    first = api_client.post(
        f"{BASE}/objects/{company['id']}/ledger-entries",
        json=_entry_payload(basis["id"], "reality", "revenue"),
    )
    assert first.status_code == 201, first.text
    stale = api_client.post(
        f"{BASE}/objects/{company['id']}/ledger-entries",
        json=_entry_payload(basis["id"], "reality", "revenue"),
    )
    assert stale.status_code == 409
    assert _error_code(stale) == "conflict"
