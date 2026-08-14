from __future__ import annotations

import uuid
import os
import copy
import pytest
from threading import Barrier, Event, Thread
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase
from app.services.research_preparation import ResearchPreparationService
from app.models.operational import Job, ResearchRun, ResearchTask
from app.models.research_preparation import ResearchPreparation, ResearchPreparationArtifact
from app.models.ledger import SourceSpan, AtomicClaimReview
from app.models.ledger import Thesis
from app.models.source_governance import SourceContract
from app.models.event_research import EventResearchScopeFactor, EventResearchScopeVersion
from app.models.research_protocol import MechanismTemplateVersion, MechanismNodeVersion, MechanismEdgeVersion, MetricDefinitionVersion, OutcomeBindingVersion, CaseMechanismSelectionVersion, VerificationRuleVersion
from app.domain.atomic_claims import AtomicClaimDraft
from app.services.atomic_claims import AtomicClaimService
from sqlalchemy import select
from app.main import app


def _prepared_case(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="preparation API", industry_topic="test", created_by="tester", created_at=now)
    session.add(case); session.flush()
    document = DocumentVersion(content_sha256=uuid.uuid4().hex * 2, source_url="https://example.test/preparation", available_at=now, acquired_at=now, parser_version="test", title="冻结测试材料", parse_state="success")
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
    assert body["case_title"] == "preparation API"
    assert body["initial_material"]["document_version_id"]
    assert body["initial_material"]["title"] == "冻结测试材料"
    assert body["initial_material"]["parse_state"] == "success"
    assert body["progress"] == {"completed_steps": 0, "total_steps": 3, "current_step": None, "failed_step": None}
    assert "sk-secret" not in response.text
    assert "Bearer" not in response.text


def test_preparation_summary_uses_fixed_error_message_and_no_run_before_authorization(
    cmd_client, cmd_session
):
    case, preparation = _prepared_case(cmd_session)
    preparation.status = "recoverable_failure"
    preparation.parse_claims_state = "failed"
    preparation.last_error_code = "preparation_internal_error"
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["research_run_id"] is None
    assert summary["last_error_message"] == "准备任务暂时失败"
    assert summary["system"]["claims"]["state"] == "failed"
    assert summary["review"]["plan"]["state"] == "locked"


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


def test_preparation_reads_redact_sensitive_key_variants_and_url_queries(cmd_client, cmd_session):
    case, preparation, plan, _theses = _ready_authorization_case(cmd_session, cmd_client)
    body = {"revision": 1, "actor": "human", "plan_sequence": plan.sequence, "idempotency_key": "redaction-plan"}
    assert cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize", json=body).status_code == 201
    leaks = {
        "api_key": "artifact-api-key",
        "apiKey": "artifact-api-camel",
        "x-api-key": "artifact-x-api",
        "access_key": "artifact-access-key",
        "signature": "artifact-signature",
        "query": "https://storage.test/file?X-Amz-Signature=artifact-amz-signature&safe=ok",
    }
    plan.payload = {"nested": leaks, "safe": "artifact-safe"}
    preparation.authorized_evidence_plan = {"deep": {"apiKey": "authorized-api-key", "url": "https://vendor.test/?access_key=authorized-access-key"}}
    ResearchPreparationService(cmd_session)._repo.append_event(
        preparation,
        research_case_id=case.id,
        type="unsafe",
        step=None,
        message="https://worker.test/?signature=event-signature",
        detail={"nested": {"X-Amz-Signature": "event-amz-signature", "access_key": "event-access-key"}},
    )
    cmd_session.commit()

    summary = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation")
    events = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation/events")

    assert summary.status_code == events.status_code == 200
    for secret in (*leaks.values(), "authorized-api-key", "authorized-access-key", "event-signature", "event-amz-signature", "event-access-key"):
        assert secret not in summary.text
        assert secret not in events.text
    assert summary.json()["artifacts"]["plan"]["payload"]["safe"] == "artifact-safe"


