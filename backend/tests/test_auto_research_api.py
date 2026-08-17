from __future__ import annotations
import uuid
import pytest
from threading import Event, Thread
from datetime import datetime, timezone
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session, sessionmaker
from app.models.ledger import (
    Base,
    CaseDocumentVersion,
    ResearchCase,
    Thesis,
    EvidenceLink,
    SourceStatement,
    SourceSpan,
    DocumentVersion,
    CaseTenantAdmission,
    AtomicClaimCandidate,
    AIAssessment,
    AIRun,
    EvidenceSnapshot,
)
from app.models.operational import ResearchRun, ResearchTask, TaskItem
from app.models.proposals import Proposal
from app.models.source_governance import SourceContract
from app.services.auto_research import AutoResearchService
from app.repositories.auto_research import AutoResearchRepository
from app.scripts.run_ai_engine import _pending_versions
from app.domain.atomic_claims import AtomicClaimDraft
from app.services.atomic_claims import AtomicClaimService
from app.services.case_monitor import (
    CaseMonitorConfig,
    CaseMonitorService,
    ResearchRunEventRepository,
)
from app.models.research_monitor import ResearchRunEvent


def _admit_case(cmd_session, case: ResearchCase) -> None:
    """HTTP run routes operate only on an explicitly tenant-admitted Case."""
    now = datetime.now(timezone.utc)
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url=f"https://example.test/admission/{case.id}",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    cmd_session.add(document)
    cmd_session.flush()
    cmd_session.add_all(
        [
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
    cmd_session.flush()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    from app.models import operational
    operational.ResearchRun.__table__.create(engine, checkfirst=True)
    operational.ResearchTask.__table__.create(engine, checkfirst=True)
    Proposal.__table__.create(engine, checkfirst=True)
    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as s:
        yield s


def test_start_run_not_found(session):
    with pytest.raises(ValueError, match="not found"):
        AutoResearchService(session).start(uuid.uuid4())


def test_waiting_for_sources_run_is_active_for_exact_thesis_scope(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="waiting for sources",
        industry_topic="i",
        created_by="u",
        created_at=now,
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="Primary evidence is still being acquired",
        created_by="u",
        created_at=now,
    )
    session.add(thesis)
    session.flush()
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_sources",
        stage="acquire",
        scope_thesis_ids=[str(thesis.id)],
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()

    active = AutoResearchRepository(session).active_run_for_exact_thesis_scope(
        research_case_id=case.id,
        thesis_id=thesis.id,
    )

    assert active is run


def test_waiting_for_sources_run_can_be_cancelled(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="cancel source wait",
        industry_topic="i",
        created_by="u",
        created_at=now,
    )
    session.add(case)
    session.flush()
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_sources",
        stage="acquire",
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()

    cancelled = AutoResearchRepository(session).cancel_run(run)

    assert cancelled is True
    assert run.status == "cancelled"
    assert run.stage == "stopped"


def test_service_cancels_waiting_for_sources_run(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="service cancel source wait",
        industry_topic="i",
        created_by="u",
        created_at=now,
    )
    session.add(case)
    session.flush()
    run = AutoResearchRepository(session).create_run(research_case_id=case.id)
    run.status = "waiting_for_sources"
    run.stage = "retrieve"

    summary = AutoResearchService(session).cancel_run(
        run.id,
        actor="human:test",
        change_reason="stop parked run",
    )

    assert summary["status"] == "cancelled"
    assert run.status == "cancelled"


def test_active_endpoint_shows_and_cancels_waiting_for_sources_run(
    cmd_client, cmd_session
):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="parked automatic run",
        industry_topic="i",
        created_by="u",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    _admit_case(cmd_session, case)
    run = AutoResearchRepository(cmd_session).create_run(research_case_id=case.id)
    run.status = "waiting_for_sources"
    run.stage = "retrieve"
    cmd_session.commit()

    active = cmd_client.get("/api/v1/research-runs/active")

    assert active.status_code == 200
    assert [item["run_id"] for item in active.json()["items"]] == [str(run.id)]

    cancelled = cmd_client.post(
        f"/api/v1/research-runs/{run.id}/cancel",
        json={"actor": "human:test", "change_reason": "stop parked run"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_pending_documents_are_isolated_to_the_research_case(session):
    """A run must never extract a document merely because another case owns it."""
    now = datetime.now(timezone.utc)
    first_case = ResearchCase(title="first", industry_topic="i", created_by="u", created_at=now)
    second_case = ResearchCase(title="second", industry_topic="i", created_by="u", created_at=now)
    session.add_all([first_case, second_case])
    session.flush()

    first_document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://example.com/first", available_at=now, acquired_at=now, parser_version="test")
    second_document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://example.com/second", available_at=now, acquired_at=now, parser_version="test")
    session.add_all([first_document, second_document])
    session.flush()
    session.add_all([
        SourceSpan(document_version_id=first_document.id, locator={"page": 1}, verbatim_text="First case source with enough concrete research detail to pass quality screening."),
        SourceSpan(document_version_id=second_document.id, locator={"page": 1}, verbatim_text="Second case source with enough concrete research detail to pass quality screening."),
        CaseDocumentVersion(research_case_id=first_case.id, document_version_id=first_document.id, linked_at=now),
        CaseDocumentVersion(research_case_id=second_case.id, document_version_id=second_document.id, linked_at=now),
    ])
    session.commit()

    assert [item.id for item in _pending_versions(session, first_case.id)] == [first_document.id]


def test_pending_documents_exclude_a_frozen_contract_that_forbids_ai_processing(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="restricted", industry_topic="i", created_by="u", created_at=now)
    session.add(case)
    session.flush()
    document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://provider.example.com/restricted", available_at=now, acquired_at=now, parser_version="provider-v1")
    session.add(document)
    session.flush()
    session.add_all([
        SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text="Source text with enough detail that content quality would otherwise allow extraction."),
        CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now),
        SourceContract(document_version_id=document.id, source_type="licensed_provider", provider_or_tenant="provider", allow_ai_processing=False, allow_display=True, allow_export=False, allow_api=False, region="not_recorded", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="not_recorded", downstream_restrictions=["no AI"], contract_version="v1", intake_metadata={}, declared_by="human", created_at=now),
    ])
    session.commit()

    assert _pending_versions(session, case.id) == []


def test_monitor_run_extracts_only_the_frozen_allowed_source_types(session, monkeypatch):
    """Frozen scope uses research category, not the document intake channel."""
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="source-scoped run", industry_topic="i", created_by="u", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="订单增长将改善收入", created_by="u", created_at=now)
    session.add(thesis)
    session.flush()
    disclosure = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://issuer.example.com/report", available_at=now, acquired_at=now, parser_version="test")
    pasted_annual_report = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://static.cninfo.com.cn/finalpage/2026-03-20/annual-report.pdf", available_at=now, acquired_at=now, parser_version="test")
    pasted = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="event://pasted", available_at=now, acquired_at=now, parser_version="test")
    session.add_all([disclosure, pasted_annual_report, pasted])
    session.flush()
    disclosure_text = "公司披露订单同比增长20%，并说明收入确认进度、客户验收节奏、产能准备情况及下一季度收入确认安排，相关指标均可回到本页公告原文核对。"
    pasted_annual_report_text = "工业富联年报披露营业收入同比增长15%，并列明客户结构、交付节奏和下一年度产能规划，原始年报已从巨潮资讯网冻结留档。"
    pasted_text = "研究员粘贴的事件摘要，不属于本次公司披露补证范围；它只用于记录最初的问题和背景，不能替代有明确主体、期间、来源许可及原文定位的公司披露材料。"
    disclosure_span = SourceSpan(document_version_id=disclosure.id, locator={"page": 1}, verbatim_text=disclosure_text)
    pasted_annual_report_span = SourceSpan(document_version_id=pasted_annual_report.id, locator={"page": 1}, verbatim_text=pasted_annual_report_text)
    pasted_span = SourceSpan(document_version_id=pasted.id, locator={"paragraph": 1}, verbatim_text=pasted_text)
    session.add_all([
        disclosure_span,
        pasted_annual_report_span,
        pasted_span,
        CaseDocumentVersion(research_case_id=case.id, document_version_id=disclosure.id, linked_at=now),
        CaseDocumentVersion(research_case_id=case.id, document_version_id=pasted_annual_report.id, linked_at=now),
        CaseDocumentVersion(research_case_id=case.id, document_version_id=pasted.id, linked_at=now),
        SourceContract(document_version_id=disclosure.id, source_type="company_disclosure", provider_or_tenant="issuer", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="CN", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="not_recorded", downstream_restrictions=[], contract_version="v1", intake_metadata={}, declared_by="human", created_at=now),
        SourceContract(document_version_id=pasted_annual_report.id, source_type="pasted_snapshot", research_source_type="company_disclosure", provider_or_tenant="issuer", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="CN", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="not_recorded", downstream_restrictions=[], contract_version="v1", intake_metadata={}, declared_by="human", created_at=now),
        SourceContract(document_version_id=pasted.id, source_type="pasted_snapshot", provider_or_tenant="researcher", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="CN", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="not_recorded", downstream_restrictions=[], contract_version="v1", intake_metadata={}, declared_by="human", created_at=now),
    ])
    session.flush()
    disclosure_candidate = AtomicClaimService(session).admit(
        AtomicClaimDraft(source_span_id=disclosure_span.id, quote="订单同比增长20%", quote_start=4, quote_end=13, normalized_text="公司披露订单同比增长20%", claim_type="reported_claim", assertion_actor="公司", subject="订单", predicate="同比增长", object_text="20%", numeric_value="20", unit="%", observed_period=None, scope={}),
        authority_level="primary_disclosure",
        run_ref="extract:existing-company-candidate",
    )
    annual_report_candidate = AtomicClaimService(session).admit(
        AtomicClaimDraft(source_span_id=pasted_annual_report_span.id, quote="营业收入同比增长15%", quote_start=8, quote_end=19, normalized_text="工业富联营业收入同比增长15%", claim_type="reported_claim", assertion_actor="工业富联", subject="营业收入", predicate="同比增长", object_text="15%", numeric_value="15", unit="%", observed_period=None, scope={}),
        authority_level="primary_disclosure",
        run_ref="extract:existing-pasted-annual-report-candidate",
    )
    pasted_candidate = AtomicClaimService(session).admit(
        AtomicClaimDraft(source_span_id=pasted_span.id, quote="事件摘要", quote_start=6, quote_end=10, normalized_text="研究员粘贴事件摘要", claim_type="reported_claim", assertion_actor="研究员", subject="事件", predicate="摘要", object_text="背景", numeric_value=None, unit=None, observed_period=None, scope={}),
        authority_level="user_supplied",
        run_ref="extract:existing-pasted-candidate",
    )
    CaseMonitorService(session).save(
        case.id,
        actor="human:researcher",
        config=CaseMonitorConfig(frequency="daily_20_00", factor_ids=[thesis.id], allowed_source_types=["company_disclosure"], next_verification_event="下一次财报", budget=20, change_reason="仅用公司披露补证"),
    )
    session.commit()
    extracted: list[uuid.UUID] = []

    class FakeExtractor:
        def __init__(self, _client):
            pass

        def extract(self, document_id, _session, *, before_persist=None):
            if before_persist is not None and not before_persist():
                return None
            extracted.append(document_id)
            return []

    import app.services.auto_research as auto_research_module

    monkeypatch.setattr(auto_research_module, "StatementExtractor", FakeExtractor)
    monkeypatch.setattr(AutoResearchService, "client", property(lambda _self: object()))
    service = AutoResearchService(session)
    run = service.start_from_monitor(case.id)

    service.execute(run)

    assert set(extracted) == {disclosure.id, pasted_annual_report.id}
    pending_candidate_ids = {
        candidate.id
        for candidate in service._pending_atomic_claims(
            case.id,
            allowed_source_types={"company_disclosure"},
        )
    }
    assert disclosure_candidate.id in pending_candidate_ids
    assert annual_report_candidate.id in pending_candidate_ids
    assert pasted_candidate.id not in pending_candidate_ids
    events = list(session.scalars(select(ResearchRunEvent).where(ResearchRunEvent.run_id == run.id).order_by(ResearchRunEvent.seq)))
    assert any(
        event.stage == "source_scope" and event.payload_json["excluded_count"] == 1
        for event in events
    )


