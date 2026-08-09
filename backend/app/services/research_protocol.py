"""Validation and append-only writes for fixed research outcomes."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.ledger import ResearchCase, Thesis, ValidationError
from app.models.research_protocol import MetricDefinitionVersion, OutcomeBindingVersion
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
        return ResearchabilityResult(
            status="blocked",
            reason_codes=[
                "missing_mechanism_template",
                "missing_verification_rule",
                "insufficient_primary_metrics",
                "missing_counter_hypothesis",
            ],
            effective_binding_id=binding.id,
            next_action="选择机制模板并补齐可验证规则",
        )