def test_preparation_summary_hides_candidate_text_when_source_contract_forbids_display(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    now = datetime.now(timezone.utc)
    document = cmd_session.scalar(
        select(DocumentVersion)
        .join(CaseDocumentVersion)
        .where(CaseDocumentVersion.research_case_id == case.id)
    )
    assert document is not None
    cmd_session.add(SourceContract(
        document_version_id=document.id,
        source_type="licensed_provider",
        provider_or_tenant="licensed",
        allow_ai_processing=True,
        allow_display=False,
        allow_export=False,
        allow_api=False,
        region="cn",
        effective_from=None,
        effective_until=None,
        retention_policy="case_retained",
        deletion_policy="manual",
        downstream_restrictions=[],
        contract_version="test",
        intake_metadata={},
        declared_by="human",
        created_at=now,
    ))
    quote = "licensed quote must not be displayed"
    normalized = "licensed normalized claim must not be displayed"
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text=quote)
    cmd_session.add(span)
    cmd_session.flush()
    candidate = AtomicClaimService(cmd_session).admit(
        AtomicClaimDraft(
            source_span_id=span.id,
            quote=quote,
            quote_start=0,
            quote_end=len(quote),
            normalized_text=normalized,
            claim_type="forecast",
            assertion_actor="company",
            subject="company",
            predicate="expects",
            object_text=None,
            numeric_value=None,
            unit=None,
            observed_period=None,
            scope={},
        ),
        authority_level="primary_disclosure",
        run_ref="display-policy-test",
    )
    ResearchPreparationService(cmd_session)._repo.append_artifact(
        preparation,
        research_case_id=case.id,
        kind="atomic_claim_candidates",
        input_fingerprint=preparation.input_fingerprint,
        payload={"candidates": [{"candidate_id": str(candidate.id), "quote": quote, "normalized_text": normalized, "safe": "kept"}]},
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation")

    assert response.status_code == 200, response.text
    assert quote not in response.text
    assert normalized not in response.text
    artifact = response.json()["artifacts"]["claims"]
    assert artifact["display_withheld"] is True
    assert artifact["payload"] == {}


def test_preparation_summary_withholds_each_artifact_payload_for_display_restricted_source(
    cmd_client, cmd_session
):
    case, preparation, theses, context, _sequence = _protocol_draft_case(
        cmd_session, cmd_client, allow_display=False
    )
    preparation.protocol_review_state = "confirmed"
    plan = ResearchPreparationService(cmd_session).complete_system_step(
        case.id,
        "draft_evidence_plan",
        {"items": [
            {"factor": thesis.statement, "evidence_target": "primary source", "allowed_source_roles": ["primary_disclosure"], "priority": "normal", "stop_condition": "one reviewed source", "budget": 3}
            for thesis in theses
        ]},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=context,
    )
    artifacts = {
        artifact.kind: artifact
        for artifact in cmd_session.scalars(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.state == "current",
            )
        )
    }
    leaks = {
        "claims": "licensed nested candidate original text",
        "protocol": "licensed protocol baseline and rationale text",
        "plan": "licensed evidence target text",
    }
    artifacts["atomic_claim_candidates"].payload = {
        "candidates": [{"candidate_id": artifacts["atomic_claim_candidates"].payload["candidates"][0]["candidate_id"], "quote": leaks["claims"]}],
        "nested": {"raw": leaks["claims"]},
    }
    artifacts["research_protocol_draft"].payload["baseline"]["original_text"] = leaks["protocol"]
    artifacts["research_protocol_draft"].payload["rationale"] = {"nested": leaks["protocol"]}
    artifacts["evidence_acquisition_plan"].payload["items"][0]["evidence_target"] = leaks["plan"]
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/preparation")

    assert response.status_code == 200, response.text
    for leak in leaks.values():
        assert leak not in response.text
    for artifact in response.json()["artifacts"].values():
        assert artifact["display_withheld"] is True
        assert artifact["payload"] == {}


