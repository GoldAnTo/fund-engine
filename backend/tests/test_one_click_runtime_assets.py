from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_backend_image_includes_migrations_and_unprivileged_user() -> None:
    backend = (ROOT / "backend" / "Dockerfile").read_text()
    assert "COPY alembic ./alembic" in backend
    assert "USER app" in backend




def test_compose_has_separate_data_and_automatic_services() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    environment = (ROOT / ".env.one-click.example").read_text()
    ignore = (ROOT / ".gitignore").read_text().splitlines()

    assert "  frontend:" not in compose
    assert "context: ./frontend" not in compose
    assert "name: fund-engine-one-click" in compose
    assert "fund-engine-one-click-data" in compose
    for name in (
        "postgres:",
        "migrate:",
        "api:",
        "research-worker:",
        "acquisition-worker:",
    ):
        assert name in compose
    assert "127.0.0.1:8000:8000" in compose
    assert "alembic upgrade head" in compose
    assert "condition: service_completed_successfully" in compose
    assert "postgresql+psycopg://${ONE_CLICK_POSTGRES_USER:?}:${ONE_CLICK_POSTGRES_PASSWORD:?}@postgres:5432/${ONE_CLICK_POSTGRES_DB:?}" in compose
    assert "RESEARCH_TENANT_TOKENS" in compose
    assert "GILDATA_TOKEN" in compose
    assert "ACQUISITION_ENABLED_ADAPTERS" in compose
    assert "scheduler:" not in compose
    assert "fund-engine-event" not in compose
    assert "RESEARCH_TENANT_TOKENS" in environment
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata" in environment
    assert ".env.one-click.local" in ignore


def test_compose_healthchecks_backend_services() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    api = compose[compose.index("  api:\n") : compose.index("  research-worker:\n")]
    research_worker = compose[compose.index("  research-worker:\n") : compose.index("  acquisition-worker:\n")]
    acquisition_worker = compose[compose.index("  acquisition-worker:\n") : compose.index("\nvolumes:\n")]

    assert "http://127.0.0.1:8000/health" in api
    assert "healthcheck:" in api
    for worker, kind in ((research_worker, "research_run"), (acquisition_worker, "acquisition")):
        assert "healthcheck:" in worker
        assert "app.scripts.check_worker_heartbeat" in worker
        assert f"--worker-kind {kind}" in worker
        assert "--worker-id $$HOSTNAME" in worker
        assert "restart: unless-stopped" in worker
