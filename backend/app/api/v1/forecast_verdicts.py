"""Tenant-scoped commands and historical replay for report forecast verdicts."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.api.v1.tenant_context import ResearchActor, require_research_actor, require_research_tenant
from app.db import get_db
from app.errors import NotFoundError
from app.models.research_expression import ForecastEvaluationCandidate, ForecastTargetVersion
from app.queries.forecast_verdicts import ForecastVerdictQueries
from app.queries.time import api_datetime
from app.schemas.v1.forecast_verdicts import (
    ActualMetricObservationDTO,
    CreateForecastTargetRequest,
    CreateForecastVerdictRequest,
    EvaluateForecastTargetRequest,
    ForecastEvaluationCandidateDTO,
    ForecastTargetDTO,
    ForecastVerdictDTO,
    ForecastVerdictHistoryResponse,
    RecordActualMetricObservationRequest,
)
from app.services.case_tenant_access import CaseTenantAccess
from app.api.v1.dependencies import RequireCaseRoute
from app.services.forecast_verdicts import (
    ActualObservationInput,
    ForecastTargetInput,
    ForecastVerdictInput,
    ForecastVerdictService,
)


router = APIRouter(
    tags=["forecast-verdicts-v1"], dependencies=[Depends(require_research_tenant)]
)


def _require_case(db: Session, case_id: uuid.UUID, tenant_id: str) -> None:
    CaseTenantAccess(db).require_case(case_id, tenant_id)


def _target_for_case(db: Session, *, target_id: uuid.UUID, case_id: uuid.UUID) -> ForecastTargetVersion:
    target = db.get(ForecastTargetVersion, target_id)
    if target is None or target.research_case_id != case_id:
        raise NotFoundError("forecast target not found in this research case")
    return target


def _case_for_candidate(
    db: Session,
    candidate_id: uuid.UUID,
    actor: ResearchActor,
    case_policy: RequireCaseRoute,
) -> ForecastTargetVersion:
    candidate = db.get(ForecastEvaluationCandidate, candidate_id)
    target = db.get(ForecastTargetVersion, candidate.forecast_target_id) if candidate else None
    if target is None:
        raise NotFoundError("forecast evaluation candidate not found")
    _require_case(db, target.research_case_id, actor.tenant_id)
    case_policy.require(target.research_case_id)
    return target


def _history_item(db: Session, case_id: uuid.UUID, verdict_id: uuid.UUID) -> ForecastVerdictDTO:
    item = ForecastVerdictQueries(db).item(case_id, verdict_id)
    if item is not None:
        return item
    raise NotFoundError("forecast verdict is not visible in this research case")


@router.get("/research-cases/{case_id}/forecast-verdicts", response_model=ForecastVerdictHistoryResponse)
def get_forecast_verdicts(
    case_id: uuid.UUID,
    cutoff: datetime,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> ForecastVerdictHistoryResponse:
    _require_case(db, case_id, tenant_id)
    return ForecastVerdictQueries(db).history(case_id, cutoff=cutoff)


@router.post("/research-cases/{case_id}/forecast-targets", response_model=ForecastTargetDTO, status_code=status.HTTP_201_CREATED)
def create_forecast_target(
    case_id: uuid.UUID,
    payload: CreateForecastTargetRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ForecastTargetDTO:
    _require_case(db, case_id, actor.tenant_id)
    record = translate_validation(ForecastVerdictService(db).create_target, case_id, ForecastTargetInput(
        key_factor_id=payload.key_factor_id, report_claim_id=payload.report_claim_id,
        forecast_source_statement_id=payload.forecast_source_statement_id,
        baseline_source_statement_id=payload.baseline_source_statement_id,
        metric_name=payload.metric_name, entity_key=payload.entity_key,
        baseline_value=Decimal(str(payload.baseline_value)) if payload.baseline_value is not None else None,
        expected_value=Decimal(str(payload.expected_value)), unit=payload.unit,
        forecast_period_start=payload.forecast_period_start, forecast_period_end=payload.forecast_period_end,
        comparator=payload.comparator,
        relative_tolerance=Decimal(str(payload.relative_tolerance)) if payload.relative_tolerance is not None else None,
        reviewed_by=actor.server_actor, review_reason=payload.review_reason,
    ))
    commit_or_rollback(db)
    source = ForecastVerdictQueries(db)._source(record.forecast_source_statement_id)
    return ForecastTargetDTO(
        id=str(record.id), case_id=str(record.research_case_id), key_factor_id=str(record.key_factor_id),
        report_claim_id=str(record.report_claim_id), metric_name=record.metric_name, entity_key=record.entity_key,
        baseline_value=float(record.baseline_value) if record.baseline_value is not None else None,
        expected_value=float(record.expected_value), unit=record.unit,
        forecast_period_start=record.forecast_period_start, forecast_period_end=record.forecast_period_end,
        comparator=record.comparator, relative_tolerance=float(record.relative_tolerance) if record.relative_tolerance is not None else None,
        reviewed_by=record.reviewed_by, review_reason=record.review_reason, reviewed_at=api_datetime(record.reviewed_at),
        forecast_source=source,
        baseline_source=ForecastVerdictQueries(db)._source(record.baseline_source_statement_id) if record.baseline_source_statement_id else None,
    )


@router.post("/research-cases/{case_id}/actual-metric-observations", response_model=ActualMetricObservationDTO, status_code=status.HTTP_201_CREATED)
def record_actual_metric_observation(
    case_id: uuid.UUID,
    payload: RecordActualMetricObservationRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ActualMetricObservationDTO:
    _require_case(db, case_id, actor.tenant_id)
    _target_for_case(db, target_id=payload.forecast_target_id, case_id=case_id)
    record = translate_validation(ForecastVerdictService(db).record_actual, payload.forecast_target_id, ActualObservationInput(
        source_statement_id=payload.source_statement_id, entity_key=payload.entity_key,
        observed_value=Decimal(str(payload.observed_value)), unit=payload.unit,
        observed_period_start=payload.observed_period_start, observed_period_end=payload.observed_period_end,
        available_at=payload.available_at, recorded_by=actor.server_actor, record_reason=payload.record_reason,
    ))
    commit_or_rollback(db)
    return ActualMetricObservationDTO(
        id=str(record.id), forecast_target_id=str(record.forecast_target_id), entity_key=record.entity_key,
        observed_value=float(record.observed_value), unit=record.unit,
        observed_period_start=record.observed_period_start, observed_period_end=record.observed_period_end,
        available_at=api_datetime(record.available_at), recorded_by=record.recorded_by, record_reason=record.record_reason,
        source=ForecastVerdictQueries(db)._source(record.source_statement_id),
    )


@router.post("/forecast-targets/{target_id}/evaluate", response_model=ForecastEvaluationCandidateDTO, status_code=status.HTTP_201_CREATED)
def evaluate_forecast_target(
    case_policy: RequireCaseRoute,
    target_id: uuid.UUID,
    payload: EvaluateForecastTargetRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ForecastEvaluationCandidateDTO:
    target = db.get(ForecastTargetVersion, target_id)
    if target is None:
        raise NotFoundError("forecast target not found")
    _require_case(db, target.research_case_id, actor.tenant_id)
    case_policy.require(target.research_case_id)
    record = translate_validation(ForecastVerdictService(db).evaluate, target_id, payload.actual_observation_id, cutoff=payload.cutoff)
    commit_or_rollback(db)
    return ForecastEvaluationCandidateDTO(
        id=str(record.id), forecast_target_id=str(record.forecast_target_id), actual_observation_id=str(record.actual_observation_id),
        cutoff=api_datetime(record.cutoff), outcome=record.outcome, rule_version=record.rule_version,
        inputs=dict(record.inputs), rationale=record.rationale, review_state=record.review_state, created_at=api_datetime(record.created_at),
    )


@router.post("/forecast-evaluations/{candidate_id}/verdicts", response_model=ForecastVerdictDTO, status_code=status.HTTP_201_CREATED)
def create_forecast_verdict(
    case_policy: RequireCaseRoute,
    candidate_id: uuid.UUID,
    payload: CreateForecastVerdictRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ForecastVerdictDTO:
    target = _case_for_candidate(db, candidate_id, actor, case_policy)
    record = translate_validation(ForecastVerdictService(db).create_verdict, candidate_id, ForecastVerdictInput(
        decision=payload.decision, outcome=payload.outcome, reason=payload.reason,
        reviewed_by=actor.server_actor, supersedes_id=payload.supersedes_id,
    ))
    commit_or_rollback(db)
    return _history_item(db, target.research_case_id, record.id)
