"""Validation and append-only writes for fixed research outcomes."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.models.ledger import Thesis, ValidationError
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