def test_display_restricted_source_rejects_human_protocol_confirmation(cmd_client, cmd_session):
    case, preparation, _theses, _context, sequence = _protocol_draft_case(
        cmd_session, cmd_client, allow_display=False
    )

    response = cmd_client.post(
        f"/api/v1/event-research/{case.id}/preparation/protocol/confirm",
        json={"revision": 1, "actor": "human", "draft_sequence": sequence, "edits": {}},
    )

    assert response.status_code == 422, response.text
    cmd_session.expire_all()
    assert cmd_session.get(type(preparation), preparation.id).protocol_review_state == "awaiting_review"
    assert list(cmd_session.scalars(select(MetricDefinitionVersion))) == []


def test_display_restricted_source_rejects_human_claim_confirmation(cmd_client, cmd_session):
    case, preparation = _prepared_case(cmd_session)
    now = datetime.now(timezone.utc)
    document = cmd_session.scalar(
        select(DocumentVersion).join(CaseDocumentVersion).where(
            CaseDocumentVersion.research_case_id == case.id
        )
    )
    assert document is not None
    cmd_session.add(SourceContract(
        document_version_id=document.id,
        source_type="licensed_provider",
        provider_or_tenant="licensed",
        allow_ai_processing=True,
        allow_display=False,
        allow_export=False,
        allow_api=False,
        region="cn",
        effective_from=None,
        effective_until=None,
        retention_policy="case_retained",
        deletion_policy="manual",
        downstream_restrictions=[],
        contract_version="test",
        intake_metadata={},
        declared_by="human",
        created_at=now,
    ))
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text="licensed claim")
    cmd_session.add(span)
    cmd_session.flush()
    candidate = AtomicClaimService(cmd_session).admit(
        AtomicClaimDraft(
            source_span_id=span.id, quote="licensed claim", quote_start=0,
            quote_end=14, normalized_text="licensed claim", claim_type="forecast",
            assertion_actor="company", subject="company", predicate="expects",
            object_text=None, numeric_value=None, unit=None, observed_period=None, scope={},
        ),
        authority_level="primary_disclosure", run_ref="display-restricted",
    )
    ResearchPreparationService(cmd_session).complete_system_step(
        case.id, "parse_claims", {"candidates": [{"candidate_id": str(candidate.id)}]},
        expected_version=1, expected_fingerprint="a" * 64,
    )
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/event-research/{case.id}/preparation/claims/confirm",
        json={"revision": 1, "actor": "human", "decisions": [{"candidate_id": str(candidate.id), "outcome": "confirmed", "reason": "checked"}]},
    )

    assert response.status_code == 422, response.text
    assert list(cmd_session.scalars(select(AtomicClaimReview))) == []
    assert cmd_session.get(type(preparation), preparation.id).claim_review_state == "awaiting_review"


def test_display_restricted_source_rejects_human_plan_authorization(cmd_client, cmd_session):
    case, preparation, theses, context, _sequence = _protocol_draft_case(
        cmd_session, cmd_client, allow_display=False
    )
    preparation.protocol_review_state = "confirmed"
    ResearchPreparationService(cmd_session).complete_system_step(
        case.id,
        "draft_evidence_plan",
        {"items": [
            {"factor": thesis.statement, "evidence_target": "primary source", "allowed_source_roles": ["primary_disclosure"], "priority": "normal", "stop_condition": "one reviewed source", "budget": 3}
            for thesis in theses
        ]},
        expected_version=preparation.version,
        expected_fingerprint=preparation.input_fingerprint,
        expected_context_fingerprint=context,
    )
    cmd_session.commit()
    plan = cmd_session.scalar(
        select(ResearchPreparationArtifact).where(
            ResearchPreparationArtifact.research_preparation_id == preparation.id,
            ResearchPreparationArtifact.kind == "evidence_acquisition_plan",
            ResearchPreparationArtifact.state == "current",
        )
    )
    assert plan is not None

    response = cmd_client.post(
        f"/api/v1/event-research/{case.id}/preparation/authorize",
        json={"revision": 1, "actor": "human", "plan_sequence": plan.sequence, "idempotency_key": "display-restricted"},
    )

    assert response.status_code == 422, response.text
    cmd_session.expire_all()
    refreshed = cmd_session.get(type(preparation), preparation.id)
    assert refreshed.status == "awaiting_plan_authorization"
    assert refreshed.research_run_id is None
    assert list(cmd_session.scalars(select(ResearchRun))) == []


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


