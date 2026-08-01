"""Ingest command API tests (POST /api/v1/documents/ingest).

The endpoint COMMITS, so it runs against the private ``cmd_*`` engine
fixtures.  The real Gildata client is replaced via dependency override —
no network, no GILDATA_TOKEN needed for the success paths.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.api.v1.commands.ingest import get_gildata_client
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
    from app.models.ledger import DocumentVersion, ValuationSnapshot

    seeded_vals = cmd_seeded.scalar(
        select(func.count()).select_from(ValuationSnapshot)
    )

    resp = fake_gildata.post("/api/v1/documents/ingest", json={})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["research_reports"] == 2
    assert body["announcements"] == 1
    assert body["spans"] == 3
    assert body["valuations_written"] == 3
    assert body["valuations_skipped"] == 0
    assert body["stock_id"] is not None
    # cmd_seeded has a case; omitted case_id resolves to the first case.
    assert body["case_id"] is not None

    docs = cmd_seeded.scalar(select(func.count()).select_from(DocumentVersion))
    vals = cmd_seeded.scalar(select(func.count()).select_from(ValuationSnapshot))
    assert docs >= 2  # seeded docs + newly frozen ones (hash dedupe may vary)
    assert vals == seeded_vals + 3


def test_ingest_is_idempotent_via_api(fake_gildata, cmd_seeded):
    first = fake_gildata.post("/api/v1/documents/ingest", json={})
    assert first.status_code == 201

    second = fake_gildata.post("/api/v1/documents/ingest", json={})
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


def test_ingest_without_token_returns_503(cmd_client, cmd_seeded, monkeypatch):
    """No dependency override and no GILDATA_TOKEN -> 503 envelope."""
    monkeypatch.delenv("GILDATA_TOKEN", raising=False)
    resp = cmd_client.post("/api/v1/documents/ingest", json={})
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "upstream_unavailable"
