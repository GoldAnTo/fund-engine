"""Reviewed Case market-expression read endpoint."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.queries.market_expression import MarketExpressionQueries
from app.schemas.v1.market_expression import (
    KeyFactorDTO,
    MarketExpressionResponse,
    RegisterKeyFactorRequest,
    RegisterReportClaimRequest,
    ReportClaimDTO,
    SourceStatementOptionsResponse,
)
from app.services.market_expression import KeyFactorInput, MarketExpressionService, ReportClaimInput


router = APIRouter(tags=["market-expression-v1"])


@router.get("/research-cases/{case_id}/market-expression", response_model=MarketExpressionResponse)
def get_market_expression(case_id: uuid.UUID, as_of: date | None = None, cutoff: datetime | None = None, db: Session = Depends(get_db)) -> MarketExpressionResponse:
    effective_as_of = as_of or datetime.now(timezone.utc).date()
    effective_cutoff = cutoff or datetime.combine(effective_as_of, time.max, tzinfo=timezone.utc)
    return MarketExpressionQueries(db).get(case_id=case_id, as_of=effective_as_of, cutoff=effective_cutoff)


@router.get("/research-cases/{case_id}/source-statements", response_model=SourceStatementOptionsResponse)
def admitted_source_statements(case_id: uuid.UUID, db: Session = Depends(get_db)) -> SourceStatementOptionsResponse:
    return MarketExpressionQueries(db).admitted_source_statements(case_id)


@router.post("/research-cases/{case_id}/report-claims", response_model=ReportClaimDTO, status_code=status.HTTP_201_CREATED)
def register_report_claim(case_id: uuid.UUID, payload: RegisterReportClaimRequest, db: Session = Depends(get_db)) -> ReportClaimDTO:
    record = translate_validation(
        MarketExpressionService(db).register_report_claim,
        case_id,
        ReportClaimInput(
            source_statement_id=payload.source_statement_id, text=payload.text,
            claim_kind=payload.claim_kind, asserted_period=payload.asserted_period,
            asserted_by=payload.asserted_by, reviewed_by=payload.reviewed_by,
            review_reason=payload.review_reason,
        ),
    )
    commit_or_rollback(db)
    return MarketExpressionQueries(db)._claim(record)


@router.post("/research-cases/{case_id}/key-factors", response_model=KeyFactorDTO, status_code=status.HTTP_201_CREATED)
def register_key_factor(case_id: uuid.UUID, payload: RegisterKeyFactorRequest, db: Session = Depends(get_db)) -> KeyFactorDTO:
    record = translate_validation(
        MarketExpressionService(db).register_key_factor,
        case_id,
        KeyFactorInput(
            report_claim_id=payload.report_claim_id, thesis_id=payload.thesis_id,
            name=payload.name, expected_direction=payload.expected_direction, metric_name=payload.metric_name,
            allowed_source_types=list(payload.allowed_source_types), verification_window_start=payload.verification_window_start,
            verification_window_end=payload.verification_window_end, support_condition=payload.support_condition,
            refutation_condition=payload.refutation_condition, next_verification_event=payload.next_verification_event,
            reviewed_by=payload.reviewed_by, review_reason=payload.review_reason,
        ),
    )
    commit_or_rollback(db)
    return MarketExpressionQueries(db)._factor(record, datetime.now(timezone.utc))
