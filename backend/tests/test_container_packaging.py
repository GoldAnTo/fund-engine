from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_backend_image_runs_as_non_root_and_contains_runtime_entrypoints() -> None:
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")

    assert "USER app" in dockerfile
    assert '"uvicorn", "app.main:app"' in dockerfile
    assert "COPY alembic" in dockerfile
    assert "COPY app" in dockerfile


def test_frontend_image_serves_spa_and_proxies_api_without_secrets() -> None:
    dockerfile = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    nginx = (ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "nginxinc/nginx-unprivileged" in dockerfile
    assert "VITE_OIDC_AUTHORITY" in dockerfile
    assert "VITE_RESEARCH_BEARER_TOKEN" not in dockerfile
    assert "try_files $uri $uri/ /index.html" in nginx
    assert "proxy_pass http://$api_upstream" in nginx
    assert "client_max_body_size 25m" in nginx
    assert "resolver 127.0.0.11" in nginx
