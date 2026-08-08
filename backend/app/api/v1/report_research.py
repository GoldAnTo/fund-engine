"""Report-first research intake endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.v1.report_research import (
    CreateReportResearchRequest,
    ReportResearchCreatedResponse,
    ReportResearchCaseDTO,
    ReportResearchDocumentDTO,
)
from app.services.report_research import CreatedReportResearch, ReportResearchService

router = APIRouter(prefix="/report-research", tags=["report-research-v1"])


def _response(created: CreatedReportResearch) -> ReportResearchCreatedResponse:
    published_at = created.document.published_at
    # SQLite drops timezone information for ``DateTime(timezone=True)`` while
    # PostgreSQL preserves it. All intake timestamps are normalized to UTC, so
    # make the API's published timestamp unambiguous on either backend.
    if published_at is not None and published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return ReportResearchCreatedResponse(
        case=ReportResearchCaseDTO(id=created.case.id, title=created.case.title),
        document=ReportResearchDocumentDTO(
            id=created.document.id,
            input_kind=created.input_kind,
            title=created.document.title or created.case.title,
            publisher=created.publisher,
            published_at=published_at,
            source_url=created.document.source_url,
            parse_state=created.document.parse_state,
        ),
        state=created.state,
        needs_text_or_pages=created.state == "needs_text_or_pages",
        source_statement_ids=created.source_statement_ids,
    )


@router.post("", response_model=ReportResearchCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_report_research(
    payload: CreateReportResearchRequest, db: Session = Depends(get_db)
) -> ReportResearchCreatedResponse:
    return _response(ReportResearchService(db).create_text(payload))


@router.post("/pdf", response_model=ReportResearchCreatedResponse, status_code=status.HTTP_201_CREATED)
def upload_pdf_report(
    raw: bytes = Body(media_type="application/pdf", min_length=1),
    title: str = Query(min_length=1, max_length=300),
    publisher: str | None = Query(default=None, max_length=200),
    published_at: datetime | None = Query(default=None),
    filename: str | None = Query(default=None, max_length=512),
    created_by: str = Query(default="report-research-system", min_length=1, max_length=128),
    db: Session = Depends(get_db),
) -> ReportResearchCreatedResponse:
    created = ReportResearchService(db).create_pdf(
        raw=raw,
        title=title,
        publisher=publisher,
        published_at=published_at,
        filename=filename,
        created_by=created_by,
    )
    return _response(created)
