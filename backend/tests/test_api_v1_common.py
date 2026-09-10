import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.errors import NotFoundError
from app.main import app
from app.schemas.v1.common import (
    CursorPage,
    ErrorBody,
    ErrorEnvelope,
    HistoricalBasisDTO,
)


@app.get("/_test/unhandled-server-error")
def raise_unhandled_server_error() -> None:
    raise RuntimeError("test server error")


def test_v1_health_exposes_schema_version_and_generated_request_id(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "industry-evidence-workspace",
        "status": "ok",
        "schema_version": "v1",
    }
    uuid.UUID(response.headers["x-request-id"])


def test_request_id_middleware_preserves_caller_supplied_id_on_legacy_route(client):
    response = client.get("/health", headers={"x-request-id": "caller-request-id"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "caller-request-id"


def test_unhandled_server_error_gets_generated_request_id():
    with TestClient(app, raise_server_exceptions=False) as server_client:
        response = server_client.get("/_test/unhandled-server-error")

    assert response.status_code == 500
    uuid.UUID(response.headers["x-request-id"])
    assert "test server error" not in response.text


def test_unhandled_server_error_preserves_caller_supplied_request_id():
    with TestClient(app, raise_server_exceptions=False) as server_client:
        response = server_client.get(
            "/_test/unhandled-server-error",
            headers={"x-request-id": "caller-server-error-id"},
        )

    assert response.status_code == 500
    assert response.headers["x-request-id"] == "caller-server-error-id"


def test_unhandled_server_error_still_propagates_for_server_logging():
    with TestClient(app) as server_client:
        with pytest.raises(RuntimeError, match="test server error"):
            server_client.get("/_test/unhandled-server-error")


def test_not_found_error_uses_stable_error_envelope(client):
    @app.get("/_test/not-found")
    def raise_not_found():
        raise NotFoundError("test resource not found")

    response = client.get(
        "/_test/not-found", headers={"x-request-id": "not-found-request-id"}
    )

    assert response.status_code == 404
    assert response.headers["x-request-id"] == "not-found-request-id"
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "test resource not found",
            "request_id": "not-found-request-id",
            "details": {},
        }
    }


def test_v1_wire_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        CursorPage(has_more=False, unexpected=True)


def test_historical_basis_and_cursor_page_expose_stable_defaults():
    cutoff = datetime(2026, 7, 31, tzinfo=UTC)

    basis = HistoricalBasisDTO(cutoff=cutoff, is_historical=True)
    page = CursorPage()

    assert basis.model_dump() == {
        "cutoff": cutoff,
        "is_historical": True,
        "ledger_high_watermark": None,
        "projection_built_at": None,
        "projection_schema_version": None,
    }
    assert page.model_dump() == {"next_cursor": None, "has_more": False}


def test_error_envelope_uses_an_independent_details_dict_per_body():
    first = ErrorBody(code="one", message="first", request_id="request-1")
    second = ErrorBody(code="two", message="second", request_id="request-2")

    first.details["resource"] = "case"
    envelope = ErrorEnvelope(error=first)

    assert second.details == {}
    assert envelope.model_dump() == {
        "error": {
            "code": "one",
            "message": "first",
            "request_id": "request-1",
            "details": {"resource": "case"},
        }
    }
