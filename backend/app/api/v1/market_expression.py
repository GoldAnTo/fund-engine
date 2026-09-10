"""Reviewed Case market-expression read endpoint."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.queries.market_expression import MarketExpressionQueries
from app.schemas.v1.market_expression import (
    ClaimVerificationDTO,
    FundamentalImpactDTO,
    KeyFactorDTO,
    MarketInstrumentBindingDTO,
    MarketInstrumentBindingsResponse,
    MarketInstrumentCatalogResponse,
    MarketExpressionResponse,
    MarketObservationDTO,
    RegisterKeyFactorRequest,
    RegisterClaimVerificationRequest,
    RegisterFundamentalImpactRequest,
    RegisterMarketObservationRequest,
    RegisterMarketInstrumentBindingRequest,
    RegisterReportClaimRequest,
    ReportClaimDTO,
    SourceStatementOptionsResponse,
)
from app.services.market_expression import ClaimVerificationInput, FundamentalImpactInput, KeyFactorInput, MarketExpressionService, MarketInstrumentBindingInput, MarketObservationInput, ReportClaimInput


router = APIRouter(tags=["market-expression-v1"])


@router.get("/research-cases/{case_id}/market-expression", response_model=MarketExpressionResponse)
def get_market_expression(case_id: uuid.UUID, as_of: date | None = None, cutoff: datetime | None = None, db: Session = Depends(get_db)) -> MarketExpressionResponse:
    effective_as_of = as_of or datetime.now(timezone.utc).date()
    effective_cutoff = cutoff or datetime.combine(effective_as_of, time.max, tzinfo=timezone.utc)
    return MarketExpressionQueries(db).get(case_id=case_id, as_of=effective_as_of, cutoff=effective_cutoff)


@router.get("/research-cases/{case_id}/source-statements", response_model=SourceStatementOptionsResponse)
def admitted_source_statements(case_id: uuid.UUID, db: Session = Depends(get_db)) -> SourceStatementOptionsResponse:
    return MarketExpressionQueries(db).admitted_source_statements(case_id)


@router.get("/research-cases/{case_id}/market-instruments", response_model=MarketInstrumentBindingsResponse)
def market_instruments(case_id: uuid.UUID, db: Session = Depends(get_db)) -> MarketInstrumentBindingsResponse:
    return MarketExpressionQueries(db).market_instruments(case_id)


@router.get("/market-instruments", response_model=MarketInstrumentCatalogResponse)
def market_instrument_catalog(query: str = "", db: Session = Depends(get_db)) -> MarketInstrumentCatalogResponse:
    return MarketExpressionQueries(db).market_instrument_catalog(query)


@router.post("/research-cases/{case_id}/market-instruments", response_model=MarketInstrumentBindingDTO, status_code=status.HTTP_201_CREATED)
def register_market_instrument_binding(case_id: uuid.UUID, payload: RegisterMarketInstrumentBindingRequest, db: Session = Depends(get_db)) -> MarketInstrumentBindingDTO:
    record = translate_validation(
        MarketExpressionService(db).register_market_instrument_binding,
        case_id,
        MarketInstrumentBindingInput(
            company_id=payload.company_id, stock_id=payload.stock_id,
            source_statement_id=payload.source_statement_id,
            relationship_role=payload.relationship_role,
            reviewed_by=payload.reviewed_by, review_reason=payload.review_reason,
        ),
    )
    commit_or_rollback(db)
    return MarketExpressionQueries(db)._market_instrument(record)


@router.post("/research-cases/{case_id}/key-factors/{factor_id}/fundamental-impacts", response_model=FundamentalImpactDTO, status_code=status.HTTP_201_CREATED)
def register_fundamental_impact(case_id: uuid.UUID, factor_id: uuid.UUID, payload: RegisterFundamentalImpactRequest, db: Session = Depends(get_db)) -> FundamentalImpactDTO:
    record = translate_validation(
        MarketExpressionService(db).register_fundamental_impact,
        case_id,
        factor_id,
        FundamentalImpactInput(
            market_instrument_binding_id=payload.market_instrument_binding_id,
            source_statement_id=payload.source_statement_id,
            metric_name=payload.metric_name,
            expected_direction=payload.expected_direction,
            rationale=payload.rationale,
            reviewed_by=payload.reviewed_by,
            review_reason=payload.review_reason,
        ),
    )
    commit_or_rollback(db)
    return MarketExpressionQueries(db)._fundamental(record)


@router.post("/research-cases/{case_id}/key-factors/{factor_id}/market-observations", response_model=MarketObservationDTO, status_code=status.HTTP_201_CREATED)
def register_market_observation(case_id: uuid.UUID, factor_id: uuid.UUID, payload: RegisterMarketObservationRequest, db: Session = Depends(get_db)) -> MarketObservationDTO:
    record = translate_validation(
        MarketExpressionService(db).register_market_observation,
        case_id,
        factor_id,
        MarketObservationInput(
            market_instrument_binding_id=payload.market_instrument_binding_id,
            event_at=payload.event_at,
            available_at=payload.available_at,
            window_label=payload.window_label,
            benchmark=payload.benchmark,
            price_source=payload.price_source,
            after_hours_treatment=payload.after_hours_treatment,
            relative_return=Decimal(str(payload.relative_return)) if payload.relative_return is not None else None,
            reviewed_by=payload.reviewed_by,
            review_reason=payload.review_reason,
        ),
    )
    commit_or_rollback(db)
    return MarketExpressionQueries(db)._observation(record)


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


@router.post("/research-cases/{case_id}/key-factors/{factor_id}/verifications", response_model=ClaimVerificationDTO, status_code=status.HTTP_201_CREATED)
def register_claim_verification(case_id: uuid.UUID, factor_id: uuid.UUID, payload: RegisterClaimVerificationRequest, db: Session = Depends(get_db)) -> ClaimVerificationDTO:
    record = translate_validation(
        MarketExpressionService(db).register_claim_verification,
        case_id,
        factor_id,
        ClaimVerificationInput(
            source_statement_id=payload.source_statement_id, outcome=payload.outcome,
            rationale=payload.rationale, reviewed_by=payload.reviewed_by,
            review_reason=payload.review_reason,
        ),
    )
    commit_or_rollback(db)
    return ClaimVerificationDTO(
        outcome=record.outcome, rationale=record.rationale,
        reviewed_by=record.reviewed_by or "未记录",
        reviewed_at=record.reviewed_at or record.created_at,
        source=MarketExpressionQueries(db)._source(record.source_statement_id),
    )