def test_start_and_get_run(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis)
    session.commit()
    run = AutoResearchService(session).start(case.id, max_rounds=1, budget=1)
    assert run.id is not None
    detail = AutoResearchService(session).detail(run.id)
    assert detail["status"] == "queued"
    assert detail["stop_reason"] is None
    job = AutoResearchRepository(session).job_for_run(run.id)
    assert job is not None
    assert job.status == "queued"


def test_extraction_provider_failure_after_success_counts_both_attempts_and_stops(
    session, monkeypatch
):
    from app.ai.client import LLMClient

    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="provider failure",
        industry_topic="i",
        created_by="u",
        created_at=now,
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="订单增长将改善收入",
        created_by="u",
        created_at=now,
    )
    first_document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://example.test/provider-success",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    failed_document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://example.test/provider-failure",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add_all([thesis, first_document, failed_document])
    session.flush()
    session.add_all(
        [
            SourceSpan(
                document_version_id=first_document.id,
                locator={"page": 1},
                verbatim_text=(
                    "Management described sustained accelerator demand and "
                    "a longer order backlog in the latest operating update."
                ),
            ),
            CaseDocumentVersion(
                research_case_id=case.id,
                document_version_id=first_document.id,
                linked_at=now,
            ),
            SourceSpan(
                document_version_id=failed_document.id,
                locator={"page": 1},
                verbatim_text=(
                    "The second operating update contains another narrative "
                    "source that requires provider extraction."
                ),
            ),
            CaseDocumentVersion(
                research_case_id=case.id,
                document_version_id=failed_document.id,
                linked_at=now,
            ),
        ]
    )
    session.commit()

    client = LLMClient(model_version="provider-test", mock=True)
    service = AutoResearchService(session)
    service._client = client
    run = service.start(case.id, max_rounds=1, budget=10)
    run_id = run.id
    output_slot_calls = 0
    original_output_slot = service._claim_extraction_output_slot

    def count_output_slot(checked_run):
        nonlocal output_slot_calls
        output_slot_calls += 1
        return original_output_slot(checked_run)

    monkeypatch.setattr(service, "_claim_extraction_output_slot", count_output_slot)

    provider_calls = 0

    def fail_second_provider_call(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            return {"statements": []}
        raise RuntimeError("provider transport failed")

    monkeypatch.setattr(client, "chat_json", fail_second_provider_call)

    service.execute(run)
    session.commit()
    session.rollback()

    assert output_slot_calls == 2

    with Session(session.get_bind()) as check:
        persisted_run = check.get(ResearchRun, run_id)
        assert persisted_run is not None
        assert persisted_run.status == "failed"
        assert persisted_run.stage == "failed"
        assert persisted_run.stop_reason == "task_failed"
        assert persisted_run.budget_used == 2

        ai_runs = list(
            check.scalars(select(AIRun).order_by(AIRun.started_at, AIRun.id))
        )
        assert [(item.kind, item.status) for item in ai_runs] == [
            ("extract", "success"),
            ("extract", "failed"),
        ]
        assert check.scalar(select(func.count()).select_from(Proposal)) == 0
        assert check.scalar(select(func.count()).select_from(AIAssessment)) == 0
        assert all(
            task.status == "queued"
            for task in check.scalars(
                select(ResearchTask).where(ResearchTask.run_id == run_id)
            )
        )
        failed_events = list(
            check.scalars(
                select(ResearchRunEvent)
                .where(ResearchRunEvent.run_id == run_id)
                .where(ResearchRunEvent.stage == "failed")
                .where(ResearchRunEvent.status == "failed")
            )
        )
        assert len(failed_events) == 1
        assert failed_events[0].payload_json == {
            "status": "failed",
            "stop_reason": "task_failed",
            "budget_used": 2,
        }


def test_cancelled_run_discards_inflight_extraction_output(
    tmp_path, monkeypatch
):
    from app.ai.client import LLMClient
    from app.models.operational import Job

    engine = create_engine(f"sqlite:///{tmp_path / 'extract-cancel.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title="cancel extraction",
            industry_topic="i",
            created_by="u",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        setup.add(
            Thesis(
                research_case_id=case.id,
                statement="Late extraction must be discarded",
                created_by="u",
                created_at=now,
            )
        )
        document = DocumentVersion(
            content_sha256=uuid.uuid4().hex,
            source_url="https://example.test/cancel-extraction",
            available_at=now,
            acquired_at=now,
            parser_version="test",
        )
        setup.add(document)
        setup.flush()
        span = SourceSpan(
            document_version_id=document.id,
            locator={"page": 1},
            verbatim_text="Management reported accelerator demand remained strong.",
        )
        setup.add_all(
            [
                span,
                CaseDocumentVersion(
                    research_case_id=case.id,
                    document_version_id=document.id,
                    linked_at=now,
                ),
            ]
        )
        setup.commit()
        run = AutoResearchService(setup).start(case.id, max_rounds=1, budget=10)
        run_id = run.id
        span_id = span.id

    client = LLMClient(model_version="provider-test", mock=True)

    def cancel_then_return(*_args, **_kwargs):
        with session_local() as cancelling:
            cancelled_run = cancelling.get(ResearchRun, run_id)
            assert cancelled_run is not None
            assert AutoResearchService(cancelling).repo.cancel_run(cancelled_run)
            cancelling.commit()
        text = "Management reported accelerator demand remained strong."
        return {
            "statements": [
                {
                    "span_id": str(span_id),
                    "quote": text,
                    "quote_start": 0,
                    "quote_end": len(text),
                    "normalized_text": "Accelerator demand remained strong.",
                    "kind": "reported_claim",
                }
            ]
        }

    monkeypatch.setattr(client, "chat_json", cancel_then_return)
    with session_local() as worker:
        run = worker.get(ResearchRun, run_id)
        assert run is not None
        service = AutoResearchService(worker)
        service._client = client
        service.execute(run)
        worker.commit()

    with Session(engine) as check:
        run = check.get(ResearchRun, run_id)
        job = check.scalar(
            select(Job).where(Job.target_type == "research_run", Job.target_id == run_id)
        )
        assert run is not None and run.status == "cancelled"
        assert job is not None and job.status == "cancelled"
        assert check.scalar(select(func.count()).select_from(AtomicClaimCandidate)) == 0
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(AIRun.kind == "extract")
        ) == 0


def test_task_output_slot_refreshes_cached_job_cancellation(tmp_path):
    from app.api.v1.jobs import cancel_job
    from app.models.operational import Job

    engine = create_engine(
        f"sqlite:///{tmp_path / 'cached-job-cancellation.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title="cached cancellation",
            industry_topic="i",
            created_by="u",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        thesis = Thesis(
            research_case_id=case.id,
            statement="empty recall must still observe cancellation",
            created_by="u",
            created_at=now,
        )
        setup.add(thesis)
        setup.flush()
        repo = AutoResearchRepository(setup)
        run = repo.create_run(
            research_case_id=case.id,
            max_rounds=1,
            budget=1,
            scope_thesis_ids=[str(thesis.id)],
        )
        task = repo.create_task(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            task_type="support",
            query="no recalled statements",
        )
        job = repo.enqueue_run_job(run)
        job.status = "running"
        setup.commit()
        run_id, task_id, job_id = run.id, task.id, job.id

    with session_local() as worker:
        run = worker.get(ResearchRun, run_id)
        task = worker.get(ResearchTask, task_id)
        cached_job = worker.get(Job, job_id)
        assert run is not None and task is not None and cached_job is not None
        assert cached_job.cancel_requested is False

        with session_local() as cancelling:
            response = cancel_job(job_id, db=cancelling)
            assert response.cancel_requested is True

        service = AutoResearchService(worker)
        assert service._claim_task_output_slot(run, task) is False


@pytest.mark.pg_only
def test_postgres_extraction_failure_waits_for_cancellation_transition(
    engine, monkeypatch
):
    from app.ai.client import LLMClient

    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local() as setup:
        case = ResearchCase(
            title="failure cancellation race",
            industry_topic="i",
            created_by="u",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        setup.add(
            Thesis(
                research_case_id=case.id,
                statement="Cancellation must win provider failure",
                created_by="u",
                created_at=now,
            )
        )
        document = DocumentVersion(
            content_sha256=uuid.uuid4().hex,
            source_url="https://example.test/failure-cancel-race",
            available_at=now,
            acquired_at=now,
            parser_version="test",
        )
        setup.add(document)
        setup.flush()
        setup.add_all(
            [
                SourceSpan(
                    document_version_id=document.id,
                    locator={"page": 1},
                    verbatim_text="Provider work is still in flight for this source.",
                ),
                CaseDocumentVersion(
                    research_case_id=case.id,
                    document_version_id=document.id,
                    linked_at=now,
                ),
            ]
        )
        setup.commit()
        run = AutoResearchService(setup).start(case.id, max_rounds=1, budget=10)
        run_id = run.id

    cancellation_locked = Event()
    provider_failed = Event()
    worker_marked_failed = Event()
    cancellation_errors: list[BaseException] = []
    worker_errors: list[BaseException] = []
    failed_before_cancel_commit: list[bool] = []

    client = LLMClient(model_version="provider-test", mock=True)

    def fail_after_cancellation_locks(*_args, **_kwargs):
        assert cancellation_locked.wait(timeout=5)
        provider_failed.set()
        raise RuntimeError("provider failed during cancellation")

    monkeypatch.setattr(client, "chat_json", fail_after_cancellation_locks)
    original_update_run = AutoResearchRepository.update_run

    def observe_failed_transition(self, updated_run, **kwargs):
        if updated_run.id == run_id and kwargs.get("status") == "failed":
            worker_marked_failed.set()
        return original_update_run(self, updated_run, **kwargs)

    monkeypatch.setattr(
        AutoResearchRepository, "update_run", observe_failed_transition
    )

    def execute_run():
        with session_local() as worker:
            try:
                run = worker.get(ResearchRun, run_id)
                assert run is not None
                service = AutoResearchService(worker)
                service._client = client
                service.execute(run)
                worker.commit()
            except BaseException as exc:
                worker_errors.append(exc)
                worker.rollback()

    def cancel_run_while_provider_is_inflight():
        with session_local() as cancelling:
            try:
                service = AutoResearchService(cancelling)
                run = service._lock_run_for_transition(run_id)
                assert run is not None
                assert service.repo.cancel_run(run)
                cancelling.flush()
                cancellation_locked.set()
                assert provider_failed.wait(timeout=5)
                failed_before_cancel_commit.append(
                    worker_marked_failed.wait(timeout=1)
                )
                cancelling.commit()
            except BaseException as exc:
                cancellation_errors.append(exc)
                cancelling.rollback()

    worker_thread = Thread(target=execute_run)
    cancellation_thread = Thread(target=cancel_run_while_provider_is_inflight)
    worker_thread.start()
    cancellation_thread.start()
    worker_thread.join(timeout=10)
    cancellation_thread.join(timeout=10)

    assert not worker_thread.is_alive()
    assert not cancellation_thread.is_alive()
    assert not worker_errors
    assert not cancellation_errors
    assert failed_before_cancel_commit == [False]

    with session_local() as check:
        run = check.get(ResearchRun, run_id)
        assert run is not None and run.status == "cancelled"
        assert check.scalar(
            select(func.count()).select_from(AIRun).where(AIRun.kind == "extract")
        ) == 0


def test_tasks_created(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    run = AutoResearchService(session).start(case.id, max_rounds=1, budget=1)
    tasks = AutoResearchRepository(session).tasks_for_run(run.id)
    assert any(t.task_type == "support" for t in tasks)
    assert any(t.task_type == "contradict" for t in tasks)
    assert any(t.task_type == "result" for t in tasks)
    assert any(t.task_type == "alternative" for t in tasks)


def test_worker_commits_final_protocol_block_audit_in_clean_transaction(
    session, monkeypatch
):
    from app.ai.client import LLMClient
    from app.services.research_protocol import ResearchabilityResult
    from tests.protocol_provenance import seed_protocol_footprint

    case = ResearchCase(
        title="worker final protocol block",
        industry_topic="i",
        created_by="u",
        created_at=datetime.now(timezone.utc),
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="Protocol becomes blocked while assessment provider runs",
        research_protocol_required=True,
        created_by="u",
        created_at=datetime.now(timezone.utc),
    )
    session.add(thesis)
    session.flush()
    footprint = seed_protocol_footprint(session, thesis, status="ready")
    ready = ResearchabilityResult(
        "ready",
        [],
        footprint.binding.id,
        "assess",
        footprint.template.id,
        tuple(rule.id for rule in footprint.rules),
    )
    blocked = ResearchabilityResult(
        "blocked",
        ["missing_verification_rule"],
        footprint.binding.id,
        "complete protocol",
        footprint.template.id,
        (),
    )
    gates = iter([ready, blocked])
    monkeypatch.setattr(
        "app.ai.assessment_gen.ResearchProtocolService.check_researchability",
        lambda _service, _thesis_id: next(gates),
    )
    repo = AutoResearchRepository(session)
    run = repo.create_run(
        research_case_id=case.id,
        max_rounds=1,
        budget=1,
        scope_thesis_ids=[str(thesis.id)],
    )
    task = repo.create_task(
        run_id=run.id,
        research_case_id=case.id,
        thesis_id=thesis.id,
        task_type="result",
        query="form a conclusion",
    )
    session.commit()
    run_id, task_id, thesis_id = run.id, task.id, thesis.id
    service = AutoResearchService(session)
    service._client = LLMClient(model_version="mock-worker-audit", mock=True)

    service.execute(run)

    with Session(session.get_bind()) as check:
        persisted_task = check.get(ResearchTask, task_id)
        assert persisted_task is not None and persisted_task.status == "failed"
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
        persisted_run = check.get(ResearchRun, run_id)
        assert persisted_run is not None and persisted_run.status == "failed"
    assert len(runs) == 1
    assert runs[0].input_ref["final_protocol_status"] == "blocked"


def test_auto_research_stops_before_propose_or_assess_when_atomic_claims_await_review(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="claim gate", industry_topic="i", created_by="u", created_at=now)
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="订单增长将改善收入", created_by="u", created_at=now)
    document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://issuer.example.com/disclosure", available_at=now, acquired_at=now, parser_version="test")
    session.add_all([thesis, document]); session.flush()
    source_text = "公司披露订单同比增长20%。"
    quote = "订单同比增长20%"
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text=source_text)
    session.add_all([span, CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now)])
    session.flush()
    AtomicClaimService(session).admit(
        AtomicClaimDraft(source_span_id=span.id, quote=quote, quote_start=source_text.index(quote), quote_end=source_text.index(quote) + len(quote), normalized_text="公司披露订单同比增长 20%", claim_type="disclosed_fact", assertion_actor="公司", subject="订单", predicate="同比增长", object_text="20%", numeric_value="20", unit="%", observed_period=None, scope={}),
        authority_level="primary_disclosure",
        run_ref="extract:already-pending",
    )
    session.commit()

    run = AutoResearchService(session).start(case.id, max_rounds=1, budget=20)
    AutoResearchService(session).execute(run)
    session.commit()

    assert run.status == "waiting_for_review"
    assert run.stop_reason == "pending_atomic_claim_review"
    assert session.scalar(select(func.count()).select_from(Proposal)) == 0
    assert session.scalar(select(func.count()).select_from(AIAssessment)) == 0
    assert session.scalar(select(func.count()).select_from(AtomicClaimCandidate)) == 1
    assert any(event.stage == "claim_review" and "原子陈述" in event.message for event in session.scalars(select(ResearchRunEvent).where(ResearchRunEvent.run_id == run.id)))
    assert any(task.task_type == "review_atomic_claim" and task.status == "open" for task in session.scalars(select(TaskItem).where(TaskItem.research_case_id == case.id)))


def test_run_reconciliation_scopes_atomic_claim_gates_to_each_run(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="run-local claim gate",
        industry_topic="i",
        created_by="u",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://issuer.example.com/run-local-claim",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add_all([case, document])
    session.flush()
    source_text = "公司披露订单同比增长20%。"
    quote = "订单同比增长20%"
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text=source_text,
    )
    session.add_all([
        span,
        CaseDocumentVersion(
            research_case_id=case.id,
            document_version_id=document.id,
            linked_at=now,
        ),
    ])
    session.flush()
    candidate = AtomicClaimService(session).admit(
        AtomicClaimDraft(
            source_span_id=span.id,
            quote=quote,
            quote_start=source_text.index(quote),
            quote_end=source_text.index(quote) + len(quote),
            normalized_text="公司披露订单同比增长 20%",
            claim_type="disclosed_fact",
            assertion_actor="公司",
            subject="订单",
            predicate="同比增长",
            object_text="20%",
            numeric_value="20",
            unit="%",
            observed_period=None,
            scope={},
        ),
        authority_level="primary_disclosure",
        run_ref="extract:run-local-gate",
    )
    unrelated_run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        created_at=now,
        updated_at=now,
    )
    claim_run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        created_at=now,
        updated_at=now,
    )
    session.add_all([unrelated_run, claim_run])
    session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={},
        target_context={},
        proposed_by_type="ai",
        proposed_by_ref="test-run",
        proposed_at=now,
        status="decided",
        research_case_id=case.id,
    )
    session.add(proposal)
    session.flush()
    for run in (unrelated_run, claim_run):
        session.add(
            ResearchTask(
                run_id=run.id,
                research_case_id=case.id,
                status="done",
                stage="completed",
                round=1,
                task_type="result",
                query="final review output",
                result={"proposed_proposal_ids": [str(proposal.id)]},
                created_at=now,
                updated_at=now,
            )
        )
    ResearchRunEventRepository(session).append(
        claim_run.id,
        stage="claim_review",
        status="waiting_for_review",
        message="等待此运行的原子陈述审核",
        payload_json={"candidate_ids": [str(candidate.id)]},
    )

    service = AutoResearchService(session)
    assert service.reconcile_runs_for_output(
        key="proposed_proposal_ids",
        value=proposal.id,
        trigger_ref=f"proposal:{proposal.id}",
    ) == [unrelated_run.id]
    assert not service.reconcile_run(claim_run.id, trigger_ref=f"proposal:{proposal.id}")


