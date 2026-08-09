from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select, update

from app.ai.assessment_gen import AssessmentGenerator
from app.models.ledger import EvidenceSnapshot, ImmutableLedgerError, ResearchCase, Thesis, ValidationError
from app.models.research_protocol import MechanismNodeVersion, MechanismTemplateVersion
from app.services.mechanism_templates import seed_ai_capex_template
from app.services.research_protocol import (
    MetricDefinitionInput,
    OutcomeBindingInput,
    ResearchProtocolService,
)


def test_mechanism_template_versions_are_append_only(session) -> None:
    template = MechanismTemplateVersion(
        template_key="overseas_ai_capex_to_china_hardware",
        version=1,
        display_name="海外 AI CapEx 到中国硬件",
        industry_scope="ai_hardware",
        approved_by="human:owner",
        reason="初始模板",
        created_at=datetime.now(timezone.utc),
    )
    session.add(template)
    session.flush()

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(MechanismTemplateVersion)
            .where(MechanismTemplateVersion.id == template.id)
            .values(display_name="被改写")
        )


def test_seeded_ai_capex_template_has_required_and_alternative_nodes(session) -> None:
    template = seed_ai_capex_template(session)
    roles = set(session.scalars(
        select(MechanismNodeVersion.role)
        .where(MechanismNodeVersion.template_version_id == template.id)
    ))

    assert {"required_for_outcome", "required_for_attribution", "alternative_explanation", "scope_guard"}.issubset(roles)
    assert seed_ai_capex_template(session).id == template.id


def test_case_template_selection_is_append_only(session) -> None:
    case = ResearchCase(
        title="机制协议 Case", industry_topic="ai", created_by="human", created_at=datetime.now(timezone.utc)
    )
    session.add(case)
    session.flush()
    template = seed_ai_capex_template(session)

    selection = ResearchProtocolService(session).select_template(
        case.id, template.id, reviewer="human:reviewer", reason="适用范围已核对"
    )

    assert selection.research_case_id == case.id
    assert selection.template_version_id == template.id


def test_gate_requires_rules_and_independent_metrics_after_template_selection(session) -> None:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="门槛 Case", industry_topic="ai", created_by="human", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="业务线收入增长", research_protocol_required=True, created_by="human", created_at=now)
    session.add(thesis)
    session.flush()
    service = ResearchProtocolService(session)
    metric = service.add_metric_version(MetricDefinitionInput(
        metric_id="business_line_revenue", display_name="业务线收入", canonical_definition="指定业务线季度收入",
        entity_scope="business_line", unit="yuan", frequency="quarterly", period_semantics="period_end",
        allowed_source_roles=["primary_disclosure"], role_eligibility=["outcome"],
    ), approved_by="human:owner", reason="结果指标")
    binding = service.create_outcome_binding(thesis.id, OutcomeBindingInput(
        metric_definition_id=metric.id, entity_scope={"company_id": "company-a", "business_line": "光模块"},
        direction="increase", baseline={"source_ref": "doc:baseline", "value": "1", "unit": "yuan", "observed_period": "2025-12-31", "available_at": "2026-03-01T00:00:00Z"},
        horizon_start=date(2026, 4, 1), horizon_end=date(2026, 12, 31), reviewer="human", reason="固定结果",
    ))
    service.approve_outcome_binding(binding.id, reviewer="human", reason="审核基线")
    service.select_template(case.id, seed_ai_capex_template(session).id, reviewer="human", reason="适用范围")

    result = service.check_researchability(thesis.id)

    assert result.reason_codes == ["missing_verification_rule", "insufficient_primary_metrics", "missing_counter_hypothesis"]


def test_blocked_protocol_thesis_never_freezes_an_assessment_snapshot(session) -> None:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="拦截 Case", industry_topic="ai", created_by="human", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="未定义结果变量", research_protocol_required=True, created_by="human", created_at=now)
    session.add(thesis)
    session.flush()

    with pytest.raises(ValidationError, match="researchability gate blocked: missing_outcome_binding"):
        AssessmentGenerator(object()).generate(thesis.id, now, session)

    assert list(session.scalars(select(EvidenceSnapshot))) == []
