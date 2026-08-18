from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_runtime_images_include_migrations_and_server_side_auth() -> None:
    backend = (ROOT / "backend" / "Dockerfile").read_text()
    frontend = (ROOT / "frontend" / "Dockerfile").read_text()
    nginx = (ROOT / "frontend" / "nginx.one-click.conf.template").read_text()

    assert "COPY alembic ./alembic" in backend
    assert "USER app" in backend
    assert "npm run build" in frontend
    assert 'proxy_set_header Authorization "Bearer ${RESEARCH_BEARER_TOKEN}";' in nginx
    assert "VITE_RESEARCH_BEARER_TOKEN" not in nginx


def test_frontend_docker_build_context_excludes_client_environment_files() -> None:
    dockerignore_path = ROOT / "frontend" / ".dockerignore"
    frontend = (ROOT / "frontend" / "Dockerfile").read_text()

    assert dockerignore_path.is_file()
    dockerignore = dockerignore_path.read_text().splitlines()
    assert ".env*" in dockerignore
    assert "VITE_RESEARCH_BEARER_TOKEN" not in frontend


def test_compose_has_separate_data_and_automatic_services() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    environment = (ROOT / ".env.one-click.example").read_text()
    ignore = (ROOT / ".gitignore").read_text().splitlines()

    assert "name: fund-engine-one-click" in compose
    assert "fund-engine-one-click-data" in compose
    for name in (
        "postgres:",
        "migrate:",
        "api:",
        "research-worker:",
        "acquisition-worker:",
        "frontend:",
    ):
        assert name in compose
    assert "127.0.0.1:8000:8000" in compose
    assert "127.0.0.1:8080:8080" in compose
    assert "alembic upgrade head" in compose
    assert "condition: service_completed_successfully" in compose
    assert "postgresql+psycopg://${ONE_CLICK_POSTGRES_USER:?}:${ONE_CLICK_POSTGRES_PASSWORD:?}@postgres:5432/${ONE_CLICK_POSTGRES_DB:?}" in compose
    assert "RESEARCH_TENANT_TOKENS" in compose
    assert "GILDATA_TOKEN" in compose
    assert "ACQUISITION_ENABLED_ADAPTERS" in compose
    assert "RESEARCH_BEARER_TOKEN" in compose
    assert "scheduler:" not in compose
    assert "fund-engine-event" not in compose
    assert "RESEARCH_TENANT_TOKENS" in environment
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse,gildata" in environment
    assert ".env.one-click.local" in ignore


def test_compose_healthchecks_http_services_before_starting_frontend() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    api = compose[compose.index("  api:\n") : compose.index("  research-worker:\n")]
    research_worker = compose[compose.index("  research-worker:\n") : compose.index("  acquisition-worker:\n")]
    acquisition_worker = compose[compose.index("  acquisition-worker:\n") : compose.index("  frontend:\n")]
    frontend = compose[compose.index("  frontend:\n") : compose.index("\nvolumes:\n")]

    assert "http://127.0.0.1:8000/health" in api
    assert "http://127.0.0.1:8080/health" in frontend
    assert "healthcheck:" in api
    assert "healthcheck:" in frontend
    assert "api:\n        condition: service_healthy" in frontend
    for worker, kind in ((research_worker, "research_run"), (acquisition_worker, "acquisition")):
        assert "healthcheck:" in worker
        assert "app.scripts.check_worker_heartbeat" in worker
        assert f"--worker-kind {kind}" in worker
        assert "--worker-id $$HOSTNAME" in worker
        assert "restart: unless-stopped" in worker
