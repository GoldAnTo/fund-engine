from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models.acquisition import AcquisitionException, AcquisitionJob
from app.models.ledger import SourceSpan, SourceStatement
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.event_extraction import EventExtraction


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

    duplicate = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )
    assert duplicate.status_code == 409


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
