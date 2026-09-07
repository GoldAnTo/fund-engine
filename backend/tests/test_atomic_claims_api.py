from __future__ import annotations

import uuid
import pytest
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
from app.models.operational import ResearchRun, ResearchTask, TaskItem
from app.models.research_monitor import ResearchRunEvent
from app.models.source_governance import SourceContract
from app.services.atomic_claims import AtomicClaimService
from app.services.auto_research import AutoResearchService
from app.services.case_monitor import (
    CaseMonitorConfig,
    CaseMonitorService,
    ResearchRunEventRepository,
)
from app.repositories.research_preparation import ResearchPreparationRepository
from app.services.research_preparation import ResearchPreparationService


@pytest.fixture
def cmd_session(session):
    """Use the selected database, including real PG when TEST_DATABASE_URL is set."""
    import os
    expected = "postgresql" if os.environ.get("TEST_DATABASE_URL") else "sqlite"
    assert session.get_bind().dialect.name == expected
    return session


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


def test_atomic_claim_review_rejects_shared_cross_tenant_candidate_before_locking(
    cmd_client, cmd_session, monkeypatch
) -> None:
    """A cross-tenant shared source cannot enter the global review/lock path."""
    _case, candidate = _candidate_for_case(cmd_session)
    span = cmd_session.get(SourceSpan, candidate.source_span_id)
    assert span is not None
    now = datetime.now(timezone.utc)
    other_case = ResearchCase(
        title="其他租户共享来源",
        industry_topic="ai",
        created_by="human:other",
        created_at=now,
    )
    cmd_session.add(other_case)
    cmd_session.flush()
    cmd_session.add_all([
        CaseDocumentVersion(
            research_case_id=other_case.id,
            document_version_id=span.document_version_id,
            linked_at=now,
        ),
        CaseTenantAdmission(
            research_case_id=other_case.id,
            tenant_id="other-team",
            initial_document_version_id=span.document_version_id,
            admitted_by="test-fixture",
            admitted_at=now,
        ),
    ])
    other_preparation = ResearchPreparationService(cmd_session).create_for_case(
        other_case.id, input_fingerprint="o" * 64, actor="tester"
    )
    ResearchPreparationService(cmd_session).complete_system_step(
        other_case.id,
        "parse_claims",
        {"candidates": [{"candidate_id": str(candidate.id)}]},
        expected_version=other_preparation.version,
        expected_fingerprint=other_preparation.input_fingerprint,
    )
    cmd_session.commit()

    lock_calls: list[uuid.UUID] = []

    def record_lock(self, candidate_id):
        lock_calls.append(candidate_id)
        return []

    monkeypatch.setattr(
        ResearchPreparationRepository,
        "lock_preparation_for_candidate_review",
        record_lock,
    )
    response = cmd_client.post(
        f"/api/v1/atomic-claims/{candidate.id}/reviews",
        json={
            "outcome": "rejected",
            "reviewer": "human:reviewer",
            "reason": "shared cross-tenant candidate",
            "idempotency_key": "cross-tenant-review",
        },
    )

    assert response.status_code == 404
    assert lock_calls == []


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
    job = service.repo.job_for_run(run.id)
    assert job is not None
    job.status = "waiting_for_review"
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


