from __future__ import annotations

import os
import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.acquisition import AcquisitionException, AcquisitionJob
from app.models.event_research import (
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    AIAssessment,
    EvidenceSnapshot,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import (
    EventResearchLifecycle,
    ResearchRun,
    ResearchTask,
)
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.event_extraction import EventExtraction


def _stage_statuses(body: dict) -> dict[str, str]:
    return {stage["key"]: stage["status"] for stage in body["stages"]}


class _FakeExtractor:
    def extract(self, raw_input: str, source_url: str | None) -> EventExtraction:
        return EventExtraction(
            event_title=raw_input,
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question=f"{raw_input} 的关键变化是什么？",
            candidate_factors=("需求变化", "供给约束", "替代解释"),
        )


def test_start_accepts_only_input_and_returns_queued_ids(
    cmd_client, monkeypatch
) -> None:
    from app.api.v1 import automatic_research as api

    monkeypatch.setattr(
        api,
        "AutomaticResearchIntakeService",
        lambda db: AutomaticResearchIntakeService(db, extractor=_FakeExtractor()),
    )

    response = cmd_client.post(
        "/api/v1/automatic-research",
        json={"input": "光模块行业需求会如何变化"},
    )

    assert response.status_code == 201
    assert set(response.json()) == {"case_id", "run_id", "status"}
    assert response.json()["status"] == "queued"


def test_openapi_marks_exact_automatic_research_wire_fields_required(client) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    expected_required = {
        "AutomaticResearchStartResponse": {"case_id", "run_id", "status"},
        "AutomaticResearchStageDTO": {"key", "label", "status", "summary"},
        "AutomaticResearchSourceDTO": {"title", "url", "role", "review_state"},
        "AutomaticResearchResultDTO": {
            "label",
            "human_reviewed",
            "conclusion",
            "key_findings",
            "counter_evidence",
            "limitations",
            "sources",
        },
        "AutomaticResearchStatsDTO": {
            "source_count",
            "admitted_evidence_count",
            "skipped_count",
            "duration_seconds",
        },
        "AutomaticResearchExceptionDTO": {"reason", "stage", "count"},
        "AutomaticResearchViewDTO": {
            "case_id",
            "run_id",
            "title",
            "status",
            "stages",
            "stats",
            "recent_activity",
            "exceptions",
            "failure_reason",
            "result",
        },
    }
    for name, fields in expected_required.items():
        assert set(schemas[name]["required"]) == fields
    assert (
        schemas["AutomaticResearchStatsDTO"]["properties"]["duration_seconds"][
            "type"
        ]
        == "integer"
    )
    assert schemas["AutomaticResearchExceptionDTO"]["properties"]["stage"] == {
        "title": "Stage",
        "type": "string",
    }
    source_properties = schemas["AutomaticResearchSourceDTO"]["properties"]
    assert {item["type"] for item in source_properties["title"]["anyOf"]} == {
        "string",
        "null",
    }
    result_properties = schemas["AutomaticResearchResultDTO"]["properties"]
    assert result_properties["human_reviewed"]["const"] is False
    for field in ("key_findings", "counter_evidence", "limitations", "sources"):
        assert result_properties[field]["type"] == "array"
    exception_properties = schemas["AutomaticResearchExceptionDTO"]["properties"]
    assert exception_properties["reason"]["type"] == "string"
    assert exception_properties["count"]["type"] == "integer"


def _start(cmd_client, monkeypatch) -> dict:
    from app.api.v1 import automatic_research as api

    monkeypatch.setattr(
        api,
        "AutomaticResearchIntakeService",
        lambda db: AutomaticResearchIntakeService(db, extractor=_FakeExtractor()),
    )
    response = cmd_client.post(
        "/api/v1/automatic-research", json={"input": "自动研究主题"}
    )
    assert response.status_code == 201
    return response.json()


def _completed_case(cmd_client, cmd_session, monkeypatch) -> tuple[dict, ResearchRun]:
    from tests.test_automatic_research_pipeline import (
        _AssessmentGenerator,
        _admit_link,
    )
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    pipeline = AutomaticResearchPipeline(
        cmd_session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    _admit_link(cmd_session, jobs[0])
    _admit_link(cmd_session, jobs[1])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index < 2 else "failed"
        job.stage = job.status
        job.admitted_count = 1 if index < 2 else 0
    assert pipeline.advance(run) == "completed"
    cmd_session.commit()
    return created, run


def _scope_payload(cmd_session, run_id: uuid.UUID) -> dict:
    from app.models.research_monitor import ResearchRunEvent

    event = cmd_session.scalar(
        select(ResearchRunEvent)
        .where(
            ResearchRunEvent.run_id == run_id,
            ResearchRunEvent.stage == "scope",
        )
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    assert event is not None
    return copy.deepcopy(event.payload_json)


def test_get_queued_view_always_has_five_ordered_stages(
    cmd_client, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)

    response = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "queued"
    assert [stage["key"] for stage in body["stages"]] == [
        "acquire",
        "parse",
        "admit",
        "analyze",
        "conclude",
    ]
    assert body["result"] is None

    listed = cmd_client.get("/api/v1/event-research")
    assert listed.status_code == 200
    item = next(
        item
        for item in listed.json()["items"]
        if item["case_id"] == created["case_id"]
    )
    assert item["workflow_mode"] == "automatic"


def test_get_failed_view_redacts_internal_failure_and_has_no_result(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "provider sk-private traceback /tmp/internal.py"
    run.updated_at = datetime.now(timezone.utc)
    lifecycle.status = "exhausted"
    lifecycle.status_summary = "provider sk-private traceback"
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["failure_reason"] == "自动研究未能完成，请稍后重试"
    assert body["result"] is None
    assert "sk-private" not in response.text
    assert "traceback" not in response.text


def test_no_usable_evidence_failure_stops_at_admit(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.case_monitor import ResearchRunEventRepository

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    pipeline = AutomaticResearchPipeline(cmd_session)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    for job in jobs:
        job.status = "failed"
        job.stage = "failed"
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "no_usable_evidence"
    lifecycle.status = "exhausted"
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="failed",
        status="failed",
        message="no usable evidence",
        payload_json={"stop_reason": "no_usable_evidence"},
    )
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert stages["admit"] == "failed"
    assert stages["analyze"] == "pending"
    assert stages["conclude"] == "pending"


def test_dispatch_failure_without_source_jobs_stops_at_acquire(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "automatic_source_dispatch_failed"
    lifecycle.status = "exhausted"
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert stages["acquire"] == "failed"
    assert all(stages[key] == "pending" for key in ("parse", "admit", "analyze", "conclude"))


def test_analyze_failure_does_not_complete_conclude_stage(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.case_monitor import ResearchRunEventRepository

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    assert AutomaticResearchPipeline(cmd_session).advance(run) == "waiting_for_sources"
    for job in cmd_session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        job.status = "succeeded"
        job.stage = "succeeded"
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "assessment_generation_failed"
    lifecycle.status = "exhausted"
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="analyze",
        status="failed",
        message="internal provider detail",
        payload_json={},
    )
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert stages["analyze"] == "failed"
    assert stages["conclude"] == "pending"


def test_get_rejects_reviewed_case_and_other_tenant_without_disclosure(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from tests.test_event_research_api import _confirmed_event

    reviewed_created = cmd_client.post(
        "/api/v1/event-research", json=_confirmed_event()
    )
    assert reviewed_created.status_code == 201
    reviewed = cmd_client.get(
        f"/api/v1/automatic-research/{reviewed_created.json()['case_id']}"
    )
    assert reviewed.status_code == 404

    created = _start(cmd_client, monkeypatch)
    from app.main import app

    previous = os.environ["RESEARCH_TENANT_TOKENS"]
    os.environ["RESEARCH_TENANT_TOKENS"] = (
        '{"test-tenant-token":"test-team","other-token":"other-team"}'
    )
    try:
        other = TestClient(
            app, headers={"Authorization": "Bearer other-token"}
        ).get(f"/api/v1/automatic-research/{created['case_id']}")
    finally:
        os.environ["RESEARCH_TENANT_TOKENS"] = previous
    assert other.status_code == 404


def test_retry_failed_case_creates_new_run_and_preserves_old_run(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    old_run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert old_run is not None and lifecycle is not None
    old_run.status = "failed"
    old_run.stage = "failed"
    old_run.stop_reason = "no_usable_evidence"
    lifecycle.status = "exhausted"
    old_scope = _scope_payload(cmd_session, old_run.id)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    assert response.status_code == 201
    retried = response.json()
    assert retried["run_id"] != created["run_id"]
    assert retried["status"] == "queued"
    cmd_session.refresh(old_run)
    assert old_run.status == "failed"
    assert old_run.stop_reason == "no_usable_evidence"
    assert lifecycle.active_run_id == uuid.UUID(retried["run_id"])
    new_run = cmd_session.get(ResearchRun, uuid.UUID(retried["run_id"]))
    assert new_run is not None
    new_scope = _scope_payload(cmd_session, new_run.id)
    assert new_run.scope_thesis_ids == old_run.scope_thesis_ids
    assert new_run.max_rounds == old_run.max_rounds
    assert new_run.budget == old_run.budget
    for key in (
        "factor_ids",
        "factor_statements",
        "automatic_protocol",
        "automatic_evidence_plan",
        "allowed_source_types",
        "monitor_version_id",
        "frequency",
        "next_verification_event",
        "configured_by",
        "configuration_change_reason",
    ):
        assert new_scope.get(key) == old_scope.get(key)
    assert new_scope["trigger"] == "retry"
    assert new_scope["retried_from_run_id"] == str(old_run.id)

    duplicate = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )
    assert duplicate.status_code == 409


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["automatic_protocol"].update(
            {"generated_by": "human"}
        ),
        lambda payload: payload.update(
            {"factor_ids": list(reversed(payload["factor_ids"]))}
        ),
        lambda payload: payload["automatic_evidence_plan"].update(
            {"budget": payload["automatic_evidence_plan"]["budget"] + 1}
        ),
        lambda payload: payload["automatic_evidence_plan"]["items"][0].update(
            {"objectives": ["support"]}
        ),
    ],
    ids=["protocol", "factor-order", "budget", "plan"],
)
def test_retry_rejects_malformed_frozen_scope(
    cmd_client, cmd_session, monkeypatch, mutate
) -> None:
    from app.services.case_monitor import ResearchRunEventRepository

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    malformed = _scope_payload(cmd_session, run.id)
    mutate(malformed)
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="scope",
        status="completed",
        message="malformed frozen scope",
        payload_json=malformed,
    )
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "no_usable_evidence"
    lifecycle.status = "exhausted"
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    assert response.status_code == 409
    assert lifecycle.active_run_id == run.id
    assert len(
        list(
            cmd_session.scalars(
                select(ResearchRun).where(
                    ResearchRun.research_case_id == run.research_case_id
                )
            )
        )
    ) == 1


def test_completed_view_returns_machine_result_and_display_safe_sources(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from tests.test_automatic_research_pipeline import (
        _AssessmentGenerator,
        _admit_link,
    )
    from app.models.source_governance import SourceContract
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    pipeline = AutomaticResearchPipeline(
        cmd_session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    running = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )
    assert running.status_code == 200
    assert running.json()["status"] == "running"
    visible_link = _admit_link(cmd_session, jobs[0])
    hidden_link = _admit_link(cmd_session, jobs[1])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index < 2 else "failed"
        job.stage = job.status
        job.admitted_count = 1 if index < 2 else 0
    jobs[0].exception_count = 2
    cmd_session.add_all(
        [
            AcquisitionException(
                job_id=jobs[0].id,
                reason_code="fetch_failed",
                detail_json={"secret": "sk-private", "stack": "/tmp/provider.py"},
                created_at=datetime.now(timezone.utc),
            ),
            AcquisitionException(
                job_id=jobs[0].id,
                reason_code="provider_secret_failure",
                detail_json={"secret": "sk-private"},
                created_at=datetime.now(timezone.utc),
            ),
        ]
    )
    for link, allow_display in ((visible_link, True), (hidden_link, False)):
        statement = cmd_session.get(SourceStatement, link.source_statement_id)
        assert statement is not None
        span = cmd_session.get(SourceSpan, statement.source_span_id)
        assert span is not None
        cmd_session.add(
            SourceContract(
                document_version_id=span.document_version_id,
                source_type="licensed_provider",
                research_source_type="licensed_provider",
                provider_or_tenant="test",
                allow_ai_processing=True,
                allow_display=allow_display,
                allow_export=False,
                allow_api=False,
                region="CN",
                retention_policy="retain",
                deletion_policy="none",
                downstream_restrictions=[],
                declared_by="test",
                created_at=datetime.now(timezone.utc),
            )
        )
    assert pipeline.advance(run) == "completed"
    cmd_session.commit()

    response = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["label"] == "系统生成，未经人工审核"
    assert body["result"]["human_reviewed"] is False
    assert body["stats"]["admitted_evidence_count"] == 2
    assert body["stats"]["skipped_count"] == 2
    assert {item["reason"] for item in body["exceptions"]} == {
        "部分来源获取失败",
        "部分材料处理异常",
    }
    assert "sk-private" not in response.text
    assert "provider_secret_failure" not in response.text
    assert len(body["result"]["sources"]) == 2
    assert any(source["url"] is not None for source in body["result"]["sources"])
    assert any(
        source["title"] is None and source["url"] is None
        for source in body["result"]["sources"]
    )


def test_completed_view_uses_only_active_run_deterministic_conclusion(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    deterministic_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"fund-engine:event-research:automatic:{run.id}",
    )
    valid = cmd_session.get(EventResearchConclusion, deterministic_id)
    assert valid is not None
    cmd_session.add(
        EventResearchConclusion(
            research_case_id=run.research_case_id,
            scope_version_id=valid.scope_version_id,
            state="system_generated",
            text="FORGED LATER CASE CONCLUSION",
            primary_factor=None,
            evidence_link_ids=list(valid.evidence_link_ids),
            based_on_conclusion_id=None,
            reviewer=None,
            created_at=valid.created_at + timedelta(seconds=1),
        )
    )
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    assert body["status"] == "completed"
    assert body["result"]["conclusion"] == valid.text


def test_completed_view_fails_closed_when_current_scope_order_drifted(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    current = cmd_session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == run.research_case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    assert current is not None
    factors = list(
        cmd_session.scalars(
            select(EventResearchScopeFactor)
            .where(EventResearchScopeFactor.scope_version_id == current.id)
            .order_by(EventResearchScopeFactor.position)
        )
    )
    drifted = EventResearchScopeVersion(
        research_case_id=run.research_case_id,
        version=current.version + 1,
        changed_by="reviewer",
        change_summary="reordered scope",
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.add(drifted)
    cmd_session.flush()
    for position, factor in enumerate(reversed(factors), start=1):
        cmd_session.add(
            EventResearchScopeFactor(
                scope_version_id=drifted.id,
                statement=factor.statement,
                position=position,
            )
        )
    assignments = list(
        cmd_session.scalars(
            select(EventResearchScopeEvidenceAssignment).where(
                EventResearchScopeEvidenceAssignment.scope_version_id == current.id
            )
        )
    )
    for assignment in assignments:
        cmd_session.add(
            EventResearchScopeEvidenceAssignment(
                scope_version_id=drifted.id,
                evidence_link_id=assignment.evidence_link_id,
                factor_statement=assignment.factor_statement,
                disposition=assignment.disposition,
                created_at=datetime.now(timezone.utc),
            )
        )
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    assert body["status"] == "failed"
    assert body["result"] is None


@pytest.mark.parametrize(
    ("displayed_as_provisional", "creator_type"),
    [(False, "ai"), (True, "human")],
)
def test_completed_view_fails_closed_on_assessment_provenance_drift(
    cmd_client,
    cmd_session,
    monkeypatch,
    displayed_as_provisional: bool,
    creator_type: str,
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    task = cmd_session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
            ResearchTask.status == "done",
        )
    )
    assert task is not None and isinstance(task.result, dict)
    original = cmd_session.get(AIAssessment, uuid.UUID(task.result["assessment_id"]))
    assert original is not None
    original_snapshot = cmd_session.get(EvidenceSnapshot, original.snapshot_id)
    assert original_snapshot is not None
    forged_snapshot = EvidenceSnapshot(
        thesis_id=original_snapshot.thesis_id,
        cutoff=original_snapshot.cutoff,
        evidence_link_ids=list(original_snapshot.evidence_link_ids),
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.add(forged_snapshot)
    cmd_session.flush()
    forged = AIAssessment(
        snapshot_id=forged_snapshot.id,
        conclusion=original.conclusion,
        rationale="forged assessment rationale",
        gaps=[],
        displayed_as_provisional=displayed_as_provisional,
        creator_type=creator_type,
        model_version="forged",
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.add(forged)
    cmd_session.flush()
    task.result = {**task.result, "assessment_id": str(forged.id)}
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    assert body["status"] == "failed"
    assert body["result"] is None
