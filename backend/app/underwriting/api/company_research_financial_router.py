"""HTTP boundary for unreviewed, conditional financial model drafts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ValidationFailedError
from app.models.ledger import ValidationError
from app.underwriting.api.schemas import UnderwritingErrorEnvelope
from app.underwriting.api.transactions import commit_write
from app.underwriting.services.company_research_financial_workspace import (
    CompanyResearchFinancialWorkspace,
)

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
_HASH = r"^[0-9a-f]{64}$"
ERRORS = {code: {"model": UnderwritingErrorEnvelope} for code in (404, 409, 422)}


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinancialDraftSummary(_Closed):
    id: UUID
    sequence: StrictInt = Field(ge=1)
    created_at: datetime
    input_hash: StrictStr = Field(pattern=_HASH)
    content_hash: StrictStr = Field(pattern=_HASH)


class FinancialDraftResponse(FinancialDraftSummary):
    project_id: UUID
    parent_revision_id: UUID
    parent_manifest_hash: StrictStr = Field(pattern=_HASH)
    cutoff_at: datetime
    status: Literal["unreviewed"]
    baseline: dict
    market: dict | None
    inputs: dict
    result: dict


class FinancialWorkspaceResponse(_Closed):
    project_id: UUID
    parent_revision_id: UUID
    parent_manifest_hash: StrictStr = Field(pattern=_HASH)
    cutoff_at: datetime
    baseline: dict
    market: dict | None
    initial_inputs: dict
    latest: FinancialDraftResponse | None
    history: list[FinancialDraftSummary]


class SaveFinancialDraftRequest(_Closed):
    parent_revision_id: UUID
    expected_latest_id: UUID | None
    baseline_content_hash: StrictStr = Field(pattern=_HASH)
    inputs: dict


class FinancialDraftExportResponse(_Closed):
    filename: StrictStr
    media_type: Literal["text/markdown"]
    content: StrictStr
    content_hash: StrictStr = Field(pattern=_HASH)


def _service(db):
    return CompanyResearchFinancialWorkspace(db, now=lambda: datetime.now(UTC))


def _read(operation):
    try:
        return operation()
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc


@router.get(
    "/projects/{project_id}/financial-model",
    response_model=FinancialWorkspaceResponse,
    responses=ERRORS,
)
def get_financial_workspace(project_id: UUID, db: DbSession):
    return _read(lambda: _service(db).read(project_id))


@router.post(
    "/projects/{project_id}/financial-model/drafts",
    response_model=FinancialDraftResponse,
    status_code=201,
    responses=ERRORS,
)
def save_financial_draft(
    project_id: UUID,
    payload: SaveFinancialDraftRequest,
    db: DbSession,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
):
    return commit_write(
        db,
        lambda: _service(db).save(
            project_id=project_id,
            idempotency_key=idempotency_key,
            **payload.model_dump(),
        ),
    )


@router.get(
    "/projects/{project_id}/financial-model/drafts/{draft_id}",
    response_model=FinancialDraftResponse,
    responses=ERRORS,
)
def get_financial_draft(project_id: UUID, draft_id: UUID, db: DbSession):
    return _read(lambda: _service(db).draft(project_id, draft_id))


@router.get(
    "/projects/{project_id}/financial-model/drafts/{draft_id}/export",
    response_model=FinancialDraftExportResponse,
    responses=ERRORS,
)
def export_financial_draft(project_id: UUID, draft_id: UUID, db: DbSession):
    return _read(lambda: _service(db).export(project_id, draft_id))