def test_historical_scalar_proposal_output_is_a_review_gate(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="scalar result", industry_topic="i", created_by="u", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=now)
    session.add(thesis)
    session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={},
        target_context={"thesis_id": str(thesis.id)},
        proposed_by_type="ai",
        proposed_by_ref="test-run",
        proposed_at=now,
        research_case_id=case.id,
    )
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        created_at=now,
        updated_at=now,
    )
    session.add_all([proposal, run])
    session.flush()
    session.add(
        ResearchTask(
            run_id=run.id,
            research_case_id=case.id,
            thesis_id=thesis.id,
            status="done",
            stage="completed",
            round=1,
            task_type="result",
            query="historical scalar output",
            result={"proposed_proposal_ids": str(proposal.id)},
            created_at=now,
            updated_at=now,
        )
    )

    service = AutoResearchService(session)
    assert service._successful_terminal_status(run) == "waiting_for_review"
    service._handoff_for_review(run)
    assert session.scalar(
        select(TaskItem).where(TaskItem.ref_id == proposal.id)
    ) is not None
    detail = service.detail(run.id)
    assert detail is not None
    assert detail["pending_proposals"][0]["id"] == str(proposal.id)


def test_non_dict_task_result_is_ignored_by_review_gate_helpers(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="malformed result", industry_topic="i", created_by="u", created_at=now)
    session.add(case)
    session.flush()
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    session.add(
        ResearchTask(
            run_id=run.id,
            research_case_id=case.id,
            status="done",
            stage="completed",
            round=1,
            task_type="result",
            query="malformed historical output",
            result="not a JSON object",
            created_at=now,
            updated_at=now,
        )
    )

    service = AutoResearchService(session)
    assert service._successful_terminal_status(run) == "succeeded"
    service._handoff_for_review(run)
    detail = service.detail(run.id)
    assert detail is not None
    assert detail["assessments"] == []
    assert detail["pending_assessments"] == []


