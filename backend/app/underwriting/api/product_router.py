"""Thin HTTP handlers for the independent investment-research product."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import ConflictError as HttpConflictError
from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import ConflictError as DomainConflictError
from app.models.ledger import ValidationError
from app.underwriting.api.product_schemas import (
    AgendaGeneratorRequest,
    AgendaGeneratorResponse,
    AssessmentPreviewResponse,
    CapitalStructureSnapshotResponse,
    CreateCapitalStructureSnapshotRequest,
    CreateFXSnapshotRequest,
    CreatePriceSnapshotRequest,
    CreateProductHistoricalBasisRequest,
    CreateProductMandateRequest,
    CreateResearchAgendaRequest,
    CreateResearchProjectRequest,
    CreateResearchScopeRequest,
    CreateSecurityRightsRequest,
    EffectiveSecurityRightsResponse,
    FXSnapshotResponse,
    PatchWorkspaceDraftRequest,
    PreviewProductRevisionRequest,
    PriceSnapshotResponse,
    ProductHistoricalBasisResponse,
    ProductMandateResponse,
    ProductObjectSearchItemResponse,
    ProductObjectSearchResponse,
    ProductRevisionResponse,
    ProjectCompanyIdentityResponse,
    ProjectSecurityIdentityResponse,
    PublicationPreviewResponse,
    PublishProductRevisionRequest,
    ResearchAgendaPayloadResponse,
    ResearchAgendaResponse,
    ResearchProjectListResponse,
    ResearchProjectResponse,
    ResearchScopePayloadResponse,
    ResearchScopeResponse,
    RevisionBoundaryResponse,
    SecurityRightsResponse,
    WorkspaceDraftContentResponse,
    WorkspaceDraftResponse,
)
from app.underwriting.api.schemas import UnderwritingErrorEnvelope
from app.underwriting.api.transactions import commit_write
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    ProductHistoricalBasisInput,
    ResearchAgendaInput,
    ResearchScopeInput,
    SecurityRightsInput,
)
from app.underwriting.domain.types import InvestmentMandateInput
from app.underwriting.services.market_snapshots import MarketSnapshotService
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.research_revision_diff import (
    ProductResearchRevisionSummary,
    ResearchRevisionDiffService,
)
from app.underwriting.services.revision_publisher import RevisionPublisher
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)
from app.underwriting.persistence.repository import StaleParentError


router = APIRouter(prefix="/product", tags=["investment-research-product-v1"])
WRITE_ERROR_RESPONSES = {
    409: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}
READ_ERROR_RESPONSES = {
    404: {"model": UnderwritingErrorEnvelope},
    422: {"model": UnderwritingErrorEnvelope},
}
T = TypeVar("T")


def _now() -> datetime:
    return datetime.now(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _domain_value(factory: Callable[[], T]) -> T:
    """Translate dataclass value errors into the shared domain validation type."""
    try:
        return factory()
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _read_value(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except ValidationError as exc:
        raise ValidationFailedError(str(exc)) from exc
    except (DomainConflictError, StaleParentError) as exc:
        raise HttpConflictError(str(exc)) from exc


def _project_response(value) -> ResearchProjectResponse:
    if value.company_identity is None or len(value.security_identities) != len(value.target_security_ids):
        raise ValidationError("project identity snapshot is incomplete")
    return ResearchProjectResponse(
        id=value.id,
        primary_company_id=value.primary_company_id,
        target_security_ids=value.target_security_ids,
        company_identity=ProjectCompanyIdentityResponse(**asdict(value.company_identity)),
        security_identities=tuple(
            ProjectSecurityIdentityResponse(**asdict(item))
            for item in value.security_identities
        ),
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _mandate_response(value) -> ProductMandateResponse:
    if value.project_id is None or value.effective_at is None:
        raise ValidationError("product mandate is incomplete")
    return ProductMandateResponse(
        id=value.id,
        project_id=value.project_id,
        mandate_key=value.mandate_key,
        horizon_years=value.horizon_years,
        base_currency=value.base_currency,
        required_return=value.required_return,
        permanent_loss_limit=value.permanent_loss_limit,
        comparison_set=tuple(value.comparison_set),
        benchmark_key=value.benchmark_key,
        required_excess_return=value.required_excess_return,
        effective_at=_stored_utc(value.effective_at),
        expires_at=(
            _stored_utc(value.expires_at) if value.expires_at is not None else None
        ),
        version=value.version,
        supersedes_id=value.supersedes_id,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _scope_response(value) -> ResearchScopeResponse:
    return ResearchScopeResponse(
        id=value.id,
        project_id=value.project_id,
        version=value.version,
        payload=ResearchScopePayloadResponse(**value.payload),
        supersedes_id=value.supersedes_id,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _agenda_generator_response(value: dict) -> AgendaGeneratorResponse:
    return AgendaGeneratorResponse(**value)


def _agenda_response(value) -> ResearchAgendaResponse:
    return ResearchAgendaResponse(
        id=value.id,
        project_id=value.project_id,
        version=value.version,
        scope_id=value.scope_id,
        payload=ResearchAgendaPayloadResponse(**value.payload),
        generator_provenance=_agenda_generator_response(value.generator_provenance),
        supersedes_id=value.supersedes_id,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _basis_response(value) -> ProductHistoricalBasisResponse:
    return ProductHistoricalBasisResponse(
        id=value.id,
        cutoff_at=_stored_utc(value.cutoff),
        price_as_of=None,
        source_manifest_hash=value.source_manifest_hash,
        definition_bundle_hash=value.definition_bundle_hash,
        parser_bundle_hash=value.parser_bundle_hash,
        boundary_schema_version=value.boundary_schema_version,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _price_response(value) -> PriceSnapshotResponse:
    return PriceSnapshotResponse(
        id=value.id,
        security_identity_id=value.security_identity_id,
        price=value.price,
        currency=value.currency,
        price_type=value.price_type,
        adjustment_basis=value.adjustment_basis,
        market_at=_stored_utc(value.market_at),
        available_at=_stored_utc(value.available_at),
        source_id=value.source_id,
        raw_hash=value.raw_hash,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _fx_response(value) -> FXSnapshotResponse:
    return FXSnapshotResponse(
        id=value.id,
        base_currency=value.base_currency,
        quote_currency=value.quote_currency,
        rate=value.rate,
        quote_direction=value.quote_direction,
        market_at=_stored_utc(value.market_at),
        available_at=_stored_utc(value.available_at),
        source_id=value.source_id,
        raw_hash=value.raw_hash,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _capital_response(value) -> CapitalStructureSnapshotResponse:
    return CapitalStructureSnapshotResponse(
        id=value.id,
        company_id=value.company_id,
        currency=value.currency,
        cash=value.cash,
        debt=value.debt,
        minority_interest=value.minority_interest,
        investments=value.investments,
        pension_liabilities=value.pension_liabilities,
        other_adjustments=value.other_adjustments,
        basic_shares=value.basic_shares,
        diluted_shares=value.diluted_shares,
        potential_dilution_descriptors=tuple(value.potential_dilution_descriptors),
        report_period_start=_stored_utc(value.report_period_start),
        report_period_end=_stored_utc(value.report_period_end),
        market_at=_stored_utc(value.market_at),
        available_at=_stored_utc(value.available_at),
        source_id=value.source_id,
        raw_hash=value.raw_hash,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _rights_response(value) -> SecurityRightsResponse:
    return SecurityRightsResponse(
        id=value.id,
        security_identity_id=value.security_identity_id,
        version=value.version,
        economic_units=value.economic_units,
        votes_per_unit=value.votes_per_unit,
        conversion_ratio=value.conversion_ratio,
        adr_ratio=value.adr_ratio,
        dividend_rights_per_unit=value.dividend_rights_per_unit,
        effective_from=_stored_utc(value.effective_from),
        effective_to=(
            _stored_utc(value.effective_to) if value.effective_to is not None else None
        ),
        source_id=value.source_id,
        raw_hash=value.raw_hash,
        supersedes_id=value.supersedes_id,
        content_hash=value.content_hash,
        created_at=_stored_utc(value.created_at),
    )


def _draft_response(value) -> WorkspaceDraftResponse:
    content = value.content.model_dump(mode="python")
    content.pop("schema_version", None)
    return WorkspaceDraftResponse(
        id=value.id,
        project_id=value.project_id,
        base_revision_id=value.base_revision_id,
        lock_version=value.lock_version,
        content=WorkspaceDraftContentResponse(**content),
        created_at=_stored_utc(value.created_at),
        updated_at=_stored_utc(value.updated_at),
    )


def _preview_response(value) -> PublicationPreviewResponse:
    return PublicationPreviewResponse(
        project_id=value.project_id,
        expected_lock_version=value.expected_lock_version,
        boundary_as_of=value.boundary_as_of,
        assessment=AssessmentPreviewResponse(
            answerability=value.assessment.answerability,
            direction=value.assessment.direction,
            confidence=value.assessment.confidence,
            publication_status=value.assessment.publication_status,
            blockers=value.assessment.blockers,
            resolution_requirements=value.assessment.resolution_requirements,
            next_review_at=value.assessment.next_review_at,
            parent_assessment_id=value.assessment.parent_assessment_id,
            content_hash=value.assessment.content_hash,
        ),
        boundary=RevisionBoundaryResponse(
            historical_basis_id=value.boundary.historical_basis_id,
            mandate_id=value.boundary.mandate_id,
            scope_id=value.boundary.scope_id,
            agenda_id=value.boundary.agenda_id,
            price_snapshot_ids=value.boundary.price_snapshot_ids,
            fx_snapshot_ids=value.boundary.fx_snapshot_ids,
            capital_structure_snapshot_id=value.boundary.capital_structure_snapshot_id,
            security_rights_ids=value.boundary.security_rights_ids,
            parent_revision_id=value.boundary.parent_revision_id,
        ),
        boundary_hash=value.boundary_hash,
        manifest=value.manifest,
        manifest_hash=value.manifest_hash,
    )


def _revision_response(
    value: ProductResearchRevisionSummary,
) -> ProductRevisionResponse:
    return ProductRevisionResponse(
        id=value.id,
        project_id=value.project_id,
        object_id=value.object_id,
        basis_id=value.basis_id,
        boundary_id=value.boundary_id,
        manifest_id=value.manifest_id,
        version_kind=value.version_kind,
        sequence=value.sequence,
        content_hash=value.content_hash,
        cutoff=value.cutoff,
        source_manifest_hash=value.source_manifest_hash,
        manifest_hash=value.manifest_hash,
        parent_revision_id=value.parent_revision_id,
        price_snapshot_ids=value.price_snapshot_ids,
        fx_snapshot_ids=value.fx_snapshot_ids,
        capital_structure_snapshot_id=value.capital_structure_snapshot_id,
        security_rights_ids=value.security_rights_ids,
        market_snapshot_ids=value.market_snapshot_ids,
        answerability=value.answerability,
        direction=value.direction,
        confidence=value.confidence,
        publication_status=value.publication_status,
    )


@router.get(
    "/objects",
    response_model=ProductObjectSearchResponse,
    responses={422: {"model": UnderwritingErrorEnvelope}},
)
def search_product_objects(
    query: Annotated[str, Query(min_length=1, max_length=160)],
    as_of: datetime | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    db: Session = Depends(get_db),
) -> ProductObjectSearchResponse:
    values = _read_value(
        lambda: ResearchProjectService(db, now=_now).search_objects(
            query, as_of or _now(), limit
        )
    )
    return ProductObjectSearchResponse(
        items=tuple(
            ProductObjectSearchItemResponse(**asdict(value)) for value in values
        )
    )


@router.get(
    "/projects",
    response_model=ResearchProjectListResponse,
    responses={422: {"model": UnderwritingErrorEnvelope}},
)
def list_research_projects(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    db: Session = Depends(get_db),
) -> ResearchProjectListResponse:
    values = _read_value(
        lambda: ResearchProjectService(db, now=_now).list_projects(limit)
    )
    return ResearchProjectListResponse(
        items=tuple(_project_response(item) for item in values)
    )


@router.post(
    "/projects",
    response_model=ResearchProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_research_project(
    payload: CreateResearchProjectRequest,
    db: Session = Depends(get_db),
) -> ResearchProjectResponse:
    def operation():
        project = ResearchProjectService(db, now=_now).create_project(
            payload.primary_company_id, payload.target_security_ids
        )
        WorkspaceDraftService(db, now=_now).create(project.id)
        return project

    return _project_response(commit_write(db, operation))


@router.get(
    "/projects/{project_id}",
    response_model=ResearchProjectResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_research_project(
    project_id: UUID, db: Session = Depends(get_db)
) -> ResearchProjectResponse:
    value = _read_value(
        lambda: ResearchProjectService(db, now=_now).project(project_id)
    )
    if value is None:
        raise NotFoundError("research project not found")
    return _project_response(value)


@router.post(
    "/projects/{project_id}/mandates",
    response_model=ProductMandateResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_product_mandate(
    project_id: UUID,
    payload: CreateProductMandateRequest,
    db: Session = Depends(get_db),
) -> ProductMandateResponse:
    def operation():
        value = InvestmentMandateInput(
            f"product.project:{project_id}",
            payload.horizon_years,
            payload.base_currency,
            payload.required_return,
            payload.permanent_loss_limit,
            payload.comparison_set,
        )
        return ResearchProjectService(db, now=_now).append_product_mandate(
            project_id=project_id,
            value=value,
            benchmark_key=payload.benchmark_key,
            required_excess_return=payload.required_excess_return,
            effective_at=payload.effective_at,
            expires_at=payload.expires_at,
            expected_parent_id=payload.expected_parent_id,
        )

    return _mandate_response(commit_write(db, operation))


@router.post(
    "/projects/{project_id}/scopes",
    response_model=ResearchScopeResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_research_scope(
    project_id: UUID,
    payload: CreateResearchScopeRequest,
    db: Session = Depends(get_db),
) -> ResearchScopeResponse:
    def operation():
        value = _domain_value(
            lambda: ResearchScopeInput(
                payload.primary_company_id,
                payload.target_security_ids,
                payload.industry_ids,
                payload.covered_segments,
                payload.user_focus,
                payload.exclusions,
            )
        )
        return ResearchProjectService(db, now=_now).append_scope(
            project_id, value, payload.expected_parent_id
        )

    return _scope_response(commit_write(db, operation))


def _agenda_input(payload: CreateResearchAgendaRequest) -> ResearchAgendaInput:
    generator: AgendaGeneratorRequest = payload.generator
    return _domain_value(
        lambda: ResearchAgendaInput(
            payload.scope_id,
            payload.items,
            AgendaGeneratorInput(
                AgendaGenerationMethod(generator.method),
                generator.template_key,
                generator.template_version,
                generator.model_name,
                generator.prompt_template_version,
                generator.input_summary_hash,
                generator.output_hash,
            ),
        )
    )


@router.post(
    "/projects/{project_id}/agendas",
    response_model=ResearchAgendaResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_research_agenda(
    project_id: UUID,
    payload: CreateResearchAgendaRequest,
    db: Session = Depends(get_db),
) -> ResearchAgendaResponse:
    value = commit_write(
        db,
        lambda: ResearchProjectService(db, now=_now).append_agenda(
            project_id, _agenda_input(payload), payload.expected_parent_id
        ),
    )
    return _agenda_response(value)


@router.post(
    "/historical-bases",
    response_model=ProductHistoricalBasisResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_product_historical_basis(
    payload: CreateProductHistoricalBasisRequest,
    db: Session = Depends(get_db),
) -> ProductHistoricalBasisResponse:
    value = commit_write(
        db,
        lambda: ResearchProjectService(db, now=_now).create_historical_basis(
            _domain_value(
                lambda: ProductHistoricalBasisInput(
                    payload.cutoff_at,
                    payload.source_manifest_hash,
                    payload.definition_bundle_hash,
                    payload.parser_bundle_hash,
                )
            )
        ),
    )
    return _basis_response(value)


@router.post(
    "/market/price-snapshots",
    response_model=PriceSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_price_snapshot(
    payload: CreatePriceSnapshotRequest,
    db: Session = Depends(get_db),
) -> PriceSnapshotResponse:
    value = commit_write(
        db,
        lambda: MarketSnapshotService(db, now=_now).freeze_price(
            _domain_value(
                lambda: PriceSnapshotInput(
                    payload.security_identity_id,
                    payload.price,
                    payload.currency,
                    payload.price_type,
                    payload.adjustment_basis,
                    payload.market_at,
                    payload.available_at,
                    payload.source_id,
                    payload.raw_hash,
                )
            )
        ),
    )
    return _price_response(value)


@router.post(
    "/market/fx-snapshots",
    response_model=FXSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_fx_snapshot(
    payload: CreateFXSnapshotRequest,
    db: Session = Depends(get_db),
) -> FXSnapshotResponse:
    value = commit_write(
        db,
        lambda: MarketSnapshotService(db, now=_now).freeze_fx(
            _domain_value(
                lambda: FXSnapshotInput(
                    payload.base_currency,
                    payload.quote_currency,
                    payload.rate,
                    FxQuoteDirection(payload.quote_direction),
                    payload.market_at,
                    payload.available_at,
                    payload.source_id,
                    payload.raw_hash,
                )
            )
        ),
    )
    return _fx_response(value)


@router.post(
    "/market/capital-structure-snapshots",
    response_model=CapitalStructureSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_capital_structure_snapshot(
    payload: CreateCapitalStructureSnapshotRequest,
    db: Session = Depends(get_db),
) -> CapitalStructureSnapshotResponse:
    value = commit_write(
        db,
        lambda: MarketSnapshotService(db, now=_now).freeze_capital_structure(
            _domain_value(
                lambda: CapitalStructureSnapshotInput(
                    payload.company_id,
                    payload.currency,
                    payload.cash,
                    payload.debt,
                    payload.minority_interest,
                    payload.investments,
                    payload.pension_liabilities,
                    payload.other_adjustments,
                    payload.basic_shares,
                    payload.diluted_shares,
                    payload.potential_dilution_descriptors,
                    payload.report_period_start,
                    payload.report_period_end,
                    payload.market_at,
                    payload.available_at,
                    payload.source_id,
                    payload.raw_hash,
                )
            )
        ),
    )
    return _capital_response(value)


@router.get(
    "/market/security-rights/effective",
    response_model=EffectiveSecurityRightsResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_effective_security_rights(
    security_identity_id: UUID,
    as_of: datetime,
    db: Session = Depends(get_db),
) -> EffectiveSecurityRightsResponse:
    service = MarketSnapshotService(db, now=_now)
    effective = _read_value(
        lambda: service.effective_security_rights(security_identity_id, as_of)
    )
    head = _read_value(lambda: service.security_rights_head(security_identity_id))
    return EffectiveSecurityRightsResponse(
        security_identity_id=security_identity_id,
        as_of=as_of,
        effective=_rights_response(effective) if effective is not None else None,
        head_id=head.id if head is not None else None,
    )


@router.post(
    "/market/security-rights",
    response_model=SecurityRightsResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def create_security_rights(
    payload: CreateSecurityRightsRequest,
    db: Session = Depends(get_db),
) -> SecurityRightsResponse:
    value = commit_write(
        db,
        lambda: MarketSnapshotService(db, now=_now).freeze_security_rights(
            _domain_value(
                lambda: SecurityRightsInput(
                    payload.security_identity_id,
                    payload.economic_units,
                    payload.votes_per_unit,
                    payload.conversion_ratio,
                    payload.adr_ratio,
                    payload.dividend_rights_per_unit,
                    payload.effective_from,
                    payload.effective_to,
                    payload.source_id,
                    payload.raw_hash,
                )
            ),
            expected_parent_id=payload.expected_parent_id,
        ),
    )
    return _rights_response(value)


@router.get(
    "/projects/{project_id}/draft",
    response_model=WorkspaceDraftResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_workspace_draft(
    project_id: UUID, db: Session = Depends(get_db)
) -> WorkspaceDraftResponse:
    value = _read_value(lambda: WorkspaceDraftService(db, now=_now).read(project_id))
    if value is None:
        raise NotFoundError("workspace draft not found")
    return _draft_response(value)


@router.patch(
    "/projects/{project_id}/draft",
    response_model=WorkspaceDraftResponse,
    responses=WRITE_ERROR_RESPONSES,
)
def patch_workspace_draft(
    project_id: UUID,
    payload: PatchWorkspaceDraftRequest,
    db: Session = Depends(get_db),
) -> WorkspaceDraftResponse:
    patch_payload = payload.model_dump(
        mode="python",
        exclude={"schema_version", "expected_lock_version"},
        exclude_unset=True,
    )
    value = commit_write(
        db,
        lambda: WorkspaceDraftService(db, now=_now).save(
            project_id,
            expected_lock_version=payload.expected_lock_version,
            patch=_domain_value(lambda: WorkspaceDraftPatch(**patch_payload)),
        ),
    )
    return _draft_response(value)


@router.post(
    "/projects/{project_id}/publication-preview",
    response_model=PublicationPreviewResponse,
    responses=WRITE_ERROR_RESPONSES,
)
def preview_product_revision(
    project_id: UUID,
    payload: PreviewProductRevisionRequest,
    db: Session = Depends(get_db),
) -> PublicationPreviewResponse:
    value = _read_value(
        lambda: RevisionPublisher(db, now=_now).preview(
            project_id, payload.expected_lock_version
        ),
    )
    return _preview_response(value)


@router.post(
    "/projects/{project_id}/publish",
    response_model=ProductRevisionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=WRITE_ERROR_RESPONSES,
)
def publish_product_revision(
    project_id: UUID,
    payload: PublishProductRevisionRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=120),
    ],
    db: Session = Depends(get_db),
) -> ProductRevisionResponse:
    view = commit_write(
        db,
        lambda: RevisionPublisher(db, now=_now).publish(
            project_id,
            payload.expected_lock_version,
            idempotency_key=idempotency_key,
        ),
    )
    summary = ResearchRevisionDiffService(db).revision_summary(view.id)
    if not isinstance(summary, ProductResearchRevisionSummary):
        raise ValidationError("published revision is not a product revision")
    return _revision_response(summary)


@router.get(
    "/revisions/{revision_id}",
    response_model=ProductRevisionResponse,
    responses=READ_ERROR_RESPONSES,
)
def get_product_revision(
    revision_id: UUID, db: Session = Depends(get_db)
) -> ProductRevisionResponse:
    from app.underwriting.persistence.models import UnderwritingResearchVersion

    if db.get(UnderwritingResearchVersion, revision_id) is None:
        raise NotFoundError("product research revision not found")
    summary = ResearchRevisionDiffService(db).revision_summary(revision_id)
    if not isinstance(summary, ProductResearchRevisionSummary):
        raise NotFoundError("product research revision not found")
    return _revision_response(summary)
