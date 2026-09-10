"""Cross-case theme read routes (横切主题 v1)."""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.themes import ThemeReadQueries
from app.schemas.v1.themes import ThemeListResponse, ThemeViewResponse
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(prefix="/themes", tags=["themes-v1"])


@router.get("", response_model=ThemeListResponse)
def list_themes(
    case_policy: RequireCaseRoute,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    visible_case_ids = set(
        db.scalars(case_policy.authorized_case_ids())
    )
    return ThemeReadQueries(db).list_themes(authorized_case_ids=visible_case_ids)


@router.get("/{tag}", response_model=ThemeViewResponse)
def theme_view(
    case_policy: RequireCaseRoute,
    tag: str,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    visible_case_ids = set(
        db.scalars(case_policy.authorized_case_ids())
    )
    return ThemeReadQueries(db).theme_view(
        tag=tag,
        basis=HistoricalBasis.from_cutoff(cutoff),
        authorized_case_ids=visible_case_ids,
    )
