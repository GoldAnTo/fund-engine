"""Report-first research intake endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

import uuid

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFoundError
from app.models.ledger import DocumentBlob
from app.schemas.v1.report_research import (
    AppendReportResearchScopeRequest,
    CreateReportResearchRequest,
    ReportResearchCreatedResponse,
    ReportResearchCaseDTO,
    ReportResearchDocumentDTO,
    ReportResearchScopeDTO,
    ReportResearchScopeListResponse,
    ReportEmbedWikiGraphDTO,
    ReportWikiGraphDTO,
)
from app.queries.report_wiki import ReportWikiQueries
from app.services.report_research import CreatedReportResearch, ReportResearchService
from app.services.document_blobs import LocalImmutableBlobStore
from app.services.embed_access import EmbedAccessDenied, EmbedAccessService

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
        initial_scope_version=created.initial_scope_version,
        source_statement_ids=created.source_statement_ids,
    )


def _scope_response(scope) -> ReportResearchScopeDTO:
    return ReportResearchScopeDTO(
        version=scope.scope.version,
        document_id=scope.scope.document_version_id,
        visibility_cutoff_at=scope.scope.visibility_cutoff_at,
        research_question=scope.scope.research_question,
        factor_selection=list(scope.scope.factor_selection),
        evidence_plan=list(scope.scope.evidence_plan),
        selected_claim_ids=list(scope.selected_claim_ids),
        selected_relation_ids=list(scope.selected_relation_ids),
        changed_by=scope.scope.changed_by,
        change_summary=scope.scope.change_summary,
        created_at=scope.scope.created_at,
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


@router.get("/{case_id}/scopes", response_model=ReportResearchScopeListResponse)
def list_report_research_scopes(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> ReportResearchScopeListResponse:
    service = ReportResearchService(db)
    try:
        scopes = service.list_scope_selections(case_id)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from None
    items = [_scope_response(scope) for scope in scopes]
    return ReportResearchScopeListResponse(
        items=items,
        current_scope_version=items[-1].version if items else None,
    )


@router.get("/{case_id}/scopes/current", response_model=ReportResearchScopeDTO)
def current_report_research_scope(
    case_id: uuid.UUID, db: Session = Depends(get_db)
) -> ReportResearchScopeDTO:
    service = ReportResearchService(db)
    try:
        scopes = service.list_scope_selections(case_id)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from None
    if not scopes:
        raise NotFoundError("report research scope not found")
    return _scope_response(scopes[-1])


@router.post(
    "/{case_id}/scopes",
    response_model=ReportResearchScopeDTO,
    status_code=status.HTTP_201_CREATED,
)
def append_report_research_scope(
    case_id: uuid.UUID,
    payload: AppendReportResearchScopeRequest,
    db: Session = Depends(get_db),
) -> ReportResearchScopeDTO:
    service = ReportResearchService(db)
    try:
        scope = service.append_scope(
            case_id,
            payload.document_id,
            changed_by=payload.changed_by,
            change_summary=payload.change_summary,
            research_question=payload.research_question,
            factor_selection=payload.factor_selection,
            evidence_plan=payload.evidence_plan,
            selected_claim_ids=payload.selected_claim_ids,
            selected_relation_ids=payload.selected_relation_ids,
        )
        db.commit()
        selections = service.list_scope_selections(case_id)
    except ValueError as exc:
        db.rollback()
        if str(exc) == "report research case not found":
            raise NotFoundError(str(exc)) from None
        from app.errors import ValidationFailedError

        raise ValidationFailedError(str(exc)) from None
    selected = next(item for item in selections if item.scope.id == scope.id)
    return _scope_response(selected)


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


@router.api_route(
    "/{case_id}/embed/wiki",
    methods=["GET", "HEAD"],
    response_model=ReportEmbedWikiGraphDTO,
)
def embedded_report_wiki_graph(
    case_id: uuid.UUID,
    request: Request,
    response: Response,
    x_embed_token: str | None = Header(default=None, alias="X-Embed-Token"),
    db: Session = Depends(get_db),
) -> ReportEmbedWikiGraphDTO:
    """Render one case's redacted Wiki graph under a scoped bearer grant.

    Tokens are accepted only in a request header.  They are intentionally not
    read from query parameters so an embedding host cannot leak a credential
    through browser history, server logs, or a referrer header.
    """
    origin = request.headers.get("origin")
    try:
        grant = EmbedAccessService(db).require_read_only(x_embed_token, case_id, origin)
    except EmbedAccessDenied as exc:
        raise HTTPException(status_code=exc.status_code, detail="embed access denied") from None
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if origin is not None:
        # ``require_read_only`` has already checked this exact normalized
        # origin.  Never send a wildcard for bearer-token protected content.
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    return ReportWikiQueries(db).embed_graph(grant.research_case_id)


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
