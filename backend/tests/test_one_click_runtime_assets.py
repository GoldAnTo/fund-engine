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
        "company-research-worker:",
    ):
        assert name in compose
    assert "127.0.0.1:${ONE_CLICK_API_PORT:-8000}:8000" in compose
    assert "alembic upgrade head" in compose
    assert "condition: service_completed_successfully" in compose
    assert "postgresql+psycopg://${ONE_CLICK_POSTGRES_USER:?}:${ONE_CLICK_POSTGRES_PASSWORD:?}@postgres:5432/${ONE_CLICK_POSTGRES_DB:?}" in compose
    assert "RESEARCH_TENANT_TOKENS" in compose
    assert "GILDATA_TOKEN" in compose
    assert "ACQUISITION_ENABLED_ADAPTERS" in compose
    assert "scheduler:" not in compose
    assert "fund-engine-event" not in compose
    assert "RESEARCH_TENANT_TOKENS" in environment
    assert "ACQUISITION_ENABLED_ADAPTERS=sse,szse" in environment
    assert ".env.one-click.local" in ignore


def test_compose_applies_the_low_resource_backend_profile() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    environment = (ROOT / ".env.one-click.example").read_text()
    environment_values: dict[str, str] = {}
    for line in environment.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, value = line.split("=", 1)
        assert name not in environment_values
        environment_values[name] = value

    for name, value in (
        ("DATABASE_POOL_SIZE", "2"),
        ("DATABASE_MAX_OVERFLOW", "2"),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "30"),
        ("DATABASE_POOL_RECYCLE_SECONDS", "300"),
    ):
        assert f"{name}: ${{{name}:-{value}}}" in compose
        assert environment_values[name] == value

    service_slices = {
        service: compose[compose.index(f"  {service}:\n") : compose.index(f"\n  {next_service}:\n")]
        for service, next_service in (
            ("postgres", "migrate"),
            ("migrate", "file-store-init"),
            ("file-store-init", "api"),
            ("api", "research-worker"),
            ("research-worker", "acquisition-worker"),
            ("acquisition-worker", "company-research-worker"),
        )
    }

    service_slices["company-research-worker"] = compose[compose.index("  company-research-worker:\n") : compose.index("\nvolumes:\n")]

    for service in ("api", "research-worker", "acquisition-worker", "company-research-worker"):
        assert "<<: *database-pool-environment" in service_slices[service]
    for service in ("postgres", "migrate", "file-store-init"):
        assert "<<: *database-pool-environment" not in service_slices[service]

    for service, name, value in (
        ("postgres", "ONE_CLICK_POSTGRES_MEMORY_LIMIT", "1536m"),
        ("api", "ONE_CLICK_API_MEMORY_LIMIT", "1536m"),
        ("research-worker", "ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT", "768m"),
        ("acquisition-worker", "ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT", "768m"),
        ("company-research-worker", "ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT", "1280m"),
    ):
        assert f"mem_limit: ${{{name}:-{value}}}" in service_slices[service]
        assert environment_values[name] == value

    for service, name, value in (
        ("postgres", "ONE_CLICK_POSTGRES_CPU_LIMIT", "1.5"),
        ("api", "ONE_CLICK_API_CPU_LIMIT", "1.5"),
        ("research-worker", "ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT", "1.0"),
        ("acquisition-worker", "ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT", "1.0"),
        ("company-research-worker", "ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT", "1.5"),
    ):
        assert f"cpus: ${{{name}:-{value}}}" in service_slices[service]
        assert environment_values[name] == value

    assert environment_values["ONE_CLICK_ACQUISITION_REPLICAS"] == "1"



def test_compose_healthchecks_backend_services() -> None:
    compose = (ROOT / "docker-compose.one-click.yml").read_text()
    api = compose[compose.index("  api:\n") : compose.index("  research-worker:\n")]
    research_worker = compose[compose.index("  research-worker:\n") : compose.index("  acquisition-worker:\n")]
    acquisition_worker = compose[compose.index("  acquisition-worker:\n") : compose.index("  company-research-worker:\n")]
    company_research_worker = compose[compose.index("  company-research-worker:\n") : compose.index("\nvolumes:\n")]

    assert "http://127.0.0.1:8000/health" in api
    assert "healthcheck:" in api
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
