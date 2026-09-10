"""Ingest command (数据接入 · Gildata).

Wraps the Gildata ingest pipeline as a v1 command so the first step of the
engine loop (接入) is triggerable from the UI, alongside extract / propose /
rerun.  The run is idempotent: documents dedupe by content hash and
valuation snapshots by stock + date + metric + source.

Missing ``GILDATA_TOKEN`` or upstream transport failures surface as a 503
``upstream_unavailable`` envelope — real datasource, no silent mock.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback
from app.datasources.gildata.client import GildataMCPClient, GildataMCPError
from app.db import get_db
from app.errors import NotFoundError, UpstreamUnavailableError
from app.models.ledger import ResearchCase
from app.schemas.v1.commands import IngestRequest, IngestResponse
from app.scripts.ingest_real_data import ingest

router = APIRouter(prefix="/documents", tags=["ingest-commands-v1"])


def get_gildata_client() -> Iterator[GildataMCPClient]:
    """Build the real Gildata client from env; tests override this."""
    try:
        client = GildataMCPClient.from_env()
    except GildataMCPError as exc:
        raise UpstreamUnavailableError(str(exc)) from exc
    try:
        yield client
    finally:
        client.close()


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_documents(
    payload: IngestRequest,
    db: Session = Depends(get_db),
    client: GildataMCPClient = Depends(get_gildata_client),
):
    case_id: uuid.UUID | None = None
    if payload.case_id is not None:
        try:
            case_id = uuid.UUID(payload.case_id)
        except ValueError as exc:
            raise NotFoundError(f"case {payload.case_id} not found") from exc
        if db.get(ResearchCase, case_id) is None:
            raise NotFoundError(f"case {payload.case_id} not found")
    try:
        summary = ingest(
            db,
            client,
            case_id=case_id,
            research_queries=payload.research_queries,
            announcement_query=payload.announcement_query,
            news_query=payload.news_query,
            quote_query=payload.quote_query,
            quote_stock_code=payload.quote_stock_code,
            macro_queries=payload.macro_queries,
        )
    except GildataMCPError as exc:
        db.rollback()
        raise UpstreamUnavailableError(str(exc)) from exc
    commit_or_rollback(db)
    return IngestResponse(**summary)
