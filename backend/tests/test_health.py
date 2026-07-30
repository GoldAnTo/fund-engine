def test_health_returns_service_identity(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "industry-evidence-workspace", "status": "ok"}
