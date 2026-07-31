"""Honest research overview v1 read contract."""

from datetime import UTC, datetime

from app.models.ledger import ResearchCase


def test_overview_uses_ledger_counts_and_visible_assessment(
    api_client, workbench_case
):
    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(workbench_case.case.id)}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["case"]["id"] == str(workbench_case.case.id)
    assert payload["assessment"]["provisional"] is True
    assert payload["totals"]["evidence_total"] >= 1
    assert payload["totals"]["pending_review"] >= 1
    assert payload["task_queue"] == []
    assert payload["activity"] == []


def test_overview_does_not_invent_reliability_or_maturity(
    api_client, workbench_case
):
    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(workbench_case.case.id)}
    )
    text = response.text
    assert "reliable_pct" not in text
    assert "maturity" not in text
    assert "ready_for_review" not in text


def test_overview_hides_case_created_after_cutoff(api_client, session):
    future = datetime(2026, 12, 31, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    case = ResearchCase(
        title="future", industry_topic="t", created_by="u", created_at=future
    )
    session.add(case)
    session.flush()
    response = api_client.get(
        "/api/v1/overview",
        params={"case_id": str(case.id), "cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_overview_missing_case_returns_404(api_client):
    response = api_client.get(
        "/api/v1/overview",
        params={"case_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
