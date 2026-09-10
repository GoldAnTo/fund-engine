"""Safe live progress follows the material-first native acquisition lifecycle."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.v1.tenant_context import ResearchActor
from app.models.acquisition import AcquisitionJob
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_gateway import ResearchRunSpec
from app.services.automatic_research_pipeline import AutomaticResearchPipeline
from app.services.event_extraction import EventExtraction
from app.services.research_gateway import ResearchGateway
from app.services.research_gateway_automatic_adapter import AutomaticResearchRuntime
from app.services.research_gateway_projection import project_conversation

ACTOR = ResearchActor("team-a", frozenset(), "alice")


def material_run(session, *, company_name="Example Corp"):
    class Extractor:
        def extract(self, *, raw_input, source_url):
            return EventExtraction(
                event_title="Material", company_name=company_name, ticker=None, event_at=None,
                market_reaction=None, summary=None, research_question="Verify material",
                candidate_factors=("Revenue", "Margin", "Orders"), input_kind="material",
            )

    receipt = ResearchGateway(session, AutomaticResearchRuntime(session, extractor=Extractor())).send_message(
        ACTOR, conversation_id=None, text="Revenue was 100. Margin improved. Orders increased.",
        idempotency_key="material-progress",
    )
    spec = session.get(ResearchRunSpec, receipt.run_spec_id)
    run = session.get(ResearchRun, receipt.native_run_id)
    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"
    session.commit()
    jobs = list(session.scalars(select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)))
    assert len(jobs) == 3
    return spec, run, jobs


def test_material_first_jobs_are_visible_while_external_tasks_are_unbound(cmd_session):
    spec, _, jobs = material_run(cmd_session)
    jobs[0].status, jobs[0].stage = "running", "searching"
    jobs[0].attempt = 1
    jobs[0].updated_at = datetime.now(UTC)
    jobs[0].error_detail = "PRIVATE Authorization: Bearer SECRET"
    cmd_session.commit()
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    assert next(role.status for role in view.roles if role.role == "sources_evidence") == "running"
    execution = view.runs[0].execution
    assert execution.projection_state == "available"
    assert len(execution.tasks) == 3
    assert {task.source_name for task in execution.tasks} == {"用户提供材料"}
    assert {task.status for task in execution.tasks} == {"queued", "running"}
    assert all(task.providers == [] for task in execution.tasks)
    assert all(task.counts.discovered == 0 for task in execution.tasks)
    assert execution.worker_state == "unknown"
    assert "PRIVATE" not in view.model_dump_json() and "SECRET" not in view.model_dump_json()


@pytest.mark.parametrize("mutation", ["unbound_running", "foreign_tenant", "wrong_document"])
def test_material_progress_keeps_complete_frozen_binding_checks(cmd_session, mutation):
    spec, run, jobs = material_run(cmd_session)
    if mutation == "unbound_running":
        task = cmd_session.scalar(select(ResearchTask).where(
            ResearchTask.run_id == run.id, ResearchTask.task_type == "support"))
        task.status = "running"
    elif mutation == "foreign_tenant":
        jobs[0].tenant_id = "other-team"
    else:
        jobs[0].request_snapshot = {**jobs[0].request_snapshot, "document_version_id": str(run.id)}
    cmd_session.commit()
    execution = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0].execution
    assert execution.projection_state == "unavailable"
    assert execution.tasks == []
    assert execution.reason_code == "execution_state_unavailable"


def test_stale_acquisition_heartbeat_is_service_liveness_not_job_proof(cmd_session):
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    spec, _, jobs = material_run(cmd_session)
    jobs[0].status, jobs[0].stage = "running", "searching"
    WorkerHeartbeatService(cmd_session).touch(worker_id="PRIVATE-host", worker_kind="acquisition",
        mode="loop", state="polling", seen_at=datetime.now(UTC) - timedelta(minutes=6))
    cmd_session.commit()
    execution = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0].execution
    assert execution.worker_state == "offline"
    assert execution.worker_scope == "acquisition_service"
    assert execution.reason_code == "worker_unavailable"
    assert execution.next_action == "check_execution"
    assert "PRIVATE-host" not in execution.model_dump_json()


def test_execution_stream_updates_without_advancing_event_cursor(cmd_session, monkeypatch):
    from app.services.research_gateway_stream import sse_frames

    spec, _, jobs = material_run(cmd_session)
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))
    factory = sessionmaker(bind=cmd_session.get_bind())

    async def collect():
        frames = []
        async for frame in sse_frames(session_factory=factory, actor=ACTOR, conversation_id=spec.conversation_id,
                after_sequence=view.latest_sequence, authorization="Bearer token", poll_seconds=0, max_polls=3):
            frames.append(frame.decode())
            if "event: execution_progress" in frame.decode() and len([f for f in frames if "execution_progress" in f]) == 1:
                with factory() as writer:
                    job = writer.get(AcquisitionJob, jobs[0].id)
                    job.status, job.stage = "retry_wait", "searching"
                    job.retry_at = datetime.now(UTC) + timedelta(minutes=1)
                    job.updated_at = datetime.now(UTC)
                    writer.commit()
        return frames

    frames = asyncio.run(collect())
    progress = [frame for frame in frames if "event: execution_progress" in frame]
    assert len(progress) == 2
    assert all(not frame.startswith("id:") for frame in progress)
    assert '"next_action":"wait_for_retry"' in progress[-1]


def test_external_provider_names_require_actual_allowed_attempts(cmd_session):
    from app.models.acquisition import AcquisitionAttempt
    from tests.test_research_gateway_automatic_adapter import seed_spec

    spec, run = seed_spec(cmd_session)
    AutomaticResearchPipeline(cmd_session).advance(run)
    cmd_session.commit()
    job = cmd_session.scalar(select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id))
    before = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0].execution
    assert all(task.providers == [] for task in before.tasks)
    for key in ("gildata", "PRIVATE-provider"):
        cmd_session.add(AcquisitionAttempt(job_id=job.id, adapter_key=key, operation="search", attempt_no=1,
            started_at=datetime.now(UTC), outcome="failed", retryable=False,
            safe_metadata={"prompt": "SECRET", "provider": "PRIVATE-provider"}))
    cmd_session.commit()
    after = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0].execution
    assert [task.providers for task in after.tasks if task.providers] == [["gildata"]]
    assert "PRIVATE" not in after.model_dump_json() and "SECRET" not in after.model_dump_json()


def test_material_display_revocation_replaces_live_progress_without_new_ledger_event(cmd_session, monkeypatch):
    from sqlalchemy import text

    from app.services.automatic_research_scope import load_automatic_research_scope
    from app.services.research_gateway_stream import sse_frames

    spec, run, _ = material_run(cmd_session)
    document_id = load_automatic_research_scope(cmd_session, run).material_document_version_id
    view = project_conversation(cmd_session, conversation_id=spec.conversation_id)
    monkeypatch.setenv("RESEARCH_TENANT_TOKENS", json.dumps({"token": {"tenant_id": "team-a", "subject_id": "alice"}}))
    factory = sessionmaker(bind=cmd_session.get_bind())

    async def collect():
        progress = []
        async for frame in sse_frames(session_factory=factory, actor=ACTOR, conversation_id=spec.conversation_id,
                after_sequence=view.latest_sequence, authorization="Bearer token", poll_seconds=0, max_polls=2):
            if b"event: execution_progress" not in frame:
                continue
            progress.append(json.loads(frame.decode().split("data: ", 1)[1]))
            if len(progress) == 1:
                with factory() as writer:
                    # Immutable contracts are altered only to simulate external
                    # permission revocation, like existing authorization tests.
                    writer.execute(text("UPDATE source_contracts SET allow_display = false WHERE document_version_id = :id"),
                                   {"id": document_id.hex})
                    writer.commit()
        return progress

    progress = asyncio.run(collect())
    assert len(progress) == 2
    assert len(progress[0]["execution"]["tasks"]) == 3
    assert progress[1]["execution"]["tasks"] == []
    assert progress[1]["execution"]["reason_code"] == "source_policy_blocked"


def test_execution_schema_is_optional_on_legacy_runs_and_rejects_raw_fields(cmd_session):
    from pydantic import ValidationError

    from app.schemas.v1.research_gateway import GatewayExecutionDTO, GatewayRunDTO

    spec, _, _ = material_run(cmd_session)
    run = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0]
    legacy = run.model_dump(exclude={"execution"})
    assert GatewayRunDTO.model_validate(legacy).execution is None
    payload = run.execution.model_dump()
    for extra in ({"prompt": "SECRET"}, {"reason_code": "raw-error"}):
        with pytest.raises(ValidationError):
            GatewayExecutionDTO.model_validate({**payload, **extra})
    payload["tasks"][0]["counts"]["discovered"] = -1
    with pytest.raises(ValidationError):
        GatewayExecutionDTO.model_validate(payload)


def test_admitted_counts_follow_current_source_display_rights(cmd_session, monkeypatch):
    from app.services import research_gateway_artifacts as artifacts
    from tests.test_research_gateway_projection import complete_with_evidence

    spec, _, _ = complete_with_evidence(cmd_session)
    before = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0].execution
    assert sum(task.counts.admitted for task in before.tasks) == 3
    monkeypatch.setattr(artifacts, "source_contract_is_active", lambda contract: False)
    after = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0].execution
    assert sum(task.counts.admitted for task in after.tasks) == 0
    assert after.next_action == "check_execution"


@pytest.mark.parametrize("company_name", [None, "   "])
def test_missing_research_subject_is_visible_without_discarding_valid_job_history(cmd_session, company_name):
    spec, _, jobs = material_run(cmd_session, company_name=None)
    if company_name is not None:
        for job in jobs:
            job.request_snapshot = {**job.request_snapshot, "entity_names": [company_name]}
    jobs[0].reference_count = 2
    jobs[0].fetched_count = 1
    cmd_session.commit()
    execution = project_conversation(cmd_session, conversation_id=spec.conversation_id).runs[0].execution
    assert execution.projection_state == "available"
    assert execution.reason_code == "research_subject_missing"
    assert execution.next_action == "check_execution"
    assert len(execution.tasks) == 3
    assert sum(task.counts.discovered for task in execution.tasks) == 2
    assert sum(task.counts.fetched for task in execution.tasks) == 1
