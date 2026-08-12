"""Small complete protocol footprint used by assessment provenance tests."""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace
import uuid

from app.models.research_protocol import (
    CaseMechanismSelectionVersion,
    MechanismEdgeVersion,
    MechanismNodeVersion,
    MechanismTemplateVersion,
    MetricDefinitionVersion,
    OutcomeBindingVersion,
    VerificationRuleVersion,
)


def seed_protocol_footprint(session, thesis):
    now = datetime.now(timezone.utc)
    suffix = uuid.uuid4().hex
    metric = MetricDefinitionVersion(
        metric_id=f"assessment_protocol_{suffix}",
        version=1,
        display_name="Assessment protocol metric",
        canonical_definition="A test-only frozen protocol metric",
        entity_scope="business_line",
        unit="yuan",
        frequency="quarterly",
        period_semantics="period_end",
        allowed_source_roles=["primary_disclosure"],
        role_eligibility=["outcome"],
        approved_by="tester",
        reason="assessment provenance fixture",
        created_at=now,
    )
    template = MechanismTemplateVersion(
        template_key=f"assessment_protocol_{suffix}",
        version=1,
        display_name="Assessment protocol template",
        industry_scope="test",
        approved_by="tester",
        reason="assessment provenance fixture",
        created_at=now,
    )
    session.add_all([metric, template])
    session.flush()
    binding = OutcomeBindingVersion(
        thesis_id=thesis.id,
        metric_definition_id=metric.id,
        entity_scope={"company_id": "company-a", "business_line": "test"},
        direction="increase",
        baseline={
            "source_ref": "fixture",
            "value": "1",
            "unit": "yuan",
            "observed_period": "2025-12-31",
            "available_at": "2026-03-01T00:00:00Z",
        },
        horizon_start=date(2026, 4, 1),
        horizon_end=date(2026, 12, 31),
        state="approved",
        reviewer="tester",
        reason="assessment provenance fixture",
        created_at=now,
    )
    source = MechanismNodeVersion(
        template_version_id=template.id,
        node_key="source",
        display_name="Source",
        role="required_for_attribution",
        created_at=now,
    )
    target = MechanismNodeVersion(
        template_version_id=template.id,
        node_key="target",
        display_name="Target",
        role="required_for_outcome",
        created_at=now,
    )
    selection = CaseMechanismSelectionVersion(
        research_case_id=thesis.research_case_id,
        template_version_id=template.id,
        reviewer="tester",
        reason="assessment provenance fixture",
        created_at=now,
    )
    session.add_all([binding, selection, source, target])
    session.flush()
    edges = [
        MechanismEdgeVersion(
            template_version_id=template.id,
            edge_key=f"source_to_target_{index}",
            source_node_id=source.id,
            target_node_id=target.id,
            created_at=now,
        )
        for index in range(2)
    ]
    session.add_all(edges)
    session.flush()
    rules = []
    for index, edge in enumerate(edges):
        rule = VerificationRuleVersion(
            research_case_id=thesis.research_case_id,
            mechanism_edge_id=edge.id,
            metric_definition_id=metric.id,
            expected_direction="increase",
            support_predicate=f"support {index}",
            contradiction_predicate=f"contradiction {index}",
            allowed_source_roles=["primary_disclosure"],
            observed_period_start=date(2026, 4, 1),
            observed_period_end=date(2026, 6, 30),
            available_at_deadline=date(2026, 8, 31),
            next_verification_event=f"event {index}",
            reviewer="tester",
            reason="assessment provenance fixture",
            created_at=now,
        )
        session.add(rule)
        session.flush()
        rules.append(rule)
    return SimpleNamespace(binding=binding, template=template, rules=rules)
