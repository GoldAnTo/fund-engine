"""Case/thesis creation commands (prototype 新建研究).

Write twin of the read-only ``app/api/v1/cases.py``: same URL prefix,
different methods, different tag — no shared state between the two routers.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.repositories.research import ResearchRepository
from app.schemas.v1.commands import (
    CreateCaseRequest,
    CreateCaseResponse,
    CreatedThesisDTO,
    CreateThesisRequest,
    CreateThesisResponse,
)
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
):
    service = _service(db)
    case = translate_validation(
        service.add_case,
        title=payload.title,
        industry_topic=payload.industry_topic,
        created_by=payload.created_by,
        research_object=payload.research_object,
        phenomenon=payload.phenomenon,
        core_question=payload.core_question,
        period_start=payload.period_start,
        period_end=payload.period_end,
        evidence_cutoff=payload.evidence_cutoff,
    )
    theses = []
    for spec in payload.initial_theses:
        # AI-drafted propositions start as unconfirmed drafts (AI 草案·未经
        # 人工复核); human-authored ones are confirmed on entry.
        review_state = "draft" if spec.creator_type == "ai" else "confirmed"
        theses.append(
            translate_validation(
                service.add_thesis,
                case.id,
                statement=spec.statement,
                created_by=payload.created_by,
                title=spec.title,
                observation_start=spec.observation_start,
                observation_end=spec.observation_end,
                support_condition=spec.support_condition,
                falsification_condition=spec.falsification_condition,
                next_verification_event=spec.next_verification_event,
                creator_type=spec.creator_type,
                review_state=review_state,
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
):
    service = _service(db)
    review_state = "draft" if payload.creator_type == "ai" else "confirmed"
    thesis = translate_validation(
        service.add_thesis,
        case_id,
        statement=payload.statement,
        created_by=payload.created_by,
        title=payload.title,
        observation_start=payload.observation_start,
        observation_end=payload.observation_end,
        support_condition=payload.support_condition,
        falsification_condition=payload.falsification_condition,
        next_verification_event=payload.next_verification_event,
        creator_type=payload.creator_type,
        review_state=review_state,
    )
    commit_or_rollback(db)
    return CreateThesisResponse(thesis=_thesis_dto(thesis))
