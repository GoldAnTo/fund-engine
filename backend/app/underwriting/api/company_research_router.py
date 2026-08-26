"""Thin HTTP boundary for high-level company research initialization."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import ValidationError
from app.underwriting.api.company_research_schemas import (
    CompanyResearchAgendaModuleResponse,
    CompanyResearchArtifactResponse,
    CompanyResearchEvidenceReviewResponse,
    CompanyResearchIdentityResponse,
    CompanyResearchPreparationResponse,
    CompanyResearchPreviewRequest,
    CompanyResearchPreviewResponse,
    CompanyResearchProjectResponse,
    CompanyResearchSecurityIdentityResponse,
    CompanyResearchWorkbenchModuleResponse,
    CompanyResearchWorkspaceCompanyResponse,
    CompanyResearchWorkspaceDraftResponse,
    CompanyResearchWorkspacePreparationResponse,
    CompanyResearchWorkspaceResponse,
    InitializeCompanyResearchRequest,
    ReviewCompanyEvidenceRequest,
)
from app.underwriting.api.schemas import UnderwritingErrorEnvelope
from app.underwriting.api.transactions import commit_write
from app.underwriting.domain.company_research import CompanyResearchPreview
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitialization,
    CompanyResearchInitializer,
    CompanyResearchPreparationService,
    CompanyResearchProjectStatus,
)
from app.underwriting.services.company_research_workbench import (
    CompanyResearchWorkbench,
    WorkbenchArtifact,
)

router = APIRouter(prefix="/product/company-research", tags=["company-research-v1"])
DbSession = Annotated[Session, Depends(get_db)]
WRITE_ERROR_RESPONSES = {
    409: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}
READ_ERROR_RESPONSES = {
    404: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}


def _now() -> datetime:
    return datetime.now(UTC)


def _stored_utc(value: datetime) -> datetime:
    """SQLite returns timezone columns as naive values; persisted values are UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _preview_response(value: CompanyResearchPreview) -> CompanyResearchPreviewResponse:
    return CompanyResearchPreviewResponse(
        company=CompanyResearchIdentityResponse(**value.company.canonical_payload()),
        securities=tuple(
            CompanyResearchSecurityIdentityResponse(
                object_id=security.object_id,
                external_key=security.external_key,
                canonical_name=security.canonical_name,
                symbol=security.symbol,
                exchange=security.exchange,
                share_class=security.share_class,
                trading_currency=security.trading_currency,
            )
            for security in value.securities
        ),
        strategy_version=value.strategy_version,
        horizon_years=value.horizon_years,
        base_currency=value.base_currency,
        required_return=value.required_return,
        permanent_loss_limit=value.permanent_loss_limit,
        cutoff_at=value.cutoff_at,
        agenda=tuple(
            CompanyResearchAgendaModuleResponse(**module.canonical_payload())
            for module in value.generic_modules
        ),
        preview_hash=value.input_hash,
    )


def _project_response(
    value: CompanyResearchProjectStatus,
) -> CompanyResearchProjectResponse:
    preparation = value.preparation
    return CompanyResearchProjectResponse(
        project_id=value.project.id,
        company_id=value.project.primary_company_id,
        preparation=CompanyResearchPreparationResponse(
            id=preparation.id,
            project_id=preparation.project_id,
            request_hash=preparation.request_hash,
            strategy_version=preparation.strategy_version,
            status=preparation.status,
            current_step=preparation.current_step,
            progress=preparation.progress,
            attempt=preparation.attempt,
            next_attempt_at=(
                _stored_utc(preparation.next_attempt_at)
                if preparation.next_attempt_at is not None
                else None
            ),
            last_error_code=preparation.last_error_code,
        ),
    )


def _initialization_response(
    value: CompanyResearchInitialization,
) -> CompanyResearchProjectResponse:
    return _project_response(
        CompanyResearchProjectStatus(
            project=value.project, preparation=value.preparation
        )
    )


def _artifact_response(value: WorkbenchArtifact) -> CompanyResearchArtifactResponse:
    return CompanyResearchArtifactResponse(
        id=value.id, kind=value.kind, version=value.version,
        input_hash=value.input_hash, content_hash=value.content_hash,
        payload=value.payload, source_refs=value.source_refs,
    )


