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

    metric = cmd_client.post("/api/v1/metric-definitions", json={
        "metric_id": "customer_capex", "display_name": "客户 CapEx", "canonical_definition": "客户季度资本开支",
        "entity_scope": "company", "unit": "yuan", "frequency": "quarterly", "period_semantics": "period_end",
        "allowed_source_roles": ["primary_disclosure"], "role_eligibility": ["driver"], "approved_by": "human", "reason": "规则指标",
    })
    assert metric.status_code == 201, metric.text
    rule = cmd_client.post(
        f"/api/v1/research-cases/{case.id}/mechanism-edges/{protocol.json()['template']['edges'][0]['id']}/verification-rules",
        json={"metric_definition_id": metric.json()["id"], "expected_direction": "increase", "support_predicate": "披露的 CapEx 同比增长", "contradiction_predicate": "CapEx 下调", "allowed_source_roles": ["primary_disclosure"], "observed_period_start": "2026-01-01", "observed_period_end": "2026-03-31", "available_at_deadline": "2026-05-31", "next_verification_event": "季度财报", "reviewer": "human:reviewer", "reason": "定义反证规则"},
    )
    assert rule.status_code == 201, rule.text
    assert rule.json()["research_case_id"] == str(case.id)
    assert rule.json()["contradiction_predicate"] == "CapEx 下调"