def test_authorization_requires_a_confirmed_materialized_protocol(cmd_client, cmd_session):
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


def _protocol_draft_case(session, client, mutate_protocol=None, allow_display=True):
    case, preparation = _prepared_case(session)
    now = datetime.now(timezone.utc)
    document = session.scalar(select(DocumentVersion).join(CaseDocumentVersion).where(CaseDocumentVersion.research_case_id == case.id))
    theses=[]; candidates=[]
    for number in range(3):
        thesis=Thesis(research_case_id=case.id, statement=f"factor {number}", research_protocol_required=True, created_by="human", created_at=now); session.add(thesis); session.flush(); theses.append(thesis)
        text=f"claim {number}"; span=SourceSpan(document_version_id=document.id, locator={"page": number + 1}, verbatim_text=text); session.add(span); session.flush()
        candidates.append(AtomicClaimService(session).admit(AtomicClaimDraft(source_span_id=span.id, quote=text, quote_start=0, quote_end=len(text), normalized_text=text, claim_type="forecast", assertion_actor="company", subject="x", predicate="will", object_text=None, numeric_value=None, unit=None, observed_period=None, scope={}), authority_level="primary_disclosure", run_ref=f"prep:{number}"))
    scope = EventResearchScopeVersion(research_case_id=case.id, version=1, changed_by="human", change_summary="fixture scope", created_at=now)
    session.add(scope); session.flush()
    session.add_all([EventResearchScopeFactor(scope_version_id=scope.id, statement=thesis.statement, description=None, position=index) for index, thesis in enumerate(theses, start=1)])
    ResearchPreparationService(session).complete_system_step(case.id,"parse_claims",{"candidates":[{"candidate_id":str(candidate.id)} for candidate in candidates]},expected_version=1,expected_fingerprint="a" * 64)
    template=MechanismTemplateVersion(template_key=f"test-template-{case.id}",version=1,display_name="Template",industry_scope="test",supersedes_id=None,approved_by="human",reason="fixture",created_at=now); session.add(template); session.flush()
    source=MechanismNodeVersion(template_version_id=template.id,node_key="source",display_name="Source",role="driver",created_at=now); target=MechanismNodeVersion(template_version_id=template.id,node_key="target",display_name="Target",role="outcome",created_at=now); session.add_all([source,target]); session.flush()
    edge=MechanismEdgeVersion(template_version_id=template.id,edge_key="edge",source_node_id=source.id,target_node_id=target.id,created_at=now); session.add(edge); session.commit()
    claims=client.post(f"/api/v1/event-research/{case.id}/preparation/claims/confirm",json={"revision":1,"actor":"human","decisions":[{"candidate_id":str(c.id),"outcome":"confirmed","reason":"checked"} for c in candidates]}); assert claims.status_code==200, claims.text
    session.add(SourceContract(document_version_id=document.id, source_type="uploaded_file", provider_or_tenant="team", allow_ai_processing=True, allow_display=allow_display, allow_export=False, allow_api=False, region="cn", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="manual", downstream_restrictions=[], contract_version="test", intake_metadata={}, declared_by="human", created_at=now))
    session.commit()
    service=ResearchPreparationService(session); session.refresh(preparation); context=service.current_candidate_context_fingerprint(case.id)
    baseline={"source_ref":f"document:{document.id}","value":"1","unit":"yuan","observed_period":"2025-12-31","available_at":document.available_at.replace(tzinfo=timezone.utc).isoformat()}
    outcomes=[]
    for thesis in theses:
        outcomes.append({"thesis_id":str(thesis.id),"metric":{"metric_id":f"metric-{thesis.id}","display_name":"Revenue","canonical_definition":"Revenue", "entity_scope":"company","unit":"yuan","frequency":"quarterly","period_semantics":"period_end","allowed_source_roles":["primary_disclosure"],"role_eligibility":["outcome"]},"binding":{"entity_scope":{"company_id":"company","company":"company"},"direction":"increase","baseline":baseline,"horizon_start":"2026-01-01","horizon_end":"2026-12-31"},"template_version_id":str(template.id),"verification_rules":[{"mechanism_edge_id":str(edge.id),"expected_direction":"increase","support_predicate":"support","contradiction_predicate":"contradict","allowed_source_roles":["primary_disclosure"],"observed_period_start":"2026-01-01","observed_period_end":"2026-12-31","available_at_deadline":"2027-01-01","next_verification_event":"earnings"}]})
    protocol_payload={"outcomes":outcomes,"baseline":{"document_id":str(document.id)},"horizon":{"start":"2026-01-01","end":"2026-12-31"},"mechanisms":[{"template_version_id":str(template.id)}],"verification_rules":[{"rule":"per-outcome rules below"}]}
    if mutate_protocol is not None:
        mutate_protocol(protocol_payload)
    service.complete_system_step(case.id,"draft_protocol",protocol_payload,expected_version=preparation.version,expected_fingerprint=preparation.input_fingerprint,expected_context_fingerprint=context); session.commit()
    sequence=session.scalar(select(ResearchPreparationArtifact.sequence).where(ResearchPreparationArtifact.research_preparation_id==preparation.id,ResearchPreparationArtifact.kind=="research_protocol_draft",ResearchPreparationArtifact.state=="current"))
    return case, preparation, theses, context, sequence


