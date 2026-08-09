"""Validation and append-only writes for fixed research outcomes."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ResearchCase, Thesis, ValidationError
from app.models.research_protocol import (
    MechanismEdgeVersion,
    MechanismNodeVersion,
    MetricDefinitionVersion,
    OutcomeBindingVersion,
)
from app.repositories.research_protocol import ResearchProtocolRepository


_METRIC_ROLES = frozenset({"outcome", "driver", "mediator", "context"})
_ENTITY_SCOPES = frozenset({"company", "business_line", "product_line"})
_OUTCOME_DIRECTIONS = frozenset({"increase", "decrease", "stable", "mixed"})
_BASELINE_FIELDS = frozenset({"source_ref", "value", "unit", "observed_period", "available_at"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class MetricDefinitionInput:
    metric_id: str
    display_name: str
    canonical_definition: str
    entity_scope: str
    unit: str
    frequency: str
    period_semantics: str
    allowed_source_roles: list[str]
    role_eligibility: list[str]


@dataclass(frozen=True, slots=True)
class OutcomeBindingInput:
    metric_definition_id: uuid.UUID
    entity_scope: dict[str, str]
    direction: str
    baseline: dict[str, str]
    horizon_start: date
    horizon_end: date
    reviewer: str
    reason: str


@dataclass(frozen=True, slots=True)
class VerificationRuleInput:
    metric_definition_id: uuid.UUID
    expected_direction: str
    support_predicate: str
    contradiction_predicate: str
    allowed_source_roles: list[str]
    observed_period_start: date
    observed_period_end: date
    available_at_deadline: date
    next_verification_event: str
    reviewer: str
    reason: str


@dataclass(frozen=True, slots=True)
class ResearchabilityResult:
    status: str
    reason_codes: list[str]
    effective_binding_id: uuid.UUID | None
    next_action: str


def validate_metric_definition(value: MetricDefinitionInput) -> None:
    for name in ("metric_id", "display_name", "canonical_definition", "unit", "frequency", "period_semantics"):
        if not getattr(value, name).strip():
            raise ValidationError(f"metric {name} must not be empty")
    if value.entity_scope not in _ENTITY_SCOPES:
        raise ValidationError("metric entity_scope is invalid")
    if not value.allowed_source_roles or not all(str(role).strip() for role in value.allowed_source_roles):
        raise ValidationError("metric allowed_source_roles must not be empty")
    if not value.role_eligibility or not set(value.role_eligibility).issubset(_METRIC_ROLES):
        raise ValidationError("metric role_eligibility is invalid")


def validate_outcome_binding(metric: MetricDefinitionVersion, value: OutcomeBindingInput) -> None:
    if "outcome" not in metric.role_eligibility:
        raise ValidationError("metric is not outcome eligible")
    if value.direction not in _OUTCOME_DIRECTIONS:
        raise ValidationError("outcome direction is invalid")
    if value.horizon_start > value.horizon_end:
        raise ValidationError("horizon_start must not be after horizon_end")
    if not value.entity_scope.get("company_id") or not value.entity_scope.get(metric.entity_scope):
        raise ValidationError("entity_scope must identify company_id and the metric scope")
    if not _BASELINE_FIELDS.issubset(value.baseline) or value.baseline.get("unit") != metric.unit:
        raise ValidationError("baseline.source_ref/value/unit/observed_period/available_at must match metric")
    if not all(str(value.baseline[field]).strip() for field in _BASELINE_FIELDS):
        raise ValidationError("baseline.source_ref/value/unit/observed_period/available_at must match metric")
    if not value.reviewer.strip() or not value.reason.strip():
        raise ValidationError("binding reviewer and reason must not be empty")


class ResearchProtocolService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ResearchProtocolRepository(session)

    def add_metric_version(
        self,
        value: MetricDefinitionInput,
        *,
        approved_by: str,
        reason: str,
        supersedes_id: uuid.UUID | None = None,
    ) -> MetricDefinitionVersion:
        validate_metric_definition(value)
        if not approved_by.strip() or not reason.strip():
            raise ValidationError("metric approved_by and reason must not be empty")
        try:
            return self._repo.add_metric_version(
                metric_id=value.metric_id.strip(), display_name=value.display_name.strip(), canonical_definition=value.canonical_definition.strip(),
                entity_scope=value.entity_scope, unit=value.unit.strip(), frequency=value.frequency.strip(), period_semantics=value.period_semantics.strip(),
                allowed_source_roles=list(value.allowed_source_roles), role_eligibility=list(value.role_eligibility), approved_by=approved_by.strip(), reason=reason.strip(),
                created_at=_utcnow(), supersedes_id=supersedes_id,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    def create_outcome_binding(self, thesis_id: uuid.UUID, value: OutcomeBindingInput) -> OutcomeBindingVersion:
        thesis = self._session.get(Thesis, thesis_id)
        if thesis is None:
            raise ValidationError("thesis not found")
        metric = self._session.get(MetricDefinitionVersion, value.metric_definition_id)
        if metric is None:
            raise ValidationError("metric definition not found")
        validate_outcome_binding(metric, value)
        return self._repo.add_outcome_binding_version(
            thesis_id=thesis_id, metric_definition_id=metric.id, entity_scope=dict(value.entity_scope), direction=value.direction,
            baseline=dict(value.baseline), horizon_start=value.horizon_start, horizon_end=value.horizon_end, state="draft",
            reviewer=value.reviewer.strip(), reason=value.reason.strip(), created_at=_utcnow(),
        )

    def effective_metric(self, metric_id: str) -> MetricDefinitionVersion | None:
        return self._repo.effective_metric(metric_id)

    def select_template(
        self,
        research_case_id: uuid.UUID,
        template_version_id: uuid.UUID,
        *,
        reviewer: str,
        reason: str,
    ):
        if self._session.get(ResearchCase, research_case_id) is None:
            raise ValidationError("research case not found")
        if self._repo.template(template_version_id) is None:
            raise ValidationError("mechanism template not found")
        if not reviewer.strip() or not reason.strip():
            raise ValidationError("template reviewer and reason must not be empty")
        return self._repo.add_case_template_selection(
            research_case_id=research_case_id,
            template_version_id=template_version_id,
            reviewer=reviewer.strip(),
            reason=reason.strip(),
            created_at=_utcnow(),
        )

    def add_verification_rule(self, mechanism_edge_id: uuid.UUID, value: VerificationRuleInput):
        edge = self._session.get(MechanismEdgeVersion, mechanism_edge_id)
        metric = self._session.get(MetricDefinitionVersion, value.metric_definition_id)
        if edge is None:
            raise ValidationError("mechanism edge not found")
        if metric is None:
            raise ValidationError("verification metric not found")
        if value.expected_direction not in _OUTCOME_DIRECTIONS:
            raise ValidationError("verification expected_direction is invalid")
        if not value.support_predicate.strip() or not value.contradiction_predicate.strip():
            raise ValidationError("verification needs support and contradiction predicates")
        if not value.allowed_source_roles or not set(value.allowed_source_roles).issubset(set(metric.allowed_source_roles)):
            raise ValidationError("verification source roles exceed metric permission")
        if value.observed_period_start > value.observed_period_end:
            raise ValidationError("verification observed period is invalid")
        if not value.next_verification_event.strip() or not value.reviewer.strip() or not value.reason.strip():
            raise ValidationError("verification event, reviewer and reason must not be empty")
        return self._repo.add_verification_rule_version(
            mechanism_edge_id=mechanism_edge_id,
            metric_definition_id=metric.id,
            expected_direction=value.expected_direction,
            support_predicate=value.support_predicate.strip(),
            contradiction_predicate=value.contradiction_predicate.strip(),
            allowed_source_roles=list(value.allowed_source_roles),
            observed_period_start=value.observed_period_start,
            observed_period_end=value.observed_period_end,
            available_at_deadline=value.available_at_deadline,
            next_verification_event=value.next_verification_event.strip(),
            reviewer=value.reviewer.strip(),
            reason=value.reason.strip(),
            created_at=_utcnow(),
        )

    def approve_outcome_binding(
        self, binding_id: uuid.UUID, *, reviewer: str, reason: str
    ) -> OutcomeBindingVersion:
        draft = self._session.get(OutcomeBindingVersion, binding_id)
        if draft is None:
            raise ValidationError("outcome binding not found")
        if draft.state != "draft":
            raise ValidationError("only a draft outcome binding can be approved")
        if not reviewer.strip() or not reason.strip():
            raise ValidationError("binding reviewer and reason must not be empty")
        return self._repo.add_outcome_binding_version(
            thesis_id=draft.thesis_id,
            metric_definition_id=draft.metric_definition_id,
            entity_scope=dict(draft.entity_scope),
            direction=draft.direction,
            baseline=dict(draft.baseline),
            horizon_start=draft.horizon_start,
            horizon_end=draft.horizon_end,
            state="approved",
            reviewer=reviewer.strip(),
            reason=reason.strip(),
            created_at=_utcnow(),
            supersedes_id=draft.id,
        )

    def check_researchability(self, thesis_id: uuid.UUID) -> ResearchabilityResult:
        thesis = self._session.get(Thesis, thesis_id)
        if thesis is None:
            raise ValidationError("thesis not found")
        if not thesis.research_protocol_required:
            return ResearchabilityResult(
                status="not_applicable",
                reason_codes=[],
                effective_binding_id=None,
                next_action="继续既有研究流程",
            )
        binding = self._repo.effective_binding(thesis_id)
        if binding is None:
            return ResearchabilityResult(
                status="blocked",
                reason_codes=["missing_outcome_binding"],
                effective_binding_id=None,
                next_action="确认结果指标、范围、基线和时间窗",
            )
        if binding.state != "approved":
            return ResearchabilityResult(
                status="blocked",
                reason_codes=["binding_not_approved"],
                effective_binding_id=binding.id,
                next_action="审核结果绑定",
            )
        selection = self._repo.effective_case_template(thesis.research_case_id)
        if selection is None:
            return ResearchabilityResult("blocked", ["missing_mechanism_template"], binding.id, "选择已审核机制模板")
        edges = list(self._session.scalars(
            select(MechanismEdgeVersion)
            .where(MechanismEdgeVersion.template_version_id == selection.template_version_id)
        ))
        node_roles = {
            node.id: node.role for node in self._session.scalars(
                select(MechanismNodeVersion)
                .where(MechanismNodeVersion.template_version_id == selection.template_version_id)
            )
        }
        required_edges = [edge for edge in edges if node_roles.get(edge.target_node_id) in {"required_for_outcome", "required_for_attribution"}]
        rules = {edge.id: self._repo.effective_rule(edge.id) for edge in edges}
        reasons: list[str] = []
        if any(rules.get(edge.id) is None for edge in required_edges):
            reasons.append("missing_verification_rule")
        primary_metrics = {rule.metric_definition_id for edge in required_edges if (rule := rules.get(edge.id)) is not None}
        if binding.entity_scope.get("business_line") and len(primary_metrics) < 2:
            reasons.append("insufficient_primary_metrics")
        alternative_edges = [edge for edge in edges if node_roles.get(edge.source_node_id) == "alternative_explanation" or node_roles.get(edge.target_node_id) == "alternative_explanation"]
        if not any(rules.get(edge.id) and rules[edge.id].contradiction_predicate for edge in alternative_edges) and not any(rule and rule.contradiction_predicate for rule in rules.values()):
            reasons.append("missing_counter_hypothesis")
        if reasons:
            return ResearchabilityResult("blocked", reasons, binding.id, "补齐机制边的验证规则与竞争解释")
        return ResearchabilityResult("ready", [], binding.id, "研究协议完整；仍须按规则采集并人工审核证据")
