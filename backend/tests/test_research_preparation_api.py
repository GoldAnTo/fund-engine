from __future__ import annotations

import uuid
import os
import copy
import pytest
from threading import Event, Thread
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

from app.models.ledger import CaseDocumentVersion, CaseTenantAdmission, DocumentVersion, ResearchCase
from app.services.research_preparation import ResearchPreparationService
from app.models.operational import Job, ResearchRun
from app.models.research_preparation import ResearchPreparationArtifact
from app.models.ledger import SourceSpan, AtomicClaimReview
from app.models.ledger import Thesis
from app.models.source_governance import SourceContract
from app.models.research_protocol import MechanismTemplateVersion, MechanismNodeVersion, MechanismEdgeVersion, MetricDefinitionVersion, OutcomeBindingVersion, CaseMechanismSelectionVersion, VerificationRuleVersion
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


def _ready_authorization_case(session, client):
    case, preparation = _prepared_case(session)
    now = datetime.now(timezone.utc)
    document = session.scalar(select(DocumentVersion).join(CaseDocumentVersion).where(CaseDocumentVersion.research_case_id == case.id))
    session.add(SourceContract(document_version_id=document.id, source_type="uploaded_file", provider_or_tenant="team", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="cn", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="manual", downstream_restrictions=[], contract_version="test", intake_metadata={}, declared_by="human", created_at=now))
    theses=[]; candidates=[]
    for number in range(3):
        thesis=Thesis(research_case_id=case.id, statement=f"factor {number}", research_protocol_required=True, created_by="human", created_at=now); session.add(thesis); session.flush(); theses.append(thesis)
        text=f"claim {number}"; span=SourceSpan(document_version_id=document.id, locator={"page": number + 1}, verbatim_text=text); session.add(span); session.flush()
        candidates.append(AtomicClaimService(session).admit(AtomicClaimDraft(source_span_id=span.id, quote=text, quote_start=0, quote_end=len(text), normalized_text=text, claim_type="forecast", assertion_actor="company", subject="x", predicate="will", object_text=None, numeric_value=None, unit=None, observed_period=None, scope={}), authority_level="primary_disclosure", run_ref=f"prep:{number}"))
    ResearchPreparationService(session).complete_system_step(case.id,"parse_claims",{"candidates":[{"candidate_id":str(candidate.id)} for candidate in candidates]},expected_version=1,expected_fingerprint="a" * 64)
    template=MechanismTemplateVersion(template_key="test-template",version=1,display_name="Template",industry_scope="test",supersedes_id=None,approved_by="human",reason="fixture",created_at=now); session.add(template); session.flush()
    source=MechanismNodeVersion(template_version_id=template.id,node_key="source",display_name="Source",role="driver",created_at=now); target=MechanismNodeVersion(template_version_id=template.id,node_key="target",display_name="Target",role="outcome",created_at=now); session.add_all([source,target]); session.flush()
    edge=MechanismEdgeVersion(template_version_id=template.id,edge_key="edge",source_node_id=source.id,target_node_id=target.id,created_at=now); session.add(edge); session.commit()
    claims=client.post(f"/api/v1/event-research/{case.id}/preparation/claims/confirm",json={"revision":1,"actor":"human","decisions":[{"candidate_id":str(c.id),"outcome":"confirmed","reason":"checked"} for c in candidates]}); assert claims.status_code==200, claims.text
    service=ResearchPreparationService(session); session.refresh(preparation); context=service.current_candidate_context_fingerprint(case.id)
    baseline={"source_ref":f"document:{document.id}","value":"1","unit":"yuan","observed_period":"2025-12-31","available_at":document.available_at.replace(tzinfo=timezone.utc).isoformat()}
    outcomes=[]
    for thesis in theses:
        outcomes.append({"thesis_id":str(thesis.id),"metric":{"metric_id":f"metric-{thesis.id}","display_name":"Revenue","canonical_definition":"Revenue", "entity_scope":"business_line","unit":"yuan","frequency":"quarterly","period_semantics":"period_end","allowed_source_roles":["primary_disclosure"],"role_eligibility":["outcome"]},"binding":{"entity_scope":{"company_id":"company","business_line":"line"},"direction":"increase","baseline":baseline,"horizon_start":"2026-01-01","horizon_end":"2026-12-31"},"template_version_id":str(template.id),"verification_rules":[{"mechanism_edge_id":str(edge.id),"expected_direction":"increase","support_predicate":"support","contradiction_predicate":"contradict","allowed_source_roles":["primary_disclosure"],"observed_period_start":"2026-01-01","observed_period_end":"2026-12-31","available_at_deadline":"2027-01-01","next_verification_event":"earnings"}]})
    draft=service.complete_system_step(case.id,"draft_protocol",{"outcomes":outcomes,"baseline":{},"horizon":{},"mechanisms":{},"verification_rules":[]},expected_version=preparation.version,expected_fingerprint=preparation.input_fingerprint,expected_context_fingerprint=context); session.commit()
    confirmed=client.post(f"/api/v1/event-research/{case.id}/preparation/protocol/confirm",json={"revision":1,"actor":"human","draft_sequence":session.scalar(select(ResearchPreparationArtifact.sequence).where(ResearchPreparationArtifact.research_preparation_id==preparation.id,ResearchPreparationArtifact.kind=="research_protocol_draft",ResearchPreparationArtifact.state=="current")),"edits":{}}); assert confirmed.status_code==200,confirmed.text
    session.refresh(preparation); service.complete_system_step(case.id,"draft_evidence_plan",{"items":[{"budget":3},{"budget":3},{"budget":3}]},expected_version=preparation.version,expected_fingerprint=preparation.input_fingerprint,expected_context_fingerprint=context); session.commit()
    plan=session.scalar(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id==preparation.id,ResearchPreparationArtifact.kind=="evidence_acquisition_plan",ResearchPreparationArtifact.state=="current")); return case, preparation, plan, theses


