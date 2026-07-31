"""Versioned API seam: health, request IDs, error envelope, and historical basis."""

from datetime import UTC, datetime

from app.queries.basis import HistoricalBasis


def test_v1_health_exposes_schema_version(api_client):
    response = api_client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "service": "industry-evidence-workspace",
        "status": "ok",
        "schema_version": "v1",
    }
    assert response.headers["x-request-id"]


def test_v1_not_found_uses_stable_error_envelope(api_client):
    response = api_client.get(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/dossier"
    )
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "not_found"
    assert payload["error"]["request_id"] == response.headers["x-request-id"]


def test_basis_normalizes_naive_cutoff_to_utc():
    basis = HistoricalBasis.from_cutoff(datetime(2024, 5, 31, 12, 0))
    assert basis.cutoff == datetime(2024, 5, 31, 12, 0, tzinfo=UTC)
    assert basis.is_historical is True


def test_basis_current_uses_injected_clock():
    now = datetime(2026, 7, 31, 4, 0, tzinfo=UTC)
    basis = HistoricalBasis.from_cutoff(None, now=lambda: now)
    assert basis.cutoff == now
    assert basis.is_historical is False


def test_basis_normalizes_naive_injected_clock_to_utc():
    naive_now = datetime(2026, 7, 31, 4, 0)
    basis = HistoricalBasis.from_cutoff(None, now=lambda: naive_now)
    assert basis.cutoff == datetime(2026, 7, 31, 4, 0, tzinfo=UTC)
    assert basis.cutoff.tzinfo is not None
    assert basis.is_historical is False


def test_basis_to_dto_preserves_timezone():
    basis = HistoricalBasis.from_cutoff(datetime(2024, 5, 31, 12, 0))
    dto = basis.to_dto()
    assert dto.cutoff == datetime(2024, 5, 31, 12, 0, tzinfo=UTC)
    assert dto.cutoff.tzinfo is not None
    assert dto.is_historical is True


def test_unhandled_exception_returns_500_envelope_with_request_id(api_client):
    from app.main import app

    @app.get("/api/v1/__raise__")
    def _raise():
        raise RuntimeError("boom")

    try:
        response = api_client.get("/api/v1/__raise__")
        assert response.status_code == 500
        payload = response.json()
        assert payload["schema_version"] == "v1"
        assert payload["error"]["code"] == "internal_error"
        assert payload["error"]["request_id"] == response.headers["x-request-id"]
        # Internal exception text must not leak to the client (design 7.3).
        assert "boom" not in response.text
    finally:
        app.router.routes = [
            r
            for r in app.router.routes
            if getattr(r, "path", "") != "/api/v1/__raise__"
        ]
