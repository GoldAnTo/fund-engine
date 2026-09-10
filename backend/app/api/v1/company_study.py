"""Only mounted on the isolated authenticated Gateway application."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1.gateway_runtime import require_gateway_runtime
from app.api.v1.research_gateway import Database, GatewayActor, IdempotencyKey
from app.api.v1.tenant_context import require_gateway_actor
from app.schemas.v1.company_study import (
    ActivityCreate,
    ActivityDTO,
    LinkCreate,
    MonitorCreate,
    MonitorDTO,
    RetryRequest,
    RevisionCreate,
    RevisionDTO,
    StudyCreate,
    StudyDetailDTO,
    StudyDTO,
    StudyListDTO,
)
from app.services.company_study import CompanyStudyService

router = APIRouter(
    prefix="/company-studies",
    tags=["company-studies-v1"],
    dependencies=[Depends(require_gateway_actor), Depends(require_gateway_runtime)],
)


@router.get("", response_model=StudyListDTO)
def list_studies(actor: GatewayActor, db: Database):
    return CompanyStudyService(db).list(actor)


@router.post("", response_model=StudyDTO, status_code=201)
def create_study(
    payload: StudyCreate,
    actor: GatewayActor,
    db: Database,
    idempotency_key: IdempotencyKey,
):
    return CompanyStudyService(db).create(
        actor, **payload.model_dump(), idempotency_key=idempotency_key
    )


@router.get("/{study_id}", response_model=StudyDetailDTO)
def read_study(study_id: UUID, actor: GatewayActor, db: Database):
    return CompanyStudyService(db).read(actor, study_id)


@router.post("/{study_id}/activities", response_model=ActivityDTO, status_code=201)
def add_activity(
    study_id: UUID,
    payload: ActivityCreate,
    actor: GatewayActor,
    db: Database,
    idempotency_key: IdempotencyKey,
):
    return CompanyStudyService(db).add_activity(
        actor, study_id, **payload.model_dump(), idempotency_key=idempotency_key
    )


@router.post("/{study_id}/links", response_model=ActivityDTO, status_code=201)
def link(
    study_id: UUID,
    payload: LinkCreate,
    actor: GatewayActor,
    db: Database,
    idempotency_key: IdempotencyKey,
):
    return CompanyStudyService(db).link(
        actor, study_id, **payload.model_dump(), idempotency_key=idempotency_key
    )


@router.post("/{study_id}/revisions", response_model=RevisionDTO, status_code=201)
def adopt(
    study_id: UUID,
    payload: RevisionCreate,
    actor: GatewayActor,
    db: Database,
    idempotency_key: IdempotencyKey,
):
    return CompanyStudyService(db).adopt(
        actor, study_id, **payload.model_dump(), idempotency_key=idempotency_key
    )


@router.post("/{study_id}/monitor", response_model=MonitorDTO, status_code=201)
def configure_monitor(
    study_id: UUID,
    payload: MonitorCreate,
    actor: GatewayActor,
    db: Database,
    idempotency_key: IdempotencyKey,
):
    return CompanyStudyService(db).configure_monitor(
        actor, study_id, **payload.model_dump(), idempotency_key=idempotency_key
    )


@router.post(
    "/{study_id}/activities/{activity_id}/retry",
    response_model=ActivityDTO,
    status_code=201,
)
def retry(
    study_id: UUID,
    activity_id: UUID,
    payload: RetryRequest,
    actor: GatewayActor,
    db: Database,
    idempotency_key: IdempotencyKey,
):
    return CompanyStudyService(db).retry(
        actor, study_id, activity_id, idempotency_key=idempotency_key
    )
