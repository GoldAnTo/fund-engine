from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy import update

from app.models.ledger import ImmutableLedgerError
from app.models.research_protocol import MetricDefinitionVersion, OutcomeBindingVersion


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
