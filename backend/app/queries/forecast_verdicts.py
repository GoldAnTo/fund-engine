"""Point-in-time projection of published, source-admitted forecast verdicts."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import CaseDocumentVersion, DocumentVersion, SourceSpan, SourceStatement
from app.models.research_expression import (
    ActualMetricObservation,
    ForecastEvaluationCandidate,
    ForecastTargetVersion,
    ForecastVerdict,
)
from app.models.source_governance import SourceContract
from app.schemas.v1.forecast_verdicts import (
    ActualMetricObservationDTO,
    ForecastTargetDTO,
    ForecastVerdictDTO,
    ForecastVerdictHistoryResponse,
)
from app.schemas.v1.market_expression import ExpressionSourceDTO
from app.queries.time import api_datetime
from app.services.source_admission import source_contract_is_active


class ForecastVerdictQueries:
    def __init__(self, db: Session) -> None:
        self._db = db

    def history(self, case_id: uuid.UUID, *, cutoff: datetime) -> ForecastVerdictHistoryResponse:
        verdicts = self._db.scalars(
            select(ForecastVerdict)
            .join(ForecastEvaluationCandidate, ForecastEvaluationCandidate.id == ForecastVerdict.candidate_id)
            .join(ForecastTargetVersion, ForecastTargetVersion.id == ForecastEvaluationCandidate.forecast_target_id)
            .join(ActualMetricObservation, ActualMetricObservation.id == ForecastEvaluationCandidate.actual_observation_id)
            .where(ForecastTargetVersion.research_case_id == case_id)
            .where(ForecastVerdict.decision.in_(("confirmed", "modified")))
            .where(ForecastVerdict.reviewed_at <= cutoff)
            .where(ForecastVerdict.created_at <= cutoff)
            .where(ForecastEvaluationCandidate.cutoff <= cutoff)
            .where(ForecastEvaluationCandidate.created_at <= cutoff)
            .where(ActualMetricObservation.available_at <= cutoff)
            .order_by(ForecastVerdict.reviewed_at.desc(), ForecastVerdict.id.desc())
        ).all()
        superseded_ids = {
            item.supersedes_id for item in verdicts if item.supersedes_id is not None
        }
        items: list[ForecastVerdictDTO] = []
        for verdict in verdicts:
            if verdict.id in superseded_ids:
                continue
            candidate = self._db.get(ForecastEvaluationCandidate, verdict.candidate_id)
            if candidate is None:
                continue
            target = self._db.get(ForecastTargetVersion, candidate.forecast_target_id)
            actual = self._db.get(ActualMetricObservation, candidate.actual_observation_id)
            if target is None or actual is None:
                continue
            if not self._statement_is_admitted_for_case(case_id, target.forecast_source_statement_id):
                continue
            if target.baseline_source_statement_id and not self._statement_is_admitted_for_case(case_id, target.baseline_source_statement_id):
                continue
            if not self._statement_is_admitted_for_case(case_id, actual.source_statement_id):
                continue
            forecast_source = self._source(target.forecast_source_statement_id)
            actual_source = self._source(actual.source_statement_id)
            items.append(ForecastVerdictDTO(
                id=str(verdict.id),
                candidate_id=str(candidate.id),
                supersedes_id=str(verdict.supersedes_id) if verdict.supersedes_id else None,
                decision=verdict.decision,
                outcome=verdict.outcome,
                reason=verdict.reason,
                reviewed_by=verdict.reviewed_by,
                reviewed_at=api_datetime(verdict.reviewed_at),
                target=ForecastTargetDTO(
                    id=str(target.id), case_id=str(target.research_case_id),
                    key_factor_id=str(target.key_factor_id), report_claim_id=str(target.report_claim_id),
                    metric_name=target.metric_name, entity_key=target.entity_key,
                    baseline_value=float(target.baseline_value) if target.baseline_value is not None else None,
                    expected_value=float(target.expected_value), unit=target.unit,
                    forecast_period_start=target.forecast_period_start,
                    forecast_period_end=target.forecast_period_end,
                    comparator=target.comparator,
                    relative_tolerance=float(target.relative_tolerance) if target.relative_tolerance is not None else None,
                    reviewed_by=target.reviewed_by, review_reason=target.review_reason,
                    reviewed_at=api_datetime(target.reviewed_at), forecast_source=forecast_source,
                    baseline_source=self._source(target.baseline_source_statement_id) if target.baseline_source_statement_id else None,
                ),
                actual=ActualMetricObservationDTO(
                    id=str(actual.id), forecast_target_id=str(actual.forecast_target_id),
                    entity_key=actual.entity_key, observed_value=float(actual.observed_value), unit=actual.unit,
                    observed_period_start=actual.observed_period_start,
                    observed_period_end=actual.observed_period_end, available_at=api_datetime(actual.available_at),
                    recorded_by=actual.recorded_by, record_reason=actual.record_reason,
                    source=actual_source,
                ),
                rule_version=candidate.rule_version, inputs=dict(candidate.inputs),
                candidate_rationale=candidate.rationale,
                forecast_source=forecast_source, actual_source=actual_source,
            ))
        return ForecastVerdictHistoryResponse(case_id=str(case_id), cutoff=cutoff, items=items)

    def item(self, case_id: uuid.UUID, verdict_id: uuid.UUID) -> ForecastVerdictDTO | None:
        """Return one command result, including an explicitly rejected verdict."""
        verdict = self._db.get(ForecastVerdict, verdict_id)
        if verdict is None:
            return None
        candidate = self._db.get(ForecastEvaluationCandidate, verdict.candidate_id)
        target = self._db.get(ForecastTargetVersion, candidate.forecast_target_id) if candidate else None
        actual = self._db.get(ActualMetricObservation, candidate.actual_observation_id) if candidate else None
        if target is None or actual is None or target.research_case_id != case_id:
            return None
        if not self._statement_is_admitted_for_case(case_id, target.forecast_source_statement_id):
            return None
        if target.baseline_source_statement_id and not self._statement_is_admitted_for_case(case_id, target.baseline_source_statement_id):
            return None
        if not self._statement_is_admitted_for_case(case_id, actual.source_statement_id):
            return None
        forecast_source = self._source(target.forecast_source_statement_id)
        actual_source = self._source(actual.source_statement_id)
        return ForecastVerdictDTO(
            id=str(verdict.id), candidate_id=str(candidate.id),
            supersedes_id=str(verdict.supersedes_id) if verdict.supersedes_id else None,
            decision=verdict.decision, outcome=verdict.outcome, reason=verdict.reason,
            reviewed_by=verdict.reviewed_by, reviewed_at=api_datetime(verdict.reviewed_at),
            target=ForecastTargetDTO(
                id=str(target.id), case_id=str(target.research_case_id),
                key_factor_id=str(target.key_factor_id), report_claim_id=str(target.report_claim_id),
                metric_name=target.metric_name, entity_key=target.entity_key,
                baseline_value=float(target.baseline_value) if target.baseline_value is not None else None,
                expected_value=float(target.expected_value), unit=target.unit,
                forecast_period_start=target.forecast_period_start,
                forecast_period_end=target.forecast_period_end, comparator=target.comparator,
                relative_tolerance=float(target.relative_tolerance) if target.relative_tolerance is not None else None,
                reviewed_by=target.reviewed_by, review_reason=target.review_reason,
                reviewed_at=api_datetime(target.reviewed_at), forecast_source=forecast_source,
                baseline_source=self._source(target.baseline_source_statement_id) if target.baseline_source_statement_id else None,
            ),
            actual=ActualMetricObservationDTO(
                id=str(actual.id), forecast_target_id=str(actual.forecast_target_id),
                entity_key=actual.entity_key, observed_value=float(actual.observed_value), unit=actual.unit,
                observed_period_start=actual.observed_period_start,
                observed_period_end=actual.observed_period_end, available_at=api_datetime(actual.available_at),
                recorded_by=actual.recorded_by, record_reason=actual.record_reason,
                source=actual_source,
            ),
            rule_version=candidate.rule_version, inputs=dict(candidate.inputs),
            candidate_rationale=candidate.rationale,
            forecast_source=forecast_source, actual_source=actual_source,
        )

    def _statement_is_admitted_for_case(self, case_id: uuid.UUID, statement_id: uuid.UUID) -> bool:
        statement = self._db.get(SourceStatement, statement_id)
        span = self._db.get(SourceSpan, statement.source_span_id) if statement else None
        if span is None:
            return False
        contract = self._db.scalar(
            select(SourceContract).where(SourceContract.document_version_id == span.document_version_id)
        )
        if contract is None or not contract.allow_ai_processing or not contract.allow_display or not source_contract_is_active(contract):
            return False
        return self._db.scalar(
            select(CaseDocumentVersion.id)
            .where(CaseDocumentVersion.research_case_id == case_id)
            .where(CaseDocumentVersion.document_version_id == span.document_version_id)
            .limit(1)
        ) is not None

    def _source(self, statement_id: uuid.UUID) -> ExpressionSourceDTO:
        statement = self._db.get(SourceStatement, statement_id)
        span = self._db.get(SourceSpan, statement.source_span_id) if statement else None
        document = self._db.get(DocumentVersion, span.document_version_id) if span else None
        return ExpressionSourceDTO(
            source_statement_id=str(statement_id),
            document_version_id=str(document.id) if document else None,
            document_title=document.title if document else None,
            source_url=document.source_url if document else None,
            locator=span.locator if span else None,
            available_at=api_datetime(document.available_at) if document else None,
            permission_status="admitted",
        )
