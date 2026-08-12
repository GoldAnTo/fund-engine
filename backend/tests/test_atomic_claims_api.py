from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    Thesis,
)
from app.models.operational import ResearchTask, TaskItem
from app.models.research_monitor import ResearchRunEvent
from app.models.source_governance import SourceContract
from app.services.atomic_claims import AtomicClaimService
from app.services.auto_research import AutoResearchService
from app.services.case_monitor import CaseMonitorConfig, CaseMonitorService


def _candidate_for_case(session):
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="原子陈述审核 Case",
        industry_topic="ai",
        created_by="human:owner",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="https://disclosure.example.org/atomic-claim",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    session.add_all([case, document])
    session.flush()
    session.add(CaseDocumentVersion(
        research_case_id=case.id,
        document_version_id=document.id,
        linked_at=now,
    ))
    session.add(CaseTenantAdmission(
        research_case_id=case.id,
        tenant_id="test-team",
        initial_document_version_id=document.id,
        admitted_by="test-fixture",
        admitted_at=now,
    ))
    source_text = "公司公告：2026年第一季度订单同比增长20%。"
    quote = "订单同比增长20%"
    quote_start = source_text.index(quote)
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 2, "paragraph": 3},
        verbatim_text=source_text,
    )
    session.add(span)
    session.flush()
    candidate = AtomicClaimService(session).admit(
        AtomicClaimDraft(
            source_span_id=span.id,
            quote=quote,
            quote_start=quote_start,
            quote_end=quote_start + len(quote),
            normalized_text="公司披露 2026 年第一季度订单同比增长 20%",
            claim_type="disclosed_fact",
            assertion_actor="公司",
            subject="订单",
            predicate="同比增长",
            object_text="20%",
            numeric_value="20",
            unit="%",
            observed_period=None,
            scope={"company": "测试公司"},
        ),
        authority_level="primary_disclosure",
        run_ref="extract:fixture",
    )
    session.commit()
    return case, candidate


def test_atomic_claim_queue_exposes_source_quote_and_review_history(cmd_client, cmd_session) -> None:
    case, candidate = _candidate_for_case(cmd_session)

    response = cmd_client.get(f"/api/v1/research-cases/{case.id}/atomic-claims")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["id"] == str(candidate.id)
    assert item["quote"] == "订单同比增长20%"
    assert item["quote_start"] == 14
    assert item["quote_end"] == 23
    assert item["locator"] == {"page": 2, "paragraph": 3}
    assert item["review_state"] == "awaiting_review"
    assert item["review_history"] == []


