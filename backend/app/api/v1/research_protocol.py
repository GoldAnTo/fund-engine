from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.models.research_protocol import (
    MechanismEdgeVersion,
    MechanismNodeVersion,
    MechanismTemplateVersion,
    MetricDefinitionVersion,
    VerificationRuleVersion,
)
from app.repositories.research_protocol import ResearchProtocolRepository
from app.schemas.v1.research_protocol import (
    ApproveOutcomeBindingRequest, CaseMechanismProtocolDTO, MechanismEdgeDTO,
    MechanismNodeDTO, MechanismSelectionDTO, MechanismTemplateDTO,
    MetricDefinitionDTO, MetricDefinitionRequest, OutcomeBindingDTO,
    OutcomeBindingRequest, ResearchabilityDTO, SelectMechanismTemplateRequest,
    VerificationRuleDTO, VerificationRuleRequest,
)
from app.services.mechanism_templates import seed_ai_capex_template
from app.services.research_protocol import (
    MetricDefinitionInput, OutcomeBindingInput, ResearchProtocolService,
    VerificationRuleInput,
)


router = APIRouter(tags=["research-protocol-v1"])


def _metric_dto(value) -> MetricDefinitionDTO:
    return MetricDefinitionDTO(id=str(value.id), metric_id=value.metric_id, version=value.version, display_name=value.display_name, entity_scope=value.entity_scope, unit=value.unit, role_eligibility=list(value.role_eligibility), approved_by=value.approved_by, reason=value.reason, created_at=value.created_at)


def _binding_dto(value) -> OutcomeBindingDTO:
    return OutcomeBindingDTO(id=str(value.id), thesis_id=str(value.thesis_id), metric_definition_id=str(value.metric_definition_id), entity_scope=dict(value.entity_scope), direction=value.direction, baseline=dict(value.baseline), horizon_start=value.horizon_start, horizon_end=value.horizon_end, state=value.state, supersedes_id=str(value.supersedes_id) if value.supersedes_id else None, reviewer=value.reviewer, reason=value.reason, created_at=value.created_at)


def _template_dto(db: Session, value: MechanismTemplateVersion) -> MechanismTemplateDTO:
    nodes = list(db.scalars(select(MechanismNodeVersion).where(MechanismNodeVersion.template_version_id == value.id).order_by(MechanismNodeVersion.node_key)))
    edges = list(db.scalars(select(MechanismEdgeVersion).where(MechanismEdgeVersion.template_version_id == value.id).order_by(MechanismEdgeVersion.edge_key)))
    return MechanismTemplateDTO(id=str(value.id), template_key=value.template_key, version=value.version, display_name=value.display_name, industry_scope=value.industry_scope, approved_by=value.approved_by, reason=value.reason, created_at=value.created_at, nodes=[MechanismNodeDTO(id=str(node.id), node_key=node.node_key, display_name=node.display_name, role=node.role) for node in nodes], edges=[MechanismEdgeDTO(id=str(edge.id), edge_key=edge.edge_key, source_node_id=str(edge.source_node_id), target_node_id=str(edge.target_node_id)) for edge in edges])


def _selection_dto(value) -> MechanismSelectionDTO:
    return MechanismSelectionDTO(id=str(value.id), research_case_id=str(value.research_case_id), template_version_id=str(value.template_version_id), supersedes_id=str(value.supersedes_id) if value.supersedes_id else None, reviewer=value.reviewer, reason=value.reason, created_at=value.created_at)


def _rule_dto(value) -> VerificationRuleDTO:
    return VerificationRuleDTO(id=str(value.id), mechanism_edge_id=str(value.mechanism_edge_id), metric_definition_id=str(value.metric_definition_id), expected_direction=value.expected_direction, support_predicate=value.support_predicate, contradiction_predicate=value.contradiction_predicate, allowed_source_roles=list(value.allowed_source_roles), observed_period_start=value.observed_period_start, observed_period_end=value.observed_period_end, available_at_deadline=value.available_at_deadline, next_verification_event=value.next_verification_event, supersedes_id=str(value.supersedes_id) if value.supersedes_id else None, reviewer=value.reviewer, reason=value.reason, created_at=value.created_at)


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


@router.get("/mechanism-templates", response_model=list[MechanismTemplateDTO])
def list_mechanism_templates(db: Session = Depends(get_db)):
    seed_ai_capex_template(db)
    db.commit()
    return [_template_dto(db, value) for value in db.scalars(select(MechanismTemplateVersion).order_by(MechanismTemplateVersion.template_key, MechanismTemplateVersion.version.desc()))]


@router.post("/research-cases/{case_id}/mechanism-selection", response_model=MechanismSelectionDTO, status_code=status.HTTP_201_CREATED)
def select_mechanism_template(case_id: uuid.UUID, payload: SelectMechanismTemplateRequest, db: Session = Depends(get_db)):
    selection = translate_validation(ResearchProtocolService(db).select_template, case_id, payload.template_version_id, reviewer=payload.reviewer, reason=payload.reason)
    commit_or_rollback(db)
    return _selection_dto(selection)


@router.get("/research-cases/{case_id}/mechanism-protocol", response_model=CaseMechanismProtocolDTO)
def case_mechanism_protocol(case_id: uuid.UUID, db: Session = Depends(get_db)):
    repo = ResearchProtocolRepository(db)
    selection = repo.effective_case_template(case_id)
    if selection is None:
        return CaseMechanismProtocolDTO(selection=None, template=None, rules=[])
    template = repo.template(selection.template_version_id)
    edge_ids = [edge.id for edge in db.scalars(select(MechanismEdgeVersion).where(MechanismEdgeVersion.template_version_id == selection.template_version_id))]
    rules = [_rule_dto(rule) for edge_id in edge_ids if (rule := repo.effective_rule(edge_id)) is not None]
    return CaseMechanismProtocolDTO(selection=_selection_dto(selection), template=_template_dto(db, template), rules=rules)


@router.post("/mechanism-edges/{edge_id}/verification-rules", response_model=VerificationRuleDTO, status_code=status.HTTP_201_CREATED)
def create_verification_rule(edge_id: uuid.UUID, payload: VerificationRuleRequest, db: Session = Depends(get_db)):
    rule = translate_validation(ResearchProtocolService(db).add_verification_rule, edge_id, VerificationRuleInput(metric_definition_id=payload.metric_definition_id, expected_direction=payload.expected_direction, support_predicate=payload.support_predicate, contradiction_predicate=payload.contradiction_predicate, allowed_source_roles=list(payload.allowed_source_roles), observed_period_start=payload.observed_period_start, observed_period_end=payload.observed_period_end, available_at_deadline=payload.available_at_deadline, next_verification_event=payload.next_verification_event, reviewer=payload.reviewer, reason=payload.reason))
    commit_or_rollback(db)
    return _rule_dto(rule)
