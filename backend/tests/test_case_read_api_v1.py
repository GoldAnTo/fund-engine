"""Case list and dossier v1 read contract."""

import base64
import json
from datetime import UTC, datetime

from app.models.ledger import (
    AIAssessment,
    CausalStep,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Thesis,
)


def test_case_list_returns_navigation_rows(api_client, workbench_case):
    response = api_client.get("/api/v1/research-cases")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["items"][0]["id"] == str(workbench_case.case.id)
    assert payload["items"][0]["title"] == workbench_case.case.title


def test_case_list_paginates_with_cursor(api_client, research_service):
    cases = [
        research_service.add_case(title=f"c{i}", industry_topic="t", created_by="u")
        for i in range(3)
    ]
    page1 = api_client.get("/api/v1/research-cases", params={"limit": 2})
    assert page1.status_code == 200
    p1 = page1.json()
    assert len(p1["items"]) == 2
    assert p1["page"]["has_more"] is True
    assert p1["page"]["next_cursor"]

    page2 = api_client.get(
        "/api/v1/research-cases",
        params={"limit": 2, "cursor": p1["page"]["next_cursor"]},
    )
    p2 = page2.json()
    assert len(p2["items"]) == 1
    assert p2["page"]["has_more"] is False

    ids1 = {i["id"] for i in p1["items"]}
    ids2 = {i["id"] for i in p2["items"]}
    assert ids1.isdisjoint(ids2)
    assert (ids1 | ids2) == {str(c.id) for c in cases}


def test_malformed_cursor_returns_422_envelope(api_client, workbench_case):
    null_field = base64.urlsafe_b64encode(
        json.dumps({"created_at": None, "id": str(workbench_case.case.id)}).encode()
    ).decode()
    for bad in ["W10=", null_field, "!!!notbase64"]:
        response = api_client.get("/api/v1/research-cases", params={"cursor": bad})
        assert response.status_code == 422, bad
        payload = response.json()
        assert payload["schema_version"] == "v1"
        assert payload["error"]["code"] == "validation_failed"
        assert payload["error"]["request_id"] == response.headers["x-request-id"]


def test_dossier_selects_requested_thesis_and_returns_visible_evidence(
    api_client, workbench_case
):
    # workbench_case evidence/assessment are timestamped "now", so the cutoff
    # must be in the future for them to be visible (matches test_time_travel).
    cutoff = datetime(2026, 12, 31, tzinfo=UTC)
    response = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/dossier",
        params={
            "thesis_id": str(workbench_case.thesis.id),
            "cutoff": cutoff.isoformat(),
            "research_mode": "true",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["focus_thesis_id"] == str(workbench_case.thesis.id)
    # cutoff is echoed in the basis; compare parsed to tolerate Z/+00:00.
    assert datetime.fromisoformat(payload["basis"]["cutoff"]) == cutoff
    assert payload["basis"]["is_historical"] is True
    assert payload["assessment"]["provisional"] is True
    assert payload["evidence"]["supports"][0]["verbatim_text"]
    assert "confidence" not in payload["assessment"]
    assert "ready_for_review" not in payload


def test_dossier_hides_machine_generated_evidence_by_default(
    api_client, workbench_case
):
    # The workbench_case link is machine_generated: hidden by default,
    # visible only under explicit research mode (design 9.2/9.3).
    default = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/dossier"
    )
    assert default.status_code == 200
    assert default.json()["evidence"]["supports"] == []

    research = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/dossier",
        params={"research_mode": "true"},
    )
    assert research.status_code == 200
    assert len(research.json()["evidence"]["supports"]) == 1


