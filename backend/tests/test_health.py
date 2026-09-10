def test_health_returns_service_identity(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "industry-evidence-workspace", "status": "ok"}


def test_vite_dev_ports_can_read_the_v1_api_without_opening_nonlocal_origins(client):
    allowed = client.options(
        "/api/v1/health",
        headers={
            "Origin": "http://127.0.0.1:5184",
            "Access-Control-Request-Method": "GET",
        },
    )
    blocked = client.options(
        "/api/v1/health",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5184"
    assert "access-control-allow-origin" not in blocked.headers
