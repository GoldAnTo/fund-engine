"""Document library v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.documents import DocumentReadQueries
from app.schemas.v1.documents import DocumentDetailResponse, DocumentListResponse
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(prefix="/documents", tags=["documents-v1"])


@router.get("", response_model=DocumentListResponse)
def list_documents(
    case_policy: RequireCaseRoute,
    q: str | None = None,
    case_id: uuid.UUID | None = None,
    cutoff: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    if case_id is not None:
        case_policy.require(case_id)
    return DocumentReadQueries(db).list_documents(
        query=q,
        case_id=case_id,
        basis=HistoricalBasis.from_cutoff(cutoff),
        limit=limit,
        cursor=cursor,
        authorized_case_ids=case_policy.authorized_case_ids(),
    )


@router.get("/{version_id}", response_model=DocumentDetailResponse)
def document_detail(
    case_policy: RequireCaseRoute,
    version_id: uuid.UUID,
    research_mode: bool = False,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    return DocumentReadQueries(db).detail(
        version_id=version_id,
        research_mode=research_mode,
        authorized_case_ids=case_policy.authorized_case_ids(),
    )
