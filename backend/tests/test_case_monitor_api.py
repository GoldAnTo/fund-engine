from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.ledger import ResearchCase, Thesis
from app.models.operational import ResearchRun
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent  # noqa: F401
from app.models.research_expression import KeyFactor


def _case_with_confirmed_factor(cmd_session) -> tuple[ResearchCase, Thesis]:
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="API 透明运行测试",
        industry_topic="事件研究",
        created_by="tester",
        created_at=now,
    )
    cmd_session.add(case)
    cmd_session.flush()
    factor = Thesis(
        research_case_id=case.id,
        statement="订单指引是否高于市场预期",
        created_by="human:lin",
        created_at=now,
        creator_type="human",
        review_state="confirmed",
    )
    cmd_session.add(factor)
    cmd_session.commit()
    return case, factor


def _monitor_payload(factor_id: uuid.UUID, **overrides) -> dict:
    payload = {
        "actor": "human:lin",
        "frequency": "weekday_08_30",
        "factor_ids": [str(factor_id)],
        "allowed_source_types": ["licensed_provider"],
        "next_verification_event": "2026Q1 财报披露",
        "budget": 20,
        "change_reason": "建立可复现的监控范围",
    }
    payload.update(overrides)
    return payload


def test_monitor_read_update_and_run_events_are_transparent(cmd_client, cmd_session) -> None:
    case, factor = _case_with_confirmed_factor(cmd_session)

    saved = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/monitor",
        json=_monitor_payload(factor.id),
    )

    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["allowed_source_types"] == ["licensed_provider"]
    assert saved.json()["factor_ids"] == [str(factor.id)]

    effective = cmd_client.get(f"/api/v1/research-cases/{case.id}/monitor")
    assert effective.status_code == 200
    assert effective.json()["monitor"]["id"] == saved.json()["id"]
    assert effective.json()["latest_run"] is None
    assert effective.json()["next_scheduled_at"] is not None
    assert effective.json()["confirmed_factors"] == [
        {"id": str(factor.id), "statement": factor.statement}
    ]

    started = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/runs",
        json={"max_rounds": 1, "budget": 20},
    )
    assert started.status_code == 201, started.text
    events = cmd_client.get(f"/api/v1/research-runs/{started.json()['id']}/events")
    assert events.status_code == 200
    item = events.json()["items"][0]
    assert item["seq"] == 1
    assert item["stage"] == "scope"
    assert item["details"]["monitor_version_id"] == saved.json()["id"]
    assert item["details"]["factor_ids"] == [str(factor.id)]


def test_manual_monitor_run_uses_the_saved_version_not_caller_options(
    cmd_client, cmd_session
) -> None:
    case, factor = _case_with_confirmed_factor(cmd_session)
    first = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/monitor",
        json=_monitor_payload(
            factor.id,
            allowed_source_types=["company_disclosure"],
            budget=7,
        ),
    )
    assert first.status_code == 200, first.text

    started = cmd_client.post(f"/api/v1/research-cases/{case.id}/monitor/runs")

    assert started.status_code == 201, started.text
    first_scope = cmd_client.get(
        f"/api/v1/research-runs/{started.json()['id']}/events"
    ).json()["items"][0]["details"]
    assert first_scope == {
        "trigger": "manual",
        "monitor_version_id": first.json()["id"],
        "factor_ids": [str(factor.id)],
        "factor_statements": [factor.statement],
        "allowed_source_types": ["company_disclosure"],
        "budget": 7,
    }

    second = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/monitor",
        json=_monitor_payload(
            factor.id,
            allowed_source_types=["uploaded_file"],
            budget=13,
            change_reason="改为人工上传补证",
        ),
    )
    assert second.status_code == 200, second.text
    next_started = cmd_client.post(f"/api/v1/research-cases/{case.id}/monitor/runs")
    assert next_started.status_code == 201, next_started.text
    second_scope = cmd_client.get(
        f"/api/v1/research-runs/{next_started.json()['id']}/events"
    ).json()["items"][0]["details"]
    assert second_scope["monitor_version_id"] == second.json()["id"]
    assert second_scope["allowed_source_types"] == ["uploaded_file"]
    assert second_scope["budget"] == 13

    replayed_first_scope = cmd_client.get(
        f"/api/v1/research-runs/{started.json()['id']}/events"
    ).json()["items"][0]["details"]
    assert replayed_first_scope == first_scope


