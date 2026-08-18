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
