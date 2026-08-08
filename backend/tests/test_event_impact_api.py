from __future__ import annotations

import uuid


def test_impact_trace_reads_only_the_current_scope(cmd_client) -> None:
    created = cmd_client.post(
        "/api/v1/event-research",
        json={
            "raw_input": "event fixture",
            "event_title": "Impact trace fixture",
            "research_question": "what transmits?",
            "candidate_factors": ["factor one", "factor two", "factor three"],
            "created_by": "tester",
        },
    )
    assert created.status_code == 201
    case_id = uuid.UUID(created.json()["case_id"])

    response = cmd_client.get(f"/api/v1/event-research/{case_id}/impact-trace")

    assert response.status_code == 200
    body = response.json()
    assert body["scope_version"] == 1
    assert body["factors"] == []
    assert body["alternatives"] == []
