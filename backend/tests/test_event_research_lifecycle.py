from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.event_research import EventResearchBrief, EventResearchFactorDraft
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentVersion,
    ImmutableLedgerError,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.models.operational import TaskItem
from app.models.proposals import Proposal
from app.repositories.operational import TaskRepository
from app.services.auto_research import AutoResearchService


def _case(session, title: str) -> ResearchCase:
    case = ResearchCase(
        title=title,
        industry_topic="事件研究",
        created_by="tester",
        created_at=datetime.now(timezone.utc),
    )
    session.add(case)
    session.flush()
    return case


def _evidence_proposal(
    session,
    *,
    case: ResearchCase,
    source_url: str,
) -> Proposal:
    now = datetime.now(timezone.utc)
    thesis = Thesis(
        research_case_id=case.id,
        statement=f"evidence for {source_url}",
        created_by="tester",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=hashlib.sha256(source_url.encode()).hexdigest(),
        source_url=source_url,
        title="Frozen source",
        available_at=now,
        acquired_at=now,
        parser_version="html-v1",
        parse_state="success",
    )
    session.add_all([thesis, document])
    session.flush()
    session.add(
        CaseDocumentVersion(
            research_case_id=case.id,
            document_version_id=document.id,
            linked_at=now,
        )
    )
    span = SourceSpan(
        document_version_id=document.id,
        verbatim_text="Frozen evidence excerpt",
        locator={"page": 1},
    )
    session.add(span)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="fact",
        normalized_text="Frozen evidence statement",
        created_at=now,
    )
    session.add(statement)
    session.flush()
    proposal = Proposal(
        kind="evidence_link",
        payload={
            "source_statement_id": str(statement.id),
            "role": "supports",
            "reason": "test evidence",
            "scope": {"period": "event"},
        },
        target_context={"thesis_id": str(thesis.id), "entity_type": "evidence_link"},
        proposed_by_type="ai",
        proposed_by_ref="test",
        proposed_at=now,
        research_case_id=case.id,
        status="pending",
    )
    session.add(proposal)
    session.flush()
    return proposal


def test_event_brief_is_case_scoped_and_append_only(session) -> None:
    first, second = _case(session, "Alphabet 财报后下跌"), _case(session, "另一独立事件")
    brief = EventResearchBrief(
        research_case_id=first.id,
        raw_input="Alphabet 上调资本开支后盘后下跌",
        source_url="https://example.com/news",
        event_title="Alphabet 财报后下跌",
        company_name="Alphabet",
        ticker="GOOGL",
        event_at=datetime(2026, 8, 7, tzinfo=timezone.utc),
        market_reaction="盘后下跌 4%",
        research_question="资本开支是否是盘后下跌的主要因素？",
        extraction_state="human_confirmed",
        created_at=datetime.now(timezone.utc),
    )
    session.add(brief)
    session.commit()

    assert list(
        session.scalars(
            select(EventResearchBrief).where(EventResearchBrief.research_case_id == first.id)
        )
    ) == [brief]
    assert list(
        session.scalars(
            select(EventResearchBrief).where(EventResearchBrief.research_case_id == second.id)
        )
    ) == []
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(EventResearchBrief).values(event_title="changed"))


def test_factor_drafts_are_case_scoped_and_append_only(session) -> None:
    first, second = _case(session, "第一件事"), _case(session, "第二件事")
    factor = EventResearchFactorDraft(
        research_case_id=first.id,
        statement="资本开支上调可能加剧市场对自由现金流的担忧",
        position=1,
        created_by="ai",
        created_at=datetime.now(timezone.utc),
    )
    session.add(factor)
    session.commit()

    assert list(
        session.scalars(
            select(EventResearchFactorDraft).where(
                EventResearchFactorDraft.research_case_id == first.id
            )
        )
    ) == [factor]
    assert list(
        session.scalars(
            select(EventResearchFactorDraft).where(
                EventResearchFactorDraft.research_case_id == second.id
            )
        )
    ) == []
    with pytest.raises(ImmutableLedgerError):
        session.execute(update(EventResearchFactorDraft).values(position=2))


