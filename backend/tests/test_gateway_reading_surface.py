"""The shipped private application actually exposes the bounded readers."""
from fastapi.testclient import TestClient


def test_private_app_mounts_only_scoped_research_readers():
    from app.gateway_main import app

    prefix = "/api/v1/research-conversations/{conversation_id}/runs/{run_spec_id}"
    with TestClient(app) as client:
        paths = client.get("/openapi.json").json()["paths"]
    for suffix in ("/research", "/evidence/{evidence_link_id}", "/tasks/{task_id}/trace"):
        assert prefix + suffix in paths
        assert set(paths[prefix + suffix]) == {"get"}
    assert "/api/v1/documents/{document_id}" not in paths
    assert "/api/v1/acquisition/jobs/{job_id}/evidence" not in paths


def test_shipped_readers_require_identity_before_any_private_lookup():
    from app.gateway_main import app

    identifier = "4001171b-f697-4085-b111-2a49935dee93"
    prefix = f"/api/v1/research-conversations/{identifier}/runs/{identifier}"
    with TestClient(app) as client:
        for suffix in ("/research", f"/evidence/{identifier}", f"/tasks/{identifier}/trace"):
            response = client.get(prefix + suffix)
            assert response.status_code == 401
