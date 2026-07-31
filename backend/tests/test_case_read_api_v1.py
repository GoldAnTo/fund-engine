"""Case list and dossier v1 read contract."""

from datetime import UTC, datetime


def test_case_list_returns_navigation_rows(api_client, workbench_case):
    response = api_client.get("/api/v1/research-cases")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["items"][0]["id"] == str(workbench_case.case.id)
    assert payload["items"][0]["title"] == workbench_case.case.title


def test_dossier_selects_requested_thesis_and_respects_cutoff(
    api_client, workbench_case
):
    # workbench_case evidence/assessment are timestamped "now", so the cutoff
    # must be in the future for them to be visible (matches test_time_travel).
    cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    response = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/dossier",
        params={
            "thesis_id": str(workbench_case.thesis.id),
            "cutoff": cutoff.isoformat(),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["focus_thesis_id"] == str(workbench_case.thesis.id)
    # cutoff is echoed in the basis; compare parsed to tolerate Z/+00:00.
    assert datetime.fromisoformat(payload["basis"]["cutoff"]) == cutoff
    assert payload["basis"]["is_historical"] is True
    assert payload["assessment"]["provisional"] is True
    assert payload["evidence"]["supports"][0]["verbatim_text"]
    assert "confidence" not in payload["assessment"]
    assert "ready_for_review" not in payload


def test_missing_case_returns_v1_error(api_client):
    response = api_client.get(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/dossier"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
