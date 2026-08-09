from __future__ import annotations

from datetime import datetime, timezone

from app.models.ledger import ResearchCase


def test_case_protocol_api_lists_templates_and_appends_selection(cmd_client, cmd_session) -> None:
    case = ResearchCase(
        title="机制 API Case", industry_topic="ai", created_by="human", created_at=datetime.now(timezone.utc)
    )
    cmd_session.add(case)
    cmd_session.commit()

    templates = cmd_client.get("/api/v1/mechanism-templates")
    assert templates.status_code == 200, templates.text
    assert templates.json()[0]["template_key"] == "overseas_ai_capex_to_china_hardware"

    selection = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/mechanism-selection",
        json={"template_version_id": templates.json()[0]["id"], "reviewer": "human:reviewer", "reason": "适用范围已核对"},
    )
    assert selection.status_code == 201, selection.text
    assert selection.json()["template_version_id"] == templates.json()[0]["id"]

    protocol = cmd_client.get(f"/api/v1/research-cases/{case.id}/mechanism-protocol")
    assert protocol.status_code == 200, protocol.text
    assert protocol.json()["selection"]["reason"] == "适用范围已核对"
    assert protocol.json()["template"]["edges"]
