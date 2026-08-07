from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.models.events import DomainEvent
from app.models.ledger import (
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.proposals import Proposal
from app.repositories.event_research import EventResearchLifecycleRepository
from app.repositories.operational import TaskRepository
from app.schemas.v1.event_research import EventReviewQueueItemDTO
from app.services.event_review_queue import EventReviewQueueService
from app.services.auto_research import AutoResearchService


def _seed_evidence_proposal(
    session,
    *,
    case: ResearchCase,
    proposed_at: datetime,
    source_url: str,
    parser_version: str = "html-v1",
    parse_state: str = "success",
    status: str = "pending",
) -> Proposal:
    thesis = Thesis(
        research_case_id=case.id,
        statement=f"factor-{source_url}",
        created_by="tester",
        created_at=proposed_at,
    )
    document = DocumentVersion(
        content_sha256=hashlib.sha256(source_url.encode()).hexdigest(),
        source_url=source_url,
        title=f"Source for {source_url}",
        available_at=proposed_at,
        acquired_at=proposed_at,
        parser_version=parser_version,
        parse_state=parse_state,
    )
    session.add_all([thesis, document])
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        verbatim_text="Evidence excerpt",
        locator={"page": 1},
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="Evidence statement",
        created_at=proposed_at,
    )
    session.add(statement)
    session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement.id),
            "role": "supports",
            "reason": "supports the factor",
            "scope": {"period": "event"},
        },
        target_context={"thesis_id": str(thesis.id), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="test",
        proposed_at=proposed_at,
        research_case_id=case.id,
        status=status,
    )
    session.add(proposal)
    session.flush()
    return proposal


def _case(session) -> ResearchCase:
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="event evidence queue",
        industry_topic="event",
        created_by="tester",
        created_at=now,
    )
    session.add(case)
    session.flush()
    session.add(
        EventResearchLifecycle(
            research_case_id=case.id,
            status="awaiting_key_review",
            current_round=2,
            status_summary="waiting for evidence review",
            next_human_action="审核 1 条关键证据",
            updated_at=now,
        )
    )
    return case


def test_event_review_queue_schema_rejects_unknown_source_status() -> None:
    with pytest.raises(ValidationError):
        EventReviewQueueItemDTO(
            proposal_id="proposal-1",
            status="pending",
            proposed_at=datetime.now(timezone.utc),
            link_id="link-1",
            thesis_id=None,
            case_id="case-1",
            thesis_statement=None,
            ai_role="supports",
            ai_reason="reason",
            ai_scope={},
            statement_id=None,
            statement_text=None,
            statement_kind=None,
            span_id=None,
            verbatim_text=None,
            locator={},
            document_version_id=None,
            document_source_url=None,
            document_published_at=None,
            available_at=None,
            source_title=None,
            source_status="retired",
            source_status_reason="unknown state",
            can_accept=False,
            proposal_reason="reason",
            position=None,
        )


def test_event_review_queue_summarizes_pending_invalid_and_reviewed_items(
    cmd_client, cmd_session
) -> None:
    case = _case(cmd_session)
    now = datetime.now(timezone.utc)
    valid = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now,
        source_url="https://news.example.org/valid",
    )
    invalid = _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now + timedelta(seconds=1),
        source_url="https://example.com/invalid",
    )
    _seed_evidence_proposal(
        cmd_session,
        case=case,
        proposed_at=now + timedelta(seconds=2),
        source_url="https://news.example.org/reviewed",
        status="decided",
    )
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{case.id}/review-queue")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == {
        "total": 3,
        "reviewed": 1,
        "pending": 1,
        "invalid_source": 1,
        "current_round": 2,
        "next_action": "审核 1 条关键证据",
    }
    assert [item["proposal_id"] for item in body["items"]] == [
        str(valid.id),
        str(invalid.id),
    ]
    invalid_item = next(
        item for item in body["items"] if item["proposal_id"] == str(invalid.id)
    )
    assert invalid_item["source_title"] == "Source for https://example.com/invalid"
    assert invalid_item["source_status"] == "invalid"
    assert invalid_item["can_accept"] is False
    assert "测试域名" in invalid_item["source_status_reason"]
    assert invalid_item["proposal_reason"] == "supports the factor"
    assert invalid_item["position"] is None


def test_reconcile_invalid_source_closes_only_its_task_and_lifecycle_counts_valid_items(
    session,
) -> None:
    case = _case(session)
    now = datetime.now(timezone.utc)
    valid = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=now,
        source_url="https://news.example.org/valid",
    )
    invalid = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=now + timedelta(seconds=1),
        source_url="https://example.com/invalid",
    )
    tasks = TaskRepository(session)
    valid_task = tasks.add_task(
        title="review valid",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=valid.id,
        research_case_id=case.id,
    )
    invalid_task = tasks.add_task(
        title="review invalid",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=invalid.id,
        research_case_id=case.id,
    )
    session.commit()

    reconciled = EventReviewQueueService(session).reconcile_event_review_queue(case.id)
    session.commit()

    session.refresh(valid_task)
    session.refresh(invalid_task)
    assert reconciled.invalid_source_proposal_ids == [invalid.id]
    assert valid_task.status == "open"
    assert invalid_task.status == "done"
    assert session.get(Proposal, invalid.id).status == "pending"
    assert EventResearchLifecycleRepository(session).pending_key_review_count(case.id) == 1
    audit = session.scalar(
        select(DomainEvent).where(DomainEvent.aggregate_id == str(invalid.id))
    )
    assert audit is not None
    assert audit.payload["admission_reason"] == (
        "来源为测试域名，不能作为有效证据来源。"
    )


def test_lifecycle_refresh_reconciles_invalid_source_review_tasks(session) -> None:
    case = _case(session)
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=2,
        max_rounds=3,
        budget=10,
        budget_used=1,
        stop_reason="budget_exhausted",
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    lifecycle = session.get(EventResearchLifecycle, case.id)
    lifecycle.active_run_id = run.id
    invalid = _seed_evidence_proposal(
        session,
        case=case,
        proposed_at=now,
        source_url=f"https://example.com/invalid-{case.id}",
    )
    task = TaskRepository(session).add_task(
        title="review invalid",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=invalid.id,
        research_case_id=case.id,
    )
    session.commit()

    AutoResearchService(session).refresh_event_lifecycle(run)
    session.commit()

    session.refresh(task)
    assert task.status == "done"
    audit = session.scalar(
        select(DomainEvent).where(DomainEvent.aggregate_id == str(invalid.id))
    )
    assert audit is not None
