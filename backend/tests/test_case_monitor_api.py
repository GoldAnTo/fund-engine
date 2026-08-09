from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.ledger import ResearchCase, Thesis
from app.models.research_monitor import CaseMonitorVersion, ResearchRunEvent  # noqa: F401


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


def test_monitor_rejects_unsupported_source_type(cmd_client, cmd_session) -> None:
    case, factor = _case_with_confirmed_factor(cmd_session)

    response = cmd_client.put(
        f"/api/v1/research-cases/{case.id}/monitor",
        json=_monitor_payload(factor.id, allowed_source_types=["unlicensed_scrape"]),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
