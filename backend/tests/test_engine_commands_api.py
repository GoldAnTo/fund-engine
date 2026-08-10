"""Engine command API tests: extract + propose endpoints.

These are WRITE endpoints (they commit), so they run against the private
``cmd_*`` engine fixtures — never the shared seeded session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

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


def test_extract_creates_review_gated_candidates_and_airun(cmd_client, cmd_seeded):
    version = _new_pending_version(cmd_seeded)

    resp = cmd_client.post(f"/api/v1/documents/{version.id}/extract")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["document_version_id"] == str(version.id)
    assert body["mode"] == "mock"
    assert body["candidate_count"] >= 1
    assert len(body["candidates"]) == body["candidate_count"]
    first = body["candidates"][0]
    assert first["id"]
    assert first["claim_type"]
    assert first["normalized_text"]
    assert first["quote"]
    assert first["review_state"] == "awaiting_review"

    from app.models.ledger import AIRun

    runs = list(
        cmd_seeded.scalars(select(AIRun).where(AIRun.kind == "extract"))
    )
    assert len(runs) == 1
    assert runs[0].status == "success"


def test_extract_unknown_version_returns_404(cmd_client, cmd_seeded):
    resp = cmd_client.post(f"/api/v1/documents/{ZERO_UUID}/extract")
    assert resp.status_code == 404


def test_extract_refuses_a_frozen_source_contract_that_forbids_ai_processing(cmd_client, cmd_seeded):
    from app.models.ledger import AIRun
    from app.models.source_governance import SourceContract

    version = _new_pending_version(cmd_seeded)
    cmd_seeded.add(
        SourceContract(
            document_version_id=version.id,
            source_type="licensed_provider",
            provider_or_tenant="restricted-provider",
            allow_ai_processing=False,
            allow_display=True,
            allow_export=False,
            allow_api=False,
            region="cn",
            effective_from=None,
            effective_until=None,
            retention_policy="case_retained",
            deletion_policy="manual",
            downstream_restrictions=["no AI"],
            contract_version="fixture-v1",
            intake_metadata={},
            declared_by="human:researcher",
            created_at=datetime.now(timezone.utc),
        )
    )
    cmd_seeded.commit()

    response = cmd_client.post(f"/api/v1/documents/{version.id}/extract")

    assert response.status_code == 422
    assert "禁止 AI 处理" in response.json()["error"]["message"]
    refused_run = cmd_seeded.scalar(
        select(AIRun)
        .where(AIRun.kind == "extract")
        .where(AIRun.input_ref["document_version_id"].as_string() == str(version.id))
    )
    assert refused_run is not None
    assert refused_run.status == "failed"


def test_extract_refuses_an_expired_source_contract(cmd_client, cmd_seeded):
    from app.models.source_governance import SourceContract

    version = _new_pending_version(cmd_seeded)
    cmd_seeded.add(
        SourceContract(
            document_version_id=version.id,
            source_type="licensed_provider",
            provider_or_tenant="expired-provider",
            allow_ai_processing=True,
            allow_display=True,
            allow_export=False,
            allow_api=False,
            region="cn",
            effective_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
            effective_until=datetime(2025, 1, 31, tzinfo=timezone.utc),
            retention_policy="case_retained",
            deletion_policy="manual",
            downstream_restrictions=["expired"],
            contract_version="fixture-v1",
            intake_metadata={},
            declared_by="human:researcher",
            created_at=datetime.now(timezone.utc),
        )
    )
    cmd_seeded.commit()

    response = cmd_client.post(f"/api/v1/documents/{version.id}/extract")

    assert response.status_code == 422
    assert "当前已失效" in response.json()["error"]["message"]


def test_supplement_text_creates_a_separate_case_document_with_intersected_permissions(
    cmd_client, cmd_seeded
):
    from app.models.ledger import ResearchCase
    from app.models.source_governance import SourceContract
    from app.repositories.documents import DocumentRepository
    from app.services.ingest import DocumentService

    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="Failed report intake",
        industry_topic="事件研究",
        created_by="human:researcher",
        created_at=now,
    )
    cmd_seeded.add(case)
    cmd_seeded.flush()
    docs = DocumentService(DocumentRepository(cmd_seeded))
    original = docs.freeze(
        raw=b"unreadable-pdf-placeholder",
        source_url="https://provider.example.com/report.pdf",
        parser_version="pdf-v1",
        parse_state="failed",
        title="Original report",
    )
    docs.attach_to_case(research_case_id=case.id, document_version_id=original.id)
    cmd_seeded.add(
        SourceContract(
            document_version_id=original.id,
            source_type="licensed_provider",
            provider_or_tenant="provider",
            allow_ai_processing=False,
            allow_display=True,
            allow_export=False,
            allow_api=False,
            region="cn",
            effective_from=None,
            effective_until=None,
            retention_policy="case_retained",
            deletion_policy="manual",
            downstream_restrictions=["provider no AI"],
            contract_version="provider-v1",
            intake_metadata={},
            declared_by="human:researcher",
            created_at=now,
        )
    )
    cmd_seeded.commit()

    response = cmd_client.post(
        f"/api/v1/documents/{original.id}/supplements",
        json={
            "case_id": str(case.id),
            "raw_text": "用户补充的报告正文，声称来自第 3 页。",
            "claimed_page_reference": "第 3 页",
            "created_by": "human:researcher",
            "source_metadata": {
                "permissions": {"ai_processing": True, "display": True},
                "authority_level": "user_supplied",
            },
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["original_document_version_id"] == str(original.id)
    supplement_id = uuid.UUID(body["document_version_id"])
    supplement = cmd_seeded.get(type(original), supplement_id)
    assert supplement is not None
    assert supplement.id != original.id
    assert supplement.supplements_document_version_id == original.id
    assert supplement.claimed_page_reference == "第 3 页"
    assert original.parse_state == "failed"
    assert DocumentRepository(cmd_seeded).spans_for_version(supplement.id)[0].verbatim_text == "用户补充的报告正文，声称来自第 3 页。"
    contract = cmd_seeded.scalar(
        select(SourceContract).where(SourceContract.document_version_id == supplement.id)
    )
    assert contract is not None
    assert contract.allow_ai_processing is False
    assert contract.allow_display is True

    retried = cmd_client.post(
        f"/api/v1/documents/{original.id}/supplements",
        json={
            "case_id": str(case.id),
            "raw_text": "用户补充的报告正文，声称来自第 3 页。",
            "claimed_page_reference": "第 3 页",
            "created_by": "human:researcher",
            "source_metadata": {"permissions": {"ai_processing": True, "display": True}},
        },
    )
    assert retried.status_code == 201
    assert retried.json()["document_version_id"] == str(supplement.id)
    assert len(DocumentRepository(cmd_seeded).spans_for_version(supplement.id)) == 1


# ---------------------------------------------------------------------------
# POST /api/v1/theses/{thesis_id}/propose
# ---------------------------------------------------------------------------


def test_propose_creates_links_landing_in_review_queue(cmd_client, cmd_seeded):
    from app.models.ledger import EvidenceLink, Thesis
    from app.models.proposals import Proposal

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    before = cmd_seeded.scalar(select(func.count()).select_from(EvidenceLink))

    resp = cmd_client.post(f"/api/v1/theses/{thesis.id}/propose")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["thesis_id"] == str(thesis.id)
    assert body["mode"] == "mock"
    assert body["link_count"] >= 1
    assert "job_id" in body
    assert len(body["links"]) == body["link_count"]
    first = body["links"][0]
    assert first["proposal_id"]
    assert isinstance(first["scope"], dict)

    # DESIGN CONTRACT (§9.2): the AI proposer writes Proposals, NOT reviewed
    # EvidenceLinks.  No formal link is created until a human decides.
    after = cmd_seeded.scalar(select(func.count()).select_from(EvidenceLink))
    assert after == before

    proposal_count = cmd_seeded.scalar(
        select(func.count()).select_from(Proposal)
    )
    assert proposal_count >= body["link_count"]

    # Every proposed link is pending review — nothing auto-confirmed.
    queue = cmd_client.get("/api/v1/review-proposals")
    assert queue.status_code == 200
    queued_ids = {item["id"] for item in queue.json()["items"]}
    for link in body["links"]:
        assert link["proposal_id"] in queued_ids


def test_propose_unknown_thesis_returns_404(cmd_client, cmd_seeded):
    resp = cmd_client.post(f"/api/v1/theses/{ZERO_UUID}/propose")
    assert resp.status_code == 404


def test_rerun_compliance_refusal_returns_422_and_keeps_audit(
    cmd_client, cmd_seeded, monkeypatch
):
    """Refusal surfaces as 422; refused text never lands, but the failed
    AIRun IS kept as the audit trail (snapshot rolled back in-transaction).
    """
    from app.ai.client import LLMClient
    from app.models.ledger import AIAssessment, AIRun, EvidenceSnapshot, Thesis

    def _refused_chat_json(self, messages, schema_hint=""):
        return {
            "conclusion": "supported",
            "rationale": "建议买入该标的",
            "gaps": [],
        }

    monkeypatch.setattr(LLMClient, "chat_json", _refused_chat_json)

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    snaps_before = cmd_seeded.scalar(select(func.count()).select_from(EvidenceSnapshot))
    assessments_before = cmd_seeded.scalar(
        select(func.count()).select_from(AIAssessment)
    )
    failed_runs_before = cmd_seeded.scalar(
        select(func.count()).select_from(AIRun).where(AIRun.status == "failed")
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

    failed_runs = list(
        cmd_seeded.scalars(select(AIRun).where(AIRun.status == "failed"))
    )
    assert len(failed_runs) == failed_runs_before + 1
    latest = failed_runs[-1]
    assert latest.kind == "assess"
    assert latest.input_ref["thesis_id"] == str(thesis.id)
    assert "compliance refused" in latest.error


def test_dossier_surfaces_fresh_assess_failure_and_hides_stale_one(
    cmd_client, cmd_seeded, monkeypatch
):
    """dossier.assess_failure shows a fresh refusal; a later successful
    rerun makes the failure stale and the field disappears."""
    from app.ai.client import LLMClient
    from app.models.ledger import ResearchCase, Thesis

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    case = cmd_seeded.scalars(select(ResearchCase)).first()
    dossier_url = (
        f"/api/v1/research-cases/{case.id}/dossier?thesis_id={thesis.id}"
    )

    def _refused_chat_json(self, messages, schema_hint=""):
        return {
            "conclusion": "supported",
            "rationale": "建议买入该标的",
            "gaps": [],
        }

    monkeypatch.setattr(LLMClient, "chat_json", _refused_chat_json)
    refused = cmd_client.post(f"/api/v1/theses/{thesis.id}/rerun")
    assert refused.status_code == 422

    dossier = cmd_client.get(dossier_url)
    assert dossier.status_code == 200
    failure = dossier.json()["assess_failure"]
    assert failure is not None
    assert "compliance refused" in failure["error"]
    assert failure["model_version"].startswith("mock-")
    assert failure["failed_at"]

    # A later successful rerun makes the refusal stale -> hidden.
    monkeypatch.undo()
    ok = cmd_client.post(f"/api/v1/theses/{thesis.id}/rerun")
    assert ok.status_code == 201

    dossier = cmd_client.get(dossier_url)
    assert dossier.status_code == 200
    assert dossier.json()["assess_failure"] is None