def test_atomic_claim_review_api_publishes_only_after_human_decision(cmd_client, cmd_session) -> None:
    case, candidate = _candidate_for_case(cmd_session)

    response = cmd_client.post(
        f"/api/v1/atomic-claims/{candidate.id}/reviews",
        json={
            "outcome": "modified",
            "normalized_text": "审核后确认：2026 年第一季度订单同比增长 20%",
            "reviewer": "human:reviewer",
            "reason": "已复核原文、主体和期间",
            "idempotency_key": "atomic-review-1",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "modified"
    assert body["published_source_statement"]["normalized_text"] == "审核后确认：2026 年第一季度订单同比增长 20%"

    queue = cmd_client.get(f"/api/v1/research-cases/{case.id}/atomic-claims")
    item = queue.json()["items"][0]
    assert item["review_state"] == "modified"
    assert item["review_history"][0]["reason"] == "已复核原文、主体和期间"
    assert item["published_source_statement"]["id"] == body["published_source_statement"]["id"]


def test_atomic_claim_review_requeues_the_paused_research_run(cmd_client, cmd_session) -> None:
    case, candidate = _candidate_for_case(cmd_session)
    cmd_session.add(
        Thesis(
            research_case_id=case.id,
            statement="订单增长将改善收入",
            created_by="human:owner",
            created_at=datetime.now(timezone.utc),
        )
    )
    cmd_session.flush()
    service = AutoResearchService(cmd_session)
    run = service.start(case.id)
    for task in service.repo.tasks_for_run(run.id):
        task.status = "blocked"
        task.stage = "claim_review"
    service.repo.update_run(
        run,
        status="waiting_for_review",
        stage="claim_review",
        stop_reason="pending_atomic_claim_review",
    )
    job = service.repo.job_for_run(run.id)
    assert job is not None
    job.status = "waiting_for_review"
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/atomic-claims/{candidate.id}/reviews",
        json={
            "outcome": "rejected",
            "reviewer": "human:reviewer",
            "reason": "不属于本次可用来源范围",
            "idempotency_key": "atomic-review-requeue-1",
        },
    )

    assert response.status_code == 201, response.text
    cmd_session.expire_all()
    resumed = service.repo.get_run(run.id)
    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.stage == "resume_after_claim_review"
    assert resumed.stop_reason is None
    assert all(task.status == "queued" for task in service.repo.tasks_for_run(run.id))
    resumed_job = service.repo.job_for_run(run.id)
    assert resumed_job is not None
    assert resumed_job.status == "queued"


def test_scoped_atomic_claim_review_only_gates_and_resumes_its_frozen_run(
    cmd_client, cmd_session
) -> None:
    """A frozen company-disclosure run ignores pending pasted candidates."""
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="冻结来源范围的原子陈述审核",
        industry_topic="ai",
        created_by="human:owner",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="订单增长将改善收入",
        created_by="human:owner",
        created_at=now,
    )
    company_document = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="https://issuer.example.org/disclosure",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    pasted_document = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="event://pasted-summary",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    cmd_session.add_all([thesis, company_document, pasted_document])
    cmd_session.flush()
    company_text = "公司公告：2026年第一季度订单同比增长20%。"
    pasted_text = "研究员粘贴：订单增长的背景摘要。"
    company_span = SourceSpan(
        document_version_id=company_document.id,
        locator={"page": 2},
        verbatim_text=company_text,
    )
    pasted_span = SourceSpan(
        document_version_id=pasted_document.id,
        locator={"paragraph": 1},
        verbatim_text=pasted_text,
    )
    cmd_session.add_all(
        [
            company_span,
            pasted_span,
            CaseDocumentVersion(
                research_case_id=case.id,
                document_version_id=company_document.id,
                linked_at=now,
            ),
            CaseDocumentVersion(
                research_case_id=case.id,
                document_version_id=pasted_document.id,
                linked_at=now,
            ),
            CaseTenantAdmission(
                research_case_id=case.id,
                tenant_id="test-team",
                initial_document_version_id=company_document.id,
                admitted_by="test-fixture",
                admitted_at=now,
            ),
            SourceContract(
                document_version_id=company_document.id,
                source_type="company_disclosure",
                provider_or_tenant="issuer",
                allow_ai_processing=True,
                allow_display=True,
                allow_export=False,
                allow_api=False,
                region="CN",
                effective_from=None,
                effective_until=None,
                retention_policy="case_retained",
                deletion_policy="not_recorded",
                downstream_restrictions=[],
                contract_version="v1",
                intake_metadata={},
                declared_by="human",
                created_at=now,
            ),
            SourceContract(
                document_version_id=pasted_document.id,
                source_type="pasted_snapshot",
                provider_or_tenant="researcher",
                allow_ai_processing=True,
                allow_display=True,
                allow_export=False,
                allow_api=False,
                region="CN",
                effective_from=None,
                effective_until=None,
                retention_policy="case_retained",
                deletion_policy="not_recorded",
                downstream_restrictions=[],
                contract_version="v1",
                intake_metadata={},
                declared_by="human",
                created_at=now,
            ),
        ]
    )
    cmd_session.flush()
    company_candidate = AtomicClaimService(cmd_session).admit(
        AtomicClaimDraft(
            source_span_id=company_span.id,
            quote="订单同比增长20%",
            quote_start=company_text.index("订单同比增长20%"),
            quote_end=company_text.index("订单同比增长20%") + len("订单同比增长20%"),
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
        run_ref="extract:company",
    )
    pasted_candidate = AtomicClaimService(cmd_session).admit(
        AtomicClaimDraft(
            source_span_id=pasted_span.id,
            quote="背景摘要",
            quote_start=pasted_text.index("背景摘要"),
            quote_end=pasted_text.index("背景摘要") + len("背景摘要"),
            normalized_text="研究员粘贴的订单增长背景摘要",
            claim_type="reported_claim",
            assertion_actor="研究员",
            subject="订单",
            predicate="背景",
            object_text="摘要",
            numeric_value=None,
            unit=None,
            observed_period=None,
            scope={},
        ),
        authority_level="user_supplied",
        run_ref="extract:pasted",
    )
    CaseMonitorService(cmd_session).save(
        case.id,
        actor="human:owner",
        config=CaseMonitorConfig(
            frequency="daily_20_00",
            factor_ids=[thesis.id],
            allowed_source_types=["company_disclosure"],
            next_verification_event="下一次财报",
            budget=20,
            change_reason="只核验公司披露",
        ),
    )
    service = AutoResearchService(cmd_session)
    run = service.start(case.id, allowed_source_types=["company_disclosure"])
    service._pause_for_atomic_claim_review(run, [company_candidate], used=0)
    service._handoff_for_review(run)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/atomic-claims/{company_candidate.id}/reviews",
        json={
            "outcome": "rejected",
            "reviewer": "human:reviewer",
            "reason": "已核对公司公告原文",
            "idempotency_key": "scoped-atomic-review-requeue-1",
        },
    )

    assert response.status_code == 201, response.text
    cmd_session.expire_all()
    resumed = service.repo.get_run(run.id)
    assert resumed is not None
    assert resumed.status == "queued"
    assert resumed.stage == "resume_after_claim_review"
    assert resumed.stop_reason is None
    review_candidate_ids = {
        task.ref_id
        for task in cmd_session.scalars(
            select(TaskItem)
            .where(TaskItem.research_case_id == case.id)
            .where(TaskItem.task_type == "review_atomic_claim")
        )
    }
    assert review_candidate_ids == {company_candidate.id}
    assert pasted_candidate.id not in review_candidate_ids
    scope_events = list(
        cmd_session.scalars(
            select(ResearchRunEvent)
            .where(ResearchRunEvent.run_id == run.id)
            .where(ResearchRunEvent.stage == "scope")
        )
    )
    assert len(scope_events) == 1
    assert scope_events[0].run_id == run.id
    assert scope_events[0].payload_json["allowed_source_types"] == ["company_disclosure"]


