from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain.atomic_claims import AtomicClaimDraft
from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    Thesis,
)
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.models.source_governance import SourceContract
from app.services.atomic_claims import AtomicClaimService
from app.services.auto_research import AutoResearchService
from app.services.case_monitor import ResearchRunEventRepository


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
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="claim_review",
        status="waiting_for_review",
        message="等待本次运行的原子陈述审核",
        payload_json={"candidate_ids": [str(candidate.id)]},
    )
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


def test_atomic_claim_review_resumes_only_runs_referencing_that_claim(
    cmd_client, cmd_session
) -> None:
    case, first_candidate = _candidate_for_case(cmd_session)
    now = datetime.now(timezone.utc)
    second_document = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="https://disclosure.example.org/atomic-claim-second",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    cmd_session.add(second_document)
    cmd_session.flush()
    second_text = "公司公告：2026年第二季度订单同比增长30%。"
    second_quote = "订单同比增长30%"
    second_span = SourceSpan(
        document_version_id=second_document.id,
        locator={"page": 3},
        verbatim_text=second_text,
    )
    cmd_session.add_all([
        CaseDocumentVersion(
            research_case_id=case.id,
            document_version_id=second_document.id,
            linked_at=now,
        ),
        SourceContract(
            document_version_id=second_document.id,
            source_type="company_disclosure",
            provider_or_tenant="测试公司",
            allow_ai_processing=True,
            allow_display=True,
            allow_export=False,
            allow_api=False,
            region="not_recorded",
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
        second_span,
    ])
    cmd_session.flush()
    second_candidate = AtomicClaimService(cmd_session).admit(
        AtomicClaimDraft(
            source_span_id=second_span.id,
            quote=second_quote,
            quote_start=second_text.index(second_quote),
            quote_end=second_text.index(second_quote) + len(second_quote),
            normalized_text="公司披露 2026 年第二季度订单同比增长 30%",
            claim_type="disclosed_fact",
            assertion_actor="公司",
            subject="订单",
            predicate="同比增长",
            object_text="30%",
            numeric_value="30",
            unit="%",
            observed_period=None,
            scope={"company": "测试公司"},
        ),
        authority_level="primary_disclosure",
        run_ref="extract:fixture-second",
    )
    service = AutoResearchService(cmd_session)
    first_run = service.start(case.id)
    second_run = service.start(case.id)
    for run, candidate in (
        (first_run, first_candidate),
        (second_run, second_candidate),
    ):
        service.repo.update_run(
            run,
            status="waiting_for_review",
            stage="claim_review",
            stop_reason="pending_atomic_claim_review",
        )
        job = service.repo.job_for_run(run.id)
        assert job is not None
        job.status = "waiting_for_review"
        ResearchRunEventRepository(cmd_session).append(
            run.id,
            stage="claim_review",
            status="waiting_for_review",
            message="等待本次运行的原子陈述审核",
            payload_json={"candidate_ids": [str(candidate.id)]},
        )
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/atomic-claims/{first_candidate.id}/reviews",
        json={
            "outcome": "rejected",
            "reviewer": "human:reviewer",
            "reason": "第一条已审核",
            "idempotency_key": "atomic-review-run-local-resume",
        },
    )

    assert response.status_code == 201, response.text
    cmd_session.expire_all()
    assert service.repo.get_run(first_run.id).status == "queued"
    assert service.repo.get_run(second_run.id).status == "waiting_for_review"


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
