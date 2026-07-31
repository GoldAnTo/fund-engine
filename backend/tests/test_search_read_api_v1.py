"""Grouped ledger search v1 read contract."""

from datetime import UTC, datetime

from app.models.ledger import (
    DocumentVersion,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)


def test_search_groups_case_thesis_and_statement(api_client, workbench_case):
    response = api_client.get("/api/v1/search", params={"q": "GPU"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert {group["object_type"] for group in payload["groups"]} >= {
        "thesis",
        "evidence",
    }
    for group in payload["groups"]:
        for hit in group["hits"]:
            assert hit["deep_link"].startswith("/")


def test_search_cutoff_excludes_future_evidence(api_client, workbench_case):
    response = api_client.get(
        "/api/v1/search",
        params={"q": "CapEx", "cutoff": "2000-01-01T00:00:00Z", "research_mode": "true"},
    )
    assert response.status_code == 200
    evidence = next(
        (g for g in response.json()["groups"] if g["object_type"] == "evidence"),
        None,
    )
    assert evidence is None or evidence["hits"] == []


def test_search_evidence_respects_research_mode(api_client, workbench_case):
    base = {"q": "CapEx"}
    default = api_client.get("/api/v1/search", params=base)
    assert default.status_code == 200
    evidence_default = next(
        g for g in default.json()["groups"] if g["object_type"] == "evidence"
    )
    assert evidence_default["hits"] == []

    research = api_client.get(
        "/api/v1/search", params={**base, "research_mode": "true"}
    )
    assert research.status_code == 200
    evidence_research = next(
        g for g in research.json()["groups"] if g["object_type"] == "evidence"
    )
    assert len(evidence_research["hits"]) >= 1


def test_search_matches_case_title(api_client, workbench_case):
    response = api_client.get("/api/v1/search", params={"q": "compute"})
    assert response.status_code == 200
    case_group = next(
        g for g in response.json()["groups"] if g["object_type"] == "case"
    )
    assert len(case_group["hits"]) >= 1


def test_search_rejects_unknown_types(api_client, workbench_case):
    response = api_client.get(
        "/api/v1/search", params={"q": "GPU", "types": "bogus"}
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["error"]["code"] == "validation_failed"


def test_search_validates_min_query_length(api_client, workbench_case):
    response = api_client.get("/api/v1/search", params={"q": "a"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_search_evidence_excludes_future_statement_thesis_case(
    api_client, session
):
    # A link with past timestamps that points at a statement/thesis/case
    # created in the future must not surface as an evidence hit (no hindsight
    # leakage from backfilled relation targets).
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2027, 1, 1, tzinfo=UTC)

    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=future
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="CapEx future",
        created_by="u",
        created_at=future,
    )
    session.add(thesis)
    session.flush()
    version = DocumentVersion(
        content_sha256="z" * 64,
        source_url="u",
        published_at=None,
        available_at=past,
        acquired_at=past,
        parser_version="1",
        supersedes_id=None,
    )
    session.add(version)
    session.flush()
    span = SourceSpan(
        document_version_id=version.id, locator={"p": 1}, verbatim_text="v"
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="disclosed_fact",
        normalized_text="CapEx grew",
        observed_period=None,
        created_at=future,
    )
    session.add(statement)
    session.flush()
    session.add(
        EvidenceLink(
            thesis_id=thesis.id,
            source_statement_id=statement.id,
            role="supports",
            reason="r",
            scope={"s": "d"},
            available_at=past,
            creator_type="ai",
            review_state="reviewed",
            created_at=past,
        )
    )
    session.flush()

    response = api_client.get(
        "/api/v1/search",
        params={"q": "CapEx", "cutoff": cutoff.isoformat(), "research_mode": "true"},
    )
    assert response.status_code == 200
    evidence = next(
        (g for g in response.json()["groups"] if g["object_type"] == "evidence"),
        None,
    )
    assert evidence is None or evidence["hits"] == []


def test_search_has_more_when_group_truncated(api_client, session):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    session.add(
        Thesis(
            research_case_id=case.id,
            statement="CapEx alpha",
            created_by="u",
            created_at=past,
        )
    )
    session.add(
        Thesis(
            research_case_id=case.id,
            statement="CapEx beta",
            created_by="u",
            created_at=past,
        )
    )
    session.flush()

    response = api_client.get(
        "/api/v1/search", params={"q": "CapEx", "limit": 1}
    )
    assert response.status_code == 200
    payload = response.json()
    thesis_group = next(
        g for g in payload["groups"] if g["object_type"] == "thesis"
    )
    assert len(thesis_group["hits"]) == 1
    assert payload["page"]["has_more"] is True
