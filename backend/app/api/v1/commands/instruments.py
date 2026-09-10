"""v1 instrument command endpoints (companies, stocks, funds, holding
disclosures, theme roles)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.commands.common import (
    audit_command,
    commit_or_rollback,
    translate_validation,
)
from app.api.v1.dependencies import RequireCaseRoute, require_case_route_permission
from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    Fund,
    HoldingDisclosure,
    ResearchCase,
    SourceStatement as Statement,
    SourceSpan,
    Stock,
    ThemeRole,
    ValuationSnapshot,
)
from app.schemas.v1.instrument_commands import (
    CompanyDTO,
    CreateCompanyRequest,
    CreateFundRequest,
    CreateHoldingDisclosureRequest,
    CreateStockRequest,
    CreateThemeRoleRequest,
    CreateValuationSnapshotRequest,
    FundDTO,
    HoldingDisclosureDTO,
    StockDTO,
    ThemeRoleDTO,
    ValuationSnapshotDTO,
)
from app.services.instruments import InstrumentService

router = APIRouter(tags=["instrument-commands-v1"])


@router.post("/companies", response_model=CompanyDTO, status_code=201)
def create_company(
    payload: CreateCompanyRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> Company:
    company = audit_command(
        db,
        request,
        actor=actor.server_actor,
        action="create_company",
        entity_type="Company",
        payload=payload.model_dump(mode="json"),
        fn=translate_validation,
        args=(InstrumentService(db).create_company,),
        kwargs={
            "code": payload.code,
            "name": payload.name,
            "type": payload.type,
        },
    )
    commit_or_rollback(db)
    return company


@router.post(
    "/companies/{company_id}/stocks",
    response_model=StockDTO,
    status_code=201,
)
def create_stock(
    company_id: uuid.UUID,
    payload: CreateStockRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> Stock:
    company = db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company", str(company_id))

    stock = audit_command(
        db,
        request,
        actor=actor.server_actor,
        action="add_stock",
        entity_type="Stock",
        payload=payload.model_dump(mode="json"),
        fn=translate_validation,
        args=(InstrumentService(db).add_stock,),
        kwargs={
            "company": company,
            "code": payload.code,
            "name": payload.name,
            "market": payload.market,
        },
    )
    commit_or_rollback(db)
    return stock


@router.post("/funds", response_model=FundDTO, status_code=201)
def create_fund(
    payload: CreateFundRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> Fund:
    fund = audit_command(
        db,
        request,
        actor=actor.server_actor,
        action="create_fund",
        entity_type="Fund",
        payload=payload.model_dump(mode="json"),
        fn=translate_validation,
        args=(InstrumentService(db).create_fund,),
        kwargs={
            "code": payload.code,
            "name": payload.name,
            "fund_type": payload.fund_type,
            "scale": payload.scale,
            "establish_date": payload.establish_date,
            "management_company_id": payload.management_company_id,
        },
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
    request: Request,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> HoldingDisclosure:
    fund = db.get(Fund, fund_id)
    if fund is None:
        raise NotFoundError("Fund", str(fund_id))
    stock = db.get(Stock, payload.stock_id)
    if stock is None:
        raise NotFoundError("Stock", str(payload.stock_id))

    row = audit_command(
        db,
        request,
        actor=actor.server_actor,
        action="add_holding_disclosure",
        entity_type="HoldingDisclosure",
        payload=payload.model_dump(mode="json"),
        fn=translate_validation,
        args=(InstrumentService(db).add_holding_disclosure,),
        kwargs={
            "fund": fund,
            "stock": stock,
            "weight": payload.weight,
            "report_period": payload.report_period,
            "published_at": payload.published_at,
            "source": payload.source,
            "source_document_version_id": payload.source_document_version_id,
            "source_span_id": payload.source_span_id,
            "provider_record_id": payload.provider_record_id,
            "coverage_status": payload.coverage_status,
            "filing_kind": payload.filing_kind,
            "supersedes_disclosure_id": payload.supersedes_disclosure_id,
        },
    )
    commit_or_rollback(db)
    return row


@router.post(
    "/stocks/{stock_id}/valuation-snapshots",
    response_model=ValuationSnapshotDTO,
    status_code=201,
)
def create_valuation_snapshot(
    stock_id: uuid.UUID,
    payload: CreateValuationSnapshotRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ValuationSnapshot:
    stock = db.get(Stock, stock_id)
    if stock is None:
        raise NotFoundError("Stock", str(stock_id))

    snapshot = audit_command(
        db,
        request,
        actor=actor.server_actor,
        action="add_valuation_snapshot",
        entity_type="ValuationSnapshot",
        payload=payload.model_dump(mode="json"),
        fn=translate_validation,
        args=(InstrumentService(db).add_valuation_snapshot,),
        kwargs={
            "stock": stock,
            "as_of_date": payload.as_of_date,
            "metric_name": payload.metric_name,
            "metric_value": payload.metric_value,
            "source": payload.source,
            "definition": payload.definition,
        },
    )
    commit_or_rollback(db)
    return snapshot


@router.post(
    "/companies/{company_id}/theme-roles",
    response_model=ThemeRoleDTO,
    status_code=201,
    dependencies=[Depends(require_case_route_permission)],
)
def create_theme_role(
    case_policy: RequireCaseRoute,
    company_id: uuid.UUID,
    payload: CreateThemeRoleRequest,
    request: Request,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ThemeRole:
    company = db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company", str(company_id))
    research_case: ResearchCase | None = None
    if payload.research_case_id is not None:
        case_policy.require(payload.research_case_id)
        research_case = db.get(ResearchCase, payload.research_case_id)
        if research_case is None:
            raise NotFoundError("ResearchCase", str(payload.research_case_id))
    source_statement: Statement | None = None
    if payload.source_statement_id is not None:
        if research_case is None:
            raise ValidationFailedError(
                "source_statement_id requires research_case_id"
            )
        source_statement = db.scalar(
            select(Statement)
            .join(SourceSpan, SourceSpan.id == Statement.source_span_id)
            .join(
                CaseDocumentVersion,
                CaseDocumentVersion.document_version_id
                == SourceSpan.document_version_id,
            )
            .where(
                Statement.id == payload.source_statement_id,
                CaseDocumentVersion.research_case_id == research_case.id,
            )
        )
        if source_statement is None:
            raise NotFoundError("Statement", str(payload.source_statement_id))

    row = audit_command(
        db,
        request,
        actor=actor.server_actor,
        action="add_theme_role",
        entity_type="ThemeRole",
        payload=payload.model_dump(mode="json"),
        fn=translate_validation,
        args=(InstrumentService(db).add_theme_role,),
        kwargs={
            "company": company,
            "role": payload.role,
            "research_case": research_case,
            "scope": payload.scope,
            "applicable_from": payload.applicable_from,
            "applicable_to": payload.applicable_to,
            "source_statement": source_statement,
        },
    )
    commit_or_rollback(db)
    return row
