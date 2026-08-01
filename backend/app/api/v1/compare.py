"""Snapshot-compare v1 routes (prototype 版本比较)."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.compare import CaseCompareQueries
from app.queries.knowledge import SnapshotQueries
from app.schemas.v1.compare import CaseCompareResponse
from app.schemas.v1.knowledge import CaseSnapshotsResponse

router = APIRouter(prefix="/research-cases", tags=["snapshot-compare-v1"])


def _as_utc(value: datetime) -> datetime:
    """Normalize query datetimes: naive values are interpreted as UTC.

    Clients legitimately send both aware (``...Z``) and naive (snapshot
    cutoff echoes) datetimes; mixing them in comparisons raises TypeError,
    so the boundary canonicalizes instead of 500ing.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@router.get("/{case_id}/compare", response_model=CaseCompareResponse)
def compare_case(
    case_id: uuid.UUID,
    base: datetime = Query(description="基准截止（较早）"),
    compare: datetime = Query(description="对比截止（较晚）"),
    db: Session = Depends(get_db),
):
    return CaseCompareQueries(db).compare(
        case_id=case_id,
        base_cutoff=_as_utc(base),
        compare_cutoff=_as_utc(compare),
    )


@router.get("/{case_id}/snapshots", response_model=CaseSnapshotsResponse)
def case_snapshots(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """快照列表 (prototype 版本比较 left rail), newest first."""
    return SnapshotQueries(db).snapshots_for_case(case_id=case_id)
