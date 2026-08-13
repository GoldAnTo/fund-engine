"""Visible, Case-scoped fund-disclosure configuration and run replay API."""
from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import require_research_tenant
from app.datasources.gildata.client import GildataMCPClient, GildataMCPError
from app.db import get_db
from app.errors import ValidationFailedError
from app.models.fund_disclosure_sync import FundDisclosureSyncRun, FundDisclosureSyncRunEvent
from app.models.ledger import ResearchCase
from app.queries.fund_disclosure_sync import FundDisclosureSyncDetail, FundDisclosureSyncRunView
from app.schemas.v1.fund_disclosure_sync import (
    FundDisclosureSyncConfigDTO,
    FundDisclosureSyncDetailResponse,
    ActiveFundDisclosureSyncRunDTO,
    ActiveFundDisclosureSyncRunsResponse,
    FundDisclosureSyncRunDTO,
    FundDisclosureSyncRunEventDTO,
    FundDisclosureSyncSuggestionDTO,
    SaveFundDisclosureSyncConfigRequest,
)
from app.services.case_tenant_access import CaseTenantAccess
from app.services.fund_disclosure_sync import (
    FundDisclosureSyncExecution,
    FundDisclosureSyncService,
)


router = APIRouter(
    tags=["fund-disclosure-sync-v1"], dependencies=[Depends(require_research_tenant)]
)


def get_fund_disclosure_client() -> GildataMCPClient:
    """Keep provider construction inside the route so its failure is replayable."""
    return GildataMCPClient.from_env()


def _require_case(db: Session, case_id: uuid.UUID, tenant_id: str) -> None:
    CaseTenantAccess(db).require_case(case_id, tenant_id)


def _config_dto(config) -> FundDisclosureSyncConfigDTO:
    return FundDisclosureSyncConfigDTO(
        id=str(config.id),
        version=config.version,
        frequency=config.frequency,
        report_period=config.report_period,
        fund_codes=list(config.fund_codes),
        stock_codes=list(config.stock_codes),
        allow_display=config.allow_display,
        changed_by=config.changed_by,
        change_reason=config.change_reason,
        created_at=config.created_at,
    )


def _run_dto(view: FundDisclosureSyncRunView | FundDisclosureSyncExecution) -> FundDisclosureSyncRunDTO:
    run = view.run
    events = view.events
    return FundDisclosureSyncRunDTO(
        id=str(run.id),
        config_version_id=str(run.config_version_id),
        trigger=run.trigger,
        report_period=run.report_period,
        fund_codes=list(run.fund_codes),
        stock_codes=list(run.stock_codes),
        allow_display=run.allow_display,
        status=events[-1].status if events else "queued",
        created_at=run.created_at,
        events=[
            FundDisclosureSyncRunEventDTO(
                seq=event.seq,
                stage=event.stage,
                status=event.status,
                message=event.message,
                payload=event.payload_json,
                created_at=event.created_at,
            )
            for event in events
        ],
    )


def _detail_dto(detail: FundDisclosureSyncDetail) -> FundDisclosureSyncDetailResponse:
    config = detail.effective_config
    return FundDisclosureSyncDetailResponse(
        suggestions=[
            FundDisclosureSyncSuggestionDTO(
                fund_code=item.fund_code,
                fund_name=item.fund_name,
                matching_stock_codes=item.matching_stock_codes,
                latest_report_period=item.latest_report_period,
            )
            for item in detail.suggestions
        ],
        manual_code_fallback=detail.manual_code_fallback,
        effective_config=_config_dto(config) if config is not None else None,
        config_history=[_config_dto(item) for item in detail.config_history],
        next_scheduled_at=(
            FundDisclosureSyncService.next_due_at(config.frequency) if config is not None else None
        ),
        runs=[_run_dto(item) for item in detail.runs],
    )


