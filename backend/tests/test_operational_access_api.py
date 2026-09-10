from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from app.models.ledger import ResearchCase, Thesis
from app.models.operational import TaskItem
from app.repositories.operational import JobRepository, TaskRepository
from app.repositories.outbox import emit_event
from sqlalchemy import select

from tests.tenant_admission import admit_case


def make_case(session, *, tenant_id="team-a", title="authorized research"):
    case = ResearchCase(
        title=title,
        industry_topic="test",
        created_by="fixture",
        created_at=datetime.now(UTC),
    )
    session.add(case)
    session.flush()
    admit_case(session, case.id, tenant_id=tenant_id)
    return case


@pytest.fixture
def operations(cmd_client, cmd_session, monkeypatch):
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        json.dumps(
            {
                "alice": {"tenant_id": "team-a", "subject_id": "alice", "roles": []},
                "bob": {"tenant_id": "team-a", "subject_id": "bob", "roles": []},
                "foreign": {"tenant_id": "team-b", "subject_id": "carol", "roles": []},
            }
        ),
    )
    cmd_client.headers["Authorization"] = "Bearer alice"
    own = make_case(cmd_session)
    foreign = make_case(
        cmd_session, tenant_id="team-b", title="foreign private research"
    )
    cmd_session.commit()
    return cmd_client, own, foreign


@pytest.mark.parametrize("path", ["/activity", "/evidence-changes", "/tasks"])
def test_global_operational_reads_require_explicit_case(operations, path):
    client, _, _ = operations
    assert client.get("/api/v1" + path).status_code == 422


@pytest.mark.parametrize("path", ["/activity", "/evidence-changes", "/tasks"])
def test_operational_feeds_authorize_case_before_reading(operations, path):
    client, own, foreign = operations
    assert (
        client.get("/api/v1" + path, params={"case_id": str(own.id)}).status_code == 200
    )
    assert (
        client.get("/api/v1" + path, params={"case_id": str(foreign.id)}).status_code
        == 404
    )


def test_jobs_refuse_foreign_and_caseless_reads_events_and_controls(
    operations, cmd_session
):
    client, own, foreign = operations
    repo = JobRepository(cmd_session)
    own_job = repo.add_job(kind="propose", research_case_id=own.id)
    foreign_job = repo.add_job(kind="propose", research_case_id=foreign.id)
    caseless = repo.add_job(kind="propose")
    cmd_session.commit()
    assert client.get(f"/api/v1/jobs/{own_job.id}").status_code == 200
    for job in [foreign_job, caseless]:
        for suffix in ["", "/events"]:
            assert client.get(f"/api/v1/jobs/{job.id}{suffix}").status_code == 404
        for suffix in ["/cancel", "/retries"]:
            assert client.post(f"/api/v1/jobs/{job.id}{suffix}").status_code == 404
        assert cmd_session.get(type(job), job.id).cancel_requested is False


def test_job_not_found_response_does_not_distinguish_foreign_provenance(
    operations, cmd_session
):
    client, _, foreign = operations
    job = JobRepository(cmd_session).add_job(
        kind="propose", research_case_id=foreign.id
    )
    cmd_session.commit()
    private = client.get(f"/api/v1/jobs/{job.id}")
    missing = client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert private.status_code == missing.status_code == 404
    assert private.json()["error"]["message"] == missing.json()["error"]["message"]


def test_tasks_require_case_and_validate_references_against_it(operations, cmd_session):
    client, own, foreign = operations
    foreign_thesis = Thesis(
        research_case_id=foreign.id,
        statement="foreign thesis",
        created_by="fixture",
        created_at=datetime.now(UTC),
    )
    cmd_session.add(foreign_thesis)
    cmd_session.commit()
    base = {"title": "Review", "task_type": "counter_research"}
    assert client.post("/api/v1/tasks", json=base).status_code == 422
    assert (
        client.post(
            "/api/v1/tasks", json={**base, "research_case_id": str(foreign.id)}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/tasks",
            json={
                **base,
                "research_case_id": str(own.id),
                "ref_type": "thesis",
                "ref_id": str(foreign_thesis.id),
            },
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/tasks",
            json={
                **base,
                "research_case_id": str(own.id),
                "ref_type": "arbitrary",
                "ref_id": str(uuid.uuid4()),
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/tasks",
            json={**base, "research_case_id": str(own.id), "ref_type": "thesis"},
        ).status_code
        == 422
    )
    created = client.post(
        "/api/v1/tasks",
        json={
            **base,
            "research_case_id": str(own.id),
            "ref_type": "research_case",
            "ref_id": str(own.id),
        },
    )
    assert created.status_code == 201
    assert (
        client.patch(
            f"/api/v1/tasks/{created.json()['id']}", json={"status": "done"}
        ).status_code
        == 200
    )
    assert len(list(cmd_session.scalars(select(TaskItem)))) == 1


def test_task_list_cursor_and_update_cannot_reference_foreign_or_caseless_task(
    operations, cmd_session
):
    client, own, foreign = operations
    repo = TaskRepository(cmd_session)
    foreign_task = repo.add_task(
        title="private", task_type="review", research_case_id=foreign.id
    )
    caseless = repo.add_task(title="caseless", task_type="review")
    cmd_session.commit()
    for task in [foreign_task, caseless]:
        assert (
            client.patch(
                f"/api/v1/tasks/{task.id}", json={"status": "done"}
            ).status_code
            == 404
        )
        assert (
            client.get(
                "/api/v1/tasks", params={"case_id": str(own.id), "after": str(task.id)}
            ).status_code
            == 404
        )
        assert cmd_session.get(TaskItem, task.id).status == "open"


@pytest.mark.parametrize("path", ["/activity", "/evidence-changes"])
def test_event_cursor_must_belong_to_requested_case(operations, cmd_session, path):
    client, own, foreign = operations
    event = emit_event(
        cmd_session,
        type="ai_assessment_frozen",
        aggregate_type="research_case",
        aggregate_id=foreign.id,
        payload={"secret": "foreign evidence"},
        origin="ledger",
    )
    cmd_session.commit()
    response = client.get(
        "/api/v1" + path, params={"case_id": str(own.id), "after": str(event.id)}
    )
    assert response.status_code == 404
    assert "foreign evidence" not in response.text


@pytest.mark.parametrize("path", ["/activity", "/evidence-changes", "/tasks"])
def test_malformed_operational_cursor_is_a_safe_validation_error(operations, path):
    client, own, _ = operations
    response = client.get(
        "/api/v1" + path, params={"case_id": str(own.id), "after": "malformed"}
    )
    assert response.status_code == 422


def test_legacy_workbench_authorizes_admitted_case(operations):
    client, own, foreign = operations
    assert client.get(f"/api/research-cases/{own.id}/workbench").status_code == 200
    assert client.get(f"/api/research-cases/{foreign.id}/workbench").status_code == 404
