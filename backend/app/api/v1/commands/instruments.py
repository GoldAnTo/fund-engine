"""v1 instrument command endpoints (funds, holding disclosures, theme roles)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.errors import NotFoundError
from app.models.ledger import (
    Company,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    SourceStatement as Statement,
    Stock,
    ThemeRole,
)
from app.schemas.v1.instrument_commands import (
    CreateFundRequest,
    CreateHoldingDisclosureRequest,
    CreateThemeRoleRequest,
    FundDTO,
    HoldingDisclosureDTO,
    ThemeRoleDTO,
)
from app.services.instruments import InstrumentService

router = APIRouter(tags=["instrument-commands-v1"])


@router.post("/funds", response_model=FundDTO, status_code=201)
def create_fund(payload: CreateFundRequest, db: Session = Depends(get_db)) -> Fund:
    fund = translate_validation(
        InstrumentService(db).create_fund,
        code=payload.code,
        name=payload.name,
        fund_type=payload.fund_type,
        scale=payload.scale,
        establish_date=payload.establish_date,
        management_company_id=payload.management_company_id,
    )
    commit_or_rollback(db)
    return fund


@router.post(
    "/funds/{fund_id}/holding-disclosures",
    response_model=HoldingDisclosureDTO,
    status_code=201,
)
def create_holding_disclosure(
    fund_id: uuid.UUID,
    payload: CreateHoldingDisclosureRequest,
    db: Session = Depends(get_db),
) -> HoldingDisclosure:
    fund = db.get(Fund, fund_id)
    if fund is None:
        raise NotFoundError("Fund", str(fund_id))
    stock = db.get(Stock, payload.stock_id)
    if stock is None:
        raise NotFoundError("Stock", str(payload.stock_id))

    row = translate_validation(
        InstrumentService(db).add_holding_disclosure,
        fund=fund,
        stock=stock,
        weight=payload.weight,
        report_period=payload.report_period,
        published_at=payload.published_at,
        source=payload.source,
    )
    commit_or_rollback(db)
    return row


@router.post(
    "/companies/{company_id}/theme-roles",
    response_model=ThemeRoleDTO,
    status_code=201,
)
def create_theme_role(
    company_id: uuid.UUID,
    payload: CreateThemeRoleRequest,
    db: Session = Depends(get_db),
) -> ThemeRole:
    company = db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company", str(company_id))
    research_case: ResearchCase | None = None
    if payload.research_case_id is not None:
        research_case = db.get(ResearchCase, payload.research_case_id)
        if research_case is None:
            raise NotFoundError("ResearchCase", str(payload.research_case_id))
    source_statement: Statement | None = None
    if payload.source_statement_id is not None:
        source_statement = db.get(Statement, payload.source_statement_id)
        if source_statement is None:
            raise NotFoundError("Statement", str(payload.source_statement_id))

    row = translate_validation(
        InstrumentService(db).add_theme_role,
        company=company,
        role=payload.role,
        research_case=research_case,
        scope=payload.scope,
        applicable_from=payload.applicable_from,
        applicable_to=payload.applicable_to,
        source_statement=source_statement,
    )
    commit_or_rollback(db)
    return row
