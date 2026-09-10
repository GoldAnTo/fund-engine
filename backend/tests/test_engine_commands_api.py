"""Engine command API tests: extract + propose endpoints.

These are WRITE endpoints (they commit), so they run against the private
``cmd_*`` engine fixtures — never the shared seeded session.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from threading import Event, Thread

import pytest
from sqlalchemy import func, select

ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def _grant_test_actor(
    session,
    case_id: uuid.UUID,
    *,
    user_id: uuid.UUID,
    subject: str,
) -> None:
    from app.models.identity import CaseAccessGrant, ResearchUser

    now = datetime.now(timezone.utc)
    if session.get(ResearchUser, user_id) is None:
        session.add(
            ResearchUser(
                id=user_id,
                issuer="https://identity.example.test/realms/research",
                subject=subject,
                tenant_id="test-team",
                display_name="Test Researcher",
                normalized_email=None,
                active=True,
                last_seen_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
    session.add(
        CaseAccessGrant(
            research_case_id=case_id,
            user_id=user_id,
            role="owner",
            granted_by_principal_id="test:fixture",
            reason="concurrency authorization fixture",
            created_at=now,
            updated_at=now,
        )
    )


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


def _seed_concurrent_propose_case(session):
    from app.models.ledger import (
        CaseDocumentVersion,
        CaseTenantAdmission,
        DocumentVersion,
        ResearchCase,
        SourceSpan,
        SourceStatement,
        Thesis,
    )

    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="concurrent propose",
        industry_topic="GPU demand",
        created_by="test",
        created_at=now,
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="GPU accelerator demand will grow",
        created_by="test",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://example.test/concurrent-propose",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add_all([thesis, document])
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="GPU accelerator demand and order backlog continued to grow.",
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="research_opinion",
        normalized_text="GPU accelerator demand and order backlog continued to grow.",
        created_at=now,
    )
    session.add_all(
        [
            statement,
            CaseDocumentVersion(
                research_case_id=case.id,
                document_version_id=document.id,
                linked_at=now,
            ),
            CaseTenantAdmission(
                research_case_id=case.id,
                tenant_id="test-team",
                initial_document_version_id=document.id,
                admitted_by="test-fixture",
                admitted_at=now,
            ),
        ]
    )
    _grant_test_actor(
        session,
        case.id,
        user_id=uuid.UUID("d16b39dd-5ee9-52ab-8165-93569a03f760"),
        subject="test",
    )
    session.commit()
    return thesis.id, statement.id


def _edit_case_policy(session, actor):
    from app.api.v1.dependencies import CaseRoutePolicy
    from app.services.case_authorization import CaseAuthorizationService

    return CaseRoutePolicy(
        actor=actor,
        authorization=CaseAuthorizationService(session),
        permission="edit",
    )


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


def test_extract_provider_failure_keeps_failed_airun_after_request_rollback(
    cmd_client, cmd_seeded, monkeypatch
):
    from sqlalchemy.orm import Session

    from app.ai.client import LLMClient
    from app.models.ledger import AIRun

    version = _new_pending_version(cmd_seeded)
    version_id = version.id

    def fail_provider(*_args, **_kwargs):
        raise RuntimeError("provider failed with secret-token")

    monkeypatch.setattr(LLMClient, "chat_json", fail_provider)

    response = cmd_client.post(f"/api/v1/documents/{version_id}/extract")
    assert response.status_code == 500
    assert "secret-token" not in response.text
    cmd_seeded.rollback()

    with Session(cmd_seeded.get_bind()) as check:
        failed_run = check.scalar(
            select(AIRun)
            .where(AIRun.kind == "extract", AIRun.status == "failed")
            .where(
                AIRun.input_ref["document_version_id"].as_string()
                == str(version_id)
            )
        )
        assert failed_run is not None
        assert failed_run.error == "AI operation failed"
        assert "secret-token" not in failed_run.error


def test_extract_provider_failure_rolls_back_rule_based_candidates(
    cmd_client, cmd_seeded, monkeypatch
):
    from sqlalchemy.orm import Session

    from app.ai.client import LLMClient
    from app.models.ledger import AIRun, AtomicClaimCandidate, SourceSpan

    version = _new_pending_version(cmd_seeded)
    version_id = version.id
    cmd_seeded.add(
        SourceSpan(
            document_version_id=version_id,
            locator={"page": 2},
            verbatim_text=(
                "主要会计数据 单位：千元\n"
                "指标 2025年 2024年\n"
                "营业收入 50,000,000 40,000,000\n"
            ),
        )
    )
    cmd_seeded.commit()

    def fail_provider(*_args, **_kwargs):
        raise RuntimeError("provider failed after deterministic extraction")

    monkeypatch.setattr(LLMClient, "chat_json", fail_provider)

    response = cmd_client.post(f"/api/v1/documents/{version_id}/extract")
    assert response.status_code == 500
    cmd_seeded.rollback()

    with Session(cmd_seeded.get_bind()) as check:
        assert check.scalar(
            select(func.count())
            .select_from(AtomicClaimCandidate)
            .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
            .where(SourceSpan.document_version_id == version_id)
        ) == 0
        failed_runs = list(
            check.scalars(
                select(AIRun)
                .where(AIRun.kind == "extract", AIRun.status == "failed")
                .where(
                    AIRun.input_ref["document_version_id"].as_string()
                    == str(version_id)
                )
            )
        )
        assert len(failed_runs) == 1


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
    from app.models.ledger import CaseTenantAdmission, ResearchCase
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
    cmd_seeded.add_all([
        CaseTenantAdmission(
            research_case_id=case.id,
            tenant_id="test-team",
            initial_document_version_id=original.id,
            admitted_by="test-fixture",
            admitted_at=now,
        ),
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
        ),
    ])
    _grant_test_actor(
        cmd_seeded,
        case.id,
        user_id=uuid.uuid5(
            uuid.NAMESPACE_URL,
            "test-research-principal:test-team:token:test-tenant-token",
        ),
        subject="token:test-tenant-token",
    )
    cmd_seeded.commit()

    response = cmd_client.post(
        f"/api/v1/documents/{original.id}/supplements",
        json={
            "case_id": str(case.id),
            "raw_text": "用户补充的报告正文，声称来自第 3 页。",
            "claimed_page_reference": "第 3 页",
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
    assert contract.declared_by == "user:test-team"
    assert contract.allow_ai_processing is False
    assert contract.allow_display is True

    retried = cmd_client.post(
        f"/api/v1/documents/{original.id}/supplements",
        json={
            "case_id": str(case.id),
            "raw_text": "用户补充的报告正文，声称来自第 3 页。",
            "claimed_page_reference": "第 3 页",
            "source_metadata": {"permissions": {"ai_processing": True, "display": True}},
        },
    )
    assert retried.status_code == 201
    assert retried.json()["document_version_id"] == str(supplement.id)
    assert len(DocumentRepository(cmd_seeded).spans_for_version(supplement.id)) == 1

    rejected = cmd_client.post(
        f"/api/v1/documents/{original.id}/supplements",
        json={
            "case_id": str(case.id),
            "raw_text": "用户补充的报告正文，声称来自第 3 页。",
            "claimed_page_reference": "第 3 页",
            "source_metadata": {"research_source_type": "company_disclosure"},
        },
    )

    assert rejected.status_code == 422
    assert "company_disclosure requires an HTTP(S) source_url" in rejected.json()[
        "error"
    ]["message"]


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


@pytest.mark.parametrize("provider_fails", [False, True])
def test_propose_job_cancellation_wins_over_inflight_provider_result(
    tmp_path, monkeypatch, provider_fails
):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker

    from app.ai.client import LLMClient
    from app.api.v1.commands.engine import propose_evidence
    from app.api.v1.jobs import cancel_job
    from app.api.v1.tenant_context import ResearchActor
    from app.models.events import DomainEvent
    from app.models.ledger import AIRun, Base
    from app.models.operational import Job, JobEvent
    from app.models.proposals import Proposal

    engine = create_engine(
        f"sqlite:///{tmp_path / f'propose-cancel-{provider_fails}.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as setup:
        thesis_id, statement_id = _seed_concurrent_propose_case(setup)

    provider_entered = Event()
    release_provider = Event()
    endpoint_errors: list[BaseException] = []
    endpoint_responses = []
    client = LLMClient(model_version="provider-test", mock=True)

    def blocked_provider(*_args, **_kwargs):
        provider_entered.set()
        assert release_provider.wait(timeout=5)
        if provider_fails:
            raise RuntimeError("provider failed after cancellation")
        return {
            "links": [
                {
                    "source_statement_id": str(statement_id),
                    "role": "supports",
                    "reason": "demand growth supports the thesis",
                    "scope": {"segment": "accelerators"},
                }
            ]
        }

    monkeypatch.setattr(client, "chat_json", blocked_provider)
    monkeypatch.setattr(LLMClient, "from_env", classmethod(lambda cls: client))
    research_actor = ResearchActor(
        user_id=uuid.UUID("d16b39dd-5ee9-52ab-8165-93569a03f760"),
        issuer="https://identity.example.test/realms/research",
        subject="test",
        tenant_id="test-team",
        display_name="Test Researcher",
        roles=frozenset({"tenant_administrator"}),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    def call_endpoint():
        with session_local() as api_session:
            try:
                endpoint_responses.append(
                    propose_evidence(
                        case_policy=_edit_case_policy(api_session, research_actor),
                        thesis_id=thesis_id,
                        db=api_session,
                        actor=research_actor,
                    )
                )
            except BaseException as exc:
                endpoint_errors.append(exc)

    endpoint_thread = Thread(target=call_endpoint)
    endpoint_thread.start()
    assert provider_entered.wait(timeout=5)

    with session_local() as cancelling:
        job = cancelling.scalar(
            select(Job).where(Job.kind == "propose", Job.target_id == thesis_id)
        )
        assert job is not None and job.status == "running"
        cancel_job(
            case_policy=_edit_case_policy(cancelling, research_actor),
            job_id=job.id,
            db=cancelling,
            actor=research_actor,
        )
        job_id = job.id

    release_provider.set()
    endpoint_thread.join(timeout=10)
    assert not endpoint_thread.is_alive()
    assert endpoint_errors == []
    assert len(endpoint_responses) == 1
    assert endpoint_responses[0].link_count == 0

    with Session(engine) as check:
        job = check.get(Job, job_id)
        assert job is not None
        assert job.status == "cancelled"
        assert job.cancel_requested is True
        assert check.scalar(select(func.count()).select_from(Proposal)) == 0
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(AIRun.kind == "propose")
        ) == 0
        assert check.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(DomainEvent.type == "evidence_link_proposed")
        ) == 0
        job_events = list(
            check.scalars(
                select(JobEvent)
                .where(JobEvent.job_id == job_id)
                .order_by(JobEvent.seq)
            )
        )
        assert job_events[-1].status == "cancelled"
        assert not any(
            event.status in {"succeeded", "failed"} for event in job_events
        )


@pytest.mark.pg_only
def test_postgres_propose_output_lock_serializes_late_cancellation(
    engine, session, monkeypatch
):
    from sqlalchemy.orm import Session, sessionmaker

    from app.ai.client import LLMClient
    from app.api.v1.commands.engine import propose_evidence
    from app.api.v1.jobs import cancel_job
    from app.api.v1.tenant_context import ResearchActor
    from app.errors import ConflictError
    from app.models.ledger import AIRun
    from app.models.operational import Job
    from app.models.proposals import Proposal
    from app.services.proposals import ProposalService

    thesis_id, statement_id = _seed_concurrent_propose_case(session)
    session_local = sessionmaker(bind=engine, future=True)
    output_persist_started = Event()
    cancellation_started = Event()
    cancellation_finished = Event()
    endpoint_errors: list[BaseException] = []
    cancellation_errors: list[BaseException] = []
    client = LLMClient(model_version="provider-test", mock=True)

    monkeypatch.setattr(
        client,
        "chat_json",
        lambda *_args, **_kwargs: {
            "links": [
                {
                    "source_statement_id": str(statement_id),
                    "role": "supports",
                    "reason": "valid serialized proposal",
                    "scope": {"segment": "accelerators"},
                }
            ]
        },
    )
    monkeypatch.setattr(LLMClient, "from_env", classmethod(lambda cls: client))
    original_create = ProposalService.create_proposal

    def hold_after_output_slot(self, **kwargs):
        output_persist_started.set()
        assert cancellation_started.wait(timeout=5)
        assert not cancellation_finished.is_set()
        return original_create(self, **kwargs)

    monkeypatch.setattr(ProposalService, "create_proposal", hold_after_output_slot)
    research_actor = ResearchActor(
        user_id=uuid.UUID("d16b39dd-5ee9-52ab-8165-93569a03f760"),
        issuer="https://identity.example.test/realms/research",
        subject="test",
        tenant_id="test-team",
        display_name="Test Researcher",
        roles=frozenset({"tenant_administrator"}),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    def call_endpoint():
        with session_local() as api_session:
            try:
                propose_evidence(
                    case_policy=_edit_case_policy(api_session, research_actor),
                    thesis_id=thesis_id,
                    db=api_session,
                    actor=research_actor,
                )
            except BaseException as exc:
                endpoint_errors.append(exc)

    def request_late_cancellation():
        assert output_persist_started.wait(timeout=5)
        with session_local() as cancelling:
            job = cancelling.scalar(
                select(Job).where(Job.kind == "propose", Job.target_id == thesis_id)
            )
            assert job is not None and job.status == "running"
            cancellation_started.set()
            try:
                cancel_job(
                    case_policy=_edit_case_policy(cancelling, research_actor),
                    job_id=job.id,
                    db=cancelling,
                    actor=research_actor,
                )
            except BaseException as exc:
                cancellation_errors.append(exc)
                cancelling.rollback()
            finally:
                cancellation_finished.set()

    endpoint_thread = Thread(target=call_endpoint)
    cancellation_thread = Thread(target=request_late_cancellation)
    endpoint_thread.start()
    cancellation_thread.start()
    endpoint_thread.join(timeout=10)
    cancellation_thread.join(timeout=10)

    assert not endpoint_thread.is_alive()
    assert not cancellation_thread.is_alive()
    assert endpoint_errors == []
    assert len(cancellation_errors) == 1
    assert isinstance(cancellation_errors[0], ConflictError)

    with Session(engine) as check:
        job = check.scalar(
            select(Job).where(Job.kind == "propose", Job.target_id == thesis_id)
        )
        assert job is not None
        assert job.status == "succeeded"
        assert job.cancel_requested is False
        assert check.scalar(select(func.count()).select_from(Proposal)) == 1
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(
                AIRun.kind == "propose", AIRun.status == "success"
            )
        ) == 1


@pytest.mark.parametrize("provider_error", [RuntimeError, ValueError])
def test_propose_provider_failure_keeps_failed_airun_and_failed_job(
    cmd_client, cmd_seeded, monkeypatch, provider_error
):
    from sqlalchemy.orm import Session

    from app.ai.client import LLMClient
    from app.models.ledger import AIRun, Thesis
    from app.models.operational import Job

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    thesis_id = thesis.id

    def fail_provider(*_args, **_kwargs):
        raise provider_error("provider failed with secret-token")

    monkeypatch.setattr(LLMClient, "chat_json", fail_provider)

    response = cmd_client.post(f"/api/v1/theses/{thesis_id}/propose")
    assert response.status_code == 500
    assert "secret-token" not in response.text
    cmd_seeded.rollback()

    with Session(cmd_seeded.get_bind()) as check:
        failed_run = check.scalar(
            select(AIRun)
            .where(AIRun.kind == "propose", AIRun.status == "failed")
            .where(AIRun.input_ref["thesis_id"].as_string() == str(thesis_id))
        )
        assert failed_run is not None
        assert failed_run.error == "AI operation failed"
        assert "secret-token" not in failed_run.error

        job = check.scalar(
            select(Job)
            .where(Job.kind == "propose", Job.target_id == thesis_id)
            .order_by(Job.created_at.desc())
        )
        assert job is not None
        assert job.status == "failed"
        assert job.error == "provider execution failed"
        assert "secret-token" not in job.error


def test_propose_partial_output_is_rolled_back_before_failed_audit(
    cmd_client, cmd_seeded, monkeypatch
):
    import json

    from sqlalchemy.orm import Session

    from app.ai.client import LLMClient
    from app.models.events import DomainEvent
    from app.models.ledger import AIRun, Thesis
    from app.models.operational import Job
    from app.models.proposals import Proposal

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    thesis_id = thesis.id
    proposals_before = cmd_seeded.scalar(
        select(func.count()).select_from(Proposal).where(Proposal.kind == "evidence_link")
    )
    events_before = cmd_seeded.scalar(
        select(func.count())
        .select_from(DomainEvent)
        .where(DomainEvent.type == "evidence_link_proposed")
    )

    def partial_then_invalid(self, messages, schema_hint=""):
        statements = json.loads(messages[-1]["content"])["statements"]
        assert len(statements) >= 2
        return {
            "links": [
                {
                    "source_statement_id": statements[0]["id"],
                    "role": "supports",
                    "reason": "valid first proposal",
                    "scope": {"segment": "DC"},
                },
                {
                    "source_statement_id": statements[1]["id"],
                    "reason": "missing role after partial output",
                    "scope": {"segment": "DC"},
                },
            ]
        }

    monkeypatch.setattr(LLMClient, "chat_json", partial_then_invalid)

    response = cmd_client.post(f"/api/v1/theses/{thesis_id}/propose")
    assert response.status_code == 500
    cmd_seeded.rollback()

    with Session(cmd_seeded.get_bind()) as check:
        assert check.scalar(
            select(func.count()).select_from(Proposal).where(Proposal.kind == "evidence_link")
        ) == proposals_before
        assert check.scalar(
            select(func.count())
            .select_from(DomainEvent)
            .where(DomainEvent.type == "evidence_link_proposed")
        ) == events_before
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(
                AIRun.kind == "propose", AIRun.status == "failed"
            )
        ) == 1
        job = check.scalar(
            select(Job).where(Job.kind == "propose", Job.target_id == thesis_id)
        )
        assert job is not None
        assert job.status == "failed"
        assert job.error == "provider execution failed"


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


def test_rerun_initial_protocol_block_returns_422_and_durable_failed_audit(
    cmd_client, cmd_seeded
):
    from app.models.ledger import AIAssessment, AIRun, EvidenceSnapshot, ResearchCase
    from app.repositories.research import ResearchRepository
    from app.services.research import ResearchService
    from sqlalchemy.orm import Session

    case = cmd_seeded.scalar(select(ResearchCase))
    thesis = ResearchService(ResearchRepository(cmd_seeded)).add_thesis(
        case.id,
        statement="Initial strict gate is blocked",
        created_by="tester",
        research_protocol_required=True,
    )
    cmd_seeded.commit()
    thesis_id = thesis.id

    response = cmd_client.post(f"/api/v1/theses/{thesis_id}/rerun")

    assert response.status_code == 422, response.text
    with Session(cmd_seeded.get_bind()) as check:
        assert check.scalar(
            select(func.count()).select_from(EvidenceSnapshot).where(
                EvidenceSnapshot.thesis_id == thesis_id
            )
        ) == 0
        assert check.scalar(
            select(func.count())
            .select_from(AIAssessment)
            .join(EvidenceSnapshot, AIAssessment.snapshot_id == EvidenceSnapshot.id)
            .where(EvidenceSnapshot.thesis_id == thesis_id)
        ) == 0
        runs = list(check.scalars(
            select(AIRun)
            .where(AIRun.kind == "assess", AIRun.status == "failed")
            .where(AIRun.input_ref["thesis_id"].as_string() == str(thesis_id))
        ))
    assert len(runs) == 1
    assert "researchability gate blocked" in runs[0].error
    assert runs[0].input_ref["research_protocol_status"] == "blocked"
    assert runs[0].input_ref["verification_rule_ids"] == []


def test_rerun_final_protocol_block_returns_422_and_durable_failed_audit(
    cmd_client, cmd_seeded, monkeypatch
):
    from app.models.ledger import AIAssessment, AIRun, EvidenceSnapshot, ResearchCase
    from app.repositories.research import ResearchRepository
    from app.services.research import ResearchService
    from app.services.research_protocol import ResearchabilityResult
    from sqlalchemy.orm import Session

    case = cmd_seeded.scalar(select(ResearchCase))
    thesis = ResearchService(ResearchRepository(cmd_seeded)).add_thesis(
        case.id,
        statement="Strict gate becomes blocked after the provider returns",
        created_by="tester",
        research_protocol_required=True,
    )
    cmd_seeded.commit()
    thesis_id = thesis.id
    gates = iter(
        [
            ResearchabilityResult("ready", [], None, "assess"),
            ResearchabilityResult(
                "blocked",
                ["missing_verification_rule"],
                None,
                "complete protocol",
            ),
        ]
    )
    monkeypatch.setattr(
        "app.ai.assessment_gen.ResearchProtocolService.check_researchability",
        lambda _service, _thesis_id: next(gates),
    )

    response = cmd_client.post(f"/api/v1/theses/{thesis_id}/rerun")

    assert response.status_code == 422, response.text
    with Session(cmd_seeded.get_bind()) as check:
        assert check.scalar(
            select(func.count()).select_from(EvidenceSnapshot).where(
                EvidenceSnapshot.thesis_id == thesis_id
            )
        ) == 0
        assert check.scalar(
            select(func.count())
            .select_from(AIAssessment)
            .join(EvidenceSnapshot, AIAssessment.snapshot_id == EvidenceSnapshot.id)
            .where(EvidenceSnapshot.thesis_id == thesis_id)
        ) == 0
        runs = list(
            check.scalars(
                select(AIRun)
                .where(AIRun.kind == "assess", AIRun.status == "failed")
                .where(
                    AIRun.input_ref["thesis_id"].as_string() == str(thesis_id)
                )
            )
        )
    assert len(runs) == 1
    assert runs[0].input_ref["initial_protocol_status"] == "ready"
    assert runs[0].input_ref["final_protocol_status"] == "blocked"


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


def test_dossier_does_not_lose_its_own_fresh_failure_behind_other_cases_runs(
    cmd_client, cmd_seeded, monkeypatch
):
    """A Case's latest failed assessment must not depend on a global limit."""
    from app.ai.client import LLMClient
    from app.models.ledger import AIRun, ResearchCase, Thesis

    thesis = cmd_seeded.scalars(select(Thesis)).first()
    case = cmd_seeded.scalars(select(ResearchCase)).first()

    def _refused_chat_json(self, messages, schema_hint=""):
        return {
            "conclusion": "supported",
            "rationale": "建议买入该标的",
            "gaps": [],
        }

    monkeypatch.setattr(LLMClient, "chat_json", _refused_chat_json)
    refused = cmd_client.post(f"/api/v1/theses/{thesis.id}/rerun")
    assert refused.status_code == 422

    later = datetime.now(timezone.utc) + timedelta(minutes=1)
    cmd_seeded.add_all(
        [
            AIRun(
                kind="assess",
                model_version="other-case-model",
                prompt_version="assess-v1",
                input_ref={"thesis_id": str(uuid.uuid4())},
                output_summary="other case failure",
                status="failed",
                error="other case failure",
                started_at=later + timedelta(seconds=index),
                finished_at=later + timedelta(seconds=index),
            )
            for index in range(51)
        ]
    )
    cmd_seeded.commit()

    dossier = cmd_client.get(
        f"/api/v1/research-cases/{case.id}/dossier?thesis_id={thesis.id}"
    )
    assert dossier.status_code == 200
    failure = dossier.json()["assess_failure"]
    assert failure is not None
    assert "compliance refused" in failure["error"]
