"""Cross-case theme read routes (横切主题 v1)."""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.themes import ThemeReadQueries
from app.schemas.v1.themes import ThemeListResponse, ThemeViewResponse

router = APIRouter(prefix="/themes", tags=["themes-v1"])


@router.get("", response_model=ThemeListResponse)
def list_themes(db: Session = Depends(get_db)):
    return ThemeReadQueries(db).list_themes()


@router.get("/{tag}", response_model=ThemeViewResponse)
def theme_view(
    tag: str,
    cutoff: datetime | None = None,
    db: Session = Depends(get_db),
):
    return ThemeReadQueries(db).theme_view(
        tag=tag,
        basis=HistoricalBasis.from_cutoff(cutoff),
    )
