"""「结论与关键因素」页面 v1 读路由.

GET /api/v1/research-cases/{case_id}/conclusion
对应原型：设计原型11-结论与关键因素.png
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.basis import HistoricalBasis
from app.queries.conclusion import ConclusionQueries
from app.schemas.v1.conclusion import ConclusionResponse

router = APIRouter(prefix="/research-cases", tags=["research-cases-v1"])


@router.get("/{case_id}/conclusion", response_model=ConclusionResponse)
def conclusion(
    case_id: uuid.UUID,
    cutoff: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """读取「结论与关键因素」页面的完整读模型.

    与 dossier / overview 共享同一个 HistoricalBasis：cutoff 为空时按当前时间。
    """
    basis = HistoricalBasis.from_cutoff(cutoff)
    return ConclusionQueries(db).load(case_id=case_id, basis=basis)