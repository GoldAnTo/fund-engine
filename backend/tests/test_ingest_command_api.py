"""Ingest command API tests (POST /api/v1/documents/ingest).

The endpoint COMMITS, so it runs against the private ``cmd_*`` engine
fixtures.  The real Gildata client is replaced via dependency override —
no network, no GILDATA_TOKEN needed for the success paths.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.api.v1.commands.ingest import get_gildata_client
from app.datasources.gildata.client import GildataMCPError
from app.main import app
from tests.test_gildata_client import _make_client

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def fake_gildata(cmd_client):
    """Override the Gildata client dependency with a canned fake."""

    def _override():
        yield _make_client()

    app.dependency_overrides[get_gildata_client] = _override
    try:
        yield cmd_client
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)


def test_ingest_freezes_documents_and_valuations(fake_gildata, cmd_seeded):
    from app.models.ledger import DocumentVersion, ResearchCase, ValuationSnapshot

    seeded_vals = cmd_seeded.scalar(
        select(func.count()).select_from(ValuationSnapshot)
    )

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    resp = fake_gildata.post("/api/v1/documents/ingest", json={"case_id": str(case.id)})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["research_reports"] == 2
    assert body["research_reports_skipped_degenerate"] == 0
    assert body["announcements"] == 1
    assert body["news"] == 1
    assert body["spans"] == 4
    assert body["valuations_written"] == 3
    assert body["valuations_skipped"] == 0
    assert body["stock_id"] is not None
    # cmd_seeded has a case; omitted case_id resolves to the first case.
    assert body["case_id"] is not None

    docs = cmd_seeded.scalar(select(func.count()).select_from(DocumentVersion))
    vals = cmd_seeded.scalar(select(func.count()).select_from(ValuationSnapshot))
    assert docs >= 2  # seeded docs + newly frozen ones (hash dedupe may vary)
    assert vals == seeded_vals + 3


@pytest.mark.parametrize("returned_code", ["601138.SH", "ABC"])
def test_ingest_quote_identity_failure_returns_503_and_rolls_back(
    cmd_client, cmd_seeded, returned_code,
):
    from app.models.ledger import (
        CaseDocumentVersion, Company, DocumentVersion, ResearchCase,
        SourceSpan, Stock, ValuationSnapshot,
    )

    client = _make_client()
    client._quote = [{"table_markdown": (
        "|股票名称|股票代码|最新价|市盈率TTM|市净率|总市值|\n"
        "|---|---|---|---|---|---|\n"
        f"|异常上游证券|{returned_code}|50.00|20|3|2.0e11|"
    )}]
    models = (DocumentVersion, SourceSpan, CaseDocumentVersion, Company, Stock, ValuationSnapshot)
    before = {model: cmd_seeded.scalar(select(func.count()).select_from(model)) for model in models}
    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))

    def override():
        yield client

    app.dependency_overrides[get_gildata_client] = override
    try:
        response = cmd_client.post("/api/v1/documents/ingest", json={
            "case_id": str(case.id), "quote_stock_code": "688256",
        })
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "upstream_unavailable"
    assert error["message"] == "Gildata provider request failed"
    assert error["details"] == {}
    assert returned_code not in response.text
    assert "异常上游证券" not in response.text
    assert {model: cmd_seeded.scalar(select(func.count()).select_from(model)) for model in models} == before


@pytest.mark.parametrize("invalid_code", ["", "ABC", 688256])
def test_ingest_invalid_quote_request_returns_422_without_calls_or_writes(
    cmd_client, cmd_seeded, invalid_code,
):
    from app.models.ledger import (
        CaseDocumentVersion, Company, DocumentVersion, ResearchCase,
        SourceSpan, Stock, ValuationSnapshot,
    )

    client = _make_client()
    models = (DocumentVersion, SourceSpan, CaseDocumentVersion, Company, Stock, ValuationSnapshot)
    before = {model: cmd_seeded.scalar(select(func.count()).select_from(model)) for model in models}
    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))

    def override():
        yield client

    app.dependency_overrides[get_gildata_client] = override
    try:
        response = cmd_client.post("/api/v1/documents/ingest", json={
            "case_id": str(case.id), "quote_stock_code": invalid_code,
        })
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.json()["error"]["message"] == (
        "quote_stock_code is invalid" if isinstance(invalid_code, str)
        else "request validation failed"
    )
    assert client.calls == []
    assert {model: cmd_seeded.scalar(select(func.count()).select_from(model)) for model in models} == before


def test_ingest_is_idempotent_via_api(fake_gildata, cmd_seeded):
    from app.models.ledger import ResearchCase

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    payload = {"case_id": str(case.id)}
    first = fake_gildata.post("/api/v1/documents/ingest", json=payload)
    assert first.status_code == 201

    second = fake_gildata.post("/api/v1/documents/ingest", json=payload)
    assert second.status_code == 201, second.text
    body = second.json()
    # Valuation guard: all three metrics skipped on the second run.
    assert body["valuations_written"] == 0
    assert body["valuations_skipped"] == 3


def test_ingest_unknown_case_returns_404(fake_gildata, cmd_seeded):
    resp = fake_gildata.post(
        "/api/v1/documents/ingest", json={"case_id": ZERO_UUID}
    )
    assert resp.status_code == 404


def test_ingest_requires_an_explicit_case(fake_gildata, cmd_seeded):
    resp = fake_gildata.post("/api/v1/documents/ingest", json={})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"


def test_ingest_cannot_attach_to_another_tenants_case(
    fake_gildata, cmd_seeded, monkeypatch
):
    from app.models.ledger import ResearchCase

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"test-tenant-token":"test-team","other-tenant-token":"other-team"}',
    )

    response = fake_gildata.post(
        "/api/v1/documents/ingest",
        json={"case_id": str(case.id)},
        headers={"Authorization": "Bearer other-tenant-token"},
    )

    assert response.status_code == 404


def test_ingest_without_token_returns_503(cmd_client, cmd_seeded, monkeypatch):
    """No dependency override and no GILDATA_TOKEN -> 503 envelope."""
    monkeypatch.delenv("GILDATA_TOKEN", raising=False)
    from app.models.ledger import ResearchCase

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    resp = cmd_client.post("/api/v1/documents/ingest", json={"case_id": str(case.id)})
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "upstream_unavailable"


def test_ingest_provider_failure_never_echoes_upstream_details(
    cmd_client, cmd_seeded
):
    from app.models.ledger import ResearchCase

    class FailingClient:
        def call_tool(self, name, arguments, timeout=60):
            raise GildataMCPError(
                "request https://provider.invalid?token=sentinel-secret failed"
            )

    def override():
        yield FailingClient()

    case = cmd_seeded.scalar(select(ResearchCase).order_by(ResearchCase.created_at))
    assert case is not None
    app.dependency_overrides[get_gildata_client] = override
    try:
        response = cmd_client.post(
            "/api/v1/documents/ingest", json={"case_id": str(case.id)}
        )
    finally:
        app.dependency_overrides.pop(get_gildata_client, None)

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Gildata provider request failed"
    assert "sentinel-secret" not in response.text
