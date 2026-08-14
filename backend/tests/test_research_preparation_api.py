from __future__ import annotations

import uuid
import os
from datetime import datetime, timezone

from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase
from app.services.research_preparation import ResearchPreparationService
from app.models.operational import Job, ResearchRun
from app.models.research_preparation import ResearchPreparationArtifact
from app.models.ledger import SourceSpan, AtomicClaimReview
from app.domain.atomic_claims import AtomicClaimDraft
from app.services.atomic_claims import AtomicClaimService
from sqlalchemy import select


def _prepared_case(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="preparation API", industry_topic="test", created_by="tester", created_at=now)
    session.add(case); session.flush()
    document = DocumentVersion(content_sha256=uuid.uuid4().hex * 2, source_url="https://example.test/preparation", available_at=now, acquired_at=now, parser_version="test")
    session.add(document); session.flush()
    session.add_all([CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now), CaseTenantAdmission(research_case_id=case.id, tenant_id="test-team", initial_document_version_id=document.id, admitted_by="tester", admitted_at=now)])
    preparation = ResearchPreparationService(session).create_for_case(case.id, input_fingerprint="a" * 64, actor="tester")
    session.commit()
    return case, preparation


def test_preparation_summary_is_tenant_scoped_and_hides_secret_event_detail(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    preparation.last_error_code = "preparation_provider_unavailable"
    ResearchPreparationService(cmd_session)._repo.append_event(preparation, research_case_id=case.id, type="unsafe", step=None, message="Bearer sk-secret", detail={"token": "sk-secret", "safe": "ok"})
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "preparing"
    assert body["research_run_id"] is None
    assert "sk-secret" not in response.text
    assert "Bearer" not in response.text


def test_preparation_events_enforce_tenant_cursor_and_recursive_secret_redaction(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    repo = ResearchPreparationService(cmd_session)._repo
    repo.append_event(preparation, research_case_id=case.id, type="first", step=None, message="ok", detail={"nested": {"authorization": "Bearer sk-secret", "url": "https://x.test/?token=sentinel"}})
    repo.append_event(preparation, research_case_id=case.id, type="second", step=None, message="ok", detail={"safe": ["ok", "sk-secret"]})
    cmd_session.commit()
    os.environ["RESEARCH_TENANT_TOKENS"] = '{"test-tenant-token":"test-team","other-token":"other-team"}'
    denied = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation", headers={"Authorization": "Bearer other-token"})
    page = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation/events?after_seq=1&limit=1")
    invalid = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation/events?limit=201")
    assert denied.status_code == 404
    assert page.status_code == 200
    assert [event["seq"] for event in page.json()["items"]] == [2]
    assert page.json()["next_after_seq"] == 2
    assert invalid.status_code == 422
    assert "sk-secret" not in page.text and "Bearer" not in page.text and "sentinel" not in page.text


def test_retry_requires_exact_revision_and_only_requeues_a_failed_step(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    preparation.parse_claims_state = "failed"
    preparation.last_error_code = "preparation_internal_error"
    preparation.status = "recoverable_failure"
    cmd_session.commit()
    stale = cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/retry", json={"revision": 2, "actor": "human"})
    assert stale.status_code == 409
    assert cmd_session.get(type(preparation), preparation.id).parse_claims_state == "failed"
    retried = cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/retry", json={"revision": 1, "actor": "human"})
    assert retried.status_code == 200, retried.text
    assert retried.json()["system"]["claims"]["state"] == "queued"
    assert len(list(cmd_session.scalars(select(Job).where(Job.kind == "prepare_research")))) == 1
    assert list(cmd_session.scalars(select(ResearchRun))) == []


def test_authorization_invalid_protocol_is_422_and_rolls_back(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    service = ResearchPreparationService(cmd_session)
    service.complete_system_step(case.id, "parse_claims", {"candidates": []}, expected_version=1, expected_fingerprint="a" * 64)
    preparation.claim_review_state = "confirmed"
    context = service.current_candidate_context_fingerprint(case.id)
    protocol = service._repo.append_artifact(preparation, research_case_id=case.id, kind="research_protocol_draft", input_fingerprint=preparation.input_fingerprint, payload={"not_outcomes": []}, context_fingerprint=context)
    plan = service._repo.append_artifact(preparation, research_case_id=case.id, kind="evidence_acquisition_plan", input_fingerprint=preparation.input_fingerprint, payload={"items": [{"budget": 1}]}, context_fingerprint=context)
    preparation.draft_protocol_state = preparation.draft_evidence_plan_state = "succeeded"
    preparation.protocol_review_state = "confirmed"
    preparation.plan_review_state = "awaiting_review"
    preparation.status = "awaiting_plan_authorization"
    cmd_session.commit()
    response = cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize", json={"revision": 1, "actor": "human", "plan_sequence": plan.sequence, "idempotency_key": "invalid-protocol"})
    assert response.status_code == 422, response.text
    cmd_session.expire_all()
    assert cmd_session.get(type(preparation), preparation.id).status == "awaiting_plan_authorization"
    assert list(cmd_session.scalars(select(ResearchRun))) == []


def test_claim_confirmation_rejects_incomplete_decisions_without_partial_reviews(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    document = cmd_session.scalar(select(DocumentVersion).where(DocumentVersion.id == cmd_session.scalar(select(CaseDocumentVersion.document_version_id).where(CaseDocumentVersion.research_case_id == case.id))))
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text="A source claim")
    cmd_session.add(span); cmd_session.flush()
    candidate = AtomicClaimService(cmd_session).admit(AtomicClaimDraft(source_span_id=span.id, quote="A source claim", quote_start=0, quote_end=14, normalized_text="A source claim", claim_type="forecast", assertion_actor="company", subject="x", predicate="will", object_text=None, numeric_value=None, unit=None, observed_period=None, scope={}), authority_level="primary_disclosure", run_ref="prep-api")
    service = ResearchPreparationService(cmd_session)
    service.complete_system_step(case.id, "parse_claims", {"candidates": [{"candidate_id": str(candidate.id)}]}, expected_version=1, expected_fingerprint="a" * 64)
    cmd_session.commit()
    response = cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/claims/confirm", json={"revision": 1, "actor": "human", "decisions": []})
    assert response.status_code == 422
    assert list(cmd_session.scalars(select(AtomicClaimReview))) == []
    assert cmd_session.get(type(preparation), preparation.id).claim_review_state == "awaiting_review"