@pytest.mark.parametrize("malformed_result", ["not a JSON object", ["bad"]])
def test_run_detail_and_archive_ignore_malformed_historical_results(
    cmd_client, cmd_session, malformed_result
):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="historical archive",
        industry_topic="i",
        created_by="u",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    _admit_case(cmd_session, case)
    run = ResearchRun(
        research_case_id=case.id,
        status="succeeded",
        stage="complete",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        created_at=now,
        updated_at=now,
    )
    cmd_session.add(run)
    cmd_session.flush()
    cmd_session.add(
        ResearchTask(
            run_id=run.id,
            research_case_id=case.id,
            status="done",
            stage="completed",
            round=1,
            task_type="result",
            query="malformed historical output",
            result=malformed_result,
            created_at=now,
            updated_at=now,
        )
    )
    cmd_session.commit()

    detail = cmd_client.get(f"/api/v1/research-runs/{run.id}")
    archive = cmd_client.get("/api/v1/research-runs")
    case_archive = cmd_client.get(f"/api/v1/research-cases/{case.id}/runs")

    assert detail.status_code == 200, detail.text
    assert detail.json()["assessments"] == []
    assert archive.status_code == 200, archive.text
    assert archive.json()["items"][0]["run_id"] == str(run.id)
    assert case_archive.status_code == 200, case_archive.text
    assert case_archive.json()["items"][0]["id"] == str(run.id)