def _ready_authorization_case(session, client):
    case, preparation, theses, context, sequence = _protocol_draft_case(session, client)
    service = ResearchPreparationService(session)
    confirmed=client.post(f"/api/v1/event-research/{case.id}/preparation/protocol/confirm",json={"revision":1,"actor":"human","draft_sequence":sequence,"edits":{}}); assert confirmed.status_code==200,confirmed.text
    session.refresh(preparation); service.complete_system_step(case.id,"draft_evidence_plan",{"items":[{"factor": thesis.statement, "evidence_target": "primary source", "allowed_source_roles": ["primary_disclosure"], "priority": "normal", "stop_condition": "one reviewed source", "budget": 3} for thesis in theses]},expected_version=preparation.version,expected_fingerprint=preparation.input_fingerprint,expected_context_fingerprint=context); session.commit()
    plan=session.scalar(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id==preparation.id,ResearchPreparationArtifact.kind=="evidence_acquisition_plan",ResearchPreparationArtifact.state=="current")); return case, preparation, plan, theses


@pytest.mark.parametrize("missing", ["baseline", "horizon", "mechanisms", "verification_rules"])
def test_public_protocol_confirmation_rejects_incomplete_draft_without_formal_rows(cmd_client, cmd_session, missing):
    case, preparation, _theses, _context, sequence = _protocol_draft_case(
        cmd_session,
        cmd_client,
        lambda payload: payload.pop(missing),
    )

    response = cmd_client.post(
        f"/api/v1/event-research/{case.id}/preparation/protocol/confirm",
        json={"revision": 1, "actor": "human", "draft_sequence": sequence, "edits": {}},
    )

    assert response.status_code == 422, response.text
    cmd_session.expire_all()
    refreshed = cmd_session.get(type(preparation), preparation.id)
    assert refreshed.protocol_review_state == "awaiting_review"
    assert list(cmd_session.scalars(select(MetricDefinitionVersion))) == []
    assert list(cmd_session.scalars(select(OutcomeBindingVersion))) == []
    assert list(cmd_session.scalars(select(CaseMechanismSelectionVersion))) == []
    assert list(cmd_session.scalars(select(VerificationRuleVersion))) == []


