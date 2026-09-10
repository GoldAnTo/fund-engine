"""Authenticated private conversation commands, snapshots, and safe event stream."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.v1.gateway_runtime import require_gateway_runtime
from app.api.v1.tenant_context import ResearchActor, require_gateway_actor
from app.db import SessionLocal, get_db
from app.errors import UpstreamUnavailableError, ValidationFailedError
from app.schemas.v1.research_gateway import (
    CommandReceiptDTO,
    CommandRequest,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationSnapshotDTO,
    ConversationSummaryDTO,
    GatewayIntentReceiptDTO,
    GatewayReceiptDTO,
    MessageSendRequest,
)
from app.schemas.v1.research_gateway_content import (
    EvidenceDetailDTO,
    ResearchContentDTO,
)
from app.services.event_extraction import EventExtractionProviderError
from app.services.research_gateway import ResearchGateway
from app.services.research_gateway_content import ResearchGatewayContent

router = APIRouter(
    prefix="/research-conversations",
    tags=["research-gateway-v1"],
    dependencies=[Depends(require_gateway_actor), Depends(require_gateway_runtime)],
)


def get_stream_session_factory() -> Callable[[], Session]:
    """Each stream poll owns a separate, short database session."""
    return SessionLocal


GatewayActor = Annotated[ResearchActor, Depends(require_gateway_actor)]
Database = Annotated[Session, Depends(get_db)]
IdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=512)
]
StreamSessionFactory = Annotated[
    Callable[[], Session], Depends(get_stream_session_factory)
]


def _send_message(
    db: Session,
    actor: ResearchActor,
    *,
    conversation_id: uuid.UUID | None,
    text: str,
    idempotency_key: str,
) -> GatewayReceiptDTO | GatewayIntentReceiptDTO:
    try:
        receipt = ResearchGateway(db).send_message(
            actor=actor,
            conversation_id=conversation_id,
            text=text,
            idempotency_key=idempotency_key,
        )
    except EventExtractionProviderError as exc:
        db.rollback()
        raise UpstreamUnavailableError("研究服务暂时不可用，请稍后重试") from exc
    except ValueError as exc:
        db.rollback()
        raise ValidationFailedError("研究输入无效，请检查后重试") from exc
    payload = receipt.to_payload()
    if payload.get("receipt_kind") == "intent":
        return GatewayIntentReceiptDTO.model_validate(payload)
    return GatewayReceiptDTO.model_validate(payload)


@router.post("", response_model=GatewayReceiptDTO, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreateRequest,
    idempotency_key: IdempotencyKey,
    actor: GatewayActor,
    db: Database,
) -> GatewayReceiptDTO:
    receipt = _send_message(
        db,
        actor,
        conversation_id=None,
        text=payload.initial_message,
        idempotency_key=idempotency_key,
    )
    if not isinstance(receipt, GatewayReceiptDTO):
        raise TypeError("initial Gateway research request was rejected")
    return receipt


@router.post(
    "/{conversation_id}/messages",
    response_model=GatewayReceiptDTO | GatewayIntentReceiptDTO,
    status_code=status.HTTP_201_CREATED,
)
def send_message(
    conversation_id: uuid.UUID,
    payload: MessageSendRequest,
    idempotency_key: IdempotencyKey,
    actor: GatewayActor,
    db: Database,
) -> GatewayReceiptDTO | GatewayIntentReceiptDTO:
    return _send_message(
        db,
        actor,
        conversation_id=conversation_id,
        text=payload.text,
        idempotency_key=idempotency_key,
    )


@router.get("", response_model=ConversationListResponse)
def list_conversations(
    actor: GatewayActor,
    db: Database,
) -> ConversationListResponse:
    return ConversationListResponse(
        conversations=[
            ConversationSummaryDTO(
                conversation_id=str(conversation.id),
                title=conversation.title,
                created_at=conversation.created_at,
                updated_at=conversation.updated_at,
                latest_sequence=conversation.next_event_sequence - 1,
            )
            for conversation in ResearchGateway(db).list_conversations(actor)
        ]
    )


@router.get("/{conversation_id}", response_model=ConversationSnapshotDTO)
@router.get("/{conversation_id}/snapshot", response_model=ConversationSnapshotDTO)
def read_conversation(
    conversation_id: uuid.UUID,
    actor: GatewayActor,
    db: Database,
) -> ConversationSnapshotDTO:
    return ResearchGateway(db).read_conversation(actor, conversation_id)


@router.get(
    "/{conversation_id}/runs/{run_spec_id}/research", response_model=ResearchContentDTO
)
def read_research_content(
    conversation_id: uuid.UUID,
    run_spec_id: uuid.UUID,
    response: Response,
    actor: GatewayActor,
    db: Database,
) -> ResearchContentDTO:
    content = ResearchGatewayContent(db).read_research(actor, conversation_id, run_spec_id)
    response.headers["Cache-Control"] = "no-store"
    return content


@router.get(
    "/{conversation_id}/runs/{run_spec_id}/evidence/{evidence_link_id}",
    response_model=EvidenceDetailDTO,
)
def read_research_evidence(
    conversation_id: uuid.UUID,
    run_spec_id: uuid.UUID,
    evidence_link_id: uuid.UUID,
    response: Response,
    actor: GatewayActor,
    db: Database,
) -> EvidenceDetailDTO:
    content = ResearchGatewayContent(db).read_evidence(
        actor, conversation_id, run_spec_id, evidence_link_id
    )
    response.headers["Cache-Control"] = "no-store"
    return content


@router.post(
    "/{conversation_id}/runs/{run_spec_id}/commands", response_model=CommandReceiptDTO
)
def issue_command(
    conversation_id: uuid.UUID,
    run_spec_id: uuid.UUID,
    payload: CommandRequest,
    idempotency_key: IdempotencyKey,
    actor: GatewayActor,
    db: Database,
) -> CommandReceiptDTO:
    receipt = ResearchGateway(db).issue_command(
        actor=actor,
        conversation_id=conversation_id,
        run_spec_id=run_spec_id,
        command=payload.kind,
        idempotency_key=idempotency_key,
    )
    return CommandReceiptDTO.model_validate(receipt.to_payload())


@router.get(
    "/{conversation_id}/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
def stream_events(
    conversation_id: uuid.UUID,
    authorization: Annotated[str, Header()],
    actor: GatewayActor,
    session_factory: StreamSessionFactory,
    after_sequence: str | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    # Authorize in a fully closed session before a generator or streaming
    # response exists. The stream itself revalidates credentials every poll.
    with session_factory() as db:
        snapshot = ResearchGateway(db).read_conversation(actor, conversation_id)
    from app.services.research_gateway_stream import resolve_cursor, sse_frames

    cursor = resolve_cursor(after_sequence, last_event_id)
    if cursor > snapshot.latest_sequence:
        raise ValidationFailedError("invalid Gateway event cursor")
    return StreamingResponse(
        sse_frames(
            session_factory=session_factory,
            actor=actor,
            conversation_id=conversation_id,
            after_sequence=cursor,
            authorization=authorization,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
