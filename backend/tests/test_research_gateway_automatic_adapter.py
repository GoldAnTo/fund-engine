"""The Gateway projects persisted native work, never provider transcripts."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.errors import NotFoundError
from app.models.ledger import ResearchCase
from app.models.operational import ResearchRun
from app.models.research_gateway import ROLE_KEYS, RoleEvent, RoleRun
from app.models.research_monitor import ResearchRunEvent
from app.repositories.research_gateway import ResearchGatewayRepository
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.automatic_research_scope import load_automatic_research_scope
from app.services.event_extraction import EventExtraction


class Extractor:
    def extract(self, *, raw_input, source_url):
        return EventExtraction(
            event_title=raw_input, company_name=None, ticker=None, event_at=None,
            market_reaction=None, summary=None, research_question=raw_input,
            candidate_factors=("需求变化", "供给约束", "替代解释"),
        )


def seed_spec(session, *, gateway_tenant="team-a"):
    started = AutomaticResearchIntakeService(session, extractor=Extractor()).start(
        "研究设备验证与收入兑现", tenant_id="team-a",
        actor_subject_id="alice", commit=False,
    )
    run = session.get(ResearchRun, uuid.UUID(started.run_id))
    repo = ResearchGatewayRepository(session)
    conversation = repo.create_conversation(
        tenant_id=gateway_tenant, owner_subject_id="alice", title="设备研究",
    )
    message = repo.append_message(
        conversation_id=conversation.id, tenant_id=gateway_tenant,
        subject_id="alice", text="研究设备验证与收入兑现",
    )
    intent = repo.append_intent(
        conversation_id=conversation.id, message_id=message.id,
        tenant_id=gateway_tenant, subject_id="alice", intent_kind="start",
        input_sha256=message.input_sha256,
    )
    scope = load_automatic_research_scope(session, run)
    spec = repo.create_run_spec(
        conversation_id=conversation.id, intent_id=intent.id,
        tenant_id=gateway_tenant, subject_id="alice", native_case_id=run.research_case_id,
        native_run_id=run.id, frozen_scope=scope.snapshot(),
        frozen_cutoff={"evidence_cutoff": run.created_at.replace(tzinfo=UTC).isoformat(),
                       "case_evidence_cutoff": session.get(ResearchCase, run.research_case_id).evidence_cutoff.isoformat()
                       if session.get(ResearchCase, run.research_case_id).evidence_cutoff else None},
        frozen_source_policy={"allowed_source_types": list(scope.allowed_source_types),
                              "factors": [{"thesis_id": str(f.thesis_id), "allowed_source_roles": list(f.allowed_source_roles)}
                                          for f in scope.factors]},
        role_manifest_version="fundclaw-roles.v1",
        capability_manifest_version="fundclaw-capabilities.v1",
        correlation_id=uuid.uuid4().hex, input_artifact_refs=[],
    )
    repo.initialize_role_runs(run_spec_id=spec.id)
    for key in ROLE_KEYS:
        repo.append_role_event(
            run_spec_id=spec.id, role_key=key, event_type="role_queued",
            source_key=f"initial:{spec.id}:{key}",
        )
    session.commit()
    return spec, run


def test_runtime_uses_native_intake_in_caller_transaction(cmd_session):
    from app.services.research_gateway_automatic_adapter import AutomaticResearchRuntime

    runtime = AutomaticResearchRuntime(cmd_session, extractor=Extractor())
    ref = runtime.start(
        text="研究设备验证", tenant_id="team-a", actor_subject_id="alice", commit=False,
    )
    assert cmd_session.get(ResearchRun, ref.research_run_id).research_case_id == ref.case_id
    cmd_session.rollback()
    assert cmd_session.get(ResearchCase, ref.case_id) is None


def test_scope_projection_is_durable_and_idempotent(cmd_session):
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, run = seed_spec(cmd_session)
    adapter = GatewayAutomaticResearchAdapter(cmd_session)
    adapter.sync(run_spec_id=spec.id)
    cmd_session.commit()
    first = list(cmd_session.scalars(select(RoleEvent).order_by(RoleEvent.sequence)))
    assert {event.role_key for event in first} == set(ROLE_KEYS)
    assert any(event.role_key == "scope_identity" and event.status == "completed" for event in first)
    assert all(event.status == "queued" for event in first if event.role_key == "compilation_checks")
    adapter.sync_native_run(run.id)
    cmd_session.commit()
    assert list(cmd_session.scalars(select(RoleEvent.id).order_by(RoleEvent.sequence))) == [e.id for e in first]


def test_projection_tracks_waiting_and_failure_without_raw_errors(cmd_session):
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, run = seed_spec(cmd_session)
    run.status, run.stage = "waiting_for_sources", "waiting_for_sources"
    run.updated_at = datetime.now(UTC)
    cmd_session.commit()
    adapter = GatewayAutomaticResearchAdapter(cmd_session)
    adapter.sync(run_spec_id=spec.id)
    cmd_session.commit()
    sources = cmd_session.scalar(select(RoleRun).where(RoleRun.run_spec_id == spec.id, RoleRun.role_key == "sources_evidence"))
    assert sources.status == "blocked"
    run.status, run.stage = "failed", "failed"
    run.stop_reason = "Traceback Authorization: Bearer PRIVATE-SECRET"
    run.updated_at = datetime.now(UTC) + timedelta(seconds=1)
    cmd_session.add(ResearchRunEvent(
        run_id=run.id, seq=99, stage="failed", status="failed",
        message="PRIVATE-SECRET", payload_json={"prompt": "hidden-chain"},
        created_at=run.updated_at,
    ))
    cmd_session.commit()
    adapter.sync_conversation(spec.conversation_id)
    cmd_session.commit()
    events = list(cmd_session.scalars(select(RoleEvent).order_by(RoleEvent.sequence)))
    wire = json.dumps([{"summary":e.display_text,"reason":e.reason_code,"refs":e.artifact_refs} for e in events])
    assert "PRIVATE-SECRET" not in wire and "hidden-chain" not in wire and "Traceback" not in wire
    assert any(e.reason_code == "native_execution_failed" for e in events)
    assert sources.status == "failed"
    count = len(events)
    GatewayAutomaticResearchAdapter(cmd_session).sync(run_spec_id=spec.id)
    assert len(list(cmd_session.scalars(select(RoleEvent)))) == count


def test_projector_rejects_unadmitted_native_case(cmd_session):
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, _ = seed_spec(cmd_session, gateway_tenant="other-team")
    with pytest.raises(NotFoundError):
        GatewayAutomaticResearchAdapter(cmd_session).sync(run_spec_id=spec.id)


def test_active_acquisition_is_visible_not_reported_as_a_blocked_ai(cmd_session):
    from app.models.acquisition import AcquisitionJob, AcquisitionJobEvent
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, run = seed_spec(cmd_session)
    assert AutomaticResearchPipeline(cmd_session).advance(run) == "waiting_for_sources"
    cmd_session.commit()
    adapter = GatewayAutomaticResearchAdapter(cmd_session)
    adapter.sync(run_spec_id=spec.id)
    cmd_session.commit()
    role = cmd_session.scalar(select(RoleRun).where(RoleRun.run_spec_id == spec.id, RoleRun.role_key == "sources_evidence"))
    assert role.status == "queued"
    before = cmd_session.scalar(select(RoleEvent.sequence).order_by(RoleEvent.sequence.desc()).limit(1))
    job = cmd_session.scalar(select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id))
    job.status, job.stage, job.updated_at = "running", "fetching", datetime.now(UTC)
    cmd_session.add(AcquisitionJobEvent(job_id=job.id, seq=999, status="running", stage="fetching",
                                      message="PRIVATE PROVIDER TRACE", payload_json={"prompt": "SECRET"}, created_at=datetime.now(UTC)))
    cmd_session.commit()
    adapter.sync(run_spec_id=spec.id)
    cmd_session.commit()
    assert run.status == "waiting_for_sources"
    assert role.status == "running"
    events = list(cmd_session.scalars(select(RoleEvent).where(RoleEvent.sequence > before)))
    assert any(e.role_key == "sources_evidence" and e.event_type == "role_progress" for e in events)
    assert "SECRET" not in json.dumps([e.artifact_refs for e in events])
    assert "PRIVATE" not in " ".join(e.display_text or "" for e in events)


def test_projector_refreshes_native_state_cached_before_another_worker_commit(cmd_session):
    from sqlalchemy.orm import sessionmaker

    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, cached_run = seed_spec(cmd_session)
    assert cached_run.status == "queued"
    with sessionmaker(bind=cmd_session.get_bind())() as writer:
        run = writer.get(ResearchRun, cached_run.id)
        run.status, run.stage = "failed", "failed"
        run.updated_at = datetime.now(UTC)
        writer.commit()
    assert cached_run.status == "queued"
    GatewayAutomaticResearchAdapter(cmd_session).sync(run_spec_id=spec.id)
    role = cmd_session.scalar(select(RoleRun).where(RoleRun.run_spec_id == spec.id, RoleRun.role_key == "sources_evidence"))
    assert role.status == "failed"


def test_projector_refreshes_source_job_cached_before_worker_commit(cmd_session):
    from sqlalchemy.orm import sessionmaker

    from app.models.acquisition import AcquisitionJob
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, run = seed_spec(cmd_session)
    AutomaticResearchPipeline(cmd_session).advance(run)
    cmd_session.commit()
    cached = cmd_session.scalar(select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id))
    assert cached.status == "queued"
    with sessionmaker(bind=cmd_session.get_bind())() as writer:
        job = writer.get(AcquisitionJob, cached.id)
        job.status, job.stage, job.updated_at = "running", "fetching", datetime.now(UTC)
        writer.commit()
    GatewayAutomaticResearchAdapter(cmd_session).sync(run_spec_id=spec.id)
    role = cmd_session.scalar(select(RoleRun).where(RoleRun.run_spec_id == spec.id, RoleRun.role_key == "sources_evidence"))
    assert role.status == "running"


def test_worker_projects_claim_and_completion_after_native_commits(cmd_session, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.scripts import run_research_worker as worker
    from app.services.research_gateway_automatic_adapter import (
        GatewayAutomaticResearchAdapter,
    )

    spec, run = seed_spec(cmd_session)
    factory = sessionmaker(bind=cmd_session.get_bind())
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker.MonitorScheduler, "dispatch_due", lambda self: [])
    monkeypatch.setattr(worker.FundDisclosureSyncScheduler, "dispatch_due", lambda self: [])
    calls = []
    original = GatewayAutomaticResearchAdapter.sync_native_run

    def record(self, run_id):
        calls.append(run_id)
        return original(self, run_id)

    def execute(self, native):
        native.status, native.stage = "waiting_for_sources", "waiting_for_sources"
        native.updated_at = datetime.now(UTC)

    monkeypatch.setattr(GatewayAutomaticResearchAdapter, "sync_native_run", record)
    monkeypatch.setattr(worker.AutoResearchService, "execute", execute)
    assert worker.run_once() is True
    assert calls.count(run.id) >= 2
    cmd_session.expire_all()
    source = cmd_session.scalar(select(RoleRun).where(RoleRun.run_spec_id == spec.id, RoleRun.role_key == "sources_evidence"))
    assert source.status == "blocked"
