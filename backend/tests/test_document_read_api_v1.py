"""Document library v1 read contract."""

from datetime import UTC, datetime

from app.models.ledger import DocumentVersion


def test_documents_list_frozen_versions(api_client, document, span):
    response = api_client.get("/api/v1/documents")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    item = payload["items"][0]
    assert item["id"] == str(document.id)
    assert item["content_sha256"] == document.content_sha256
    assert item["span_count"] >= 1
    assert item["parse_state"] == "parsed"


def test_document_detail_returns_spans_and_citations(api_client, document, span):
    response = api_client.get(f"/api/v1/documents/{document.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["document"]["id"] == str(document.id)
    assert payload["spans"][0]["verbatim_text"] == span.verbatim_text
    assert payload["spans"][0]["locator"] == span.locator


def test_documents_cutoff_excludes_future_available_version(api_client, document):
    response = api_client.get(
        "/api/v1/documents", params={"cutoff": "2000-01-01T00:00:00Z"}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_documents_excludes_version_acquired_after_cutoff(api_client, session):
    # available_at in the past but acquired_at in the future: a version not
    # yet acquired at the cutoff must not appear (no hindsight leakage).
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2026, 12, 31, tzinfo=UTC)
    version = DocumentVersion(
        content_sha256="z" * 64,
        source_url="u",
        published_at=None,
        available_at=past,
        acquired_at=future,
        parser_version="1",
        supersedes_id=None,
    )
    session.add(version)
    session.flush()
    response = api_client.get(
        "/api/v1/documents", params={"cutoff": cutoff.isoformat()}
    )
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_document_detail_returns_404_for_missing_version(api_client):
    response = api_client.get(
        "/api/v1/documents/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_document_detail_counts_citations(
    api_client, document, span, research_service
):
    statement = research_service.add_statement(
        span.id, "cited text", kind="disclosed_fact"
    )
    case = research_service.add_case(
        title="c", industry_topic="t", created_by="u"
    )
    thesis = research_service.add_thesis(case.id, statement="th", created_by="u")
    research_service.link_evidence(
        thesis.id, statement.id, role="supports", reason="r", scope={"s": "d"}
    )
    response = api_client.get(f"/api/v1/documents/{document.id}")
    assert response.status_code == 200
    span_dto = response.json()["spans"][0]
    assert len(span_dto["citations"]) >= 1
    assert span_dto["citations"][0]["role"] == "supports"
