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
        "company-research-worker:",
        "frontend:",
    ):
        assert name in compose
    assert "127.0.0.1:${ONE_CLICK_API_PORT:-8000}:8000" in compose
    assert "127.0.0.1:${ONE_CLICK_FRONTEND_PORT:-8080}:8080" in compose
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
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse" in environment
    assert ".env.one-click.local" in ignore


def test_compose_applies_the_low_resource_profile_without_exposing_database_to_frontend() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    environment = (ROOT / ".env.one-click.example").read_text()
    frontend = compose[compose.index("  frontend:\n") : compose.index("\nvolumes:\n")]

    for name, value in (
        ("DATABASE_POOL_SIZE", "2"),
        ("DATABASE_MAX_OVERFLOW", "2"),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "30"),
        ("DATABASE_POOL_RECYCLE_SECONDS", "300"),
    ):
        assert f"{name}: ${{{name}:-{value}}}" in compose
        assert f"{name}={value}" in environment

    for assignment in (
        "ONE_CLICK_POSTGRES_MEMORY_LIMIT=1536m",
        "ONE_CLICK_API_MEMORY_LIMIT=1536m",
        "ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT=768m",
        "ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT=768m",
        "ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT=1280m",
        "ONE_CLICK_FRONTEND_MEMORY_LIMIT=256m",
        "ONE_CLICK_POSTGRES_CPU_LIMIT=1.5",
        "ONE_CLICK_API_CPU_LIMIT=1.5",
        "ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT=1.0",
        "ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT=1.0",
        "ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT=1.5",
        "ONE_CLICK_FRONTEND_CPU_LIMIT=0.5",
    ):
        name, value = assignment.split("=", 1)
        assert f"${{{name}:-{value}}}" in compose
        assert assignment in environment

    assert "DATABASE_POOL_SIZE" not in frontend
    assert "DATABASE_URL" not in frontend


def test_compose_healthchecks_http_services_before_starting_frontend() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    api = compose[compose.index("  api:\n") : compose.index("  research-worker:\n")]
    research_worker = compose[compose.index("  research-worker:\n") : compose.index("  acquisition-worker:\n")]
    acquisition_worker = compose[compose.index("  acquisition-worker:\n") : compose.index("  company-research-worker:\n")]
    company_research_worker = compose[compose.index("  company-research-worker:\n") : compose.index("  frontend:\n")]
    frontend = compose[compose.index("  frontend:\n") : compose.index("\nvolumes:\n")]

    assert "http://127.0.0.1:8000/health" in api
    assert "http://127.0.0.1:8080/health" in frontend
    assert "healthcheck:" in api
    assert "healthcheck:" in frontend
    assert "api:\n        condition: service_healthy" in frontend
    for worker, kind in (
        (research_worker, "research_run"),
        (acquisition_worker, "acquisition"),
        (company_research_worker, "company_research"),
    ):
        assert "healthcheck:" in worker
        assert "app.scripts.check_worker_heartbeat" in worker
        assert f"--worker-kind {kind}" in worker
        assert "--worker-id $$HOSTNAME" in worker
        assert "restart: unless-stopped" in worker
