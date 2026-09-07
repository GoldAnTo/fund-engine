"""Case/thesis creation commands (prototype 新建研究).

Write twin of the read-only ``app/api/v1/cases.py``: same URL prefix,
different methods, different tag — no shared state between the two routers.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.errors import ConflictError
from app.schemas.v1.common import ErrorEnvelope
from app.api.v1.tenant_context import require_research_tenant
from app.services.case_tenant_access import CaseTenantAccess
from app.repositories.research import ResearchRepository
from app.schemas.v1.commands import (
    CreateCaseRequest,
    CreatedThesisDTO,
    CreateThesisRequest,
    CreateThesisResponse,
)
from app.services.research import ResearchService

router = APIRouter(
    prefix="/research-cases", tags=["research-case-commands-v1"],
    dependencies=[Depends(require_research_tenant)],
)


def _service(db: Session) -> ResearchService:
    return ResearchService(ResearchRepository(db))


def _thesis_dto(thesis) -> CreatedThesisDTO:
    return CreatedThesisDTO(
        id=str(thesis.id),
        statement=thesis.statement,
        title=thesis.title,
        creator_type=thesis.creator_type,
        review_state=thesis.review_state,
    )


@router.post(
    "",
    response_model=ErrorEnvelope,
    status_code=status.HTTP_409_CONFLICT,
    deprecated=True,
)
def create_case(payload: CreateCaseRequest):
    """Reject the retired source-free intake instead of creating an orphan Case."""
    raise ConflictError(
        "source-free case creation is retired; use POST /api/v1/event-research "
        "with an original source to create a tenant-owned case"
    )


@router.post(
    "/{case_id}/theses",
    response_model=CreateThesisResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_thesis(
    case_id: uuid.UUID,
    payload: CreateThesisRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    CaseTenantAccess(db).require_case(case_id, tenant_id)
    service = _service(db)
    review_state = "draft" if payload.creator_type == "ai" else "confirmed"
    thesis = translate_validation(
        service.add_thesis,
        case_id,
        statement=payload.statement,
        created_by=payload.created_by,
        title=payload.title,
        observation_start=payload.observation_start,
        observation_end=payload.observation_end,
        support_condition=payload.support_condition,
        falsification_condition=payload.falsification_condition,
        next_verification_event=payload.next_verification_event,
        creator_type=payload.creator_type,
        review_state=review_state,
    )
    commit_or_rollback(db)
    return CreateThesisResponse(thesis=_thesis_dto(thesis))
