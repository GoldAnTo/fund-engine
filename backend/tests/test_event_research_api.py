from __future__ import annotations

import uuid

from app.models.event_research import EventResearchBrief, EventResearchFactorDraft
from app.models.ledger import Thesis
from app.models.operational import EventResearchLifecycle, ResearchRun


def _confirmed_event() -> dict:
    return {
        "raw_input": "Alphabet 公布财报后上调全年资本开支指引，盘后股价下跌。",
        "source_url": "https://example.com/alphabet",
        "event_title": "Alphabet 财报后股价下跌",
        "company_name": "Alphabet",
        "ticker": "GOOGL",
        "market_reaction": "盘后下跌",
        "research_question": "资本开支上调是否是盘后下跌的主要因素？",
        "candidate_factors": [
            "资本开支上调可能加剧自由现金流担忧",
            "盈利前景与市场预期可能存在分歧",
            "估值重定价可能放大盘后波动",
        ],
        "created_by": "xiongjiali",
    }


def test_extract_event_keeps_unknown_fields_null_and_marks_confirmation(cmd_client) -> None:
    response = cmd_client.post(
        "/api/v1/event-research/extract",
        json={
            "raw_input": "公司宣布新指引，盘后下跌。",
            "source_url": "https://example.com/brief",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["company_name"] is None
    assert body["ticker"] is None
    assert body["confirmation_required"] is True
    assert body["research_question"]
    assert len(body["candidate_factors"]) in {3, 4, 5}


def test_create_event_case_enqueues_research_without_manual_run_button(cmd_client, cmd_session) -> None:
    response = cmd_client.post("/api/v1/event-research", json=_confirmed_event())

    assert response.status_code == 201
    body = response.json()
    case_id = body["case_id"]
    assert body["lifecycle"]["status"] == "researching"
    assert body["lifecycle"]["active_run_id"]
    assert body["lifecycle"]["next_human_action"] is None

    parsed_case_id = uuid.UUID(case_id)
    assert cmd_session.get(EventResearchBrief, uuid.UUID(body["brief_id"])).research_case_id == parsed_case_id
    assert cmd_session.get(EventResearchLifecycle, parsed_case_id).active_run_id
    assert len(cmd_session.query(EventResearchFactorDraft).filter_by(research_case_id=parsed_case_id).all()) == 3
    assert len(cmd_session.query(Thesis).filter_by(research_case_id=parsed_case_id).all()) == 3
    assert cmd_session.get(ResearchRun, uuid.UUID(body["lifecycle"]["active_run_id"]))


def test_create_event_requires_confirmed_question_and_three_to_five_factors(cmd_client) -> None:
    payload = _confirmed_event()
    payload["candidate_factors"] = payload["candidate_factors"][:2]

    response = cmd_client.post("/api/v1/event-research", json=payload)
    assert response.status_code == 422


def test_event_list_orders_independent_events_by_last_update(cmd_client, cmd_session) -> None:
    first = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    second_payload = _confirmed_event()
    second_payload["event_title"] = "台积电上调 CoWoS 指引后下跌"
    second_payload["ticker"] = "TSM"
    second_payload["research_question"] = "产能扩张是否是价格反应的主要因素？"
    second = cmd_client.post("/api/v1/event-research", json=second_payload).json()

    first_lifecycle = cmd_session.get(EventResearchLifecycle, uuid.UUID(first["case_id"]))
    second_lifecycle = cmd_session.get(EventResearchLifecycle, uuid.UUID(second["case_id"]))
    second_lifecycle.updated_at = first_lifecycle.updated_at.replace(year=first_lifecycle.updated_at.year + 1)
    cmd_session.commit()

    response = cmd_client.get("/api/v1/event-research", params={"status": "researching"})
    assert response.status_code == 200
    body = response.json()
    assert [item["case_id"] for item in body["items"]] == [second["case_id"], first["case_id"]]
    assert body["items"][0]["event_title"] == "台积电上调 CoWoS 指引后下跌"
    assert body["items"][0]["ticker"] == "TSM"
    assert body["items"][0]["lifecycle_status"] == "researching"
    assert body["items"][0]["next_human_action"] is None


def test_event_workbench_never_surfaces_another_case_factors_or_lifecycle(cmd_client) -> None:
    first = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    second_payload = _confirmed_event()
    second_payload["event_title"] = "另一独立新闻事件"
    second_payload["research_question"] = "另一事件的主要因素是什么？"
    second_payload["candidate_factors"] = ["因素甲", "因素乙", "因素丙"]
    second = cmd_client.post("/api/v1/event-research", json=second_payload).json()

    first_view = cmd_client.get(f"/api/v1/event-research/{first['case_id']}/workbench")
    second_view = cmd_client.get(f"/api/v1/event-research/{second['case_id']}/workbench")

    assert first_view.status_code == 200
    assert first_view.json()["event"]["event_title"] == "Alphabet 财报后股价下跌"
    assert {item["statement"] for item in first_view.json()["factors"]} == set(
        _confirmed_event()["candidate_factors"]
    )
    assert second_view.json()["event"]["event_title"] == "另一独立新闻事件"
    assert {item["statement"] for item in second_view.json()["factors"]} == {"因素甲", "因素乙", "因素丙"}
    assert all(item["case_id"] == first["case_id"] for item in first_view.json()["evidence"])


def test_event_workbench_exposes_a_reviewable_draft_when_research_is_ready(cmd_client, cmd_session) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    lifecycle = cmd_session.get(EventResearchLifecycle, uuid.UUID(created["case_id"]))
    lifecycle.status = "draft_ready"
    lifecycle.status_summary = "关键证据已审核，等待结论复核"
    lifecycle.next_human_action = "审核结论草案"
    cmd_session.commit()

    response = cmd_client.get(f"/api/v1/event-research/{created['case_id']}/workbench")

    assert response.status_code == 200
    assert response.json()["conclusion"]["state"] == "ai_draft"
    assert "已审核" in response.json()["conclusion"]["text"]


def test_event_conclusion_publish_appends_a_human_confirmed_result(cmd_client, cmd_session) -> None:
    created = cmd_client.post("/api/v1/event-research", json=_confirmed_event()).json()
    lifecycle = cmd_session.get(EventResearchLifecycle, uuid.UUID(created["case_id"]))
    lifecycle.status = "draft_ready"
    cmd_session.commit()

    response = cmd_client.post(
        f"/api/v1/event-research/{created['case_id']}/conclusion/publish",
        json={"text": "人工确认：当前材料不足以断定唯一原因。", "reviewer": "xiongjiali"},
    )

    assert response.status_code == 201
    assert response.json()["state"] == "published"
    view = cmd_client.get(f"/api/v1/event-research/{created['case_id']}/workbench").json()
    assert view["lifecycle"]["status"] == "published"
    assert view["conclusion"]["state"] == "published"
    assert view["conclusion"]["text"] == "人工确认：当前材料不足以断定唯一原因。"
