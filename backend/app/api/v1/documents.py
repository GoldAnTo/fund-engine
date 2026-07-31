"""Document library v1 routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.documents import DocumentReadQueries
from app.schemas.v1.documents import DocumentDetailResponse, DocumentListResponse

router = APIRouter(prefix="/documents", tags=["documents-v1"])


@router.get("", response_model=DocumentListResponse)
def list_documents(
    q: str | None = None,
    cutoff: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    db: Session = Depends(get_db),
):
    return DocumentReadQueries(db).list_documents(
        query=q,
        basis=HistoricalBasis.from_cutoff(cutoff),
        limit=limit,
        cursor=cursor,
    )


@router.get("/{version_id}", response_model=DocumentDetailResponse)
def document_detail(
    version_id: uuid.UUID,
    research_mode: bool = False,
    db: Session = Depends(get_db),
):
    return DocumentReadQueries(db).detail(
        version_id=version_id, research_mode=research_mode
    )