def test_factor_monitor_run_requires_an_explicit_reviewed_factor_to_thesis_link(
    cmd_client, cmd_session
) -> None:
    case, factor = _case_with_confirmed_factor(cmd_session)
    saved = cmd_client.put(f"/api/v1/research-cases/{case.id}/monitor", json=_monitor_payload(factor.id))
    now = datetime.now(timezone.utc)
    key_factor = KeyFactor(
        research_case_id=case.id,
        thesis_id=factor.id,
        report_claim_id=None,
        name="订单指引",
        expected_direction="positive",
        metric_name="订单金额",
        allowed_source_types=["licensed_provider"],
        support_condition="订单增长",
        refutation_condition="订单下降",
        next_verification_event="下一次财报",
        review_state="reviewed",
        reviewed_by="human:lin",
        review_reason="已审核并绑定当前 Case 的研究范围因素",
        reviewed_at=now,
        created_at=now,
    )
    cmd_session.add(key_factor)
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/monitor/factor-runs",
        json={"key_factor_id": str(key_factor.id)},
    )

    assert response.status_code == 201, response.text
    events = cmd_client.get(f"/api/v1/research-runs/{response.json()['id']}/events").json()["items"]
    assert events[0]["details"]["factor_ids"] == [str(factor.id)]
    assert events[0]["details"]["requested_key_factor_id"] == str(key_factor.id)


def test_manual_monitor_run_requires_a_saved_monitor(cmd_client, cmd_session) -> None:
    case, _factor = _case_with_confirmed_factor(cmd_session)

    response = cmd_client.post(f"/api/v1/research-cases/{case.id}/monitor/runs")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_monitor_rejects_unsupported_source_type(cmd_client, cmd_session) -> None:
    case, factor = _case_with_confirmed_factor(cmd_session)

    response = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/monitor",
        json=_monitor_payload(factor.id, allowed_source_types=["unlicensed_scrape"]),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_active_runs_expose_case_and_frozen_scope_without_reconstructing_current_config(
    cmd_client, cmd_session
) -> None:
    case, factor = _case_with_confirmed_factor(cmd_session)
    saved = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/monitor",
        json=_monitor_payload(factor.id, allowed_source_types=["company_disclosure"]),
    )
    assert saved.status_code == 200
    started = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/runs",
        json={"max_rounds": 1, "budget": 20},
    )
    assert started.status_code == 201

    response = cmd_client.get("/api/v1/research-runs/active")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["run_id"] == started.json()["id"]
    assert item["case_id"] == str(case.id)
    assert item["scope"]["monitor_version_id"] == saved.json()["id"]
    assert item["scope"]["allowed_source_types"] == ["company_disclosure"]
    assert item["scope"]["factor_statements"] == [factor.statement]


def test_global_run_archive_keeps_terminal_run_and_its_frozen_scope(
    cmd_client, cmd_session
) -> None:
    case, factor = _case_with_confirmed_factor(cmd_session)
    saved = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/monitor",
        json=_monitor_payload(factor.id, allowed_source_types=["company_disclosure"]),
    )
    started = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/runs",
        json={"max_rounds": 1, "budget": 20},
    )
    run = cmd_session.get(ResearchRun, uuid.UUID(started.json()["id"]))
    run.status = "failed"
    run.stage = "failed"
    run.stop_reason = "task_failed"
    cmd_session.commit()

    response = cmd_client.get("/api/v1/research-runs")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["run_id"] == started.json()["id"]
    assert item["status"] == "failed"
    assert item["stop_reason"] == "task_failed"
    assert item["scope"]["monitor_version_id"] == saved.json()["id"]
    assert item["scope"]["allowed_source_types"] == ["company_disclosure"]