def test_public_protocol_confirmation_rejects_an_outcome_without_rules(cmd_client, cmd_session):
    case, preparation, _theses, _context, sequence = _protocol_draft_case(
        cmd_session,
        cmd_client,
        lambda payload: payload["outcomes"][0].pop("verification_rules"),
    )

    response = cmd_client.post(
        f"/api/v1/event-research/{case.id}/preparation/protocol/confirm",
        json={"revision": 1, "actor": "human", "draft_sequence": sequence, "edits": {}},
    )

    assert response.status_code == 422, response.text
    cmd_session.expire_all()
    assert cmd_session.get(type(preparation), preparation.id).protocol_review_state == "awaiting_review"
    assert list(cmd_session.scalars(select(MetricDefinitionVersion))) == []


def test_public_authorization_materializes_protocol_once_and_replays_idempotently(cmd_client, cmd_session):
    case, preparation, plan, theses = _ready_authorization_case(cmd_session, cmd_client)
    expected_plan = copy.deepcopy(plan.payload)
    body={"revision":1,"actor":"human","plan_sequence":plan.sequence,"idempotency_key":"happy-authorization"}
    first=cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize",json=body)
    replay=cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize",json=body)
    assert first.status_code == replay.status_code == 201, (first.text,replay.text)
    assert first.json()["research_run_id"] == replay.json()["research_run_id"]
    assert first.json()["authorized_evidence_plan"] == expected_plan
    plan.payload = {"items": []}
    cmd_session.commit()
    assert cmd_client.get(f"/api/v1/event-research/{case.id}/preparation").json()["authorized_evidence_plan"] == expected_plan
    assert len(list(cmd_session.scalars(select(ResearchRun)))) == 1
    assert len(list(cmd_session.scalars(select(MetricDefinitionVersion)))) == 3
    assert len(list(cmd_session.scalars(select(OutcomeBindingVersion).where(OutcomeBindingVersion.state == "approved")))) == 3
    assert len(list(cmd_session.scalars(select(CaseMechanismSelectionVersion)))) == 3
    assert len(list(cmd_session.scalars(select(VerificationRuleVersion)))) == 3
    conflict=cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize",json={**body,"idempotency_key":"another-key"})
    assert conflict.status_code == 409


def test_authorization_runs_only_current_scope_theses(cmd_client, cmd_session):
    case, _preparation, plan, theses = _ready_authorization_case(cmd_session, cmd_client)
    now = datetime.now(timezone.utc)
    obsolete = Thesis(
        research_case_id=case.id,
        statement="obsolete historical factor",
        research_protocol_required=True,
        created_by="human",
        created_at=now,
    )
    cmd_session.add(obsolete)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/event-research/{case.id}/preparation/authorize",
        json={"revision": 1, "actor": "human", "plan_sequence": plan.sequence, "idempotency_key": "scope-only"},
    )

    assert response.status_code == 201, response.text
    run = cmd_session.get(ResearchRun, uuid.UUID(response.json()["research_run_id"]))
    assert set(run.scope_thesis_ids or []) == {str(thesis.id) for thesis in theses}
    assert str(obsolete.id) not in set(run.scope_thesis_ids or [])
    assert {task.thesis_id for task in cmd_session.scalars(select(ResearchTask).where(ResearchTask.run_id == run.id))} == {thesis.id for thesis in theses}


def test_authorization_rejects_an_idempotency_key_reused_for_a_different_request(cmd_client, cmd_session):
    case, _preparation, plan, _theses = _ready_authorization_case(cmd_session, cmd_client)
    body = {"revision": 1, "actor": "human", "plan_sequence": plan.sequence, "idempotency_key": "bound-key"}

    assert cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize", json=body).status_code == 201
    conflict = cmd_client.post(
        f"/api/v1/event-research/{case.id}/preparation/authorize",
        json={**body, "actor": "different-human"},
    )

    assert conflict.status_code == 409