@router.get(
    "/fund-disclosure-sync-runs/active",
    response_model=ActiveFundDisclosureSyncRunsResponse,
)
def list_active_fund_disclosure_sync_runs(
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> ActiveFundDisclosureSyncRunsResponse:
    """Expose in-flight fund replenishment beside ResearchRun without merging models.

    A fund task remains a historical-disclosure workflow, not a research
    conclusion.  Its latest append-only event is enough to identify whether
    work is currently in progress and to tell the global shell what it is
    doing without leaking source content.
    """
    case_ids = list(db.scalars(CaseTenantAccess(db).case_ids(tenant_id)))
    service = FundDisclosureSyncService(db)
    if any(service.recover_interrupted_runs(case_id) for case_id in case_ids):
        db.commit()
    latest_seq = (
        select(func.max(FundDisclosureSyncRunEvent.seq))
        .where(FundDisclosureSyncRunEvent.run_id == FundDisclosureSyncRun.id)
        .correlate(FundDisclosureSyncRun)
        .scalar_subquery()
    )
    rows = db.execute(
        select(FundDisclosureSyncRun, FundDisclosureSyncRunEvent, ResearchCase)
        .join(
            FundDisclosureSyncRunEvent,
            and_(
                FundDisclosureSyncRunEvent.run_id == FundDisclosureSyncRun.id,
                FundDisclosureSyncRunEvent.seq == latest_seq,
            ),
        )
        .join(ResearchCase, ResearchCase.id == FundDisclosureSyncRun.research_case_id)
        .where(
            FundDisclosureSyncRun.research_case_id.in_(case_ids)
        )
        .where(FundDisclosureSyncRunEvent.status.in_(("queued", "started", "running")))
        .order_by(FundDisclosureSyncRunEvent.created_at.desc(), FundDisclosureSyncRun.id.desc())
    ).all()
    return ActiveFundDisclosureSyncRunsResponse(
        items=[
            ActiveFundDisclosureSyncRunDTO(
                run_id=str(run.id),
                case_id=str(run.research_case_id),
                case_title=case.title,
                trigger=run.trigger,
                status=event.status,
                stage=event.stage,
                message=event.message,
                fund_codes=list(run.fund_codes),
                stock_codes=list(run.stock_codes),
                updated_at=event.created_at,
            )
            for run, event, case in rows
        ]
    )


@router.get(
    "/research-cases/{case_id}/fund-disclosure-sync",
    response_model=FundDisclosureSyncDetailResponse,
)
def get_fund_disclosure_sync(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> FundDisclosureSyncDetailResponse:
    _require_case(db, case_id, tenant_id)
    service = FundDisclosureSyncService(db)
    if service.recover_interrupted_runs(case_id):
        db.commit()
    return _detail_dto(service.detail(case_id))


@router.put(
    "/research-cases/{case_id}/fund-disclosure-sync/config",
    response_model=FundDisclosureSyncConfigDTO,
)
def save_fund_disclosure_sync_config(
    case_id: uuid.UUID,
    payload: SaveFundDisclosureSyncConfigRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> FundDisclosureSyncConfigDTO:
    _require_case(db, case_id, tenant_id)
    try:
        config = FundDisclosureSyncService(db).save_config(
            case_id,
            actor=payload.actor,
            fund_codes=payload.fund_codes,
            frequency=payload.frequency,
            report_period=payload.report_period,
            allow_display=payload.allow_display,
            change_reason=payload.change_reason,
        )
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return _config_dto(config)


def _execute_run(
    service: FundDisclosureSyncService,
    run_id: uuid.UUID,
    *,
    client_factory: Callable[[], GildataMCPClient],
) -> FundDisclosureSyncExecution:
    try:
        client = client_factory()
    except GildataMCPError as exc:
        return service.record_failure(run_id, error=exc, message="数据源不可用；本次范围已保存，可重试")
    try:
        return service.execute(run_id, client=client)
    finally:
        client.close()


@router.post(
    "/research-cases/{case_id}/fund-disclosure-sync/runs",
    response_model=FundDisclosureSyncRunDTO,
    status_code=status.HTTP_201_CREATED,
)
def start_fund_disclosure_sync(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> FundDisclosureSyncRunDTO:
    _require_case(db, case_id, tenant_id)
    service = FundDisclosureSyncService(db)
    try:
        run = service.start_manual_run(case_id)
        db.commit()  # make the frozen manual scope visible before provider work
        execution = _execute_run(service, run.id, client_factory=get_fund_disclosure_client)
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return _run_dto(execution)


@router.post(
    "/research-cases/{case_id}/fund-disclosure-sync/runs/{run_id}/retry",
    response_model=FundDisclosureSyncRunDTO,
    status_code=status.HTTP_201_CREATED,
)
def retry_fund_disclosure_sync(
    case_id: uuid.UUID,
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> FundDisclosureSyncRunDTO:
    _require_case(db, case_id, tenant_id)
    service = FundDisclosureSyncService(db)
    try:
        run = service.start_retry(case_id, run_id)
        db.commit()  # preserve the retry's frozen scope even if provider setup fails
        execution = _execute_run(service, run.id, client_factory=get_fund_disclosure_client)
        db.commit()
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    return _run_dto(execution)
