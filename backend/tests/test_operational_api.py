"""Tests for jobs + activity + tasks operational endpoints (design §8.7)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.errors import ConflictError
from app.models.events import DomainEvent
from app.models.operational import Job, JobEvent
from app.repositories.operational import TaskRepository
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
