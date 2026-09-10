"""Case/thesis creation commands (prototype 新建研究).

Write twin of the read-only ``app/api/v1/cases.py``: same URL prefix,
different methods, different tag — no shared state between the two routers.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.repositories.documents import DocumentRepository
from app.repositories.research import ResearchRepository
from app.schemas.v1.commands import (
    CreateCaseRequest,
    CreateCaseResponse,
    CreatedThesisDTO,
    CreateThesisRequest,
    CreateThesisResponse,
)
from app.services.case_authorization import CaseAuthorizationService
from app.services.case_tenant_access import CaseTenantAccess
from app.services.ingest import DocumentService
from app.services.research import ResearchService

router = APIRouter(prefix="/research-cases", tags=["research-case-commands-v1"])


def _service(db: Session) -> ResearchService:
    return ResearchService(ResearchRepository(db))


def _thesis_dto(thesis) -> CreatedThesisDTO:
    return CreatedThesisDTO(
        id=str(thesis.id),
        statement=thesis.statement,
        title=thesis.title,
        creator_type=thesis.creator_type,
        review_state=thesis.review_state,
    )


@router.post(
    "",
    response_model=CreateCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_case(
    payload: CreateCaseRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    service = _service(db)
    created_by = actor.server_actor
    case = translate_validation(
        service.add_case,
        title=payload.title,
        industry_topic=payload.industry_topic,
        created_by=created_by,
        research_object=payload.research_object,
        phenomenon=payload.phenomenon,
        core_question=payload.core_question,
        period_start=payload.period_start,
        period_end=payload.period_end,
        evidence_cutoff=payload.evidence_cutoff,
    )
    intake = DocumentService(DocumentRepository(db))
    intake_document = intake.freeze(
        raw=payload.model_dump_json(exclude={"created_by"}).encode("utf-8"),
        source_url=f"case-intake://{case.id}",
        parser_version="case-intake-v1",
        title=f"{payload.title} · 创建表单",
        parse_state="partial",
        source_authority="user_supplied",
    )
    intake.attach_to_case(
        research_case_id=case.id,
        document_version_id=intake_document.id,
    )
    intake.add_span(
        document_version_id=intake_document.id,
        locator={"kind": "case_intake", "case_id": str(case.id)},
        verbatim_text=payload.model_dump_json(exclude={"created_by"}),
    )
    CaseTenantAccess(db).admit_initial_case(
        case_id=case.id,
        tenant_id=actor.tenant_id,
        initial_document_version_id=intake_document.id,
        admitted_by=created_by,
    )
    CaseAuthorizationService(db).grant_initial_owner_for_new_case(
        case.id,
        actor,
        actor.user_id,
        reason="research Case creator",
    )
    theses = []
    for spec in payload.initial_theses:
        theses.append(
            translate_validation(
                service.add_thesis,
                case.id,
                statement=spec.statement,
                created_by=created_by,
                title=spec.title,
                observation_start=spec.observation_start,
                observation_end=spec.observation_end,
                support_condition=spec.support_condition,
                falsification_condition=spec.falsification_condition,
                next_verification_event=spec.next_verification_event,
                creator_type="human",
                review_state="confirmed",
            )
        )
    commit_or_rollback(db)
    return CreateCaseResponse(
        case_id=str(case.id),
        theses=[_thesis_dto(t) for t in theses],
    )


@router.post(
    "/{case_id}/theses",
    response_model=CreateThesisResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_thesis(
    case_id: uuid.UUID,
    payload: CreateThesisRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    service = _service(db)
    thesis = translate_validation(
        service.add_thesis,
        case_id,
        statement=payload.statement,
        created_by=actor.server_actor,
        title=payload.title,
        observation_start=payload.observation_start,
        observation_end=payload.observation_end,
        support_condition=payload.support_condition,
        falsification_condition=payload.falsification_condition,
        next_verification_event=payload.next_verification_event,
        creator_type="human",
        review_state="confirmed",
    )
    commit_or_rollback(db)
    return CreateThesisResponse(thesis=_thesis_dto(thesis))