def test_lifecycle_is_one_mutable_row_per_case_and_targets_its_run(session) -> None:
    first, second = _case(session, "第一件事"), _case(session, "第二件事")
    run = ResearchRun(
        research_case_id=first.id,
        status="queued",
        stage="planning",
        round=1,
        max_rounds=3,
        budget=100,
        budget_used=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    lifecycle = EventResearchLifecycle(
        research_case_id=first.id,
        status="researching",
        active_run_id=run.id,
        current_round=1,
        status_summary="正在建立第一轮证据检索",
        current_gap=None,
        next_human_action=None,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(lifecycle)
    session.commit()

    assert session.get(EventResearchLifecycle, first.id) is lifecycle
    assert session.get(EventResearchLifecycle, second.id) is None
    assert lifecycle.active_run_id == run.id

    lifecycle.status = "awaiting_key_review"
    lifecycle.next_human_action = "审核 2 条关键证据"
    session.commit()
    assert session.get(EventResearchLifecycle, first.id).status == "awaiting_key_review"


@pytest.mark.parametrize(
    "status",
    [
        "extracting",
        "researching",
        "awaiting_key_review",
        "continuing",
        "awaiting_scope",
        "draft_ready",
        "published",
        "exhausted",
    ],
)
def test_lifecycle_accepts_only_known_states(session, status: str) -> None:
    case = _case(session, status)
    lifecycle = EventResearchLifecycle(
        research_case_id=case.id,
        status=status,
        current_round=0,
        status_summary="状态测试",
        updated_at=datetime.now(timezone.utc),
    )
    session.add(lifecycle)
    session.commit()
    assert session.get(EventResearchLifecycle, case.id).status == status


def test_lifecycle_rejects_unknown_state(session) -> None:
    case = _case(session, "未知状态")
    session.add(
        EventResearchLifecycle(
            research_case_id=case.id,
            status="unknown",
            current_round=0,
            status_summary="不应保存",
            updated_at=datetime.now(timezone.utc),
        )
    )
    with pytest.raises(IntegrityError, match="ck_event_research_lifecycle_status"):
        session.flush()


def test_no_key_evidence_starts_the_next_bounded_research_cycle(session) -> None:
    case = _case(session, "尚未找到关键材料的事件")
    first = AutoResearchService(session).start(case.id, max_rounds=1, budget=10)
    lifecycle = EventResearchLifecycle(
        research_case_id=case.id,
        status="researching",
        active_run_id=first.id,
        current_round=1,
        status_summary="正在建立第一轮证据检索",
        updated_at=datetime.now(timezone.utc),
    )
    session.add(lifecycle)
    session.commit()

    AutoResearchService(session).execute(first)
    session.commit()
    session.refresh(lifecycle)

    assert lifecycle.status == "continuing"
    assert lifecycle.current_round == 2
    assert lifecycle.active_run_id != first.id
    assert session.get(ResearchRun, lifecycle.active_run_id).research_case_id == case.id


def test_pending_key_evidence_pauses_automatic_expansion_for_human_review(session) -> None:
    case = _case(session, "存在待审核证据的事件")
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        stop_reason="max_rounds_reached",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    lifecycle = EventResearchLifecycle(
        research_case_id=case.id,
        status="researching",
        active_run_id=run.id,
        current_round=1,
        status_summary="正在研究",
        updated_at=datetime.now(timezone.utc),
    )
    session.add_all(
        [
            lifecycle,
            TaskItem(
                title="审核自动研究提出的证据",
                task_type="review_proposal",
                ref_type="proposal",
                status="open",
                research_case_id=case.id,
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    session.commit()

    AutoResearchService(session).refresh_event_lifecycle(run)
    session.commit()
    session.refresh(lifecycle)

    assert lifecycle.status == "awaiting_key_review"
    assert lifecycle.active_run_id == run.id
    assert lifecycle.next_human_action == "审核 1 条关键证据"


def test_worker_refresh_does_not_overwrite_published_lifecycle(session) -> None:
    case = _case(session, "已发布结论不被后台覆盖")
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        stop_reason="max_rounds_reached",
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    lifecycle = EventResearchLifecycle(
        research_case_id=case.id,
        status="published",
        active_run_id=run.id,
        current_round=1,
        status_summary="研究结论已人工确认并发布",
        current_gap=None,
        next_human_action=None,
        updated_at=now,
    )
    session.add(lifecycle)
    session.commit()

    AutoResearchService(session).refresh_event_lifecycle(run)
    session.commit()
    session.refresh(lifecycle)

    assert lifecycle.status == "published"
    assert lifecycle.active_run_id == run.id
    assert lifecycle.next_human_action is None


def test_completed_key_review_returns_control_to_automatic_research(session) -> None:
    case = _case(session, "审核后继续研究的事件")
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=1,
        budget=10,
        budget_used=1,
        stop_reason="max_rounds_reached",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.flush()
    lifecycle = EventResearchLifecycle(
        research_case_id=case.id,
        status="awaiting_key_review",
        active_run_id=run.id,
        current_round=1,
        status_summary="已筛出 1 条关键证据，等待审核",
        next_human_action="审核 1 条关键证据",
        updated_at=datetime.now(timezone.utc),
    )
    session.add(lifecycle)
    session.commit()

    AutoResearchService(session).continue_after_key_review(case.id)
    session.commit()
    session.refresh(lifecycle)

    assert lifecycle.status == "continuing"
    assert lifecycle.current_round == 2
    assert lifecycle.active_run_id != run.id


def test_invalid_fixture_review_task_does_not_block_eligible_event_evidence(session) -> None:
    """A fixture source stays auditable but cannot prevent a real source handoff."""
    case = _case(session, "真实来源与 fixture 来源并存的事件")
    now = datetime.now(timezone.utc)
    run = ResearchRun(
        research_case_id=case.id,
        status="waiting_for_review",
        stage="stopped",
        round=1,
        max_rounds=3,
        budget=10,
        budget_used=1,
        stop_reason="max_rounds_reached",
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    lifecycle = EventResearchLifecycle(
        research_case_id=case.id,
        status="researching",
        active_run_id=run.id,
        current_round=1,
        status_summary="正在研究",
        updated_at=now,
    )
    valid = _evidence_proposal(
        session,
        case=case,
        source_url="https://investor.tsmc.com/english/quarterly-results",
    )
    invalid = _evidence_proposal(
        session, case=case, source_url="https://example.com/fixture-news"
    )
    tasks = TaskRepository(session)
    valid_task = tasks.add_task(
        title="review real source",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=valid.id,
        research_case_id=case.id,
    )
    invalid_task = tasks.add_task(
        title="review fixture source",
        task_type="review_proposal",
        ref_type="proposal",
        ref_id=invalid.id,
        research_case_id=case.id,
    )
    session.add(lifecycle)
    session.commit()

    AutoResearchService(session).refresh_event_lifecycle(run)
    session.commit()

    session.refresh(lifecycle)
    session.refresh(valid_task)
    session.refresh(invalid_task)
    assert lifecycle.status == "awaiting_key_review"
    assert lifecycle.next_human_action == "审核 1 条关键证据"
    assert valid_task.status == "open"
    assert invalid_task.status == "done"