def test_public_authorization_materializes_protocol_once_and_replays_idempotently(cmd_client, cmd_session):
    case, preparation, plan, theses = _ready_authorization_case(cmd_session, cmd_client)
    body={"revision":1,"actor":"human","plan_sequence":plan.sequence,"idempotency_key":"happy-authorization"}
    first=cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize",json=body)
    replay=cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize",json=body)
    assert first.status_code == replay.status_code == 201, (first.text,replay.text)
    assert first.json()["research_run_id"] == replay.json()["research_run_id"]
    assert len(list(cmd_session.scalars(select(ResearchRun)))) == 1
    assert len(list(cmd_session.scalars(select(MetricDefinitionVersion)))) == 3
    assert len(list(cmd_session.scalars(select(OutcomeBindingVersion).where(OutcomeBindingVersion.state == "approved")))) == 3
    assert len(list(cmd_session.scalars(select(CaseMechanismSelectionVersion)))) == 3
    assert len(list(cmd_session.scalars(select(VerificationRuleVersion)))) == 3
    conflict=cmd_client.post(f"/api/v1/event-research/{case.id}/preparation/authorize",json={**body,"idempotency_key":"another-key"})
    assert conflict.status_code == 409


@pytest.mark.parametrize("kind", ["missing_binding", "scope_omitted", "invalid_rule", "budget_limit"])
def test_public_authorization_invalid_matrix_rolls_back_protocol_and_run(cmd_client, cmd_session, kind):
    case, preparation, plan, theses = _ready_authorization_case(cmd_session, cmd_client)
    protocol = cmd_session.scalar(select(ResearchPreparationArtifact).where(ResearchPreparationArtifact.research_preparation_id == preparation.id, ResearchPreparationArtifact.kind == "research_protocol_draft", ResearchPreparationArtifact.state == "current"))
    payload = copy.deepcopy(protocol.payload)
    if kind == "missing_binding": payload["outcomes"][0].pop("binding")
    elif kind == "scope_omitted": payload["outcomes"].pop()
    elif kind == "invalid_rule": payload["outcomes"][0]["verification_rules"][0]["allowed_source_roles"] = []
    else: plan.payload = {"items": [{"budget": 10001}]}
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
def test_public_authorize_two_sessions_materializes_exactly_one_run(engine, monkeypatch):
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
    entered, release = Event(), Event()
    original = __import__("app.services.research_preparation", fromlist=["AutoResearchService"]).AutoResearchService.start
    def paused_start(self, *args, **kwargs):
        entered.set(); release.wait(3); return original(self, *args, **kwargs)
    monkeypatch.setattr("app.services.research_preparation.AutoResearchService.start", paused_start)
    outcomes=[]
    def request(key):
        db=SessionLocal()
        try:
            try:
                authorize(case_id, AuthorizeEvidencePlanRequest(revision=1, actor="human", plan_sequence=plan_sequence, idempotency_key=key), db=db, tenant_id="test-team")
                outcomes.append(201)
            except Exception as exc:
                from app.errors import ConflictError
                assert isinstance(exc, ConflictError); outcomes.append(409)
        finally: db.close()
    first=Thread(target=request,args=("race-one",)); second=Thread(target=request,args=("race-two",)); first.start(); assert entered.wait(3); second.start(); release.set(); first.join(5); second.join(5)
    assert sorted(outcomes) == [201, 409]
    with SessionLocal() as check:
        assert len(list(check.scalars(select(ResearchRun).where(ResearchRun.research_case_id == case_id)))) == 1
        assert len(list(check.scalars(select(MetricDefinitionVersion)))) == 3