def test_researcher_can_propose_one_frozen_source_span_for_review(
    cmd_client, cmd_session
) -> None:
    case, existing = _candidate_for_case(cmd_session)
    span = cmd_session.get(SourceSpan, existing.source_span_id)
    assert span is not None
    cmd_session.add(
        SourceContract(
            document_version_id=span.document_version_id,
            source_type="company_disclosure",
            provider_or_tenant="测试公司",
            allow_ai_processing=False,
            allow_display=True,
            allow_export=False,
            allow_api=False,
            region="CN",
            effective_from=None,
            effective_until=None,
            retention_policy="case_retained",
            deletion_policy="not_recorded",
            downstream_restrictions=["仅限当前 Case 审核"],
            contract_version=None,
            intake_metadata={},
            declared_by="human:owner",
            created_at=datetime.now(timezone.utc),
        )
    )
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/atomic-claims",
        json={
            "source_span_id": str(span.id),
            "normalized_text": "管理层披露 2026 年第一季度订单同比增长 20%。",
            "claim_type": "reported_claim",
            "assertion_actor": "管理层",
            "actor": "human:researcher",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_span_id"] == str(span.id)
    assert body["quote"] == span.verbatim_text
    assert body["quote_start"] == 0
    assert body["quote_end"] == len(span.verbatim_text)
    assert body["review_state"] == "awaiting_review"
    assert body["published_source_statement"] is None
    assert body["structured_fields"]["run_ref"] == "human:source-reader:human:researcher"