def _workspace_response(value) -> CompanyResearchWorkspaceResponse:
    return CompanyResearchWorkspaceResponse(
        project_id=value.project_id,
        company=CompanyResearchWorkspaceCompanyResponse(
            id=value.company.id, object_id=value.company.id,
            external_key=value.company.external_key, canonical_name=value.company.canonical_name,
        ),
        preparation=CompanyResearchWorkspacePreparationResponse(
            id=value.preparation.id, status=value.preparation.status,
            current_step=value.preparation.current_step, progress=value.preparation.progress,
        ),
        modules=tuple(CompanyResearchWorkbenchModuleResponse(
            key=item.key, state=item.state,
            artifact=_artifact_response(item.artifact) if item.artifact else None,
        ) for item in value.modules),
        source_count=value.source_count, gap_count=value.gap_count,
        draft=CompanyResearchWorkspaceDraftResponse(
            id=value.draft.id, lock_version=value.draft.lock_version,
            base_revision_id=value.draft.base_revision_id,
        ), selected_revision=value.selected_revision, change_summary=value.change_summary,
    )


def _read(operation):
    try:
        return operation()
    except ValidationError as exc:
        message = str(exc)
        if message in {
            "company research project not found",
            "company research preparation not found",
        }:
            raise NotFoundError(message) from exc
        raise ValidationFailedError(message) from exc


@router.post(
    "/preview",
    response_model=CompanyResearchPreviewResponse,
    responses=READ_ERROR_RESPONSES,
)
def preview_company_research(
    payload: CompanyResearchPreviewRequest, db: DbSession
) -> CompanyResearchPreviewResponse:
    return _preview_response(
        _read(
            lambda: CompanyResearchInitializer(db, now=_now).preview(
                company_id=payload.company_id, cutoff_at=payload.cutoff_at
            )
        )
    )


@router.post(
    "/initializations",
    response_model=CompanyResearchProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def initialize_company_research(
    payload: InitializeCompanyResearchRequest,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
    db: DbSession,
) -> CompanyResearchProjectResponse:
    return _initialization_response(
        commit_write(
            db,
            lambda: CompanyResearchInitializer(db, now=_now).initialize(
                preview_hash=payload.preview_hash,
                company_id=payload.company_id,
                cutoff_at=payload.cutoff_at,
                idempotency_key=idempotency_key,
            ),
        )
    )


@router.get(
    "/projects/{project_id}",
    response_model=CompanyResearchProjectResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_company_research_project(
    project_id: UUID, db: DbSession
) -> CompanyResearchProjectResponse:
    return _project_response(
        _read(
            lambda: CompanyResearchPreparationService(db, now=_now).status(
                project_id=project_id
            )
        )
    )


@router.post(
    "/projects/{project_id}/retry",
    response_model=CompanyResearchProjectResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**READ_ERROR_RESPONSES, **WRITE_ERROR_RESPONSES},
)
def retry_company_research_project(
    project_id: UUID, db: DbSession
) -> CompanyResearchProjectResponse:
    return _project_response(
        commit_write(
            db,
            lambda: _read(
                lambda: CompanyResearchPreparationService(db, now=_now).retry(
                    project_id=project_id
                )
            ),
        )
    )


@router.get(
    "/projects/{project_id}/workspace",
    response_model=CompanyResearchWorkspaceResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_company_research_workspace(
    project_id: UUID, db: DbSession
) -> CompanyResearchWorkspaceResponse:
    return _workspace_response(_read(lambda: CompanyResearchWorkbench(db, now=_now).workspace(project_id=project_id)))


@router.post(
    "/projects/{project_id}/evidence-reviews",
    response_model=CompanyResearchEvidenceReviewResponse,
    responses={**READ_ERROR_RESPONSES, **WRITE_ERROR_RESPONSES},
)
def review_company_evidence(
    project_id: UUID, payload: ReviewCompanyEvidenceRequest, db: DbSession
) -> CompanyResearchEvidenceReviewResponse:
    result = commit_write(db, lambda: _read(lambda: CompanyResearchWorkbench(db, now=_now).review_evidence(
        project_id=project_id, evidence_artifact_id=payload.evidence_artifact_id,
        fact_key=payload.fact_key, decision=payload.decision, expected_head_id=payload.expected_head_id,
    )))
    return CompanyResearchEvidenceReviewResponse(evidence_artifact=_artifact_response(result.evidence_artifact))
