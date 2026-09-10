"""The private task trace exposes persisted lifecycle facts, never native text."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.models.acquisition import (
    AcquisitionException,
    AcquisitionJob,
    AcquisitionJobEvent,
)
from app.models.operational import ResearchTask
from tests.test_research_gateway_artifact_authorization import (
    complete_authorized_evidence,
)


@pytest.fixture
def trace_client(cmd_session, monkeypatch):
    from app.api.v1.research_gateway_trace import router
    from app.db import get_db
    from app.main import app as legacy_app

    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({
        "alice-token": {"tenant_id": "team-a", "subject_id": "alice"},
        "bob-token": {"tenant_id": "team-a", "subject_id": "bob"},
        "foreign-token": {"tenant_id": "team-b", "subject_id": "alice"},
        "legacy-token": "team-a",
    }))
    app = FastAPI()
    app.state.gateway_isolated = True
    app.exception_handlers.update(legacy_app.exception_handlers)
    app.include_router(router, prefix="/api/v1")

    def database():
        yield cmd_session

    app.dependency_overrides[get_db] = database
    with TestClient(app, headers={"Authorization": "Bearer alice-token"}) as client:
        yield client


def _task(session, run, *, task_type="support"):
    return session.scalar(select(ResearchTask).where(
        ResearchTask.run_id == run.id, ResearchTask.task_type == task_type,
    ).order_by(ResearchTask.created_at, ResearchTask.id))


def _url(spec, task):
    return (f"/api/v1/research-conversations/{spec.conversation_id}"
            f"/runs/{spec.id}/tasks/{task.id}/trace")


def _event(session, task, *, sequence, stage="searching", status="running"):
    session.add(AcquisitionJobEvent(
        job_id=UUID(task.result["acquisition_job_id"]), seq=sequence,
        stage=stage, status=status,
        message="PRIVATE Authorization: Bearer SECRET provider error",
        payload_json={"prompt": "SECRET", "source_url": "https://private.invalid"},
        created_at=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
    ))


def test_owner_reads_persisted_safe_stage_history(trace_client, cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    _event(cmd_session, task, sequence=1000)
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"conversation_id", "run_spec_id", "task_id", "events", "exceptions", "truncated"}
    assert (body["conversation_id"], body["run_spec_id"], body["task_id"]) == (
        str(spec.conversation_id), str(spec.id), str(task.id),
    )
    assert body["events"][-1] == {
        "sequence": 1000, "stage": "searching", "status": "running",
        "label": "来源检索阶段", "occurred_at": "2026-09-05T00:00:00Z",
    }
    assert body["truncated"] is False
    assert all(marker not in response.text for marker in (
        "PRIVATE", "SECRET", "private.invalid", "payload_json", "prompt", "lease",
    ))


def test_trace_keeps_latest_hundred_events_in_sequence_order(trace_client, cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    for sequence in reversed(range(1000, 1105)):
        _event(cmd_session, task, sequence=sequence)
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert [event["sequence"] for event in body["events"]] == list(range(1005, 1105))


def test_trace_aggregates_safe_exception_categories_without_native_details(trace_client, cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    for reason in ("search_failed", "fetch_failed", "parser_failure", "parser_failed", "extraction_failed",
                   "incompatible_source_contract", "automatic_admission_quarantined",
                   "variant_conflict", "SECRET reason A", "SECRET reason B"):
        cmd_session.add(AcquisitionException(
            job_id=UUID(task.result["acquisition_job_id"]), reason_code=reason,
            detail_json={"prompt": "SECRET", "error": "PRIVATE provider response"},
            created_at=datetime.now(UTC),
        ))
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 200
    exceptions = {item["reason_code"]: item for item in response.json()["exceptions"]}
    assert {key: item["count"] for key, item in exceptions.items()} == {
        "source_unavailable": 2, "parsing_failed": 3, "source_policy_blocked": 1,
        "evidence_not_admitted": 1, "source_version_conflict": 1, "processing_error": 2,
    }
    assert exceptions["processing_error"] == {
        "reason_code": "processing_error", "message": "部分材料处理异常",
        "next_action": "check_execution", "count": 2,
    }
    assert all(set(item) == {"reason_code", "message", "next_action", "count"}
               for item in exceptions.values())
    assert "SECRET" not in response.text and "PRIVATE" not in response.text


def test_unknown_stage_and_status_use_generic_lifecycle_label(trace_client, cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    _event(cmd_session, task, sequence=1000, stage="PRIVATE-stage", status="SECRET-status")
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 200
    assert response.json()["events"][-1] == {
        "sequence": 1000, "stage": "unknown", "status": "unknown",
        "label": "来源任务状态已更新", "occurred_at": "2026-09-05T00:00:00Z",
    }
    assert "PRIVATE" not in response.text and "SECRET" not in response.text


@pytest.mark.parametrize("token", ["bob-token", "foreign-token"])
def test_other_owner_or_tenant_cannot_read_trace(trace_client, cmd_session, token):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)

    response = trace_client.get(_url(spec, task), headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 404
    assert "events" not in response.json()


@pytest.mark.parametrize("target", ["conversation", "run", "task"])
def test_path_identifiers_are_bound_to_private_run(trace_client, cmd_session, target):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    identifier = {"conversation": spec.conversation_id, "run": spec.id, "task": task.id}[target]
    path = _url(spec, task).replace(str(identifier), str(uuid4()))

    assert trace_client.get(path).status_code == 404


def test_other_real_run_task_and_result_task_cannot_be_used_as_source_trace(trace_client, cmd_session):
    from tests.test_research_gateway_execution import material_run

    spec, run, _ = complete_authorized_evidence(cmd_session)
    _, another_run, _ = material_run(cmd_session)
    other_task = _task(cmd_session, another_run, task_type="intake_material")
    result_task = _task(cmd_session, run, task_type="result")

    assert trace_client.get(_url(spec, other_task)).status_code == 404
    assert trace_client.get(_url(spec, result_task)).status_code == 404


@pytest.mark.parametrize("mutation", ["tenant", "cutoff", "roles", "policy", "task_binding"])
def test_trace_revalidates_frozen_source_authority(trace_client, cmd_session, mutation):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    job = cmd_session.get(AcquisitionJob, UUID(task.result["acquisition_job_id"]))
    if mutation == "tenant":
        job.tenant_id = "foreign-tenant"
    elif mutation == "cutoff":
        job.request_snapshot = {**job.request_snapshot, "cutoff": "2099-01-01T00:00:00Z"}
    elif mutation == "roles":
        job.request_snapshot = {**job.request_snapshot, "allowed_source_roles": ["SECRET-role"]}
    elif mutation == "policy":
        job.policy_snapshot = {**job.policy_snapshot, "version": "SECRET-policy"}
    else:
        task.result = {**task.result, "acquisition_job_id": str(uuid4())}
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 404
    assert "SECRET" not in response.text and "foreign-tenant" not in response.text


@pytest.mark.parametrize("permission", ["allow_display", "allow_ai_processing", "expired"])
def test_material_trace_is_withheld_after_source_permission_revocation(trace_client, cmd_session, permission):
    from app.services.automatic_research_scope import load_automatic_research_scope
    from tests.test_research_gateway_execution import material_run

    spec, run, _ = material_run(cmd_session)
    task = _task(cmd_session, run, task_type="intake_material")
    document_id = load_automatic_research_scope(cmd_session, run).material_document_version_id
    assert trace_client.get(_url(spec, task)).status_code == 200
    assignment = ("effective_until = '2000-01-01T00:00:00'" if permission == "expired"
                  else f"{permission} = false")
    cmd_session.execute(text(
        f"UPDATE source_contracts SET {assignment} WHERE document_version_id = :id"
    ), {"id": document_id.hex})
    cmd_session.commit()

    assert trace_client.get(_url(spec, task)).status_code == 404


def test_trace_does_not_include_another_bound_tasks_history_or_exceptions(trace_client, cmd_session):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    other_task = _task(cmd_session, run, task_type="contradict")
    _event(cmd_session, other_task, sequence=9999)
    cmd_session.add(AcquisitionException(
        job_id=UUID(other_task.result["acquisition_job_id"]), reason_code="parser_failure",
        detail_json={}, created_at=datetime.now(UTC),
    ))
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 200
    assert 9999 not in [item["sequence"] for item in response.json()["events"]]
    assert response.json()["exceptions"] == []


@pytest.mark.parametrize("token, status", [(None, 401), ("legacy-token", 403)])
def test_trace_requires_subject_bearer(trace_client, token, status):
    trace_client.headers.pop("Authorization", None)
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    response = trace_client.get(
        f"/api/v1/research-conversations/{uuid4()}/runs/{uuid4()}/tasks/{uuid4()}/trace",
        headers=headers,
    )

    assert response.status_code == status


def test_trace_requires_isolated_gateway_runtime(trace_client):
    trace_client.app.state.gateway_isolated = False

    response = trace_client.get(
        f"/api/v1/research-conversations/{uuid4()}/runs/{uuid4()}/tasks/{uuid4()}/trace",
    )

    assert response.status_code == 503


@pytest.mark.parametrize("job_status", ["succeeded", "partial", "failed", "cancelled", "running", "retry_wait"])
def test_completed_run_source_exceptions_do_not_ask_user_to_wait(trace_client, cmd_session, job_status):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    job = cmd_session.get(AcquisitionJob, UUID(task.result["acquisition_job_id"]))
    job.status = job_status
    cmd_session.add(AcquisitionException(
        job_id=job.id, reason_code="fetch_failed", detail_json={}, created_at=datetime.now(UTC),
    ))
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 200
    item = response.json()["exceptions"][0]
    assert item["next_action"] == "check_execution"


@pytest.mark.parametrize(
    "run_status,scheduled,expected",
    [
        ("succeeded", True, "check_execution"),
        ("failed", True, "check_execution"),
        ("cancelled", True, "check_execution"),
        ("running", False, "check_execution"),
        ("waiting_for_sources", False, "check_execution"),
        ("running", True, "wait_for_retry"),
        ("waiting_for_sources", True, "wait_for_retry"),
    ],
)
def test_wait_action_requires_active_run_and_scheduled_source_retry(
    trace_client, cmd_session, run_status, scheduled, expected,
):
    spec, run, _ = complete_authorized_evidence(cmd_session)
    task = _task(cmd_session, run)
    job = cmd_session.get(AcquisitionJob, UUID(task.result["acquisition_job_id"]))
    run.status = run_status
    job.status = "retry_wait"
    job.retry_at = datetime.now(UTC) + timedelta(minutes=1) if scheduled else None
    cmd_session.add(AcquisitionException(
        job_id=job.id, reason_code="fetch_failed", detail_json={}, created_at=datetime.now(UTC),
    ))
    cmd_session.commit()

    response = trace_client.get(_url(spec, task))

    assert response.status_code == 200
    assert response.json()["exceptions"][0]["next_action"] == expected