@pytest.mark.parametrize("display,start_days,end_days,visible", [(False,None,None,False),(True,None,-1,False),(True,1,None,False),(True,None,None,True)])
def test_atomic_claim_queue_hides_sources_without_current_display_permission(cmd_client, cmd_session, display, start_days, end_days, visible):
    from datetime import timedelta
    case, candidate = _candidate_for_case(cmd_session)
    span = cmd_session.get(SourceSpan, candidate.source_span_id)
    now = datetime.now(timezone.utc)
    contract = SourceContract(
        document_version_id=span.document_version_id, source_type="company_disclosure",
        provider_or_tenant="test", allow_ai_processing=True, allow_display=display,
        effective_from=now + timedelta(days=start_days) if start_days else None,
        effective_until=now + timedelta(days=end_days) if end_days else None,
        allow_export=False, allow_api=False, region="CN", retention_policy="case_retained",
        deletion_policy="not_recorded", downstream_restrictions=[], intake_metadata={},
        declared_by="test", created_at=now,
    )
    cmd_session.add(contract)
    cmd_session.commit()
    response = cmd_client.get(f"/api/v1/research-cases/{case.id}/atomic-claims")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ([str(candidate.id)] if visible else [])
    if not visible:
        review = cmd_client.post(f"/api/v1/atomic-claims/{candidate.id}/reviews", json={
            "outcome": "confirmed", "reviewer": "human:test", "reason": "review stale source",
            "idempotency_key": "hidden-source-review",
        })
        assert review.status_code == 404
        from app.models.ledger import AtomicClaimReview, SourceStatement
        assert cmd_session.scalar(select(AtomicClaimReview).where(AtomicClaimReview.atomic_claim_candidate_id == candidate.id)) is None
        assert cmd_session.scalar(select(SourceStatement).where(SourceStatement.atomic_claim_candidate_id == candidate.id)) is None


def test_atomic_claim_display_filter_precedes_limit(cmd_client, cmd_session):
    case, visible = _candidate_for_case(cmd_session)
    _, hidden = _candidate_for_case(cmd_session)
    span = cmd_session.get(SourceSpan, hidden.source_span_id)
    now = datetime.now(timezone.utc)
    cmd_session.add(CaseDocumentVersion(research_case_id=case.id, document_version_id=span.document_version_id, linked_at=now))
    cmd_session.add(SourceContract(
        document_version_id=span.document_version_id, source_type="company_disclosure",
        provider_or_tenant="test", allow_ai_processing=True, allow_display=False,
        allow_export=False, allow_api=False, region="CN", retention_policy="case_retained",
        deletion_policy="not_recorded", downstream_restrictions=[], intake_metadata={},
        declared_by="test", created_at=now,
    ))
    cmd_session.commit()
    response = cmd_client.get(f"/api/v1/research-cases/{case.id}/atomic-claims?limit=1")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(visible.id)]


def test_atomic_claim_state_filter_precedes_limit(cmd_client, cmd_session):
    case, pending = _candidate_for_case(cmd_session)
    _, reviewed = _candidate_for_case(cmd_session)
    span = cmd_session.get(SourceSpan, reviewed.source_span_id)
    cmd_session.add(CaseDocumentVersion(research_case_id=case.id, document_version_id=span.document_version_id, linked_at=datetime.now(timezone.utc)))
    AtomicClaimService(cmd_session).review(reviewed.id, outcome="rejected", reviewer="human:test", reason="not relevant", idempotency_key="filter-test")
    cmd_session.commit()
    url = f"/api/v1/research-cases/{case.id}/atomic-claims"
    response = cmd_client.get(url + "?limit=1&review_state=awaiting_review")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(pending.id)]
    rejected = cmd_client.get(url + "?limit=1&review_state=rejected")
    assert [item["id"] for item in rejected.json()["items"]] == [str(reviewed.id)]


def test_atomic_claim_filter_uses_latest_review(cmd_client, cmd_session):
    case, candidate = _candidate_for_case(cmd_session)
    service = AtomicClaimService(cmd_session)
    service.review(candidate.id, outcome="rejected", reviewer="human:test", reason="first decision", idempotency_key="first")
    service.review(candidate.id, outcome="confirmed", reviewer="human:test", reason="checked again", idempotency_key="second")
    cmd_session.commit()
    url = f"/api/v1/research-cases/{case.id}/atomic-claims"
    for state in ["awaiting_review", "rejected", "modified"]:
        assert cmd_client.get(url + f"?review_state={state}").json()["items"] == []
    result = cmd_client.get(url + "?review_state=confirmed").json()["items"]
    assert [item["id"] for item in result] == [str(candidate.id)]
    assert result[0]["review_state"] == "confirmed"
    assert [review["outcome"] for review in result[0]["review_history"]] == ["rejected", "confirmed"]


