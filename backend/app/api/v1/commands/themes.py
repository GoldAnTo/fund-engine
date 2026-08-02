"""Theme-tag assignment command (横切主题标签)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.v1.commands.common import audit_command, translate_validation
from app.db import get_db
from app.errors import NotFoundError
from app.models.ledger import ResearchCase
from app.repositories.research import ResearchRepository
from app.schemas.v1.themes import ThemeTagsResponse, UpdateThemeTagsRequest
from app.services.themes import ThemeService

router = APIRouter(prefix="/research-cases", tags=["theme-commands-v1"])


@router.patch("/{case_id}/theme-tags", response_model=ThemeTagsResponse)
def update_theme_tags(
    case_id: uuid.UUID,
    payload: UpdateThemeTagsRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ThemeTagsResponse:
    case = db.get(ResearchCase, case_id)
    if case is None:
        raise NotFoundError("ResearchCase", str(case_id))

    result = audit_command(
        db,
        request,
        action="update_theme_tags",
        entity_type="ResearchCase",
        payload=payload.model_dump(mode="json"),
        fn=translate_validation,
        args=(ThemeService(ResearchRepository(db)).apply_theme_tags,),
        kwargs={
            "case": case,
            "desired": payload.tags,
            "proposed_by": payload.proposed_by,
        },
    )
    return ThemeTagsResponse(
        case_id=case.id,
        tags=result.tags,
        events_appended=result.events_appended,
        proposed_by=result.proposed_by,
        proposal_id=result.proposal_id,
        promoted_proposal_id=result.promoted_proposal_id,
    )