def test_dossier_excludes_data_created_after_cutoff(api_client, session):
    # Past case/thesis/assessment/link are visible at the cutoff; a future
    # causal step and a future review must be excluded (design 10: historical
    # replay filters every entity, not just EvidenceLink).
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2026, 12, 31, tzinfo=UTC)

    case = ResearchCase(
        title="past case", industry_topic="ai_compute", created_by="t", created_at=past
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="past thesis",
        created_by="t",
        created_at=past,
    )
    session.add(thesis)
    session.flush()
    # future causal step -> excluded at cutoff
    session.add(
        CausalStep(
            thesis_id=thesis.id, description="future step", sequence=1, created_at=future
        )
    )
    # past document -> span -> statement -> reviewed link -> visible
    version = DocumentVersion(
        content_sha256="x" * 64,
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
        document_version_id=version.id, locator={"p": 1}, verbatim_text="past text"
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="disclosed_fact",
        normalized_text="past stmt",
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
            review_state="reviewed",
            created_at=past,
        )
    )
    snapshot = EvidenceSnapshot(
        thesis_id=thesis.id,
        cutoff=past,
        evidence_link_ids=[],
        created_at=past,
    )
    session.add(snapshot)
    session.flush()
    assessment = AIAssessment(
        snapshot_id=snapshot.id,
        conclusion="supported",
        rationale="r",
        gaps=[],
        displayed_as_provisional=True,
        creator_type="ai",
        created_at=past,
    )
    session.add(assessment)
    session.flush()
    # future review -> excluded at cutoff
    session.add(
        ReviewDecision(
            ai_assessment_id=assessment.id,
            outcome="confirmed",
            conclusion="supported",
            reason="ok",
            reviewer="rev",
            created_at=future,
        )
    )
    session.flush()

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/dossier",
        params={"cutoff": cutoff.isoformat(), "research_mode": "true"},
    )
    assert response.status_code == 200
    payload = response.json()
    # past thesis visible
    assert payload["focus_thesis_id"] == str(thesis.id)
    assert len(payload["theses"]) == 1
    # future causal step excluded
    assert payload["causal_chain"] == []
    # past assessment visible, but future review excluded
    assert payload["assessment"] is not None
    assert payload["assessment"]["provisional"] is True
    assert payload["assessment"]["review"] is None
    # past reviewed link visible
    assert len(payload["evidence"]["supports"]) == 1


def test_dossier_rejects_thesis_from_another_case(
    api_client, workbench_case, research_service
):
    other_case = research_service.add_case(
        title="other", industry_topic="t", created_by="u"
    )
    other_thesis = research_service.add_thesis(
        other_case.id, statement="other thesis", created_by="u"
    )
    response = api_client.get(
        f"/api/v1/research-cases/{workbench_case.case.id}/dossier",
        params={"thesis_id": str(other_thesis.id)},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_missing_case_returns_v1_error(api_client):
    response = api_client.get(
        "/api/v1/research-cases/00000000-0000-0000-0000-000000000000/dossier"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_dossier_hides_case_created_after_cutoff(api_client, session):
    future = datetime(2026, 12, 31, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    case = ResearchCase(
        title="future case", industry_topic="t", created_by="u", created_at=future
    )
    session.add(case)
    session.flush()
    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/dossier",
        params={"cutoff": cutoff.isoformat()},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_dossier_excludes_link_created_after_cutoff_with_backfilled_available_at(
    api_client, session
):
    # A link written to the ledger AFTER the cutoff, but with available_at
    # backfilled into the past, must not appear in a historical dossier
    # (no hindsight leakage).
    past = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = datetime(2026, 6, 1, tzinfo=UTC)
    future = datetime(2026, 12, 31, tzinfo=UTC)

    case = ResearchCase(
        title="c", industry_topic="t", created_by="u", created_at=past
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id, statement="t", created_by="u", created_at=past
    )
    session.add(thesis)
    session.flush()
    version = DocumentVersion(
        content_sha256="y" * 64,
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
        normalized_text="s",
        observed_period=None,
        created_at=past,
    )
    session.add(statement)
    session.flush()
    # created_at in the FUTURE, available_at backfilled to the past
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
            created_at=future,
        )
    )
    session.flush()

    response = api_client.get(
        f"/api/v1/research-cases/{case.id}/dossier",
        params={"cutoff": cutoff.isoformat(), "research_mode": "true"},
    )
    assert response.status_code == 200
    assert response.json()["evidence"]["supports"] == []


def test_v1_param_validation_returns_v1_422_envelope(api_client):
    response = api_client.get("/api/v1/research-cases", params={"limit": 0})
    assert response.status_code == 422
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["error"]["code"] == "validation_failed"
    assert payload["error"]["request_id"] == response.headers["x-request-id"]


def test_legacy_route_keeps_default_422_format(api_client):
    # Legacy /api/... routes must keep FastAPI's default 422 format, not the
    # v1 envelope, for backward compatibility.
    response = api_client.get("/api/research-cases/not-a-uuid/workbench")
    assert response.status_code == 422
    payload = response.json()
    assert "detail" in payload
    assert "error" not in payload