def test_atomic_claim_queue_keyset_pagination_is_complete_and_case_bound(cmd_client, cmd_session):
    case, first = _candidate_for_case(cmd_session)
    expected = {str(first.id)}
    for _ in range(2):
        _, candidate = _candidate_for_case(cmd_session)
        span = cmd_session.get(SourceSpan, candidate.source_span_id)
        cmd_session.add(CaseDocumentVersion(research_case_id=case.id, document_version_id=span.document_version_id, linked_at=datetime.now(timezone.utc)))
        expected.add(str(candidate.id))
    cmd_session.commit()
    url = f"/api/v1/research-cases/{case.id}/atomic-claims"
    seen = []
    cursor = None
    for index in range(3):
        response = cmd_client.get(url, params={"limit": 1, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200
        body = response.json()
        seen.extend(item["id"] for item in body["items"])
        assert body["has_more"] == (index < 2)
        cursor = body["next_cursor"]
        assert bool(cursor) == (index < 2)
    assert len(seen) == len(set(seen)) == 3
    assert set(seen) == expected
    _, foreign = _candidate_for_case(cmd_session)
    assert cmd_client.get(url, params={"cursor": str(foreign.id)}).status_code == 404
    assert cmd_client.get(url, params={"cursor": "invalid"}).status_code == 422


def test_atomic_claim_cursor_survives_review_and_newer_insert(cmd_client, cmd_session):
    case, older = _candidate_for_case(cmd_session)
    def attach_new():
        _, candidate = _candidate_for_case(cmd_session)
        span = cmd_session.get(SourceSpan, candidate.source_span_id)
        cmd_session.add(CaseDocumentVersion(research_case_id=case.id, document_version_id=span.document_version_id, linked_at=datetime.now(timezone.utc)))
        cmd_session.commit()
        return candidate
    newer = attach_new()
    url = f"/api/v1/research-cases/{case.id}/atomic-claims"
    body = cmd_client.get(url, params={"limit": 1, "review_state": "awaiting_review"}).json()
    assert body["next_cursor"] == str(newer.id)
    AtomicClaimService(cmd_session).review(newer.id, outcome="rejected", reviewer="human:test", reason="reviewed while paging", idempotency_key="page-review")
    cmd_session.commit()
    attach_new()
    response = cmd_client.get(url, params={"limit": 1, "review_state": "awaiting_review", "cursor": body["next_cursor"]})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [str(older.id)]
    assert response.json()["has_more"] is False


@pytest.mark.parametrize('change', [
    {'outcome': 'rejected', 'normalized_text': None},
    {'reviewer': 'human:other'}, {'reason': 'different reason'},
    {'normalized_text': '不同的正式陈述'}, {'observed_period': '2026-04-01'},
])
def test_atomic_review_rejects_changed_idempotent_request(cmd_client, cmd_session, change):
    case, candidate = _candidate_for_case(cmd_session)
    body = {'outcome': 'modified', 'normalized_text': '人工修订的陈述', 'reviewer': 'human:test',
            'reason': '已核对原文', 'idempotency_key': 'stable-review', 'observed_period': None}
    url = f'/api/v1/atomic-claims/{candidate.id}/reviews'
    first = cmd_client.post(url, json=body)
    assert first.status_code == 201
    assert cmd_client.post(url, json={**body, **change}).status_code == 409
    replay = cmd_client.post(url, json=body)
    assert replay.status_code == 201
    assert replay.json() == first.json()
    queue = cmd_client.get(f'/api/v1/research-cases/{case.id}/atomic-claims').json()['items'][0]
    assert len(queue['review_history']) == 1


def test_atomic_review_normalizes_key_before_replay_lookup(cmd_client, cmd_session):
    _, candidate = _candidate_for_case(cmd_session)
    body = {'outcome': 'confirmed', 'reviewer': 'human:test', 'reason': '已核对原文', 'idempotency_key': ' padded-key '}
    url = f'/api/v1/atomic-claims/{candidate.id}/reviews'
    first = cmd_client.post(url, json=body)
    assert first.status_code == 201
    replay = cmd_client.post(url, json=body)
    assert replay.status_code == 201
    assert replay.json() == first.json()
