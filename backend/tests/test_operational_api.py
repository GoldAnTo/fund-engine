"""Tests for jobs + activity + tasks operational endpoints (design §8.7)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.errors import ConflictError
from app.models.events import DomainEvent
from app.models.event_impact import EventImpactHypothesis
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import ResearchCase
from app.models.operational import Job, JobEvent, ResearchTask
from app.repositories.auto_research import AutoResearchRepository
from app.repositories.operational import TaskRepository
from app.services.auto_research import AutoResearchService
from app.services.event_impact import ResolvedImpactCompany
from app.services.jobs import JobService


def _make_job(session, *, kind="propose") -> Job:
    return JobService(session).create(kind=kind, research_case_id=uuid.uuid4())


def test_job_lifecycle_states(cmd_session):
    job = _make_job(cmd_session)
    js = JobService(cmd_session)
    js.start(job, step="recalling")
    js.progress(job, step="proposing", progress=40)
    js.finish(job, status="succeeded", step="done")
    cmd_session.commit()

    assert job.status == "succeeded"
    assert job.progress == 40
    # JobEvents were appended.
    assert cmd_session.scalars(
        select(JobEvent).where(JobEvent.job_id == job.id)
    ).all()
    # A domain event was emitted for creation + progress.
    evs = cmd_session.scalars(select(DomainEvent)).all()
    assert any(e.type == "job_created" for e in evs)


def test_cancel_rejected_on_terminal_job(cmd_session):
    job = _make_job(cmd_session)
    js = JobService(cmd_session)
    js.start(job)
    js.finish(job, status="failed", error="boom")
    cmd_session.commit()
    assert job.status == "failed"

    with pytest.raises(ConflictError):
        JobService(cmd_session).request_cancel(job)


def test_job_retry_endpoint_resets_to_queued(cmd_client, cmd_session):
    job = _make_job(cmd_session)
    js = JobService(cmd_session)
    js.start(job)
    js.finish(job, status="failed", error="boom")
    cmd_session.commit()
    attempt_before = job.attempt

    resp = cmd_client.post(f"/api/v1/jobs/{job.id}/retries")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["attempt"] == attempt_before + 1
    assert body["error"] is None


def test_job_retry_recovers_a_failed_current_scope_impact_refresh(
    cmd_client, cmd_session
) -> None:
    """A failed impact task is requeued only through the durable job retry path."""
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="Impact retry",
        industry_topic="events",
        created_by="tester",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    cmd_session.add(
        EventResearchBrief(
            research_case_id=case.id,
            raw_input="retry fixture",
            source_url=None,
            event_title="retry fixture",
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            research_question="does retry execute?",
            extraction_state="human_confirmed",
            created_at=now,
        )
    )
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by="tester",
        change_summary="retry fixture",
        created_at=now,
    )
    cmd_session.add(scope)
    cmd_session.flush()
    cmd_session.add_all(
        [
            EventResearchScopeFactor(
                scope_version_id=scope.id,
                statement="supplier retry factor one",
                description=None,
                position=1,
            ),
            EventResearchScopeFactor(
                scope_version_id=scope.id,
                statement="supplier retry factor two",
                description=None,
                position=2,
            ),
        ]
    )
    cmd_session.commit()

    @dataclass
    class FlakyResolver:
        calls: int = 0

        def resolve(self, *, factor_statement, statements):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("resolver temporarily unavailable")
            return [
                ResolvedImpactCompany(
                    company_name="Retry Supplier",
                    type="unlisted_supplier",
                    relation_kind="supplier",
                    direction="benefits",
                    mechanism="retry evidence pending",
                    source_statement_id=None,
                )
            ]

    resolver = FlakyResolver()
    worker = AutoResearchService(cmd_session, impact_resolver=resolver)
    run = worker.start(
        case.id,
        max_rounds=1,
        budget=1,
        thesis_ids=[],
        scope_version_id=scope.id,
    )
    worker.execute(run)
    job = AutoResearchRepository(cmd_session).job_for_run(run.id)
    assert job is not None
    AutoResearchRepository(cmd_session).record_job_completion(
        job, status="failed", step="failed", error="resolver temporarily unavailable"
    )
    cmd_session.commit()

    impact_task = cmd_session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "impact_refresh",
        )
    )
    assert impact_task is not None and impact_task.status == "failed"
    assert run.status == "failed"
    assert run.budget_used == 1
    assert list(
        cmd_session.scalars(
            select(EventImpactHypothesis).where(
                EventImpactHypothesis.scope_version_id == scope.id
            )
        )
    ) == []

    response = cmd_client.post(f"/api/v1/jobs/{job.id}/retries")
    assert response.status_code == 200, response.text
    cmd_session.refresh(run)
    cmd_session.refresh(impact_task)
    assert run.status == "queued"
    assert impact_task.status == "queued"
    assert run.budget_used == 0

    worker.execute(run)
    cmd_session.commit()
    assert resolver.calls == 4
    assert impact_task.status == "done"
    outputs = list(
        cmd_session.scalars(
            select(EventImpactHypothesis).where(
                EventImpactHypothesis.scope_version_id == scope.id
            )
        )
    )
    assert [row.statement for row in outputs if row.classification == "candidate"] == [
        "supplier retry factor one",
        "supplier retry factor two",
    ]
    assert len([row for row in outputs if row.classification == "unresolved"]) == 2


def test_jobs_api_get_and_events(cmd_client, cmd_session):
    job = _make_job(cmd_session)
    JobService(cmd_session).start(job, step="x")
    JobService(cmd_session).finish(job, status="succeeded")
    cmd_session.commit()

    resp = cmd_client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"

    events = cmd_client.get(f"/api/v1/jobs/{job.id}/events")
    assert events.status_code == 200
    assert events.json()["events"]


def test_jobs_api_cancel_endpoint(cmd_client, cmd_session):
    job = _make_job(cmd_session)
    JobService(cmd_session).start(job)
    cmd_session.commit()
    resp = cmd_client.post(f"/api/v1/jobs/{job.id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancel_requested"] is True


def test_activity_feed_from_outbox(cmd_session):
    from app.repositories.outbox import emit_event

    emit_event(
        cmd_session,
        type="evidence_link_proposed",
        aggregate_type="evidence_link",
        aggregate_id=str(uuid.uuid4()),
        payload={"thesis_id": str(uuid.uuid4())},
        origin="ledger",
    )
    cmd_session.commit()

    from app.queries.activity import ActivityQueries

    rows, has_more = ActivityQueries(cmd_session).activity(limit=10)
    assert rows
    assert any(r.type == "evidence_link_proposed" for r in rows)


def test_evidence_changes_feed(cmd_session):
    from app.queries.activity import ActivityQueries
    from app.repositories.outbox import emit_event

    emit_event(
        cmd_session,
        type="ai_assessment_frozen",
        aggregate_type="ai_assessment",
        aggregate_id=str(uuid.uuid4()),
        payload={},
        origin="ledger",
    )
    cmd_session.commit()
    rows, _ = ActivityQueries(cmd_session).evidence_changes(limit=10)
    assert any(r.type == "ai_assessment_frozen" for r in rows)


def test_tasks_api(cmd_client, cmd_session):
    TaskRepository(cmd_session).add_task(
        title="Review proposal",
        task_type="review_proposal",
        research_case_id=uuid.uuid4(),
    )
    cmd_session.commit()
    resp = cmd_client.get("/api/v1/tasks")
    assert resp.status_code == 200
    assert resp.json()["items"]


def test_close_review_task_is_idempotent(cmd_session):
    repo = TaskRepository(cmd_session)
    ref_id = uuid.uuid4()
    task = repo.add_task(
        title="Review proposal",
        task_type="review_proposal",
        status="open",
        ref_type="proposal",
        ref_id=ref_id,
    )
    cmd_session.commit()

    closed = repo.close_review_task("review_proposal", "proposal", ref_id)
    assert closed is not None
    assert closed.status == "done"
    assert closed.id == task.id

    again = repo.close_review_task("review_proposal", "proposal", ref_id)
    assert again is not None and again.status == "done"
    assert repo.close_review_task("review_proposal", "proposal", uuid.uuid4()) is None