@pytest.mark.pg_only
def test_cancel_wins_over_concurrent_final_review_reconciliation(engine):
    """A final review cannot append completion after another session cancels."""
    session_local = sessionmaker(bind=engine, future=True)
    now = datetime.now(timezone.utc)
    with session_local.begin() as setup:
        case = ResearchCase(
            title="reconcile cancellation race",
            industry_topic="i",
            created_by="u",
            created_at=now,
        )
        setup.add(case)
        setup.flush()
        run = ResearchRun(
            research_case_id=case.id,
            status="waiting_for_review",
            stage="stopped",
            round=1,
            max_rounds=1,
            budget=10,
            budget_used=1,
            created_at=now,
            updated_at=now,
        )
        setup.add(run)
        setup.flush()
        run_id = run.id

    cancelling = session_local()
    locked_run = AutoResearchService(cancelling)._lock_run_for_transition(run_id)
    assert locked_run is not None
    assert AutoResearchService(cancelling).repo.cancel_run(locked_run)
    cancelling.flush()

    finished = Event()
    result: list[bool] = []

    def reconcile_in_second_session() -> None:
        competing = session_local()
        try:
            result.append(
                AutoResearchService(competing).reconcile_run(
                    run_id, trigger_ref="proposal:final"
                )
            )
            competing.commit()
        finally:
            competing.close()
            finished.set()

    thread = Thread(target=reconcile_in_second_session)
    thread.start()
    assert not finished.wait(0.1)
    cancelling.commit()
    thread.join(timeout=5)
    cancelling.close()

    assert not thread.is_alive()
    assert result == [False]
    with session_local() as check:
        run = check.get(ResearchRun, run_id)
        assert run is not None and run.status == "cancelled"
        assert check.scalars(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run_id)
            .where(ResearchRunEvent.stage == "review_complete")
        ).all() == []


