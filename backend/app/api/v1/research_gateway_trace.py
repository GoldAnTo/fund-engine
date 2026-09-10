"""Private, read-only task history with frozen source authority validation."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.api.v1.gateway_runtime import require_gateway_runtime
from app.api.v1.tenant_context import ResearchActor, require_gateway_actor
from app.db import get_db
from app.schemas.v1.research_gateway_trace import GatewayTaskTraceDTO
from app.services.research_gateway_trace import read_gateway_task_trace

router = APIRouter(
    prefix="/research-conversations",
    tags=["research-gateway-v1"],
    dependencies=[Depends(require_gateway_actor), Depends(require_gateway_runtime)],
)


@router.get(
    "/{conversation_id}/runs/{run_spec_id}/tasks/{task_id}/trace",
    response_model=GatewayTaskTraceDTO,
)
def get_task_trace(
    conversation_id: UUID,
    run_spec_id: UUID,
    task_id: UUID,
    response: Response,
    actor: Annotated[ResearchActor, Depends(require_gateway_actor)],
    db: Annotated[Session, Depends(get_db)],
) -> GatewayTaskTraceDTO:
    response.headers["Cache-Control"] = "no-store"
    return read_gateway_task_trace(
        db, actor, conversation_id=conversation_id, run_spec_id=run_spec_id, task_id=task_id,
    )
