"""Honest research overview v1 read contract."""

from datetime import UTC, datetime

from app.models.ledger import (
    DocumentVersion,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)


def test_overview_uses_ledger_counts_and_visible_assessment(
    api_client, workbench_case
):
    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(workbench_case.case.id)}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["case"]["id"] == str(workbench_case.case.id)
    assert payload["assessment"]["provisional"] is True
    assert payload["totals"]["evidence_total"] >= 1
    assert payload["totals"]["pending_review"] >= 1
    assert payload["task_queue"] == []
    assert payload["activity"] == []


def test_overview_does_not_invent_reliability_or_maturity(
    api_client, workbench_case
):
    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(workbench_case.case.id)}
    )
    text = response.text
    assert "reliable_pct" not in text
    assert "maturity" not in text
    assert "ready_for_review" not in text


def test_overview_hides_case_created_after_cutoff(api_client, session):
    future = datetime(2026, 12, 31, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    case = ResearchCase(
        title="future", industry_topic="t", created_by="u", created_at=future
    )
    session.add(case)
    session.flush()
    response = api_client.get(
        "/api/v1/overview",
        params={"case_id": str(case.id), "cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_overview_missing_case_returns_404(api_client):
    response = api_client.get(
        "/api/v1/overview",
        params={"case_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_overview_excludes_rejected_evidence(api_client, session):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id, statement="th", created_by="u", created_at=past
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
        normalized_text="rejected secret",
        observed_period=None,
        created_at=past,
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
            review_state="rejected",
            created_at=past,
        )
    )
    session.flush()

    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(case.id)}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["evidence_total"] == 0
    assert payload["key_changes"] == []
    assert "rejected secret" not in response.text


def test_overview_excludes_future_statement_text(api_client, session):
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2027, 1, 1, tzinfo=UTC)
    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id, statement="th", created_by="u", created_at=past
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
        normalized_text="future secret",
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
        "/api/v1/overview",
        params={"case_id": str(case.id), "cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["evidence_total"] == 0
    assert "future secret" not in response.text


def test_overview_pending_review_counts_all_unreviewed_assessments(
    api_client, research_service, assessment_service
):
    case = research_service.add_case(
        title="c", industry_topic="t", created_by="u"
    )
    thesis = research_service.add_thesis(case.id, statement="th", created_by="u")
    snapshot_cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    snap1 = assessment_service.freeze_snapshot(thesis.id, cutoff=snapshot_cutoff)
    # old assessment, never reviewed
    assessment_service.create_ai_assessment(
        snap1.id, conclusion="supported", rationale="r", gaps=[]
    )
    snap2 = assessment_service.freeze_snapshot(thesis.id, cutoff=snapshot_cutoff)
    new = assessment_service.create_ai_assessment(
        snap2.id, conclusion="supported", rationale="r2", gaps=[]
    )
    # latest assessment is reviewed
    assessment_service.review(
        new.id, outcome="confirmed", conclusion="supported", reason="ok"
    )

    response = api_client.get(
        "/api/v1/overview", params={"case_id": str(case.id)}
    )
    assert response.status_code == 200
    payload = response.json()
    # no evidence links -> machine_count 0; the old unreviewed assessment
    # must still be counted even though the latest is reviewed.
    assert payload["totals"]["pending_review"] == 1
