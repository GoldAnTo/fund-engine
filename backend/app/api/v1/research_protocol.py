from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.models.research_protocol import MetricDefinitionVersion
from app.schemas.v1.research_protocol import ApproveOutcomeBindingRequest, MetricDefinitionDTO, MetricDefinitionRequest, OutcomeBindingDTO, OutcomeBindingRequest, ResearchabilityDTO
from app.services.research_protocol import MetricDefinitionInput, OutcomeBindingInput, ResearchProtocolService


router = APIRouter(tags=["research-protocol-v1"])


def _metric_dto(value) -> MetricDefinitionDTO:
    return MetricDefinitionDTO(id=str(value.id), metric_id=value.metric_id, version=value.version, display_name=value.display_name, entity_scope=value.entity_scope, unit=value.unit, role_eligibility=list(value.role_eligibility), approved_by=value.approved_by, reason=value.reason, created_at=value.created_at)


def _binding_dto(value) -> OutcomeBindingDTO:
    return OutcomeBindingDTO(id=str(value.id), thesis_id=str(value.thesis_id), metric_definition_id=str(value.metric_definition_id), entity_scope=dict(value.entity_scope), direction=value.direction, baseline=dict(value.baseline), horizon_start=value.horizon_start, horizon_end=value.horizon_end, state=value.state, supersedes_id=str(value.supersedes_id) if value.supersedes_id else None, reviewer=value.reviewer, reason=value.reason, created_at=value.created_at)


@router.post("/metric-definitions", response_model=MetricDefinitionDTO, status_code=status.HTTP_201_CREATED)
def create_metric(payload: MetricDefinitionRequest, db: Session = Depends(get_db)):
    service = ResearchProtocolService(db)
    metric = translate_validation(service.add_metric_version, MetricDefinitionInput(metric_id=payload.metric_id, display_name=payload.display_name, canonical_definition=payload.canonical_definition, entity_scope=payload.entity_scope, unit=payload.unit, frequency=payload.frequency, period_semantics=payload.period_semantics, allowed_source_roles=list(payload.allowed_source_roles), role_eligibility=list(payload.role_eligibility)), approved_by=payload.approved_by, reason=payload.reason)
    commit_or_rollback(db)
    return _metric_dto(metric)


@router.get("/metric-definitions", response_model=list[MetricDefinitionDTO])
def list_metrics(db: Session = Depends(get_db)):
    return [_metric_dto(value) for value in db.scalars(select(MetricDefinitionVersion).order_by(MetricDefinitionVersion.metric_id, MetricDefinitionVersion.version.desc()))]


@router.post("/theses/{thesis_id}/outcome-bindings", response_model=OutcomeBindingDTO, status_code=status.HTTP_201_CREATED)
def create_binding(thesis_id: uuid.UUID, payload: OutcomeBindingRequest, db: Session = Depends(get_db)):
    service = ResearchProtocolService(db)
    binding = translate_validation(service.create_outcome_binding, thesis_id, OutcomeBindingInput(metric_definition_id=payload.metric_definition_id, entity_scope=dict(payload.entity_scope), direction=payload.direction, baseline=dict(payload.baseline), horizon_start=payload.horizon_start, horizon_end=payload.horizon_end, reviewer=payload.reviewer, reason=payload.reason))
    commit_or_rollback(db)
    return _binding_dto(binding)


@router.post("/outcome-bindings/{binding_id}/approve", response_model=OutcomeBindingDTO, status_code=status.HTTP_201_CREATED)
def approve_binding(binding_id: uuid.UUID, payload: ApproveOutcomeBindingRequest, db: Session = Depends(get_db)):
    binding = translate_validation(ResearchProtocolService(db).approve_outcome_binding, binding_id, reviewer=payload.reviewer, reason=payload.reason)
    commit_or_rollback(db)
    return _binding_dto(binding)


@router.get("/theses/{thesis_id}/researchability", response_model=ResearchabilityDTO)
def researchability(thesis_id: uuid.UUID, db: Session = Depends(get_db)):
    result = translate_validation(ResearchProtocolService(db).check_researchability, thesis_id)
    return ResearchabilityDTO(status=result.status, reason_codes=result.reason_codes, effective_binding_id=str(result.effective_binding_id) if result.effective_binding_id else None, next_action=result.next_action)
