"""Report-first research intake endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

import uuid

from fastapi import APIRouter, Body, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFoundError
from app.models.ledger import DocumentBlob
from app.schemas.v1.report_research import (
    CreateReportResearchRequest,
    ReportResearchCreatedResponse,
    ReportResearchCaseDTO,
    ReportResearchDocumentDTO,
    ReportWikiGraphDTO,
)
from app.queries.report_wiki import ReportWikiQueries
from app.services.report_research import CreatedReportResearch, ReportResearchService
from app.services.document_blobs import LocalImmutableBlobStore

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
    title = title.strip()
    if not title:
        from app.errors import ValidationFailedError

        raise ValidationFailedError("title must not be blank")
    created = ReportResearchService(db).create_pdf(
        raw=raw,
        title=title,
        publisher=publisher,
        published_at=published_at,
        filename=filename,
        created_by=created_by,
    )
    return _response(created)


@router.get("/{case_id}/wiki", response_model=ReportWikiGraphDTO)
def report_wiki_graph(
    case_id: uuid.UUID,
    scope_version: int | None = Query(default=None, ge=1),
    relation_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ReportWikiGraphDTO:
    """Return one document version of a report Wiki graph.

    The default is the latest attached report document.  A caller that already
    has access to this case may select an older immutable report scope through
    ``scope_version``; no response ever combines multiple report revisions.
    Case-level authorization is supplied by the hosting application boundary,
    just as it is for the existing case read endpoints.
    """
    return ReportWikiQueries(db).graph(
        case_id, scope_version=scope_version, relation_id=relation_id
    )


@router.get("/documents/{document_id}/original")
def get_original_report_upload(
    document_id: uuid.UUID, db: Session = Depends(get_db)
) -> Response:
    blob = db.scalar(
        select(DocumentBlob).where(DocumentBlob.document_version_id == document_id)
    )
    if blob is None:
        raise NotFoundError(f"original upload for document {document_id} not found")
    raw = LocalImmutableBlobStore().read(blob)
    return Response(content=raw, media_type=blob.media_type)
