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


def _make_job(session, *, kind="propose", case_id=None) -> Job:
    return JobService(session).create(
        kind=kind,
        research_case_id=case_id or uuid.uuid4(),
        target_type="research_run" if kind == "research_run" else None,
        target_id=uuid.uuid4() if kind == "research_run" else None,
    )


def _create_admitted_case(client, *, token="test-tenant-token") -> uuid.UUID:
    response = client.post(
        "/api/v1/event-research",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "raw_input": "A durable source for an operational Job permission test.",
            "source_type": "pasted_snapshot",
            "source_metadata": {"original_url": "https://example.test/job-auth"},
            "event_title": "Job API permission test",
            "company_name": "Example Corp",
            "ticker": "EXAMPLE",
            "research_question": "Can this tenant operate the case Job?",
            "candidate_factors": ["Demand", "Supply", "Execution"],
        },
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["case_id"])


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
    case_id = _create_admitted_case(cmd_client)
    job = _make_job(cmd_session, case_id=case_id)
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
    case_id = _create_admitted_case(cmd_client)
    job = _make_job(cmd_session, case_id=case_id)
    JobService(cmd_session).start(job, step="x")
    JobService(cmd_session).finish(job, status="succeeded")
    cmd_session.commit()

    resp = cmd_client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "succeeded"

    events = cmd_client.get(f"/api/v1/jobs/{job.id}/events")
    assert events.status_code == 200
    assert events.json()["events"]


@pytest.mark.parametrize("suffix", ["", "/events"])
def test_job_reads_require_authentication(
    cmd_client, cmd_session, suffix
) -> None:
    case_id = _create_admitted_case(cmd_client)
    job = _make_job(cmd_session, case_id=case_id)
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/jobs/{job.id}{suffix}",
        headers={"Authorization": ""},
    )

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize("suffix", ["", "/events"])
def test_job_reads_conceal_cross_tenant_jobs(
    cmd_client, cmd_session, monkeypatch, suffix
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"owner-token":"team-a","foreign-token":"team-b"}',
    )
    case_id = _create_admitted_case(cmd_client, token="owner-token")
    job = _make_job(cmd_session, case_id=case_id)
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/jobs/{job.id}{suffix}",
        headers={"Authorization": "Bearer foreign-token"},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("suffix", ["", "/events"])
def test_job_reads_do_not_expose_jobs_without_case_ownership(
    cmd_client, cmd_session, suffix
) -> None:
    job = JobService(cmd_session).create(kind="unscoped-system-job")
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/jobs/{job.id}{suffix}")

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


def test_jobs_api_cancel_endpoint(cmd_client, cmd_session):
    case_id = _create_admitted_case(cmd_client)
    job = _make_job(cmd_session, case_id=case_id)
    JobService(cmd_session).start(job)
    cmd_session.commit()
    resp = cmd_client.post(f"/api/v1/jobs/{job.id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancel_requested"] is True


def test_jobs_api_cancel_rejects_terminal_job_as_conflict(cmd_client, cmd_session):
    case_id = _create_admitted_case(cmd_client)
    job = _make_job(cmd_session, case_id=case_id)
    service = JobService(cmd_session)
    service.start(job)
    service.finish(job, status="succeeded")
    cmd_session.commit()

    resp = cmd_client.post(f"/api/v1/jobs/{job.id}/cancel")

    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "conflict"


@pytest.mark.parametrize("operation", ["cancel", "retries"])
def test_job_mutations_require_authentication(
    cmd_client, cmd_session, operation
) -> None:
    case_id = _create_admitted_case(cmd_client)
    job = _make_job(cmd_session, case_id=case_id)
    if operation == "retries":
        service = JobService(cmd_session)
        service.start(job)
        service.finish(job, status="failed", error="boom")
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/jobs/{job.id}/{operation}",
        headers={"Authorization": ""},
    )

    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize("operation", ["cancel", "retries"])
def test_job_mutations_conceal_cross_tenant_jobs(
    cmd_client, cmd_session, monkeypatch, operation
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"owner-token":"team-a","foreign-token":"team-b"}',
    )
    case_id = _create_admitted_case(cmd_client, token="owner-token")
    job = _make_job(cmd_session, case_id=case_id)
    if operation == "retries":
        service = JobService(cmd_session)
        service.start(job)
        service.finish(job, status="failed", error="boom")
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/jobs/{job.id}/{operation}",
        headers={"Authorization": "Bearer foreign-token"},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("operation", ["cancel", "retries"])
def test_generic_job_mutations_reject_research_run_jobs(
    cmd_client, cmd_session, operation
) -> None:
    case_id = _create_admitted_case(cmd_client)
    job = _make_job(cmd_session, kind="research_run", case_id=case_id)
    if operation == "retries":
        job.status = "failed"
        job.error = "provider failed"
    cmd_session.commit()

    response = cmd_client.post(f"/api/v1/jobs/{job.id}/{operation}")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "conflict"
    assert "research_run" in response.json()["error"]["message"]


def test_activity_feed_from_outbox(cmd_session):
    from app.repositories.outbox import emit_event

    case_id = uuid.uuid4()
    emit_event(
        cmd_session,
        type="evidence_link_proposed",
        aggregate_type="evidence_link",
        aggregate_id=str(uuid.uuid4()),
        ref_type="research_case",
        ref_id=str(case_id),
        payload={"thesis_id": str(uuid.uuid4())},
        origin="ledger",
    )
    cmd_session.commit()

    from app.queries.activity import ActivityQueries

    rows, has_more = ActivityQueries(cmd_session).activity(
        authorized_case_ids={str(case_id)},
        limit=10,
    )
    assert rows
    assert any(r.type == "evidence_link_proposed" for r in rows)


def test_evidence_changes_feed(cmd_session):
    from app.queries.activity import ActivityQueries
    from app.repositories.outbox import emit_event

    case_id = uuid.uuid4()
    emit_event(
        cmd_session,
        type="ai_assessment_frozen",
        aggregate_type="ai_assessment",
        aggregate_id=str(uuid.uuid4()),
        ref_type="research_case",
        ref_id=str(case_id),
        payload={},
        origin="ledger",
    )
    cmd_session.commit()
    rows, _ = ActivityQueries(cmd_session).evidence_changes(
        authorized_case_ids={str(case_id)},
        limit=10,
    )
    assert any(r.type == "ai_assessment_frozen" for r in rows)


def test_legacy_unscoped_tasks_are_not_exposed_by_the_case_task_api(
    cmd_client,
    cmd_session,
):
    TaskRepository(cmd_session).add_task(
        title="Review proposal",
        task_type="review_proposal",
    )
    cmd_session.commit()
    resp = cmd_client.get("/api/v1/tasks")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


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
