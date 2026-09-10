"""Authenticated professional work, versioned controls and human review."""
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.v1.gateway_runtime import require_gateway_runtime
from app.api.v1.research_gateway import Database, GatewayActor, IdempotencyKey
from app.api.v1.tenant_context import require_gateway_actor
from app.schemas.v1.research_team import (
    ResearchTeamDTO,
    TeamActionReceiptDTO,
    TeamCommandRequest,
    TeamMessageRequest,
    TeamReviewRequest,
)
from app.services.research_team import ResearchTeamService

router = APIRouter(
    prefix='/research-conversations/{conversation_id}/runs/{run_spec_id}/team',
    tags=['professional-research-team'],
    dependencies=[Depends(require_gateway_actor), Depends(require_gateway_runtime)],
)


@router.get('', response_model=ResearchTeamDTO)
def read_team(conversation_id: UUID, run_spec_id: UUID, actor: GatewayActor, db: Database):
    return ResearchTeamService(db).read(actor, conversation_id, run_spec_id)


@router.post('/messages', response_model=TeamActionReceiptDTO)
def send_team_message(conversation_id: UUID, run_spec_id: UUID, payload: TeamMessageRequest, actor: GatewayActor, db: Database, idempotency_key: IdempotencyKey):
    return ResearchTeamService(db).message(actor, conversation_id, run_spec_id, **payload.model_dump(), idempotency_key=idempotency_key)


@router.post('/commands', response_model=TeamActionReceiptDTO)
def command_team(conversation_id: UUID, run_spec_id: UUID, payload: TeamCommandRequest, actor: GatewayActor, db: Database, idempotency_key: IdempotencyKey):
    return ResearchTeamService(db).command(actor, conversation_id, run_spec_id, **payload.model_dump(), idempotency_key=idempotency_key)


@router.post('/reviews', response_model=TeamActionReceiptDTO)
def review_team(conversation_id: UUID, run_spec_id: UUID, payload: TeamReviewRequest, actor: GatewayActor, db: Database, idempotency_key: IdempotencyKey):
    return ResearchTeamService(db).review(actor, conversation_id, run_spec_id, **payload.model_dump(), idempotency_key=idempotency_key)
