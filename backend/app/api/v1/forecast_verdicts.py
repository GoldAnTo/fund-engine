"""Controlled commands for frozen report-forecast evaluation."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.errors import NotFoundError
from app.models.research_expression import ActualMetricObservation, ForecastEvaluationCandidate, ForecastTargetVersion, ForecastVerdict
from app.queries.market_expression import MarketExpressionQueries
from app.repositories.research import ResearchRepository
from app.schemas.v1.forecast_verdicts import (
    CreateForecastTargetRequest,
    CreateForecastVerdictRequest,
    EvaluateForecastTargetRequest,
    ForecastVerdictDTO,
    ForecastVerdictsResponse,
    RecordActualMetricObservationRequest,
)
from app.services.forecast_verdicts import (
    ActualObservationInput,
    ForecastTargetInput,
    ForecastVerdictInput,
    ForecastVerdictService,
)


router = APIRouter(tags=["forecast-verdicts-v1"])


@router.post("/research-cases/{case_id}/forecast-targets", status_code=status.HTTP_201_CREATED)
def create_forecast_target(case_id: uuid.UUID, payload: CreateForecastTargetRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    record = translate_validation(ForecastVerdictService(db).create_target, case_id, ForecastTargetInput(**payload.model_dump()))
    commit_or_rollback(db)
    return {"id": str(record.id)}


@router.post("/forecast-targets/{target_id}/actual-metric-observations", status_code=status.HTTP_201_CREATED)
def record_actual_metric_observation(target_id: uuid.UUID, payload: RecordActualMetricObservationRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    record = translate_validation(ForecastVerdictService(db).record_actual, target_id, ActualObservationInput(**payload.model_dump()))
    commit_or_rollback(db)
    return {"id": str(record.id)}


@router.post("/forecast-targets/{target_id}/evaluate", status_code=status.HTTP_201_CREATED)
def evaluate_forecast_target(target_id: uuid.UUID, payload: EvaluateForecastTargetRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    record = translate_validation(ForecastVerdictService(db).evaluate, target_id, payload.actual_observation_id, cutoff=payload.cutoff)
    commit_or_rollback(db)
    return {"id": str(record.id), "outcome": record.outcome}


@router.post("/forecast-evaluations/{candidate_id}/verdicts", status_code=status.HTTP_201_CREATED)
def create_forecast_verdict(candidate_id: uuid.UUID, payload: CreateForecastVerdictRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    record = translate_validation(ForecastVerdictService(db).create_verdict, candidate_id, ForecastVerdictInput(**payload.model_dump()))
    commit_or_rollback(db)
    return {"id": str(record.id), "outcome": record.outcome, "decision": record.decision}


@router.get("/research-cases/{case_id}/forecast-verdicts", response_model=ForecastVerdictsResponse)
def list_forecast_verdicts(case_id: uuid.UUID, cutoff: datetime, db: Session = Depends(get_db)) -> ForecastVerdictsResponse:
    if ResearchRepository(db).get_case(case_id) is None:
        raise NotFoundError(f"research case {case_id} not found")
    source_queries = MarketExpressionQueries(db)
    rows = db.execute(
        select(ForecastVerdict, ForecastEvaluationCandidate, ForecastTargetVersion, ActualMetricObservation)
        .join(ForecastEvaluationCandidate, ForecastEvaluationCandidate.id == ForecastVerdict.candidate_id)
        .join(ForecastTargetVersion, ForecastTargetVersion.id == ForecastEvaluationCandidate.forecast_target_id)
        .join(ActualMetricObservation, ActualMetricObservation.id == ForecastEvaluationCandidate.actual_observation_id)
        .where(ForecastTargetVersion.research_case_id == case_id)
        .where(ForecastVerdict.reviewed_at <= cutoff)
        .where(ForecastEvaluationCandidate.cutoff <= cutoff)
        .where(ActualMetricObservation.available_at <= cutoff)
        .order_by(ForecastVerdict.created_at, ForecastVerdict.id)
    ).all()
    superseded_ids = set(db.scalars(select(ForecastVerdict.supersedes_id).where(ForecastVerdict.supersedes_id.is_not(None))))
    items = []
    for verdict, candidate, target, actual in rows:
        if verdict.id in superseded_ids or verdict.decision not in {"confirmed", "modified"}:
            continue
        if not source_queries._case_has_source(case_id, target.forecast_source_statement_id):
            continue
        if not source_queries._case_has_source(case_id, actual.source_statement_id):
            continue
        items.append(ForecastVerdictDTO(
            id=str(verdict.id), target_id=str(target.id), key_factor_id=str(target.key_factor_id),
            outcome=verdict.outcome, decision=verdict.decision, metric_name=target.metric_name,
            entity_key=target.entity_key, baseline_value=target.baseline_value, expected_value=target.expected_value,
            actual_value=actual.observed_value, unit=target.unit, forecast_period_start=target.forecast_period_start,
            forecast_period_end=target.forecast_period_end, comparator=target.comparator,
            relative_tolerance=target.relative_tolerance, rule_version=candidate.rule_version,
            rationale=candidate.rationale, reviewed_by=verdict.reviewed_by, reason=verdict.reason,
            reviewed_at=verdict.reviewed_at, forecast_source=source_queries._source(target.forecast_source_statement_id),
            baseline_source=source_queries._source(target.baseline_source_statement_id) if target.baseline_source_statement_id else None,
            actual_source=source_queries._source(actual.source_statement_id),
        ))
    return ForecastVerdictsResponse(case_id=str(case_id), cutoff=cutoff, items=items)
