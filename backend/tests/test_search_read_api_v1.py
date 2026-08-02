"""Grouped ledger search v1 read contract."""

import uuid
from datetime import UTC, datetime

from app.models.ledger import (
    DocumentVersion,
    EvidenceLink,
    EvidenceReview,
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


# ---------------------------------------------------------------------------
# P0 regression: search must derive effective review state (walkthrough 2026-08-02)
# ---------------------------------------------------------------------------


def _seed_link_for_review(session, *, link_created_at, review=None):
    """Minimal document→statement→case→thesis→link chain plus an optional
    (outcome, created_at) review, with fully explicit timestamps."""
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=link_created_at
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="CapEx thesis",
        created_by="u",
        created_at=link_created_at,
    )
    session.add(thesis)
    session.flush()
    version = DocumentVersion(
        content_sha256=(uuid.uuid4().hex * 2)[:64],
        source_url="u",
        published_at=None,
        available_at=link_created_at,
        acquired_at=link_created_at,
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
        normalized_text="CapEx grew strongly",
        observed_period=None,
        created_at=link_created_at,
    )
    session.add(statement)
    session.flush()
    link = EvidenceLink(
        thesis_id=thesis.id,
        source_statement_id=statement.id,
        role="supports",
        reason="r",
        scope={"s": "d"},
        available_at=link_created_at,
        creator_type="ai",
        review_state="machine_generated",
        created_at=link_created_at,
    )
    session.add(link)
    session.flush()
    if review is not None:
        outcome, review_created_at = review
        session.add(
            EvidenceReview(
                evidence_link_id=link.id,
                outcome=outcome,
                relation="supports",
                factor_role="f",
                scope_boundary="s",
                reason="r",
                reviewer="tester",
                created_at=review_created_at,
            )
        )
        session.flush()
    return link


def _evidence_group(payload):
    return next(
        g for g in payload["groups"] if g["object_type"] == "evidence"
    )


def test_search_evidence_surfaces_after_human_review(cmd_client, cmd_session):
    """P0 regression (walkthrough 2026-08-02): a human-confirmed link must
    appear in default search even though the frozen
    ``EvidenceLink.review_state`` stays ``machine_generated`` (append-only).
    """
    from app.repositories.documents import DocumentRepository
    from app.repositories.research import ResearchRepository
    from app.services.ingest import DocumentService
    from app.services.research import ResearchService

    documents = DocumentService(DocumentRepository(cmd_session))
    research = ResearchService(ResearchRepository(cmd_session))
    version = documents.freeze(raw=b"wb", source_url="https://example.test/r")
    span = documents.add_span(
        document_version_id=version.id,
        locator={"page": 1},
        verbatim_text="CapEx 同比增长 40%",
    )
    statement = research.add_statement(
        span.id, "CapEx 同比增长 40%", kind="disclosed_fact"
    )
    case = research.add_case(title="t", industry_topic="t", created_by="tester")
    thesis = research.add_thesis(case.id, statement="t", created_by="tester")
    link = research.link_evidence(
        thesis.id, statement.id, role="supports", reason="r", scope={"s": "d"}
    )
    cmd_session.commit()

    before = cmd_client.get("/api/v1/search", params={"q": "CapEx"})
    assert before.status_code == 200
    assert _evidence_group(before.json())["hits"] == []

    review = cmd_client.post(
        f"/api/v1/evidence-links/{link.id}/reviews",
        json={
            "outcome": "confirmed",
            "relation": "supports",
            "factor_role": "证据因素",
            "scope_boundary": "行业范围：AI 算力",
            "reason": "人工复核确认",
            "reviewer": "tester",
        },
    )
    assert review.status_code == 201

    after = cmd_client.get("/api/v1/search", params={"q": "CapEx"})
    assert after.status_code == 200
    hits = _evidence_group(after.json())["hits"]
    assert [h["object_id"] for h in hits] == [str(link.id)]
    assert hits[0]["review_state"] == "reviewed"


def test_search_evidence_review_cutoff_and_rejected(api_client, session):
    """Reviews created after the cutoff do not exist for historical replay;
    a rejected link is never returned (default or research mode)."""
    link_created = datetime(2025, 1, 1, tzinfo=UTC)
    review_created = datetime(2025, 6, 1, tzinfo=UTC)
    before_review = datetime(2025, 3, 1, tzinfo=UTC)
    after_review = datetime(2026, 1, 1, tzinfo=UTC)

    confirmed_link = _seed_link_for_review(
        session,
        link_created_at=link_created,
        review=("confirmed", review_created),
    )

    # 1. Cutoff before the review: default mode still hides the link
    #    (effective state at that time = pending machine_generated).
    resp = api_client.get(
        "/api/v1/search",
        params={"q": "CapEx", "cutoff": before_review.isoformat()},
    )
    assert resp.status_code == 200
    assert _evidence_group(resp.json())["hits"] == []

    # 2. Same cutoff under research mode: pending link is visible as
    #    machine_generated (its state at that cutoff).
    resp = api_client.get(
        "/api/v1/search",
        params={
            "q": "CapEx",
            "cutoff": before_review.isoformat(),
            "research_mode": "true",
        },
    )
    hits = _evidence_group(resp.json())["hits"]
    assert [h["object_id"] for h in hits] == [str(confirmed_link.id)]
    assert hits[0]["review_state"] == "machine_generated"

    # 3. Cutoff after the review: default mode surfaces it as reviewed.
    resp = api_client.get(
        "/api/v1/search",
        params={"q": "CapEx", "cutoff": after_review.isoformat()},
    )
    hits = _evidence_group(resp.json())["hits"]
    assert [h["object_id"] for h in hits] == [str(confirmed_link.id)]
    assert hits[0]["review_state"] == "reviewed"

    # 4. A rejected link is excluded from both modes.
    rejected_link = _seed_link_for_review(
        session,
        link_created_at=link_created,
        review=("rejected", review_created),
    )
    for params in (
        {"q": "CapEx", "cutoff": after_review.isoformat()},
        {"q": "CapEx", "cutoff": after_review.isoformat(), "research_mode": "true"},
    ):
        resp = api_client.get("/api/v1/search", params=params)
        hits = _evidence_group(resp.json())["hits"]
        assert str(rejected_link.id) not in [h["object_id"] for h in hits]
