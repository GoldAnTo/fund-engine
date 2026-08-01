"""Knowledge-layer v1 routes (prototype 资料与知识 · 已复核知识层)."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.queries.knowledge import KnowledgeQueries
from app.schemas.v1.knowledge import KnowledgeResponse

router = APIRouter(prefix="/knowledge", tags=["knowledge-v1"])


@router.get("", response_model=KnowledgeResponse)
def knowledge_layer(
    case_id: uuid.UUID | None = None,
    review_state: str | None = Query(
        default=None, pattern="^(machine_generated|reviewed|rejected)$"
    ),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return KnowledgeQueries(db).knowledge_layer(
        case_id=case_id, review_state=review_state, limit=limit
    )
