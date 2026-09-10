from __future__ import annotations

import asyncio
import os
import copy
import json
import uuid
from io import BytesIO
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.errors import ValidationFailedError
from app.models.acquisition import (
    AcquisitionException,
    AcquisitionJob,
    AcquisitionJobEvent,
)
from app.models.event_research import (
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    AIAssessment,
    CaseTenantAdmission,
    DocumentUploadArtifact,
    DocumentVersion,
    EvidenceSnapshot,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import (
    EventResearchLifecycle,
    Job,
    ResearchRun,
    ResearchTask,
)
from app.models.research_monitor import CaseMonitorVersion
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.event_extraction import EventExtraction
from app.services.event_extraction import EventExtractionProviderError


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


def test_uploaded_start_freezes_pdf_and_queues_one_automatic_run(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.api.v1 import automatic_research as api

    monkeypatch.setattr(
        api,
        "AutomaticResearchIntakeService",
        lambda db: AutomaticResearchIntakeService(db, extractor=_FakeExtractor()),
    )
    from reportlab.pdfgen import canvas

    pdf = BytesIO()
    document_canvas = canvas.Canvas(pdf)
    document_canvas.drawString(72, 720, "quarterly update")
    document_canvas.save()
    raw = pdf.getvalue()

    response = cmd_client.post(
        "/api/v1/automatic-research/uploaded",
        data={"input": " \t\n "},
        files={"file": ("quarterly-update.pdf", raw, "application/pdf")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "queued"
    case_id = uuid.UUID(body["case_id"])
    run_id = uuid.UUID(body["run_id"])
    case = cmd_session.get(ResearchCase, case_id)
    assert case is not None
    assert case.title == "quarterly-update.pdf"
    assert list(
        cmd_session.scalars(
            select(ResearchRun).where(ResearchRun.research_case_id == case_id)
        )
    ) == [cmd_session.get(ResearchRun, run_id)]
    admission = cmd_session.scalar(
        select(CaseTenantAdmission).where(
            CaseTenantAdmission.research_case_id == case_id
        )
    )
    assert admission is not None
    document = cmd_session.get(DocumentVersion, admission.initial_document_version_id)
    assert document is not None and document.parse_state == "success"
    assert cmd_session.scalar(
        select(DocumentUploadArtifact.raw_bytes).where(
            DocumentUploadArtifact.document_version_id == document.id
        )
    ) == raw
    assert len(
        list(
            cmd_session.scalars(
                select(EventResearchLifecycle).where(
                    EventResearchLifecycle.research_case_id == case_id
                )
            )
        )
    ) == 1


def test_uploaded_start_rejects_unsupported_files_without_residual_rows(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.api.v1 import automatic_research as api

    monkeypatch.setattr(
        api,
        "AutomaticResearchIntakeService",
        lambda db: AutomaticResearchIntakeService(db, extractor=_FakeExtractor()),
    )

    response = cmd_client.post(
        "/api/v1/automatic-research/uploaded",
        files={"file": ("payload.docx", b"not supported", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert list(cmd_session.scalars(select(ResearchCase.id))) == []
    assert list(cmd_session.scalars(select(ResearchRun.id))) == []
    assert list(cmd_session.scalars(select(DocumentVersion.id))) == []


def test_uploaded_start_rolls_back_when_freezing_empty_original_fails(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.api.v1 import automatic_research as api

    monkeypatch.setattr(
        api,
        "AutomaticResearchIntakeService",
        lambda db: AutomaticResearchIntakeService(db, extractor=_FakeExtractor()),
    )

    response = cmd_client.post(
        "/api/v1/automatic-research/uploaded",
        files={"file": ("empty.txt", b"", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert list(cmd_session.scalars(select(ResearchCase.id))) == []
    assert list(cmd_session.scalars(select(ResearchRun.id))) == []
    assert list(cmd_session.scalars(select(DocumentVersion.id))) == []


def test_uploaded_start_rejects_more_than_one_file(cmd_client) -> None:
    response = cmd_client.post(
        "/api/v1/automatic-research/uploaded",
        files=[
            ("file", ("first.txt", b"first", "text/plain")),
            ("file", ("second.txt", b"second", "text/plain")),
        ],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


@pytest.mark.parametrize(
    ("file_name", "raw", "mime_type"),
    [
        ("pretending.pdf", b"not a PDF", "application/pdf"),
        ("nul.txt", b"text\x00with a NUL", "text/plain"),
        ("binary.md", b"\xff\xfe", "text/markdown"),
        ("binary.csv", b"column\x00value", "text/csv"),
    ],
)
def test_uploaded_start_rejects_bytes_that_do_not_match_file_type(
    cmd_client,
    cmd_session,
    monkeypatch,
    file_name: str,
    raw: bytes,
    mime_type: str,
) -> None:
    from app.api.v1 import automatic_research as api

    monkeypatch.setattr(
        api,
        "AutomaticResearchIntakeService",
        lambda db: AutomaticResearchIntakeService(db, extractor=_FakeExtractor()),
    )

    response = cmd_client.post(
        "/api/v1/automatic-research/uploaded",
        files={"file": (file_name, raw, mime_type)},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert list(cmd_session.scalars(select(ResearchCase.id))) == []
    assert list(cmd_session.scalars(select(ResearchRun.id))) == []
    assert list(cmd_session.scalars(select(DocumentVersion.id))) == []


def test_uploaded_start_rejects_overlong_file_name_without_residual_rows(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.api.v1 import automatic_research as api

    monkeypatch.setattr(
        api,
        "AutomaticResearchIntakeService",
        lambda db: AutomaticResearchIntakeService(db, extractor=_FakeExtractor()),
    )

    response = cmd_client.post(
        "/api/v1/automatic-research/uploaded",
        files={"file": (f"{'x' * 509}.txt", b"valid text", "text/plain")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert list(cmd_session.scalars(select(ResearchCase.id))) == []
    assert list(cmd_session.scalars(select(ResearchRun.id))) == []
    assert list(cmd_session.scalars(select(DocumentVersion.id))) == []


def _asgi_events_for_upload_body(
    *, body: bytes, forged_content_length: bool
) -> list[dict]:
    from app.main import app

    headers = [
        (b"authorization", b"Bearer test-tenant-token"),
        (b"content-type", b"multipart/form-data; boundary=unused"),
    ]
    if forged_content_length:
        headers.append((b"content-length", b"1"))
    request_messages = [{"type": "http.request", "body": body, "more_body": False}]
    response_messages: list[dict] = []

    async def invoke() -> None:
        async def receive() -> dict:
            if request_messages:
                return request_messages.pop(0)
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            response_messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/automatic-research/uploaded",
                "raw_path": b"/api/v1/automatic-research/uploaded",
                "query_string": b"",
                "headers": headers,
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "root_path": "",
                "state": {},
            },
            receive,
            send,
        )

    asyncio.run(invoke())
    return response_messages


@pytest.mark.parametrize("forged_content_length", [False, True])
def test_uploaded_body_cap_streams_past_absent_or_forged_content_length(
    monkeypatch, forged_content_length: bool
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS", '{"test-tenant-token":"test-team"}'
    )

    events = _asgi_events_for_upload_body(
        body=b"x" * (20 * 1024 * 1024 + 1),
        forged_content_length=forged_content_length,
    )

    response_start = next(event for event in events if event["type"] == "http.response.start")
    response_body = b"".join(
        event.get("body", b"")
        for event in events
        if event["type"] == "http.response.body"
    )
    assert response_start["status"] == 413
    assert json.loads(response_body)["error"]["code"] == "request_too_large"


def test_uploaded_body_cap_does_not_apply_to_an_unrelated_route() -> None:
    from app.main import app

    response_messages: list[dict] = []

    async def invoke() -> None:
        async def receive() -> dict:
            return {
                "type": "http.request",
                "body": b"x" * (20 * 1024 * 1024 + 1),
                "more_body": False,
            }

        async def send(message: dict) -> None:
            response_messages.append(message)

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/api/v1/health",
                "raw_path": b"/api/v1/health",
                "query_string": b"",
                "headers": [],
                "client": ("testclient", 50000),
                "server": ("testserver", 80),
                "root_path": "",
                "state": {},
            },
            receive,
            send,
        )

    asyncio.run(invoke())
    response_start = next(
        event for event in response_messages if event["type"] == "http.response.start"
    )
    assert response_start["status"] == 200


def test_start_trims_input_and_rejects_whitespace_without_rows(
    cmd_client, cmd_session
) -> None:
    from app.schemas.v1.automatic_research import AutomaticResearchStartRequest

    assert AutomaticResearchStartRequest(input="  自动研究主题  ").input == "自动研究主题"

    response = cmd_client.post(
        "/api/v1/automatic-research",
        json={"input": " \t\n "},
    )

    assert response.status_code == 422
    assert list(cmd_session.scalars(select(ResearchCase.id))) == []


@pytest.mark.parametrize(
    ("raised", "expected_status", "expected_code", "expected_message"),
    [
        (
            EventExtractionProviderError("provider secret sk-private"),
            503,
            "upstream_unavailable",
            "自动研究服务暂时不可用，请稍后重试",
        ),
        (
            ValueError("invalid intake with provider secret sk-private"),
            422,
            "validation_failed",
            "自动研究输入无效，请检查后重试",
        ),
    ],
)
def test_start_maps_intake_failures_safely_and_rolls_back(
    cmd_client,
    cmd_session,
    monkeypatch,
    raised: Exception,
    expected_status: int,
    expected_code: str,
    expected_message: str,
) -> None:
    from app.api.v1 import automatic_research as api

    class _FailingIntake:
        def __init__(self, db) -> None:
            self._db = db

        def start(self, raw_input: str, *, tenant_id: str):
            self._db.add(
                ResearchCase(
                    title="must roll back",
                    industry_topic="test",
                    created_at=datetime.now(timezone.utc),
                    created_by="test",
                )
            )
            self._db.flush()
            raise raised

    monkeypatch.setattr(api, "AutomaticResearchIntakeService", _FailingIntake)

    response = cmd_client.post(
        "/api/v1/automatic-research",
        json={"input": "自动研究主题"},
    )

    assert response.status_code == expected_status
    error = response.json()["error"]
    assert error["code"] == expected_code
    assert error["message"] == expected_message
    assert "sk-private" not in response.text
    assert list(cmd_session.scalars(select(ResearchCase.id))) == []


def test_openapi_marks_exact_automatic_research_wire_fields_required(client) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    start_request = schemas["AutomaticResearchStartRequest"]
    assert start_request["required"] == ["input"]
    assert start_request["properties"]["input"] == {
        "type": "string",
        "maxLength": 100_000,
        "minLength": 1,
        "title": "Input",
    }
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
            "AutomaticResearchNarrativeDTO": {
                "current_action",
                "completed_count",
                "total_count",
                "next_action",
                "elapsed_seconds",
            },
            "AutomaticResearchActivityDetailDTO": {
                "occurred_at",
                "work_item",
                "internal_status",
            },
            "AutomaticResearchActivityDTO": {
                "label",
                "count",
                "technical_details",
            },
            "AutomaticResearchExceptionDTO": {
                "reason",
                "count",
                "impact",
                "system_action",
            },
            "AutomaticResearchFactorDTO": {
                "statement",
                "classification",
                "ranking_reason",
                "support_count",
                "counter_evidence_count",
                "evidence_gap",
            },
            "AutomaticResearchViewDTO": {
                "case_id",
                "run_id",
                "title",
                "status",
                "stages",
                "stats",
                "narrative",
                "activities",
                "exceptions",
                "factors",
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
    assert "stage" not in exception_properties
    assert exception_properties["impact"]["type"] == "string"
    assert exception_properties["system_action"]["type"] == "string"


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


def _fail_run_with_scope(cmd_session, run: ResearchRun, payload: dict) -> None:
    from app.services.case_monitor import ResearchRunEventRepository

    lifecycle = cmd_session.get(EventResearchLifecycle, run.research_case_id)
    assert lifecycle is not None
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="scope",
        status="completed",
        message="frozen scope mutation",
        payload_json=payload,
    )
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "no_usable_evidence"
    lifecycle.status = "exhausted"
    cmd_session.commit()


def _assert_get_retry_scope_conflict_without_new_work(
    cmd_client, cmd_session, case_id: str
) -> None:
    case_uuid = uuid.UUID(case_id)
    run_ids_before = list(
        cmd_session.scalars(
            select(ResearchRun.id).where(ResearchRun.research_case_id == case_uuid)
        )
    )
    job_ids_before = list(
        cmd_session.scalars(select(Job.id).where(Job.research_case_id == case_uuid))
    )

    get_response = cmd_client.get(f"/api/v1/automatic-research/{case_id}")
    retry_response = cmd_client.post(
        f"/api/v1/automatic-research/{case_id}/retry"
    )

    for response in (get_response, retry_response):
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "conflict"
        assert error["message"] == "自动研究范围不可用，请稍后重试"
        assert "monitor" not in response.text.lower()
    assert list(
        cmd_session.scalars(
            select(ResearchRun.id).where(ResearchRun.research_case_id == case_uuid)
        )
    ) == run_ids_before
    assert list(
        cmd_session.scalars(select(Job.id).where(Job.research_case_id == case_uuid))
    ) == job_ids_before


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
    assert [stage["label"] for stage in body["stages"]] == [
        "资料获取",
        "内容解析",
        "证据校验",
        "分析判断",
        "生成结论",
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


def test_active_duration_advances_with_current_time(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.queries import automatic_research as query_module

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    started_at = run.created_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    clock = [started_at + timedelta(seconds=1)]
    monkeypatch.setattr(query_module, "_utcnow", lambda: clock[0], raising=False)

    first = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()["stats"]["duration_seconds"]
    clock[0] += timedelta(seconds=5)
    second = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()["stats"]["duration_seconds"]

    assert second >= first + 5


@pytest.mark.parametrize("terminal_status", ["failed", "completed"])
def test_terminal_duration_ignores_later_events_and_source_updates(
    cmd_client, cmd_session, monkeypatch, terminal_status: str
) -> None:
    if terminal_status == "completed":
        created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    else:
        created = _start(cmd_client, monkeypatch)
        run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
        lifecycle = cmd_session.get(
            EventResearchLifecycle, uuid.UUID(created["case_id"])
        )
        assert run is not None and lifecycle is not None
        run.status = "failed"
        run.stage = "failed"
        run.stop_reason = "dispatch_failed"
        lifecycle.status = "exhausted"
    baseline = datetime.now(timezone.utc) - timedelta(seconds=120)
    run.created_at = baseline
    run.updated_at = baseline + timedelta(seconds=10)
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()

    assert body["status"] == terminal_status
    assert body["stats"]["duration_seconds"] == 10


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
    ResearchRunEventRepository(cmd_session).append(
        run.id,
        stage="failed",
        status="failed",
        message="generic terminal event",
        payload_json={},
    )
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert stages["analyze"] == "failed"
    assert stages["conclude"] == "pending"
    conclude = next(stage for stage in body["stages"] if stage["key"] == "conclude")
    assert conclude["started_at"] is None
    assert conclude["completed_at"] is None


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


def test_professional_start_rejects_automatic_case_without_creating_work(
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
    run_ids_before = list(
        cmd_session.scalars(
            select(ResearchRun.id).where(
                ResearchRun.research_case_id == old_run.research_case_id
            )
        )
    )
    job_ids_before = list(
        cmd_session.scalars(
            select(Job.id).where(Job.research_case_id == old_run.research_case_id)
        )
    )

    response = cmd_client.post(
        f"/api/v1/research-cases/{created['case_id']}/runs",
        json={"max_rounds": 1, "budget": 20},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert (
        response.json()["error"]["message"]
        == "自动研究 Case 必须通过自动研究重试接口重新运行"
    )
    assert list(
        cmd_session.scalars(
            select(ResearchRun.id).where(
                ResearchRun.research_case_id == old_run.research_case_id
            )
        )
    ) == run_ids_before
    assert list(
        cmd_session.scalars(
            select(Job.id).where(Job.research_case_id == old_run.research_case_id)
        )
    ) == job_ids_before


def test_professional_start_authorizes_before_automatic_workflow_disclosure(
    cmd_client, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    from app.main import app

    previous = os.environ["RESEARCH_TENANT_TOKENS"]
    os.environ["RESEARCH_TENANT_TOKENS"] = (
        '{"test-tenant-token":"test-team","other-token":"other-team"}'
    )
    try:
        response = TestClient(
            app, headers={"Authorization": "Bearer other-token"}
        ).post(
            f"/api/v1/research-cases/{created['case_id']}/runs",
            json={"max_rounds": 1, "budget": 20},
        )
    finally:
        os.environ["RESEARCH_TENANT_TOKENS"] = previous

    assert response.status_code == 404
    assert "automatic" not in response.text.lower()


def test_professional_start_still_accepts_reviewed_event_case(
    cmd_client, cmd_session
) -> None:
    from tests.test_event_research_api import _confirmed_event

    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event())
    assert created.status_code == 201

    response = cmd_client.post(
        f"/api/v1/research-cases/{created.json()['case_id']}/runs",
        json={"max_rounds": 1, "budget": 20},
    )

    assert response.status_code == 201, response.text
    run = cmd_session.get(ResearchRun, uuid.UUID(response.json()["id"]))
    assert run is not None
    assert run.status == "queued"


@pytest.mark.parametrize(
    "unmanaged_status",
    ["queued", "running", "waiting_for_sources", "waiting_for_review"],
)
def test_unmanaged_active_run_blocks_automatic_get_and_retry(
    cmd_client, cmd_session, monkeypatch, unmanaged_status
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
    unmanaged = ResearchRun(
        research_case_id=old_run.research_case_id,
        status=unmanaged_status,
        stage="planning",
        round=0,
        max_rounds=old_run.max_rounds,
        budget=old_run.budget,
        budget_used=0,
        scope_thesis_ids=list(old_run.scope_thesis_ids or []),
        monitor_version_id=old_run.monitor_version_id,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    cmd_session.add(unmanaged)
    cmd_session.commit()
    run_ids_before = list(
        cmd_session.scalars(
            select(ResearchRun.id).where(
                ResearchRun.research_case_id == old_run.research_case_id
            )
        )
    )

    get_response = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}",
        headers={"x-request-id": "unmanaged-active-run"},
    )
    retry_response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry",
        headers={"x-request-id": "unmanaged-active-run"},
    )

    for response in (get_response, retry_response):
        assert response.status_code == 409
    assert get_response.json() == retry_response.json()
    assert get_response.json()["error"] == {
        "code": "conflict",
        "message": "自动研究存在其他进行中的运行，请稍后重试",
        "details": {},
        "request_id": "unmanaged-active-run",
    }
    assert lifecycle.active_run_id == old_run.id
    assert list(
        cmd_session.scalars(
            select(ResearchRun.id).where(
                ResearchRun.research_case_id == old_run.research_case_id
            )
        )
    ) == run_ids_before


def test_retry_failed_case_creates_new_run_and_preserves_old_run(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.services.case_monitor import ResearchRunEventRepository

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
    old_scope["extension_metadata"] = {
        "ordered_values": ["first", "second"],
        "nested": {"future_contract": True},
    }
    ResearchRunEventRepository(cmd_session).append(
        old_run.id,
        stage="scope",
        status="completed",
        message="frozen scope with forward-compatible extension",
        payload_json=old_scope,
    )
    cmd_session.commit()

    view = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )
    assert view.status_code == 200
    assert view.json()["status"] == "failed"

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
    expected_scope = copy.deepcopy(old_scope)
    expected_scope["trigger"] = "retry"
    expected_scope["retried_from_run_id"] = str(old_run.id)
    assert new_scope == expected_scope
    for key in (
        "factor_ids",
        "factor_statements",
        "budget",
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


def test_retry_accepts_cancelled_automatic_run(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    old_run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert old_run is not None and lifecycle is not None
    old_run.status = "cancelled"
    old_run.stage = "stopped"
    old_run.stop_reason = "cancelled"
    lifecycle.status = "exhausted"
    cmd_session.commit()

    assert cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()["status"] == "failed"
    response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    assert response.status_code == 201
    assert response.json()["run_id"] != str(old_run.id)
    cmd_session.refresh(old_run)
    assert old_run.status == "cancelled"


def test_retry_accepts_succeeded_run_whose_result_projection_fails_closed(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created, old_run = _completed_case(cmd_client, cmd_session, monkeypatch)
    tasks = list(
        cmd_session.scalars(
            select(ResearchTask)
            .where(
                ResearchTask.run_id == old_run.id,
                ResearchTask.task_type == "result",
            )
            .order_by(ResearchTask.id)
        )
    )
    assert len(tasks) >= 2
    assert isinstance(tasks[1].result, dict)
    tasks[0].result = {
        "assessment_id": tasks[1].result["assessment_id"],
    }
    cmd_session.commit()

    view = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )
    assert view.status_code == 200
    assert view.json()["status"] == "failed"
    response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    assert response.status_code == 201
    assert response.json()["run_id"] != str(old_run.id)
    cmd_session.refresh(old_run)
    assert old_run.status == "succeeded"


@pytest.mark.pg_only
def test_postgres_duplicate_retry_creates_exactly_one_new_active_run(
    engine, session, monkeypatch
) -> None:
    from threading import Barrier, Event, Thread

    from sqlalchemy.orm import sessionmaker

    from app.errors import ConflictError
    from app.services.auto_research import AutoResearchService
    from app.services.automatic_research_retry import AutomaticResearchRetryService

    started = AutomaticResearchIntakeService(
        session, extractor=_FakeExtractor()
    ).start("automatic retry race", tenant_id="test-team")
    old_run = session.get(ResearchRun, uuid.UUID(started.run_id))
    lifecycle = session.get(
        EventResearchLifecycle, uuid.UUID(started.case_id)
    )
    assert old_run is not None and lifecycle is not None
    old_run.status = "failed"
    old_run.stage = "failed"
    old_run.stop_reason = "no_usable_evidence"
    lifecycle.status = "exhausted"
    session.commit()

    entered_start = Event()
    release_start = Event()
    original_start = AutoResearchService.start

    def pause_first_start(service, *args, **kwargs):
        if not entered_start.is_set():
            entered_start.set()
            assert release_start.wait(timeout=5)
        return original_start(service, *args, **kwargs)

    monkeypatch.setattr(AutoResearchService, "start", pause_first_start)
    session_factory = sessionmaker(bind=engine, future=True)
    ready = Barrier(2)
    outcomes: list[int] = []
    errors: list[BaseException] = []

    def retry() -> None:
        db = session_factory()
        try:
            ready.wait(timeout=5)
            AutomaticResearchRetryService(db).retry(
                uuid.UUID(started.case_id), tenant_id="test-team"
            )
            outcomes.append(201)
        except ConflictError:
            outcomes.append(409)
        except BaseException as exc:
            errors.append(exc)
        finally:
            db.close()

    first = Thread(target=retry)
    second = Thread(target=retry)
    first.start()
    second.start()
    assert entered_start.wait(timeout=5)
    release_start.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert errors == []
    assert sorted(outcomes) == [201, 409]
    session.expire_all()
    runs = list(
        session.scalars(
            select(ResearchRun).where(
                ResearchRun.research_case_id == uuid.UUID(started.case_id)
            )
        )
    )
    assert len(runs) == 2
    assert sum(run.status == "queued" for run in runs) == 1


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
        lambda payload: payload.update({"budget": payload["budget"] + 1}),
        lambda payload: payload["automatic_evidence_plan"]["items"][0].update(
            {"objectives": ["support"]}
        ),
    ],
    ids=["protocol", "factor-order", "plan-budget", "top-level-budget", "plan"],
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

    get_response = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )
    assert get_response.status_code == 409

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


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run, payload: payload.update(
            {"allowed_source_types": ["licensed_provider"]}
        ),
        lambda run, payload: payload.update(
            {"monitor_version_id": str(uuid.uuid4())}
        ),
        lambda run, payload: payload.pop("monitor_version_id"),
        lambda run, payload: payload.update({"frequency": "weekday_08_30"}),
        lambda run, payload: payload.pop("frequency"),
        lambda run, payload: payload.update(
            {"next_verification_event": "provider-secret event"}
        ),
        lambda run, payload: payload.update({"configured_by": "provider-secret"}),
        lambda run, payload: payload.update(
            {"configuration_change_reason": "provider-secret reason"}
        ),
        lambda run, payload: setattr(run, "max_rounds", 0),
        lambda run, payload: setattr(run, "max_rounds", 4),
        lambda run, payload: setattr(run, "budget", 0),
    ],
    ids=[
        "no-monitor-source-types",
        "no-monitor-id",
        "no-monitor-id-missing",
        "no-monitor-frequency",
        "no-monitor-frequency-missing",
        "no-monitor-event",
        "no-monitor-configured-by",
        "no-monitor-reason",
        "max-rounds-zero",
        "max-rounds-four",
        "budget-zero",
    ],
)
def test_get_and_retry_reject_nonreplayable_run_scope_without_new_work(
    cmd_client, cmd_session, monkeypatch, mutate
) -> None:
    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    payload = _scope_payload(cmd_session, run.id)
    mutate(run, payload)
    _fail_run_with_scope(cmd_session, run, payload)

    _assert_get_retry_scope_conflict_without_new_work(
        cmd_client, cmd_session, created["case_id"]
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run, monitor, payload: payload.update(
            {"monitor_version_id": str(uuid.uuid4())}
        ),
        lambda run, monitor, payload: payload.pop("frequency"),
        lambda run, monitor, payload: payload.update({"frequency": "forged"}),
        lambda run, monitor, payload: payload.update(
            {"allowed_source_types": []}
        ),
        lambda run, monitor, payload: payload.update(
            {"next_verification_event": "forged"}
        ),
        lambda run, monitor, payload: payload.update({"configured_by": "forged"}),
        lambda run, monitor, payload: payload.update(
            {"configuration_change_reason": "forged"}
        ),
    ],
    ids=[
        "monitor-id",
        "monitor-frequency-missing",
        "monitor-frequency-wrong",
        "monitor-sources",
        "monitor-event",
        "monitor-configured-by",
        "monitor-reason",
    ],
)
def test_get_and_retry_reject_forged_monitor_snapshot_without_new_work(
    cmd_client, cmd_session, monkeypatch, mutate
) -> None:
    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    payload = _scope_payload(cmd_session, run.id)
    monitor = CaseMonitorVersion(
        research_case_id=run.research_case_id,
        version=1,
        status="active",
        frequency="weekday_08_30",
        factor_ids=list(payload["factor_ids"]),
        allowed_source_types=["licensed_provider"],
        next_verification_event="2026Q4 财报披露",
        budget=run.budget,
        changed_by="human:reviewer",
        change_reason="monitor fixture",
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.add(monitor)
    cmd_session.flush()
    run.monitor_version_id = monitor.id
    payload.update(
        {
            "monitor_version_id": str(monitor.id),
            "allowed_source_types": list(monitor.allowed_source_types),
            "frequency": monitor.frequency,
            "next_verification_event": monitor.next_verification_event,
            "configured_by": monitor.changed_by,
            "configuration_change_reason": monitor.change_reason,
        }
    )
    mutate(run, monitor, payload)
    _fail_run_with_scope(cmd_session, run, payload)

    _assert_get_retry_scope_conflict_without_new_work(
        cmd_client, cmd_session, created["case_id"]
    )


def test_monitor_backed_retry_clones_exact_saved_monitor_scope(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    old_run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert old_run is not None
    old_payload = _scope_payload(cmd_session, old_run.id)
    monitor = CaseMonitorVersion(
        research_case_id=old_run.research_case_id,
        version=1,
        status="active",
        frequency="weekday_08_30",
        factor_ids=list(old_payload["factor_ids"]),
        allowed_source_types=["licensed_provider"],
        next_verification_event="2026Q4 财报披露",
        budget=old_run.budget,
        changed_by="human:reviewer",
        change_reason="monitor fixture",
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.add(monitor)
    cmd_session.flush()
    old_run.monitor_version_id = monitor.id
    old_payload.update(
        {
            "monitor_version_id": str(monitor.id),
            "allowed_source_types": list(monitor.allowed_source_types),
            "frequency": monitor.frequency,
            "next_verification_event": monitor.next_verification_event,
            "configured_by": monitor.changed_by,
            "configuration_change_reason": monitor.change_reason,
        }
    )
    _fail_run_with_scope(cmd_session, old_run, old_payload)

    response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    assert response.status_code == 201, response.text
    new_run = cmd_session.get(ResearchRun, uuid.UUID(response.json()["run_id"]))
    assert new_run is not None
    assert new_run.monitor_version_id == monitor.id
    new_payload = _scope_payload(cmd_session, new_run.id)
    expected = copy.deepcopy(old_payload)
    expected["trigger"] = "retry"
    expected["retried_from_run_id"] = str(old_run.id)
    assert new_payload == expected


@pytest.mark.parametrize(
    "raised",
    [
        ValueError("provider-secret invalid start"),
        ValidationFailedError("provider-secret validation failure"),
    ],
    ids=["value-error", "validation-error"],
)
def test_retry_maps_start_validation_failure_to_safe_scope_conflict(
    cmd_client, cmd_session, monkeypatch, raised
) -> None:
    from app.services.auto_research import AutoResearchService

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    payload = _scope_payload(cmd_session, run.id)
    _fail_run_with_scope(cmd_session, run, payload)
    run_ids_before = list(
        cmd_session.scalars(
            select(ResearchRun.id).where(
                ResearchRun.research_case_id == run.research_case_id
            )
        )
    )
    job_ids_before = list(
        cmd_session.scalars(
            select(Job.id).where(Job.research_case_id == run.research_case_id)
        )
    )

    def fail_start(service, *args, **kwargs):
        raise raised

    monkeypatch.setattr(AutoResearchService, "start", fail_start)
    response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "自动研究范围不可用，请稍后重试"
    assert "provider-secret" not in response.text
    assert list(
        cmd_session.scalars(
            select(ResearchRun.id).where(
                ResearchRun.research_case_id == run.research_case_id
            )
        )
    ) == run_ids_before
    assert list(
        cmd_session.scalars(
            select(Job.id).where(Job.research_case_id == run.research_case_id)
        )
    ) == job_ids_before


@pytest.mark.parametrize(
    ("attribute", "value"),
    [("max_rounds", True), ("budget", True)],
)
def test_scope_validator_rejects_boolean_run_limits(
    cmd_client, cmd_session, monkeypatch, attribute, value
) -> None:
    from app.services.automatic_research_scope import AutomaticResearchScopeError
    from app.services.automatic_research_scope import validate_automatic_research_scope

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    payload = _scope_payload(cmd_session, run.id)
    setattr(run, attribute, value)
    if attribute == "max_rounds":
        payload["automatic_evidence_plan"]["max_rounds"] = 1
    else:
        payload["budget"] = 1
        payload["automatic_evidence_plan"]["budget"] = 1

    with pytest.raises(AutomaticResearchScopeError):
        validate_automatic_research_scope(cmd_session, run, payload)


def test_scope_validator_exposes_stable_frozen_error_reason(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.services.automatic_research_scope import (
        AutomaticResearchScopeError,
        AutomaticResearchScopeReason,
        validate_automatic_research_scope,
    )

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    malformed = _scope_payload(cmd_session, run.id)
    malformed["automatic_protocol"]["generated_by"] = "provider-secret"

    with pytest.raises(AutomaticResearchScopeError) as exc_info:
        validate_automatic_research_scope(cmd_session, run, malformed)

    assert exc_info.value.reason is AutomaticResearchScopeReason.FROZEN_INVALID


def test_validated_scope_exposes_one_immutable_typed_interface(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.services.automatic_research_scope import validate_automatic_research_scope

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    payload = _scope_payload(cmd_session, run.id)

    scope = validate_automatic_research_scope(cmd_session, run, payload)
    snapshot = scope.snapshot()
    snapshot["factor_statements"].append("caller mutation")

    assert scope.factor_ids == tuple(uuid.UUID(value) for value in payload["factor_ids"])
    assert scope.factor_statements == tuple(payload["factor_statements"])
    assert scope.factors[0].objectives == (
        "support",
        "contradict",
        "alternative_explanation",
    )
    assert scope.factors[0].allowed_source_roles == (
        "company_disclosure",
        "licensed_provider",
    )
    assert scope.snapshot() == payload


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
    for link, allow_display, effective_until in (
        (visible_link, True, None),
        (
            hidden_link,
            True,
            datetime(2020, 1, 1, tzinfo=timezone.utc),
        ),
    ):
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
                effective_until=effective_until,
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


def test_professional_workbench_exposes_completed_automatic_result_and_sources(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created, _run = _completed_case(cmd_client, cmd_session, monkeypatch)
    automatic = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()

    response = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/workbench"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["event"]["workflow_mode"] == "automatic"
    assert body["conclusion"]["state"] == "system_generated"
    assert body["conclusion"]["text"] == automatic["result"]["conclusion"]
    assert body["conclusion"]["citations"]
    assert {
        citation["review_state"] for citation in body["conclusion"]["citations"]
    } == {"automatically_admitted"}
    assert {
        evidence["review_state"] for evidence in body["evidence"]
    } == {"automatically_admitted"}
    assert body["progress"]["verified"] == len(body["evidence"])


def test_professional_workbench_keeps_running_automatic_case_machine_labeled(
    cmd_client, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)

    body = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/workbench"
    ).json()

    assert body["event"]["workflow_mode"] == "automatic"
    assert body["conclusion"]["state"] == "cannot_conclude"
    assert body["conclusion"]["text"] == "自动研究正在处理材料并核验证据缺口。"


def test_professional_workbench_binds_automatic_result_to_active_run(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from tests.test_automatic_research_pipeline import (
        _AssessmentGenerator,
        _admit_link,
    )
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    created, old_run = _completed_case(cmd_client, cmd_session, monkeypatch)
    old_conclusion_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"fund-engine:event-research:automatic:{old_run.id}",
    )
    old_conclusion = cmd_session.get(EventResearchConclusion, old_conclusion_id)
    assert old_conclusion is not None
    old_text = old_conclusion.text

    # Append one more valid old-run admission after completion. The immutable
    # conclusion remains history, while its exact-evidence projection now
    # fails closed and can be retried with the same scope.
    old_jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == old_run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    extra_old_link = _admit_link(cmd_session, old_jobs[2])
    extra_old_thesis = cmd_session.get(Thesis, extra_old_link.thesis_id)
    assert extra_old_thesis is not None
    cmd_session.add(
        EventResearchScopeEvidenceAssignment(
            scope_version_id=old_conclusion.scope_version_id,
            evidence_link_id=extra_old_link.id,
            factor_statement=extra_old_thesis.statement,
            disposition="mapped",
            created_at=datetime.now(timezone.utc),
        )
    )
    cmd_session.commit()
    failed = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    assert failed["status"] == "failed"

    retried = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )
    assert retried.status_code == 201, retried.text
    new_run = cmd_session.get(ResearchRun, uuid.UUID(retried.json()["run_id"]))
    assert new_run is not None

    running = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/workbench"
    ).json()
    assert running["lifecycle"]["active_run_id"] == str(new_run.id)
    assert running["conclusion"]["state"] == "cannot_conclude"
    assert running["conclusion"]["text"] == "自动研究正在处理材料并核验证据缺口。"
    assert running["conclusion"]["citations"] == []
    assert running["evidence"] == []
    assert running["progress"]["verified"] == 0
    assert old_text not in running["conclusion"]["text"]

    history = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/conclusion-history"
    ).json()
    assert any(
        version["id"] == str(old_conclusion_id) and version["text"] == old_text
        for version in history["versions"]
    )

    pipeline = AutomaticResearchPipeline(
        cmd_session,
        assessment_generator=_AssessmentGenerator(conclusion="contradicted"),
    )
    assert pipeline.advance(new_run) == "waiting_for_sources"
    jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == new_run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    _admit_link(cmd_session, jobs[0])
    _admit_link(cmd_session, jobs[1])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index < 2 else "failed"
        job.stage = job.status
        job.admitted_count = 1 if index < 2 else 0
    assert pipeline.advance(new_run) == "completed"
    cmd_session.commit()

    completed = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/workbench"
    ).json()
    new_conclusion_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"fund-engine:event-research:automatic:{new_run.id}",
    )
    new_conclusion = cmd_session.get(EventResearchConclusion, new_conclusion_id)
    assert new_conclusion is not None
    assert completed["conclusion"]["state"] == "system_generated"
    assert completed["conclusion"]["text"] == new_conclusion.text
    assert completed["conclusion"]["text"] != old_text
    assert {
        citation["document_version_id"]
        for citation in completed["conclusion"]["citations"]
    } == {
        evidence["document_version_id"] for evidence in completed["evidence"]
    }

    completed_history = cmd_client.get(
        f"/api/v1/event-research/{created['case_id']}/conclusion-history"
    ).json()
    assert {version["id"] for version in completed_history["versions"]} >= {
        str(old_conclusion_id),
        str(new_conclusion_id),
    }


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
    from app.services.automatic_research_scope import (
        AutomaticResearchScopeError,
        AutomaticResearchScopeReason,
        validate_automatic_research_scope,
    )

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

    with pytest.raises(AutomaticResearchScopeError) as exc_info:
        validate_automatic_research_scope(
            cmd_session, run, _scope_payload(cmd_session, run.id)
        )
    assert exc_info.value.reason is AutomaticResearchScopeReason.CURRENT_MISMATCH

    get_response = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )
    retry_response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    for response in (get_response, retry_response):
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "conflict"
        assert error["message"] == "自动研究范围不可用，请稍后重试"
        assert "drift" not in response.text
        assert "reorder" not in response.text


def test_current_scope_missing_get_and_retry_return_same_safe_conflict(
    cmd_client, cmd_session, monkeypatch
) -> None:
    from app.services.automatic_research_scope import (
        AutomaticResearchScopeError,
        AutomaticResearchScopeReason,
        validate_automatic_research_scope,
    )

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == run.research_case_id
        )
    )
    assert run is not None and lifecycle is not None and scope is not None
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "no_usable_evidence"
    lifecycle.status = "exhausted"
    cmd_session.flush()
    cmd_session.connection().exec_driver_sql(
        "DELETE FROM event_research_scope_factors WHERE scope_version_id = ?",
        (scope.id.hex,),
    )
    cmd_session.connection().exec_driver_sql(
        "DELETE FROM event_research_scope_versions WHERE id = ?",
        (scope.id.hex,),
    )
    cmd_session.commit()
    cmd_session.expire_all()

    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    assert run is not None
    with pytest.raises(AutomaticResearchScopeError) as exc_info:
        validate_automatic_research_scope(
            cmd_session, run, _scope_payload(cmd_session, run.id)
        )
    assert exc_info.value.reason is AutomaticResearchScopeReason.CURRENT_MISSING

    get_response = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    )
    retry_response = cmd_client.post(
        f"/api/v1/automatic-research/{created['case_id']}/retry"
    )

    for response in (get_response, retry_response):
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "conflict"
        assert error["message"] == "自动研究范围不可用，请稍后重试"
        assert "missing" not in response.text


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
    stages = _stage_statuses(body)
    assert stages["analyze"] == "failed"
    assert stages["conclude"] == "pending"


def test_completed_projection_rejects_malformed_empty_snapshot_assessment_id(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    tasks = list(
        cmd_session.scalars(
            select(ResearchTask).where(
                ResearchTask.run_id == run.id,
                ResearchTask.task_type == "result",
            )
        )
    )
    empty_task = next(
        task
        for task in tasks
        if isinstance(task.result, dict)
        and not cmd_session.get(
            EvidenceSnapshot,
            cmd_session.get(
                AIAssessment, uuid.UUID(task.result["assessment_id"])
            ).snapshot_id,
        ).evidence_link_ids
    )
    empty_task.result = {"assessment_id": "malformed"}
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()

    assert body["status"] == "failed"
    assert body["result"] is None
    assert _stage_statuses(body)["analyze"] == "failed"


def test_completed_projection_rejects_forged_zero_evidence_conclusion(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == uuid.UUID(
                created["case_id"]
            )
        )
    )
    assert run is not None and lifecycle is not None and scope is not None
    frozen = _scope_payload(cmd_session, run.id)
    thesis_ids = [uuid.UUID(value) for value in frozen["factor_ids"]]
    statements = list(frozen["factor_statements"])
    tasks = list(
        cmd_session.scalars(
            select(ResearchTask).where(
                ResearchTask.run_id == run.id,
                ResearchTask.task_type == "result",
            )
        )
    )
    tasks_by_thesis = {task.thesis_id: task for task in tasks}
    for thesis_id in thesis_ids:
        snapshot = EvidenceSnapshot(
            thesis_id=thesis_id,
            cutoff=datetime.now(timezone.utc),
            evidence_link_ids=[],
            created_at=datetime.now(timezone.utc),
        )
        cmd_session.add(snapshot)
        cmd_session.flush()
        assessment = AIAssessment(
            snapshot_id=snapshot.id,
            conclusion="supported",
            rationale="zero evidence rationale",
            gaps=[],
            displayed_as_provisional=True,
            creator_type="ai",
            model_version="forged",
            created_at=datetime.now(timezone.utc),
        )
        cmd_session.add(assessment)
        cmd_session.flush()
        task = tasks_by_thesis[thesis_id]
        task.round = 1
        task.status = "done"
        task.stage = "completed"
        task.result = {"assessment_id": str(assessment.id)}
    conclusion_lines = [
        f"{statement}：得到当前证据支持。zero evidence rationale"
        for statement in statements
    ]
    conclusion_lines.append(
        "局限：结论仅基于本次冻结范围内自动准入且映射到当前范围的证据。"
        "采集任务：失败 0，取消 0，部分完成 0，部分完成但无准入证据 0，"
        "未执行 0，跳过/异常条目 0。"
    )
    cmd_session.add(
        EventResearchConclusion(
            id=uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"fund-engine:event-research:automatic:{run.id}",
            ),
            research_case_id=run.research_case_id,
            scope_version_id=scope.id,
            state="system_generated",
            text="\n".join(conclusion_lines),
            primary_factor=statements[0],
            evidence_link_ids=[],
            based_on_conclusion_id=None,
            reviewer=None,
            created_at=datetime.now(timezone.utc),
        )
    )
    run.round = 1
    run.status = "succeeded"
    run.stage = "complete"
    run.stop_reason = "automatic_completed"
    lifecycle.status = "completed"
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()

    assert body["status"] == "failed"
    assert body["result"] is None


def test_completed_projection_rejects_nonterminal_validated_source_job(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    job = cmd_session.scalar(
        select(AcquisitionJob).where(
            AcquisitionJob.research_run_id == run.id,
            AcquisitionJob.status == "succeeded",
        )
    )
    assert job is not None
    job.status = "running"
    job.stage = "fetching"
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()

    assert body["status"] == "failed"
    assert body["result"] is None
    assert _stage_statuses(body)["analyze"] == "failed"


@pytest.mark.parametrize("drift", ["duplicate", "reordered"])
def test_completed_projection_rejects_conclusion_evidence_order_drift(
    cmd_client, cmd_session, monkeypatch, drift: str
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    conclusion = cmd_session.get(
        EventResearchConclusion,
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"fund-engine:event-research:automatic:{run.id}",
        ),
    )
    assert conclusion is not None and len(conclusion.evidence_link_ids) >= 2
    evidence_ids = list(conclusion.evidence_link_ids)
    drifted = (
        [*evidence_ids, evidence_ids[0]]
        if drift == "duplicate"
        else list(reversed(evidence_ids))
    )
    cmd_session.connection().exec_driver_sql(
        "UPDATE event_research_conclusions "
        "SET evidence_link_ids = ? WHERE id = ?",
        (json.dumps(drifted), conclusion.id.hex),
    )
    cmd_session.commit()
    cmd_session.expire_all()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()

    assert body["status"] == "failed"
    assert body["result"] is None
    assert _stage_statuses(body)["conclude"] == "failed"


@pytest.mark.parametrize(
    ("conclusion_value", "rationale", "gaps"),
    [
        ("supported", "substituted rationale", ["substituted gap"]),
        ("unsupported-value", "valid rationale", []),
        ("supported", "valid rationale", [1]),
    ],
    ids=["valid-looking-substitution", "invalid-conclusion", "invalid-gaps"],
)
def test_completed_projection_binds_exact_assessment_content(
    cmd_client,
    cmd_session,
    monkeypatch,
    conclusion_value: str,
    rationale: str,
    gaps: list,
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    task = cmd_session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
        )
    )
    assert task is not None and isinstance(task.result, dict)
    original = cmd_session.get(AIAssessment, uuid.UUID(task.result["assessment_id"]))
    assert original is not None
    replacement = AIAssessment(
        snapshot_id=original.snapshot_id,
        conclusion=conclusion_value,
        rationale=rationale,
        gaps=gaps,
        displayed_as_provisional=True,
        creator_type="ai",
        model_version="substituted",
        created_at=datetime.now(timezone.utc),
    )
    cmd_session.add(replacement)
    cmd_session.flush()
    task.result = {**task.result, "assessment_id": str(replacement.id)}
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()

    assert body["status"] == "failed"
    assert body["result"] is None


@pytest.mark.parametrize(
    "conclusion_kind",
    ["missing", "wrong-state", "reviewer-drift"],
)
def test_completed_projection_conclusion_failure_stops_at_conclude(
    cmd_client, cmd_session, monkeypatch, conclusion_kind: str
) -> None:
    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    scope = cmd_session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == uuid.UUID(
                created["case_id"]
            )
        )
    )
    assert run is not None and lifecycle is not None and scope is not None
    if conclusion_kind != "missing":
        cmd_session.add(
            EventResearchConclusion(
                id=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"fund-engine:event-research:automatic:{run.id}",
                ),
                research_case_id=run.research_case_id,
                scope_version_id=scope.id,
                state=(
                    "draft"
                    if conclusion_kind == "wrong-state"
                    else "system_generated"
                ),
                text="forged deterministic conclusion",
                primary_factor=None,
                evidence_link_ids=[],
                based_on_conclusion_id=None,
                reviewer=(
                    "unexpected-reviewer"
                    if conclusion_kind == "reviewer-drift"
                    else None
                ),
                created_at=datetime.now(timezone.utc),
            )
        )
    run.status = "succeeded"
    run.stage = "complete"
    run.stop_reason = "automatic_completed"
    lifecycle.status = "completed"
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert body["status"] == "failed"
    assert stages["conclude"] == "failed"
    assert stages["analyze"] == "completed"


def test_completed_projection_wrong_assessment_stops_at_analyze(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created, run = _completed_case(cmd_client, cmd_session, monkeypatch)
    tasks = list(
        cmd_session.scalars(
            select(ResearchTask)
            .where(
                ResearchTask.run_id == run.id,
                ResearchTask.task_type == "result",
                ResearchTask.status == "done",
            )
            .order_by(ResearchTask.id)
        )
    )
    assert len(tasks) >= 2
    assert isinstance(tasks[0].result, dict) and isinstance(tasks[1].result, dict)
    tasks[0].result = {
        **tasks[0].result,
        "assessment_id": tasks[1].result["assessment_id"],
    }
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert body["status"] == "failed"
    assert stages["analyze"] == "failed"
    assert stages["conclude"] == "pending"


@pytest.mark.parametrize(
    ("job_stage", "expected_stage"),
    [("fetching", "acquire"), ("freezing", "parse"), ("admitting", "admit")],
)
def test_cancelled_run_uses_latest_active_acquisition_stage(
    cmd_client,
    cmd_session,
    monkeypatch,
    job_stage: str,
    expected_stage: str,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    assert AutomaticResearchPipeline(cmd_session).advance(run) == "waiting_for_sources"
    jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.id)
        )
    )
    active, unrelated = jobs[:2]
    now = datetime.now(timezone.utc)
    active.status = "running"
    active.stage = job_stage
    active.updated_at = now
    active_events = list(
        cmd_session.scalars(
            select(AcquisitionJobEvent).where(
                AcquisitionJobEvent.job_id == active.id
            )
        )
    )
    cmd_session.add(
        AcquisitionJobEvent(
            job_id=active.id,
            seq=max((event.seq for event in active_events), default=-1) + 1,
            status="running",
            stage=job_stage,
            message="active acquisition stage",
            payload_json={},
            created_at=now,
        )
    )
    unrelated.status = "failed"
    unrelated.stage = "failed"
    unrelated.updated_at = now + timedelta(seconds=1)
    unrelated_events = list(
        cmd_session.scalars(
            select(AcquisitionJobEvent).where(
                AcquisitionJobEvent.job_id == unrelated.id
            )
        )
    )
    cmd_session.add(
        AcquisitionJobEvent(
            job_id=unrelated.id,
            seq=max((event.seq for event in unrelated_events), default=-1) + 1,
            status="failed",
            stage="failed",
            message="unrelated failed source",
            payload_json={},
            created_at=now + timedelta(seconds=1),
        )
    )
    run.status = "cancelled"
    run.stage = "stopped"
    run.stop_reason = "cancelled"
    lifecycle.status = "exhausted"
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert body["status"] == "failed"
    assert stages[expected_stage] == "failed"
    ordered = ["acquire", "parse", "admit", "analyze", "conclude"]
    later = ordered[ordered.index(expected_stage) + 1 :]
    assert all(stages[key] == "pending" for key in later)


def test_cancelled_run_without_jobs_stops_at_acquire(
    cmd_client, cmd_session, monkeypatch
) -> None:
    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    run.status = "cancelled"
    run.stage = "stopped"
    run.stop_reason = "cancelled"
    lifecycle.status = "exhausted"
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert stages["acquire"] == "failed"
    assert all(
        stages[key] == "pending"
        for key in ("parse", "admit", "analyze", "conclude")
    )


@pytest.mark.parametrize("has_failed_source", [False, True])
def test_cancelled_run_after_terminal_acquisition_stops_at_analyze(
    cmd_client, cmd_session, monkeypatch, has_failed_source: bool
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    created = _start(cmd_client, monkeypatch)
    run = cmd_session.get(ResearchRun, uuid.UUID(created["run_id"]))
    lifecycle = cmd_session.get(
        EventResearchLifecycle, uuid.UUID(created["case_id"])
    )
    assert run is not None and lifecycle is not None
    assert AutomaticResearchPipeline(cmd_session).advance(run) == "waiting_for_sources"
    jobs = list(
        cmd_session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.id)
        )
    )
    assert jobs
    for job in jobs:
        job.status = "succeeded"
        job.stage = "succeeded"
    if has_failed_source:
        failed_job = jobs[0]
        failed_job.status = "failed"
        failed_job.stage = "failed"
        prior_events = list(
            cmd_session.scalars(
                select(AcquisitionJobEvent).where(
                    AcquisitionJobEvent.job_id == failed_job.id
                )
            )
        )
        cmd_session.add(
            AcquisitionJobEvent(
                job_id=failed_job.id,
                seq=max((event.seq for event in prior_events), default=-1) + 1,
                status="failed",
                stage="failed",
                message="unrelated terminal source failure",
                payload_json={},
                created_at=datetime.now(timezone.utc),
            )
        )
    run.status = "cancelled"
    run.stage = "stopped"
    run.stop_reason = "cancelled"
    lifecycle.status = "exhausted"
    cmd_session.commit()

    body = cmd_client.get(
        f"/api/v1/automatic-research/{created['case_id']}"
    ).json()
    stages = _stage_statuses(body)
    assert body["status"] == "failed"
    assert stages["analyze"] == "failed"
    assert stages["conclude"] == "pending"
