"""One-click automatic-research commands and progress read model."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import require_research_tenant
from app.db import get_db
from app.errors import (
    UpstreamUnavailableError,
    ValidationFailedError,
)
from app.queries.automatic_research import AutomaticResearchQueries
from app.schemas.v1.automatic_research import (
    AutomaticResearchStartRequest,
    AutomaticResearchStartResponse,
    AutomaticResearchViewDTO,
)
from app.services.automatic_research_intake import AutomaticResearchIntakeService
from app.services.automatic_research_retry import AutomaticResearchRetryService
from app.services.event_extraction import EventExtractionProviderError


router = APIRouter(
    prefix="/automatic-research",
    tags=["automatic-research-v1"],
    dependencies=[Depends(require_research_tenant)],
)


@router.post(
    "",
    response_model=AutomaticResearchStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def start_automatic_research(
    payload: AutomaticResearchStartRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> AutomaticResearchStartResponse:
    try:
        started = AutomaticResearchIntakeService(db).start(
            payload.input, tenant_id=tenant_id
        )
    except EventExtractionProviderError as exc:
        db.rollback()
        raise UpstreamUnavailableError(
            "自动研究服务暂时不可用，请稍后重试"
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise ValidationFailedError("自动研究输入无效，请检查后重试") from exc
    return AutomaticResearchStartResponse(
        case_id=started.case_id,
        run_id=started.run_id,
        status="queued",
    )


@router.get("/{case_id}", response_model=AutomaticResearchViewDTO)
def get_automatic_research(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> AutomaticResearchViewDTO:
    return AutomaticResearchQueries(db).get(case_id, tenant_id)


@router.post(
    "/{case_id}/retry",
    response_model=AutomaticResearchStartResponse,
    status_code=status.HTTP_201_CREATED,
)
def retry_automatic_research(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
) -> AutomaticResearchStartResponse:
    retried = AutomaticResearchRetryService(db).retry(
        case_id, tenant_id=tenant_id
    )
    return AutomaticResearchStartResponse(
        case_id=retried.case_id, run_id=retried.run_id, status="queued"
    )
