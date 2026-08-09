from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import update

from app.models.ledger import ImmutableLedgerError, ResearchCase, Thesis, ValidationError
from app.models.research_protocol import MetricDefinitionVersion, OutcomeBindingVersion
from app.services.research_protocol import MetricDefinitionInput, OutcomeBindingInput, ResearchProtocolService


def test_metric_versions_and_outcome_bindings_are_append_only(session, thesis) -> None:
    now = datetime.now(timezone.utc)
    metric = MetricDefinitionVersion(
        metric_id="business_line_revenue",
        version=1,
        display_name="相关业务收入",
        canonical_definition="目标公司指定业务线按季度确认的营业收入",
        entity_scope="business_line",
        unit="yuan",
        frequency="quarterly",
        period_semantics="period_end",
        allowed_source_roles=["primary_disclosure"],
        role_eligibility=["outcome"],
        approved_by="human:researcher",
        reason="首次定义",
        created_at=now,
    )
    session.add(metric)
    session.flush()
    binding = OutcomeBindingVersion(
        thesis_id=thesis.id,
        metric_definition_id=metric.id,
        entity_scope={"company_id": "company-a", "business_line": "800G optics"},
        direction="increase",
        baseline={
            "source_ref": "doc:baseline-1",
            "value": "10",
            "unit": "yuan",
            "observed_period": "2025-12-31",
            "available_at": "2026-03-01T00:00:00Z",
        },
        horizon_start=date(2026, 4, 1),
        horizon_end=date(2026, 12, 31),
        state="draft",
        reviewer="human:researcher",
        reason="首次绑定",
        created_at=now,
    )
    session.add(binding)
    session.flush()

    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(OutcomeBindingVersion)
            .where(OutcomeBindingVersion.id == binding.id)
            .values(direction="decrease")
        )
    with pytest.raises(ImmutableLedgerError):
        session.execute(
            update(MetricDefinitionVersion)
            .where(MetricDefinitionVersion.id == metric.id)
            .values(display_name="被改写")
        )


def test_legacy_thesis_does_not_require_research_protocol_by_default(thesis) -> None:
    assert thesis.research_protocol_required is False


def _metric_input(*, roles: list[str] | None = None) -> MetricDefinitionInput:
    return MetricDefinitionInput(
        metric_id="business_line_revenue",
        display_name="相关业务收入",
        canonical_definition="目标公司指定业务线按季度确认的营业收入",
        entity_scope="business_line",
        unit="yuan",
        frequency="quarterly",
        period_semantics="period_end",
        allowed_source_roles=["primary_disclosure"],
        role_eligibility=roles or ["outcome"],
    )


def _binding_input(metric_id) -> OutcomeBindingInput:
    return OutcomeBindingInput(
        metric_definition_id=metric_id,
        entity_scope={"company_id": "company-a", "business_line": "800G optics"},
        direction="increase",
        baseline={"source_ref": "doc:baseline-1", "value": "10", "unit": "yuan", "observed_period": "2025-12-31", "available_at": "2026-03-01T00:00:00Z"},
        horizon_start=date(2026, 4, 1),
        horizon_end=date(2026, 12, 31),
        reviewer="human:researcher",
        reason="结果变量确认",
    )


def test_outcome_binding_requires_an_outcome_eligible_metric(session, thesis) -> None:
    service = ResearchProtocolService(session)
    driver_only = service.add_metric_version(_metric_input(roles=["driver"]), approved_by="human:owner", reason="驱动指标")

    with pytest.raises(ValidationError, match="outcome eligible"):
        service.create_outcome_binding(thesis.id, _binding_input(driver_only.id))


def test_outcome_binding_rejects_invalid_horizon_and_untraceable_baseline(session, thesis) -> None:
    service = ResearchProtocolService(session)
    metric = service.add_metric_version(_metric_input(), approved_by="human:owner", reason="结果指标")
    inverted = _binding_input(metric.id)
    inverted = replace(inverted, horizon_start=date(2026, 6, 30), horizon_end=date(2026, 3, 31))
    with pytest.raises(ValidationError, match="horizon_start"):
        service.create_outcome_binding(thesis.id, inverted)
    incomplete = _binding_input(metric.id)
    incomplete = replace(incomplete, baseline={"value": "10"})
    with pytest.raises(ValidationError, match="baseline.source_ref"):
        service.create_outcome_binding(thesis.id, incomplete)


def test_effective_metric_uses_the_latest_append_only_version(session) -> None:
    service = ResearchProtocolService(session)
    first = service.add_metric_version(_metric_input(), approved_by="human:owner", reason="初版")
    second = service.add_metric_version(_metric_input(), approved_by="human:owner", reason="范围澄清", supersedes_id=first.id)

    assert (first.version, second.version) == (1, 2)
    assert service.effective_metric("business_line_revenue").id == second.id


def _protocol_thesis(session) -> Thesis:
    now = datetime.now(timezone.utc)
    case = ResearchCase(title="协议 Case", industry_topic="ai", created_by="human", created_at=now)
    session.add(case)
    session.flush()
    thesis = Thesis(research_case_id=case.id, statement="相关业务收入将增长", research_protocol_required=True, created_by="human", created_at=now)
    session.add(thesis)
    session.flush()
    return thesis


def test_researchability_is_not_applicable_to_legacy_thesis(session, thesis) -> None:
    result = ResearchProtocolService(session).check_researchability(thesis.id)

    assert result.status == "not_applicable"
    assert result.reason_codes == []


def test_protocol_thesis_without_outcome_binding_is_explicitly_blocked(session) -> None:
    thesis = _protocol_thesis(session)
    result = ResearchProtocolService(session).check_researchability(thesis.id)

    assert result.status == "blocked"
    assert result.reason_codes == ["missing_outcome_binding"]


def test_approved_outcome_binding_exposes_remaining_protocol_blockers(session) -> None:
    thesis = _protocol_thesis(session)
    service = ResearchProtocolService(session)
    metric = service.add_metric_version(_metric_input(), approved_by="human:owner", reason="结果指标")
    draft = service.create_outcome_binding(thesis.id, _binding_input(metric.id))
    service.approve_outcome_binding(draft.id, reviewer="human:reviewer", reason="范围和基线已核对")

    result = service.check_researchability(thesis.id)

    assert result.status == "blocked"
    assert result.reason_codes == [
        "missing_mechanism_template",
        "missing_verification_rule",
        "insufficient_primary_metrics",
        "missing_counter_hypothesis",
    ]