def test_budget_stop(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    run = AutoResearchService(session).start(case.id, max_rounds=3, budget=1)
    AutoResearchService(session).execute(run)
    session.commit()
    detail = AutoResearchService(session).detail(run.id)
    assert detail["stop_reason"] == "budget_exhausted"
    assert detail["budget_used"] >= 1


def test_empty_run_completes_without_a_phantom_review_task(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case)
    session.commit()
    run = AutoResearchRepository(session).create_run(
        research_case_id=case.id,
        max_rounds=1,
        budget=1000,
    )
    AutoResearchService(session).execute(run)
    session.commit()
    session.refresh(run)

    assert run.status == "succeeded"
    assert run.stop_reason == "max_rounds_reached"
    assert list(session.scalars(select(TaskItem).where(TaskItem.research_case_id == case.id))) == []
    completion = session.scalar(
        select(ResearchRunEvent)
        .where(ResearchRunEvent.run_id == run.id)
        .where(ResearchRunEvent.stage == "complete")
    )
    assert completion is not None
    assert completion.payload_json["status"] == "succeeded"
    assert "未产生新增待审材料" in completion.message


def test_run_detail_exposes_open_provisional_assessment_review(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=now)
    session.add(thesis)
    session.flush()
    snapshot = EvidenceSnapshot(
        thesis_id=thesis.id,
        cutoff=now,
        evidence_link_ids=[],
        created_at=now,
    )
    session.add(snapshot)
    session.flush()
    assessment = AIAssessment(
        snapshot_id=snapshot.id,
        conclusion="insufficient_evidence",
        rationale="缺少原始预测值",
        gaps=["补充历史预测值"],
        created_at=now,
    )
    session.add(assessment)
    session.flush()
    run = AutoResearchRepository(session).create_run(research_case_id=case.id)
    task = ResearchTask(
        run_id=run.id,
        research_case_id=case.id,
        thesis_id=thesis.id,
        status="done",
        stage="completed",
        round=1,
        task_type="result",
        query="生成临时评估",
        result={"assessment_id": str(assessment.id)},
        created_at=now,
        updated_at=now,
    )
    review_task = TaskItem(
        title="确认临时 AI 评估",
        task_type="review_assessment",
        ref_type="ai_assessment",
        ref_id=assessment.id,
        research_case_id=case.id,
        status="open",
        created_at=now,
    )
    session.add_all([task, review_task])
    session.commit()

    detail = AutoResearchService(session).detail(run.id)

    assert detail["pending_assessments"] == [{
        "assessment_id": str(assessment.id),
        "conclusion": "insufficient_evidence",
        "rationale": "缺少原始预测值",
        "gaps": ["补充历史预测值"],
        "task_id": str(review_task.id),
        "task_status": "open",
    }]


def test_round_2_gap_task_executes(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    repo = AutoResearchRepository(session)
    run = repo.create_run(research_case_id=case.id, max_rounds=2, budget=50)
    repo.create_task(run_id=run.id, research_case_id=case.id, thesis_id=thesis.id, task_type="support", query="support gap", round=2)
    session.commit()
    AutoResearchService(session).execute(run)
    session.commit()
    tasks = repo.tasks_for_run(run.id)
    assert any(t.round == 2 and t.status in {"done", "failed"} for t in tasks)


def test_duplicate_gap_does_not_duplicate(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    repo = AutoResearchRepository(session)
    run = repo.create_run(research_case_id=case.id, max_rounds=2, budget=50)
    repo.create_task(run_id=run.id, research_case_id=case.id, thesis_id=thesis.id, task_type="alternative", query="gap query", round=2)
    session.commit()
    AutoResearchService(session).execute(run)
    session.commit()
    tasks = repo.tasks_for_run(run.id)
    alternative_r2 = [t for t in tasks if t.task_type == "alternative" and t.round == 2 and t.query == "gap query"]
    assert len(alternative_r2) >= 1


def test_formal_evidence_counts_not_double_counted(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.flush()
    document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://example.com", available_at=datetime.now(timezone.utc), acquired_at=datetime.now(timezone.utc), parser_version="test")
    session.add(document); session.flush()
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text="span")
    session.add(span); session.flush()
    stmt = SourceStatement(source_span_id=span.id, kind="fact", normalized_text="stmt", created_at=datetime.now(timezone.utc))
    session.add(stmt); session.flush()
    link1 = EvidenceLink(thesis_id=thesis.id, source_statement_id=stmt.id, role="support", reason="r1", scope={}, available_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
    link2 = EvidenceLink(thesis_id=thesis.id, source_statement_id=stmt.id, role="support", reason="r2", scope={}, available_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
    session.add_all([link1, link2]); session.commit()
    repo = AutoResearchRepository(session)
    counts = repo.evidence_link_counts_by_thesis(case.id)
    assert counts.get(str(thesis.id), {}).get("support", 0) == 2


def test_failed_task_visible_in_detail(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    repo = AutoResearchRepository(session)
    run = repo.create_run(research_case_id=case.id, max_rounds=1, budget=1)
    session.commit()
    detail = AutoResearchService(session).detail(run.id)
    assert "failed_tasks" in detail
    assert isinstance(detail["failed_tasks"], list)


def test_failed_task_result_and_detail_do_not_persist_provider_exception(
    session, monkeypatch
):
    case = ResearchCase(
        title="safe provider task failure",
        industry_topic="i",
        created_by="u",
        created_at=datetime.now(timezone.utc),
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="provider errors stay private",
        created_by="u",
        created_at=datetime.now(timezone.utc),
    )
    session.add(thesis)
    session.flush()
    repo = AutoResearchRepository(session)
    run = repo.create_run(
        research_case_id=case.id,
        max_rounds=1,
        budget=1,
        scope_thesis_ids=[str(thesis.id)],
    )
    task = repo.create_task(
        run_id=run.id,
        research_case_id=case.id,
        thesis_id=thesis.id,
        task_type="support",
        query="find evidence",
    )
    session.commit()
    service = AutoResearchService(session)
    monkeypatch.setattr(
        service,
        "_propose_for_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("upstream URL token=sentinel-secret")
        ),
    )

    service.execute(run)
    session.commit()

    persisted = session.get(ResearchTask, task.id)
    assert persisted is not None and persisted.status == "failed"
    assert persisted.result["error"] == "AI operation failed"
    assert persisted.result["error_type"] == "operation_failure"
    assert "sentinel-secret" not in str(persisted.result)
    detail = service.detail(run.id)
    assert detail is not None
    assert "sentinel-secret" not in str(detail["failed_tasks"])


def test_support_contradict_balance_gap_created(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.flush()
    document = DocumentVersion(content_sha256=uuid.uuid4().hex, source_url="https://example.com", available_at=datetime.now(timezone.utc), acquired_at=datetime.now(timezone.utc), parser_version="test")
    session.add(document); session.flush()
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text="span")
    session.add(span); session.flush()
    stmt = SourceStatement(source_span_id=span.id, kind="fact", normalized_text="stmt", created_at=datetime.now(timezone.utc))
    session.add(stmt); session.flush()
    link = EvidenceLink(thesis_id=thesis.id, source_statement_id=stmt.id, role="support", reason="r", scope={}, available_at=datetime.now(timezone.utc), created_at=datetime.now(timezone.utc))
    session.add(link); session.commit()
    repo = AutoResearchRepository(session)
    run = repo.create_run(research_case_id=case.id, max_rounds=2, budget=10)
    session.commit()
    AutoResearchService(session).execute(run)
    session.commit()
    tasks = repo.tasks_for_run(run.id)
    gap_tasks = [t for t in tasks if t.gap_reason == "evidence_balance"]
    assert any(t.task_type == "contradict" for t in gap_tasks)


def test_by_thesis_counts_in_detail(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    run = AutoResearchService(session).start(case.id, max_rounds=1, budget=1)
    detail = AutoResearchService(session).detail(run.id)
    assert "by_thesis" in detail
    assert isinstance(detail["by_thesis"], dict)


def test_task_result_includes_task_type_and_proposal_ids(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    run = AutoResearchService(session).start(case.id, max_rounds=1, budget=5)
    detail = AutoResearchService(session).detail(run.id)
    tasks = detail["tasks"]
    support_tasks = [t for t in tasks if t["task_type"] == "support" and t["status"] == "done"]
    if support_tasks:
        result = support_tasks[0]["result"]
        assert result is not None
        assert "task_type" in result
        assert "proposed_proposal_ids" in result


def test_list_runs_returns_recent_runs(cmd_client, cmd_session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(case); cmd_session.flush()
    _admit_case(cmd_session, case)
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(thesis); cmd_session.commit()
    repo = AutoResearchRepository(cmd_session)
    older = repo.create_run(research_case_id=case.id, max_rounds=1, budget=10)
    newer = repo.create_run(research_case_id=case.id, max_rounds=1, budget=20)
    cmd_session.commit()

    resp = cmd_client.get(f"/api/v1/research-cases/{case.id}/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["id"] == str(newer.id)
    assert body["items"][0]["budget"] == 20
    assert body["items"][0]["next_action"]
    assert "created_at" in body["items"][0]
    assert "updated_at" in body["items"][0]
    assert "max_rounds" in body["items"][0]
    assert "budget_used" in body["items"][0]


def test_worker_status_distinguishes_an_unavailable_executor_from_a_live_loop(
    cmd_client, cmd_session
):
    unavailable = cmd_client.get("/api/v1/research-runs/worker-status")

    assert unavailable.status_code == 200
    assert unavailable.json() == {
        "status": "unavailable",
        "last_seen_at": None,
        "mode": None,
        "state": None,
    }

    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    WorkerHeartbeatService(cmd_session).touch(
        worker_id="test-loop",
        mode="loop",
        state="polling",
        seen_at=datetime.now(timezone.utc),
    )
    cmd_session.commit()

    live = cmd_client.get("/api/v1/research-runs/worker-status")

    assert live.status_code == 200
    assert live.json()["status"] == "available"
    assert live.json()["mode"] == "loop"
    assert live.json()["state"] == "polling"
    assert live.json()["last_seen_at"]


def test_cancel_run_success_and_idempotent(cmd_client, cmd_session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(case); cmd_session.flush()
    _admit_case(cmd_session, case)
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(thesis); cmd_session.commit()
    run = AutoResearchRepository(cmd_session).create_run(research_case_id=case.id, max_rounds=1, budget=10)
    run.status = "running"
    cmd_session.commit()

    payload = {"actor": "human:researcher", "change_reason": "授权来源异常，停止后重新配置"}
    resp = cmd_client.post(f"/api/v1/research-runs/{run.id}/cancel", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    events = cmd_client.get(f"/api/v1/research-runs/{run.id}/events").json()["items"]
    assert events[-1]["stage"] == "stopped"
    assert events[-1]["details"] == {"actor": payload["actor"], "change_reason": payload["change_reason"], "stop_reason": "cancelled"}

    resp2 = cmd_client.post(f"/api/v1/research-runs/{run.id}/cancel", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "cancelled"


def test_cancel_run_terminal_conflict(cmd_client, cmd_session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(case); cmd_session.flush()
    _admit_case(cmd_session, case)
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(thesis); cmd_session.commit()
    run = AutoResearchRepository(cmd_session).create_run(research_case_id=case.id, max_rounds=1, budget=10)
    run.status = "succeeded"
    cmd_session.commit()

    resp = cmd_client.post(f"/api/v1/research-runs/{run.id}/cancel", json={"actor": "human:researcher", "change_reason": "测试终态"})
    assert resp.status_code == 409


def test_real_api_human_loop_from_queued_run_to_published_proposal(cmd_client, cmd_session):
    """No frontend mock: queue a run, execute it, then publish through HTTP."""
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="API loop", industry_topic="semis", created_by="e2e", created_at=now)
    cmd_session.add(case); cmd_session.flush()
    _admit_case(cmd_session, case)
    thesis = Thesis(research_case_id=case.id, statement="订单增长将改善收入", created_by="e2e", created_at=now)
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="https://investor.tsmc.com/english/quarterly-results",
        available_at=now,
        acquired_at=now,
        parser_version="html-v1",
        parse_state="success",
    )
    cmd_session.add_all([thesis, document]); cmd_session.flush()
    span = SourceSpan(document_version_id=document.id, locator={"page": 1}, verbatim_text="公司公告显示数据中心订单持续增长，预计下一报告期收入会相应改善。")
    cmd_session.add_all([span, CaseDocumentVersion(research_case_id=case.id, document_version_id=document.id, linked_at=now)])
    cmd_session.flush()
    cmd_session.add(SourceStatement(source_span_id=span.id, kind="disclosed_fact", normalized_text="数据中心订单持续增长", created_at=now))
    cmd_session.commit()

    started = cmd_client.post(f"/api/v1/research-cases/{case.id}/runs", json={"max_rounds": 1, "budget": 20})
    assert started.status_code == 201, started.text
    assert started.json()["status"] == "queued"
    run_id = uuid.UUID(started.json()["id"])

    # The production worker calls this executor after claiming the persisted Job.
    run = AutoResearchRepository(cmd_session).get_run(run_id)
    assert run is not None
    AutoResearchService(cmd_session).execute(run)
    cmd_session.commit()

    completed = cmd_client.get(f"/api/v1/research-runs/{run_id}")
    assert completed.status_code == 200
    assert completed.json()["status"] == "waiting_for_review"
    queue = cmd_client.get("/api/v1/review-proposals", params={"case_id": str(case.id), "kind": "evidence_link"})
    assert queue.status_code == 200
    proposal = queue.json()["items"][0]

    decision = cmd_client.post(
        f"/api/v1/review-proposals/{proposal['id']}/decisions",
        json={
            "outcome": "confirmed",
            "reason": "人工核对原文后确认发布",
            "expected_version": proposal["version"],
            "reviewer_id": "e2e-human",
        },
    )
    assert decision.status_code == 201, decision.text
    assert decision.json()["published_entity_id"]
