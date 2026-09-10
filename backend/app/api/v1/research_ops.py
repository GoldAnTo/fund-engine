"""Research-operations KPI v1 routes (研究效能度量)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.queries.research_ops import ResearchOpsQueries
from app.schemas.v1.research_ops import ResearchOpsResponse
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(prefix="/research-ops", tags=["research-ops-v1"])


@router.get("/kpis", response_model=ResearchOpsResponse)
def research_ops_kpis(
    case_policy: RequireCaseRoute,
    case_id: uuid.UUID | None = None,
    as_of: datetime | None = None,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    """研究效能 KPI 快照：审核吞吐、人机一致率、判断时滞。

    ``as_of`` 缺省为当前时间；传入历史时点则按时点回放语义统计（之后
    创建的记录不参与）。
    """
    if case_id is not None:
        case_policy.require(case_id)
    return ResearchOpsQueries(db).kpis(
        case_id=case_id,
        as_of=as_of,
        authorized_case_ids=case_policy.authorized_case_ids(),
    )
