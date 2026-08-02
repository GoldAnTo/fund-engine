"""Theme-tag assignment command (横切主题标签)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
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
    db: Session = Depends(get_db),
) -> ThemeTagsResponse:
    case = db.get(ResearchCase, case_id)
    if case is None:
        raise NotFoundError("ResearchCase", str(case_id))

    tags, appended = translate_validation(
        ThemeService(ResearchRepository(db)).apply_theme_tags,
        case=case,
        desired=payload.tags,
    )
    commit_or_rollback(db)
    return ThemeTagsResponse(case_id=case.id, tags=tags, events_appended=appended)
