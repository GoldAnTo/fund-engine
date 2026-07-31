"""Versioned API seam: health, request IDs and the stable error envelope."""

import pytest


def test_v1_health_exposes_schema_version(api_client):
    response = api_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "service": "industry-evidence-workspace",
        "status": "ok",
        "schema_version": "v1",
    }
    assert response.headers["x-request-id"]


@pytest.mark.xfail(strict=True, reason="Task 3")
def test_v1_not_found_uses_stable_error_envelope(api_client):
    response = api_client.get(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/dossier"
    )
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["request_id"] == response.headers["x-request-id"]
