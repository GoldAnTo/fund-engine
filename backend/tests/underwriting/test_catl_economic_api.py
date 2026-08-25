from __future__ import annotations

from datetime import UTC, datetime

from app.underwriting.fixtures.catl_baseline import load_catl_fixture
from app.underwriting.services.catl_baseline import CatlBaselineService


NOW = datetime(2025, 5, 15, 15, 59, 59, tzinfo=UTC)


def test_evidence_only_snapshot_returns_frozen_evidence_without_false_model_claims(api_client, session) -> None:
    imported = CatlBaselineService(session, now=lambda: NOW).import_fixture(load_catl_fixture())
    response = api_client.get(
        f"/api/underwriting/v1/objects/{imported.company.id}/economic-models/{imported.basis.id}"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["schema_version"] == "underwriting.v1"
    assert body["snapshot_hash"] == imported.snapshot_hash
    assert body["valuation"] is None
    assert body["industry_state"] is None
    assert body["earnings_engine"] is None
    assert body["formal_mechanisms"] == []
    assert len(body["candidate_mechanisms"]) == 6
    assert body["answerability"]["allowed_action"] == "wait_for_validation"
    assert {item["metric_key"] for item in body["observations"]} >= {
        "industry.nominal_capacity_gwh", "industry.effective_capacity_gwh", "company.revenue"
    }
    assert any(item["value"] is None for item in body["observations"])
    assert [item["source_id"] for item in body["sources"]] == sorted(
        item["source_id"] for item in body["sources"]
    )
    assert [item["metric_key"] for item in body["observations"]] == sorted(
        item["metric_key"] for item in body["observations"]
    )
    assert [item["key"] for item in body["candidate_mechanisms"]] == sorted(
        item["key"] for item in body["candidate_mechanisms"]
    )


def test_evidence_only_snapshot_is_not_found_when_not_published(api_client, session) -> None:
    from uuid import uuid4
    response = api_client.get(f"/api/underwriting/v1/objects/{uuid4()}/economic-models/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
