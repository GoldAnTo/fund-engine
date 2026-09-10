"""Knowledge-layer v1 routes (prototype 资料与知识 · 已复核知识层)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.queries.knowledge import KnowledgeQueries
from app.schemas.v1.knowledge import KnowledgeResponse
from app.api.v1.dependencies import RequireCaseRoute

router = APIRouter(prefix="/knowledge", tags=["knowledge-v1"])


@router.get("", response_model=KnowledgeResponse)
def knowledge_layer(
    case_policy: RequireCaseRoute,
    case_id: uuid.UUID | None = None,
    review_state: str | None = Query(
        default=None, pattern="^(machine_generated|reviewed|rejected)$"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
):
    if case_id is not None:
        case_policy.require(case_id)
    return KnowledgeQueries(db).knowledge_layer(
        case_id=case_id,
        review_state=review_state,
        limit=limit,
        authorized_case_ids=case_policy.authorized_case_ids(),
    )
