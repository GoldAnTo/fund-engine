from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.ledger import (
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    Thesis,
)
from app.models.research_protocol import MetricDefinitionVersion
from app.models.source_governance import SourceContract


METRIC_BODY = {
    "metric_id": "business_line_revenue",
    "display_name": "相关业务收入",
    "canonical_definition": "目标公司指定业务线按季度确认的营业收入",
    "entity_scope": "business_line",
    "unit": "yuan",
    "frequency": "quarterly",
    "period_semantics": "period_end",
    "allowed_source_roles": ["primary_disclosure"],
    "role_eligibility": ["outcome"],
    "reason": "初始指标定义",
}


def _protocol_thesis(session) -> Thesis:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="协议 API Case", industry_topic="ai", created_by="human", created_at=now)
    session.add(case)
    session.flush()
    initial_document = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="https://disclosure.example.org/protocol-admission",
        available_at=now,
        acquired_at=now,
        parser_version="fixture-v1",
    )
    session.add(initial_document)
    session.flush()
    session.add_all([
        CaseDocumentVersion(
            research_case_id=case.id,
            document_version_id=initial_document.id,
            linked_at=now,
        ),
        CaseTenantAdmission(
            research_case_id=case.id,
            tenant_id="test-team",
            initial_document_version_id=initial_document.id,
            admitted_by="test-fixture",
            admitted_at=now,
        ),
    ])
    thesis = Thesis(research_case_id=case.id, statement="相关业务收入将增长", research_protocol_required=True, created_by="human", created_at=now)
    session.add(thesis)
    session.commit()
    return thesis


def _attach_frozen_baseline(session, thesis: Thesis) -> DocumentVersion:
    available_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        source_url="https://disclosure.example.org/api-baseline",
        available_at=available_at,
        acquired_at=available_at,
        parser_version="fixture-v1",
    )
    session.add(document)
    session.flush()
    session.add_all([
        CaseDocumentVersion(research_case_id=thesis.research_case_id, document_version_id=document.id, linked_at=available_at),
        SourceContract(document_version_id=document.id, source_type="uploaded_file", provider_or_tenant="research-team", allow_ai_processing=True, allow_display=True, allow_export=False, allow_api=False, region="cn", effective_from=None, effective_until=None, retention_policy="case_retained", deletion_policy="manual", downstream_restrictions=[], contract_version="fixture-v1", intake_metadata={}, declared_by="human", created_at=available_at),
    ])
    session.commit()
    return document


def test_metric_outcome_binding_and_researchability_api_flow(cmd_client, cmd_session) -> None:
    thesis = _protocol_thesis(cmd_session)
    baseline_document = _attach_frozen_baseline(cmd_session, thesis)
    metric = cmd_client.post("/api/v1/metric-definitions", json=METRIC_BODY)
    assert metric.status_code == 201, metric.text
    assert metric.json()["approved_by"] == "user:test-team"
    metric_id = metric.json()["id"]
    draft = cmd_client.post(
        f"/api/v1/theses/{thesis.id}/outcome-bindings",
        json={
            "metric_definition_id": metric_id,
            "entity_scope": {"company_id": "company-a", "business_line": "800G optics"},
            "direction": "increase",
            "baseline": {"source_ref": f"document:{baseline_document.id}", "value": "10", "unit": "yuan", "observed_period": "2025-12-31", "available_at": "2026-03-01T00:00:00Z"},
            "horizon_start": "2026-04-01",
            "horizon_end": "2026-12-31",
            "reason": "固定结果变量",
        },
    )
    assert draft.status_code == 201, draft.text
    assert draft.json()["state"] == "draft"
    assert draft.json()["reviewer"] == "user:test-team"
    approved = cmd_client.post(
        f"/api/v1/outcome-bindings/{draft.json()['id']}/approve",
        json={"reason": "范围和基线已核对"},
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["reviewer"] == "user:test-team"
    gate = cmd_client.get(f"/api/v1/theses/{thesis.id}/researchability")
    assert gate.status_code == 200
    assert gate.json()["status"] == "blocked"
    assert gate.json()["reason_codes"] == ["missing_mechanism_template"]
    assert cmd_session.get(MetricDefinitionVersion, uuid.UUID(metric_id)) is not None


def test_metric_definition_requires_global_catalog_role(
    cmd_client, monkeypatch
) -> None:
    monkeypatch.setenv(
        "RESEARCH_TENANT_TOKENS",
        '{"ordinary-token":{"tenant_id":"test-team","actor_id":"ordinary"}}',
    )

    response = cmd_client.post(
        "/api/v1/metric-definitions",
        headers={"Authorization": "Bearer ordinary-token"},
        json={**METRIC_BODY, "approved_by": "human:forged-admin"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


def test_metric_definition_rejects_client_approver(cmd_client) -> None:
    response = cmd_client.post(
        "/api/v1/metric-definitions",
        json={**METRIC_BODY, "approved_by": "human:forged-admin"},
    )

    assert response.status_code == 422


def test_mechanism_protocol_uses_server_reviewer_identity(cmd_client, cmd_session) -> None:
    thesis = _protocol_thesis(cmd_session)
    metric = cmd_client.post("/api/v1/metric-definitions", json=METRIC_BODY).json()
    template = cmd_client.get("/api/v1/mechanism-templates").json()[0]

    selected = cmd_client.post(
        f"/api/v1/research-cases/{thesis.research_case_id}/mechanism-selection",
        json={
            "template_version_id": template["id"],
            "reason": "固定机制模板",
        },
    )
    assert selected.status_code == 201, selected.text
    assert selected.json()["reviewer"] == "user:test-team"

    rule = cmd_client.post(
        f"/api/v1/research-cases/{thesis.research_case_id}/mechanism-edges/{template['edges'][0]['id']}/verification-rules",
        json={
            "metric_definition_id": metric["id"],
            "expected_direction": "increase",
            "support_predicate": "收入同比增长",
            "contradiction_predicate": "收入同比下降",
            "allowed_source_roles": ["primary_disclosure"],
            "observed_period_start": "2026-01-01",
            "observed_period_end": "2026-06-30",
            "available_at_deadline": "2026-08-31",
            "next_verification_event": "2026 半年报",
            "reason": "固定验证规则",
        },
    )
    assert rule.status_code == 201, rule.text
    assert rule.json()["reviewer"] == "user:test-team"


def test_outcome_binding_api_rejects_untraceable_baseline(cmd_client, cmd_session) -> None:
    thesis = _protocol_thesis(cmd_session)
    metric = cmd_client.post("/api/v1/metric-definitions", json=METRIC_BODY).json()
    response = cmd_client.post(
        f"/api/v1/theses/{thesis.id}/outcome-bindings",
        json={"metric_definition_id": metric["id"], "entity_scope": {"company_id": "company-a", "business_line": "800G optics"}, "direction": "increase", "baseline": {"value": "10"}, "horizon_start": "2026-04-01", "horizon_end": "2026-12-31", "reason": "不完整"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
