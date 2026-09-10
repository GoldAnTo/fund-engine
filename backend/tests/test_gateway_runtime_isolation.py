"""A private Gateway never inherits the legacy global data surface."""
import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def test_gateway_main_refuses_missing_isolated_database_before_engine_creation():
    """A production import must not fall back to the legacy default database."""
    environment = os.environ.copy()
    environment["APP_ENV"] = "production"
    environment.pop("GATEWAY_DATABASE_URL", None)
    environment.pop("DATABASE_URL", None)
    environment.pop("TEST_DATABASE_URL", None)
    probe = """
import sqlalchemy

def forbidden_engine(*args, **kwargs):
    raise AssertionError("gateway import attempted to create a default database engine")

sqlalchemy.create_engine = forbidden_engine
try:
    import app.gateway_main
except RuntimeError as exc:
    if "GATEWAY_DATABASE_URL" not in str(exc):
        raise
    print("isolated-url-required")
else:
    raise AssertionError("gateway import succeeded without an isolated database URL")
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "isolated-url-required"


def test_dedicated_gateway_app_does_not_mount_legacy_data_or_control_routes():
    from app.gateway_main import app

    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        for path in ("/api/v1/documents", "/api/v1/activity", "/api/v1/research-cases",
                     "/api/v1/tasks", "/api/v1/automatic-research"):
            assert client.get(path).status_code == 404
        paths = client.get("/openapi.json").json()["paths"]
        assert "/api/v1/research-conversations" in paths
        assert "/api/v1/documents" not in paths


def test_legacy_shared_app_refuses_private_gateway_creation(monkeypatch, cmd_session):
    from app.db import get_db
    from app.main import app

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "a", "subject_id": "alice"}}))
    app.dependency_overrides[get_db] = lambda: cmd_session
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/research-conversations",
                headers={"Authorization": "Bearer token", "Idempotency-Key": "no-legacy-start"},
                json={"initial_message": "研究请求"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 503
    assert "isolated" in response.json()["error"]["message"]