def test_authorization_idempotency_key_cannot_cross_cases(cmd_client, cmd_session):
    first_case, _first_preparation, first_plan, _first_theses = _ready_authorization_case(cmd_session, cmd_client)
    second_case, second_preparation, second_plan, _second_theses = _ready_authorization_case(cmd_session, cmd_client)
    body = {"revision": 1, "actor": "human", "plan_sequence": first_plan.sequence, "idempotency_key": "case-bound-key"}

    assert cmd_client.post(f"/api/v1/event-research/{first_case.id}/preparation/authorize", json=body).status_code == 201
    conflict = cmd_client.post(
        f"/api/v1/event-research/{second_case.id}/preparation/authorize",
        json={**body, "plan_sequence": second_plan.sequence},
    )

    assert conflict.status_code == 409
    cmd_session.expire_all()
    assert cmd_session.get(type(second_preparation), second_preparation.id).status == "awaiting_plan_authorization"


def test_preparation_write_routes_document_conflicts() -> None:
    paths = app.openapi()["paths"]
    for suffix in ("claims/confirm", "protocol/confirm", "authorize", "retry"):
        responses = paths[f"/api/v1/event-research/{{case_id}}/preparation/{suffix}"]["post"]["responses"]
        assert "409" in responses
        assert responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorEnvelope")
        assert "422" in responses


def test_protocol_confirmation_materializes_formal_protocol_before_authorization(cmd_client, cmd_session):
    _case, _preparation, _plan, _theses = _ready_authorization_case(cmd_session, cmd_client)
    assert len(list(cmd_session.scalars(select(MetricDefinitionVersion)))) == 3
    assert len(list(cmd_session.scalars(select(OutcomeBindingVersion).where(OutcomeBindingVersion.state == "approved")))) == 3
    assert len(list(cmd_session.scalars(select(VerificationRuleVersion)))) == 3
    assert list(cmd_session.scalars(select(ResearchRun))) == []


@pytest.mark.parametrize("kind", ["budget_limit", "unknown_factor", "missing_factor", "duplicate_factor", "extra_item_key", "empty_role"])
def test_public_authorization_invalid_matrix_rolls_back_protocol_and_run(cmd_client, cmd_session, kind):
    case, preparation, plan, theses = _ready_authorization_case(cmd_session, cmd_client)
    protocol = cmd_session.scalar(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id == preparation.id, ResearchPreparationArtifact.kind == "research_protocol_draft", ResearchPreparationArtifact.state == "current"))
    payload = copy.deepcopy(protocol.payload)
    if kind == "missing_binding": payload["outcomes"][0].pop("binding")
    elif kind == "scope_omitted": payload["outcomes"].pop()
    elif kind == "invalid_rule": payload["outcomes"][0]["verification_rules"][0]["allowed_source_roles"] = []
    else:
        plan.payload = copy.deepcopy(plan.payload)
        if kind == "budget_limit": plan.payload["items"][0]["budget"] = 10001
        elif kind == "unknown_factor": plan.payload["items"][0]["factor"] = "removed factor"
        elif kind == "missing_factor": plan.payload["items"] = plan.payload["items"][:-1]
        elif kind == "duplicate_factor": plan.payload["items"][1]["factor"] = plan.payload["items"][0]["factor"]
        elif kind == "extra_item_key": plan.payload["items"][0]["unexpected"] = True
        else: plan.payload["items"][0]["allowed_source_roles"] = [""]
    if kind != "budget_limit": protocol.payload = payload
    cmd_session.commit()
    before = [len(list(cmd_session.scalars(select(model)))) for model in (MetricDefinitionVersion, OutcomeBindingVersion, CaseMechanismSelectionVersion, VerificationRuleVersion, ResearchRun)]
    response = cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize", json={"revision":1,"actor":"human","plan_sequence":plan.sequence,"idempotency_key":f"rollback-{kind}"})
    assert response.status_code == 422, response.text
    cmd_session.expire_all()
    after = [len(list(cmd_session.scalars(select(model)))) for model in (MetricDefinitionVersion, OutcomeBindingVersion, CaseMechanismSelectionVersion, VerificationRuleVersion, ResearchRun)]
    refreshed = cmd_session.get(type(preparation), preparation.id)
    assert after == before
    assert refreshed.status == "awaiting_plan_authorization" and refreshed.plan_review_state == "awaiting_review"


