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
