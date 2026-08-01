"""Engine command API tests: extract + propose endpoints.

These are WRITE endpoints (they commit), so they run against the private
``cmd_*`` engine fixtures — never the shared seeded session.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _new_pending_version(cmd_session):
    """A fresh document version with one span and no statements."""
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    service = DocumentService(DocumentRepository(cmd_session))
    version = service.freeze(
        raw=b"FY2025 revenue grew 38% YoY on AI accelerator demand.",
        source_url="https://example.test/pending-doc",
    )
    service.add_span(
        document_version_id=version.id,
        locator={"page": 1, "paragraph": 0},
        verbatim_text="FY2025 revenue grew 38% YoY on AI accelerator demand.",
    )
    cmd_session.commit()
    return version


# ---------------------------------------------------------------------------
# POST /api/v1/documents/{document_version_id}/extract
# ---------------------------------------------------------------------------


def test_extract_creates_statements_and_airun(cmd_client, cmd_seeded):
    version = _new_pending_version(cmd_seeded)

    resp = cmd_client.post(f"/api/v1/documents/{version.id}/extract")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["document_version_id"] == str(version.id)
    assert body["mode"] == "mock"
    assert body["statement_count"] >= 1
    assert len(body["statements"]) == body["statement_count"]
    first = body["statements"][0]
    assert first["id"]
    assert first["kind"]
    assert first["normalized_text"]

    from app.models.ledger import AIRun

    runs = list(
        cmd_seeded.scalars(select(AIRun).where(AIRun.kind == "extract"))
    )
    assert len(runs) == 1
    assert runs[0].status == "success"


def test_extract_unknown_version_returns_404(cmd_client, cmd_seeded):
    resp = cmd_client.post(f"/api/v1/documents/{ZERO_UUID}/extract")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/theses/{thesis_id}/propose
# ---------------------------------------------------------------------------


def test_propose_creates_links_landing_in_review_queue(cmd_client, cmd_seeded):
    from app.models.ledger import EvidenceLink, Thesis

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    before = cmd_seeded.scalar(select(func.count()).select_from(EvidenceLink))

    resp = cmd_client.post(f"/api/v1/theses/{thesis.id}/propose")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["thesis_id"] == str(thesis.id)
    assert body["mode"] == "mock"
    assert body["link_count"] >= 1
    assert len(body["links"]) == body["link_count"]
    first = body["links"][0]
    assert first["link_id"]
    assert first["source_statement_id"]
    assert first["role"]
    assert isinstance(first["scope"], dict)

    after = cmd_seeded.scalar(select(func.count()).select_from(EvidenceLink))
    assert after == before + body["link_count"]

    # Every proposed link is pending review — nothing auto-confirmed.
    queue = cmd_client.get("/api/v1/review-queue")
    assert queue.status_code == 200
    queued_ids = {item["link_id"] for item in queue.json()["items"]}
    for link in body["links"]:
        assert link["link_id"] in queued_ids


def test_propose_unknown_thesis_returns_404(cmd_client, cmd_seeded):
    resp = cmd_client.post(f"/api/v1/theses/{ZERO_UUID}/propose")
    assert resp.status_code == 404


def test_rerun_compliance_refusal_returns_422_and_rolls_back(
    cmd_client, cmd_seeded, monkeypatch
):
    """A compliance refusal must surface as 422 and persist nothing."""
    from app.ai import assessment_gen
    from app.models.ledger import AIAssessment, EvidenceSnapshot, Thesis
    from app.services.compliance import (
        ComplianceAction,
        ComplianceDecision,
        ComplianceRefusedError,
    )

    def _refused_generate(self, thesis_id, cutoff, session):
        decision = ComplianceDecision(
            is_hit=True,
            action=ComplianceAction.REFUSE,
            hits=(),
            summary_reason="命中投资建议或个性化导向表达",
        )
        raise ComplianceRefusedError(decision)

    monkeypatch.setattr(
        assessment_gen.AssessmentGenerator, "generate", _refused_generate
    )

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    snaps_before = cmd_seeded.scalar(select(func.count()).select_from(EvidenceSnapshot))
    assessments_before = cmd_seeded.scalar(
        select(func.count()).select_from(AIAssessment)
    )

    resp = cmd_client.post(f"/api/v1/theses/{thesis.id}/rerun")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_failed"
    assert "compliance refused" in resp.json()["error"]["message"]

    snaps_after = cmd_seeded.scalar(select(func.count()).select_from(EvidenceSnapshot))
    assessments_after = cmd_seeded.scalar(
        select(func.count()).select_from(AIAssessment)
    )
    assert snaps_after == snaps_before
    assert assessments_after == assessments_before