@pytest.mark.pg_only
@pytest.mark.parametrize("keys", [("race-one", "race-two"), ("race-same", "race-same")])
def test_public_authorize_two_sessions_materializes_exactly_one_run(engine, monkeypatch, keys):
    """The route's Case/preparation row locks serialize competing authorizations."""
    from app.api.v1.research_preparation import confirm_claims, confirm_protocol, authorize
    from app.schemas.v1.research_preparation import ConfirmClaimsRequest, ConfirmProtocolRequest, AuthorizeEvidencePlanRequest
    SessionLocal = sessionmaker(bind=engine, future=True)
    class PublicClient:
        def __init__(self, db): self.db = db
        def post(self, path, json):
            case_id = uuid.UUID(path.split("/")[4])
            if path.endswith("claims/confirm"):
                confirm_claims(case_id, ConfirmClaimsRequest.model_validate(json), db=self.db, tenant_id="test-team")
            else:
                confirm_protocol(case_id, ConfirmProtocolRequest.model_validate(json), db=self.db, tenant_id="test-team")
            return type("Reply", (), {"status_code": 200, "text": ""})()
    setup = SessionLocal()
    try:
        case, preparation, plan, _ = _ready_authorization_case(setup, PublicClient(setup))
        case_id, plan_sequence = case.id, plan.sequence
    finally:
        setup.close()
    both_read_ready, entered, release = Event(), Event(), Event()
    ready = Barrier(2, action=both_read_ready.set)
    original = __import__("app.services.research_preparation", fromlist=["AutoResearchService"]).AutoResearchService.start
    def paused_start(self, *args, **kwargs):
        entered.set(); release.wait(3); return original(self, *args, **kwargs)
    monkeypatch.setattr("app.services.research_preparation.AutoResearchService.start", paused_start)
    outcomes=[]
    def request(key):
        db=SessionLocal()
        try:
            # Both independent database sessions must observe the same ready
            # preparation before either enters the authorization write path.
            preparation = db.scalar(select(ResearchPreparation).where(ResearchPreparation.research_case_id == case_id))
            current_plan = db.scalar(select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind == "evidence_acquisition_plan",
                ResearchPreparationArtifact.state == "current",
            )) if preparation is not None else None
            assert preparation is not None and preparation.status == "awaiting_plan_authorization"
            assert current_plan is not None and current_plan.sequence == plan_sequence
            ready.wait(timeout=5)
            try:
                response = authorize(case_id, AuthorizeEvidencePlanRequest(revision=1, actor="human", plan_sequence=plan_sequence, idempotency_key=key), db=db, tenant_id="test-team")
                outcomes.append((201, response.research_run_id))
            except Exception as exc:
                from app.errors import ConflictError
                assert isinstance(exc, ConflictError); outcomes.append((409, None))
        finally: db.close()
    first=Thread(target=request,args=(keys[0],)); second=Thread(target=request,args=(keys[1],)); first.start(); second.start(); assert both_read_ready.wait(3); assert entered.wait(3); release.set(); first.join(5); second.join(5)
    statuses = sorted(status for status, _ in outcomes)
    if keys[0] != keys[1]:
        assert statuses == [201, 409]
    else:
        assert statuses == [201, 201]
        assert len({run_id for status, run_id in outcomes if status == 201}) == 1
    with SessionLocal() as check:
        assert len(list(check.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)))) == 1
        assert len(list(check.scalars(select(MetricDefinitionVersion)))) == 3
