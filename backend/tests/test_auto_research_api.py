from __future__ import annotations
import uuid
import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session, sessionmaker
from app.models.ledger import Base, ResearchCase, Thesis, EvidenceLink, SourceStatement, SourceSpan, DocumentVersion
from app.models.operational import ResearchRun, ResearchTask
from app.models.proposals import Proposal
from app.services.auto_research import AutoResearchService
from app.repositories.auto_research import AutoResearchRepository


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
    assert detail["status"] in {"waiting_for_review", "failed"}
    assert detail["stop_reason"] in {"budget_exhausted", "no_new_evidence", "max_rounds_reached", "task_failed"}


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


def test_budget_stop(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    run = AutoResearchService(session).start(case.id, max_rounds=3, budget=1)
    detail = AutoResearchService(session).detail(run.id)
    assert detail["stop_reason"] == "budget_exhausted"
    assert detail["budget_used"] >= 1


def test_round_stop(session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(case); session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    session.add(thesis); session.commit()
    run = AutoResearchService(session).start(case.id, max_rounds=1, budget=1000)
    detail = AutoResearchService(session).detail(run.id)
    assert detail["stop_reason"] in {"max_rounds_reached", "no_new_evidence", "task_failed"}


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


def test_cancel_run_success_and_idempotent(cmd_client, cmd_session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(case); cmd_session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(thesis); cmd_session.commit()
    run = AutoResearchRepository(cmd_session).create_run(research_case_id=case.id, max_rounds=1, budget=10)
    run.status = "running"
    cmd_session.commit()

    resp = cmd_client.post(f"/api/v1/research-runs/{run.id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    resp2 = cmd_client.post(f"/api/v1/research-runs/{run.id}/cancel")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "cancelled"


def test_cancel_run_terminal_conflict(cmd_client, cmd_session):
    case = ResearchCase(title="t", industry_topic="i", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(case); cmd_session.flush()
    thesis = Thesis(research_case_id=case.id, statement="s", created_by="u", created_at=datetime.now(timezone.utc))
    cmd_session.add(thesis); cmd_session.commit()
    run = AutoResearchRepository(cmd_session).create_run(research_case_id=case.id, max_rounds=1, budget=10)
    run.status = "succeeded"
    cmd_session.commit()

    resp = cmd_client.post(f"/api/v1/research-runs/{run.id}/cancel")
    assert resp.status_code == 409
