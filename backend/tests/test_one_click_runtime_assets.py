from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_runtime_images_include_migrations_and_server_side_auth() -> None:
    backend = (ROOT / "backend" / "Dockerfile").read_text()
    frontend = (ROOT / "frontend" / "Dockerfile").read_text()
    server = (ROOT / "frontend" / "server" / "gatewayServer.mjs").read_text()

    assert "COPY alembic ./alembic" in backend
    assert "USER app" in backend
    assert "npm run build" in frontend
    assert "USER node" in frontend
    assert 'CMD ["node", "server/gatewayServer.mjs"]' in frontend
    assert 'process.env.GATEWAY_PROXY_TOKEN' in server
    assert 'authorization: `Bearer ${secret}`' in server
    assert 'res.flushHeaders()' in server
    assert 'response.pipe(res)' in server
    assert 'VITE_RESEARCH_BEARER_TOKEN' not in server


def test_frontend_docker_build_context_excludes_client_environment_files() -> None:
    dockerignore_path = ROOT / "frontend" / ".dockerignore"
    frontend = (ROOT / "frontend" / "Dockerfile").read_text()

    assert dockerignore_path.is_file()
    dockerignore = dockerignore_path.read_text().splitlines()
    assert ".env*" in dockerignore
    assert "VITE_RESEARCH_BEARER_TOKEN" not in frontend
    assert "COPY . " not in frontend
    assert "ARG GATEWAY_PROXY_TOKEN" not in frontend
    assert "ENV GATEWAY_PROXY_TOKEN" not in frontend
    assert "COPY --from=build /app/dist ./dist" in frontend


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
            ("company-research-worker", "frontend"),
        )
    }
    service_slices["frontend"] = frontend

    for service in ("api", "research-worker", "acquisition-worker", "company-research-worker"):
        assert "<<: *database-pool-environment" in service_slices[service]
    for service in ("postgres", "migrate", "file-store-init", "frontend"):
        assert "<<: *database-pool-environment" not in service_slices[service]

    for service, name, value in (
        ("postgres", "ONE_CLICK_POSTGRES_MEMORY_LIMIT", "1536m"),
        ("api", "ONE_CLICK_API_MEMORY_LIMIT", "1536m"),
        ("research-worker", "ONE_CLICK_RESEARCH_WORKER_MEMORY_LIMIT", "768m"),
        ("acquisition-worker", "ONE_CLICK_ACQUISITION_WORKER_MEMORY_LIMIT", "768m"),
        ("company-research-worker", "ONE_CLICK_COMPANY_RESEARCH_WORKER_MEMORY_LIMIT", "1280m"),
        ("frontend", "ONE_CLICK_FRONTEND_MEMORY_LIMIT", "256m"),
    ):
        assert f"mem_limit: ${{{name}:-{value}}}" in service_slices[service]
        assert environment_values[name] == value

    for service, name, value in (
        ("postgres", "ONE_CLICK_POSTGRES_CPU_LIMIT", "1.5"),
        ("api", "ONE_CLICK_API_CPU_LIMIT", "1.5"),
        ("research-worker", "ONE_CLICK_RESEARCH_WORKER_CPU_LIMIT", "1.0"),
        ("acquisition-worker", "ONE_CLICK_ACQUISITION_WORKER_CPU_LIMIT", "1.0"),
        ("company-research-worker", "ONE_CLICK_COMPANY_RESEARCH_WORKER_CPU_LIMIT", "1.5"),
        ("frontend", "ONE_CLICK_FRONTEND_CPU_LIMIT", "0.5"),
    ):
        assert f"cpus: ${{{name}:-{value}}}" in service_slices[service]
        assert environment_values[name] == value

    assert environment_values["ONE_CLICK_ACQUISITION_REPLICAS"] == "1"

    for name in (
        "DATABASE_POOL_SIZE",
        "DATABASE_MAX_OVERFLOW",
        "DATABASE_POOL_TIMEOUT_SECONDS",
        "DATABASE_POOL_RECYCLE_SECONDS",
        "DATABASE_URL",
    ):
        assert name not in frontend


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


def test_gateway_delivery_uses_isolated_api_database_and_loopback_publication() -> None:
    compose = (ROOT / "docker-compose.gateway.yml").read_text()
    assert "name: fundclaw-gateway" in compose
    assert "app.gateway_main:app" in compose
    assert "app.main:app" not in compose
    assert "GATEWAY_DATABASE_URL: postgresql+psycopg://gateway:" in compose
    assert "DATABASE_URL: postgresql+psycopg://gateway:" in compose
    assert "127.0.0.1:${GATEWAY_PUBLIC_PORT:-8080}:8080" in compose
    assert compose.count("ports:") == 1
    assert "ACQUISITION_ENABLED_ADAPTERS: gildata,sse,szse" in compose
    assert 'command: ["alembic", "upgrade", "head"]' in compose
    frontend = compose.split("  frontend:\n", 1)[1].split("\nvolumes:", 1)[0]
    assert "DATABASE_URL" not in frontend
    assert "GATEWAY_PROXY_TOKEN: ${GATEWAY_PROXY_TOKEN:?}" in frontend
    assert "GATEWAY_API_BASE: http://api:8000" in frontend
    assert "gateway-data:" in compose and "fund-engine-one-click-data" not in compose


def test_gateway_compose_has_one_backend_build_owner_and_worker_healthchecks() -> None:
    import yaml

    compose = (ROOT / "docker-compose.gateway.yml").read_text()
    config = yaml.safe_load(compose)
    services = config["services"]

    assert compose.count("context: ./backend") == 1
    assert services["migrate"]["image"] == "fundclaw-gateway-backend:local"
    assert services["migrate"]["build"] == {"context": "./backend"}
    for service in (
        "file-store-init",
        "api",
        "research-worker",
        "acquisition-worker",
        "professional-worker",
    ):
        assert services[service]["image"] == "fundclaw-gateway-backend:local"
        assert "build" not in services[service]

    professional = services["professional-worker"]
    assert professional["healthcheck"]["test"] == [
        "CMD-SHELL",
        "exec python -m app.scripts.check_worker_heartbeat --worker-kind professional_team --worker-id $$HOSTNAME --max-age-seconds 300",
    ]
    assert professional["restart"] == "unless-stopped"
    for name in ("research-worker", "acquisition-worker", "professional-worker", "company-study-worker"):
        worker = services[name]
        assert worker["init"] is True
        assert worker["healthcheck"]["test"][1].startswith("exec python ")


def test_gateway_launcher_uses_only_its_private_environment_and_preserves_volumes() -> None:
    script = (ROOT / "scripts" / "gateway-runtime.sh").read_text()
    assert '.env.gateway.local' in script
    assert 'docker-compose.gateway.yml' in script
    assert '--project-name fundclaw-gateway' in script
    assert '--scale professional-worker=2' in script
    assert 'down -v' not in script
    assert '.env.one-click.local' not in script
    assert 'stat.S_ISREG' in script and '0o077' in script
