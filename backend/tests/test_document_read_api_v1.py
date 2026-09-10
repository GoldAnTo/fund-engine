"""Document library v1 read contract."""

from datetime import UTC, datetime

from app.models.ledger import DocumentVersion, EvidenceLink


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
    response = api_client.get(
        f"/api/v1/documents/{document.id}", params={"research_mode": "true"}
    )
    assert response.status_code == 200
    span_dto = response.json()["spans"][0]
    assert len(span_dto["citations"]) >= 1
    assert span_dto["citations"][0]["role"] == "supports"


def test_document_detail_citations_hidden_by_default(
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
    assert response.json()["spans"][0]["citations"] == []


def test_document_detail_citations_never_return_rejected(
    api_client, document, span, research_service, session
):
    statement = research_service.add_statement(
        span.id, "cited text", kind="disclosed_fact"
    )
    case = research_service.add_case(
        title="c", industry_topic="t", created_by="u"
    )
    thesis = research_service.add_thesis(case.id, statement="th", created_by="u")
    now = datetime.now(UTC)
    session.add(
        EvidenceLink(
            thesis_id=thesis.id,
            source_statement_id=statement.id,
            role="supports",
            reason="r",
            scope={"s": "d"},
            available_at=now,
            creator_type="ai",
            review_state="rejected",
            created_at=now,
        )
    )
    session.flush()
    response = api_client.get(
        f"/api/v1/documents/{document.id}", params={"research_mode": "true"}
    )
    assert response.status_code == 200
    assert response.json()["spans"][0]["citations"] == []


def test_documents_q_and_limit_do_not_miss_results(api_client, document_service):
    # q must filter in SQL before limit+1, otherwise an older matching doc is
    # missed when newer non-matching docs fill the window.
    old = document_service.freeze(
        raw=b"old", source_url="https://example.test/old"
    )
    document_service.freeze(raw=b"mid", source_url="https://example.test/mid")
    document_service.freeze(raw=b"new", source_url="https://example.test/new")
    response = api_client.get(
        "/api/v1/documents", params={"q": "old", "limit": 1}
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(old.id)


def test_documents_cursor_reads_second_page(api_client, document_service):
    d1 = document_service.freeze(
        raw=b"p1", source_url="https://example.test/p1"
    )
    document_service.freeze(raw=b"p2", source_url="https://example.test/p2")
    document_service.freeze(raw=b"p3", source_url="https://example.test/p3")

    first = api_client.get("/api/v1/documents", params={"limit": 2})
    assert first.status_code == 200
    first_payload = first.json()
    assert len(first_payload["items"]) == 2
    assert first_payload["page"]["has_more"] is True
    assert first_payload["page"]["next_cursor"] is not None

    second = api_client.get(
        "/api/v1/documents",
        params={"limit": 2, "cursor": first_payload["page"]["next_cursor"]},
    )
    assert second.status_code == 200
    second_payload = second.json()
    assert len(second_payload["items"]) == 1
    assert second_payload["page"]["has_more"] is False
    first_ids = {i["id"] for i in first_payload["items"]}
    second_ids = {i["id"] for i in second_payload["items"]}
    assert first_ids & second_ids == set()
    assert second_ids == {str(d1.id)}


def test_documents_malformed_cursor_returns_422(api_client, document):
    response = api_client.get(
        "/api/v1/documents", params={"cursor": "W10="}
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# Extraction watermark (defect-3 fix): AIRun-derived extraction_state
# ---------------------------------------------------------------------------


def _add_extract_run(session, version_id, *, status="success", started_at):
    from app.models.ledger import AIRun

    run = AIRun(
        kind="extract",
        model_version="test-model",
        prompt_version="v1",
        input_ref={"document_version_id": str(version_id)},
        output_summary=(
            "llm returned 0 statements" if status == "success" else ""
        ),
        status=status,
        error=None if status == "success" else "boom",
        started_at=started_at,
        finished_at=started_at,
    )
    session.add(run)
    session.flush()
    return run


def _list_state(api_client, document) -> dict:
    response = api_client.get("/api/v1/documents")
    assert response.status_code == 200
    (item,) = [i for i in response.json()["items"] if i["id"] == str(document.id)]
    return item


def test_extraction_state_not_attempted_without_runs(api_client, document, span):
    item = _list_state(api_client, document)
    assert item["extraction_state"] == "not_attempted"
    assert item["last_extracted_at"] is None


def test_extraction_state_extracted_empty_after_zero_output_run(
    api_client, session, document, span
):
    run = _add_extract_run(
        session, document.id, started_at=datetime(2026, 7, 1, tzinfo=UTC)
    )
    item = _list_state(api_client, document)
    assert item["extraction_state"] == "extracted_empty"
    assert item["last_extracted_at"] == run.finished_at.isoformat()


def test_extraction_state_failed_after_failed_run(
    api_client, session, document, span
):
    _add_extract_run(
        session,
        document.id,
        status="failed",
        started_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    item = _list_state(api_client, document)
    assert item["extraction_state"] == "failed"


def test_extraction_state_latest_run_wins(
    api_client, session, document, span
):
    _add_extract_run(
        session, document.id, started_at=datetime(2026, 7, 1, tzinfo=UTC)
    )
    _add_extract_run(
        session,
        document.id,
        status="failed",
        started_at=datetime(2026, 7, 2, tzinfo=UTC),
    )
    item = _list_state(api_client, document)
    assert item["extraction_state"] == "failed"


def test_extraction_state_extracted_when_statements_exist(
    api_client, session, document, statement
):
    # A statement exists even though no run is recorded (e.g. seeded data):
    # output trumps the audit trail.
    item = _list_state(api_client, document)
    assert item["statement_count"] >= 1
    assert item["extraction_state"] == "extracted"


def test_document_detail_carries_extraction_state(
    api_client, session, document, span
):
    _add_extract_run(
        session, document.id, started_at=datetime(2026, 7, 1, tzinfo=UTC)
    )
    response = api_client.get(f"/api/v1/documents/{document.id}")
    assert response.status_code == 200
    assert response.json()["document"]["extraction_state"] == "extracted_empty"


def test_pending_versions_skip_successfully_extracted(
    session, document_service, research_service
):
    """_pending_versions must exclude zero-output-but-attempted versions."""
    from app.scripts.run_ai_engine import _pending_versions

    version = document_service.freeze(
        raw=b"degenerate content", source_url="https://example.test/deg"
    )
    document_service.add_span(
        document_version_id=version.id,
        locator={"page": 1},
        verbatim_text=(
            "寒武纪2024年营业收入11.74亿元，同比增长65.56%，云端产品线收入"
            "大幅增长，主要受益于国产AI算力需求持续爆发。"
        ),
    )
    assert version in _pending_versions(session)

    _add_extract_run(
        session, version.id, started_at=datetime(2026, 7, 1, tzinfo=UTC)
    )
    assert version not in _pending_versions(session)


def test_pending_versions_keep_failed_versions_retryable(
    session, document_service
):
    from app.scripts.run_ai_engine import _pending_versions

    version = document_service.freeze(
        raw=b"retry me", source_url="https://example.test/retry"
    )
    document_service.add_span(
        document_version_id=version.id,
        locator={"page": 1},
        verbatim_text=(
            "寒武纪2024年营业收入11.74亿元，同比增长65.56%，云端产品线收入"
            "大幅增长，主要受益于国产AI算力需求持续爆发。"
        ),
    )
    _add_extract_run(
        session,
        version.id,
        status="failed",
        started_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    assert version in _pending_versions(session)
