"""Authenticated commands for advancing the event-research workflow."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.domain.acquisition import WorkflowLedgerStatus
from app.errors import ValidationFailedError
from app.models.research_orchestration import ResearchOrchestration
from app.schemas.v1.research_workflow import (
    ConfirmResearchWorkflowRequest,
    ConfirmResearchWorkflowResponse,
    DecideResearchWorkflowRequest,
    ResumeProtocolWorkflowRequest,
    ResearchWorkflowResponse,
    WorkflowEventsPageDTO,
    WorkflowLedgerPageDTO,
    WorkflowNotInitializedEnvelopeDTO,
    WorkflowNextActionDTO,
)
from app.queries.research_workflow import ResearchWorkflowQueries
from app.services.research_orchestration import (
    OrchestrationPrincipal,
    ResearchOrchestrationService,
    ScopeDecision,
)


router = APIRouter(tags=["research-workflow-v1"])


@router.get(
    "/event-research/{case_id}/workflow",
    response_model=ResearchWorkflowResponse,
    responses={409: {"model": WorkflowNotInitializedEnvelopeDTO}},
)
def get_research_workflow(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ResearchWorkflowResponse:
    return ResearchWorkflowQueries(db).get(case_id, tenant_id=actor.tenant_id)


@router.get(
    "/event-research/{case_id}/workflow/events",
    response_model=WorkflowEventsPageDTO,
    responses={409: {"model": WorkflowNotInitializedEnvelopeDTO}},
)
def get_research_workflow_events(
    case_id: uuid.UUID,
    cursor: int | None = Query(default=None, ge=0),
    after: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> WorkflowEventsPageDTO:
    if cursor is not None and after is not None:
        raise ValidationFailedError("provide after or legacy cursor, not both")
    return ResearchWorkflowQueries(db).events(
        case_id,
        tenant_id=actor.tenant_id,
        cursor=after if after is not None else cursor,
        limit=limit,
    )


@router.get(
    "/event-research/{case_id}/workflow/ledger",
    response_model=WorkflowLedgerPageDTO,
    responses={409: {"model": WorkflowNotInitializedEnvelopeDTO}},
)
def get_research_workflow_ledger(
    case_id: uuid.UUID,
    status_filter: WorkflowLedgerStatus | None = Query(
        default=None,
        alias="status",
    ),
    cursor: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> WorkflowLedgerPageDTO:
    return ResearchWorkflowQueries(db).ledger(
        case_id,
        tenant_id=actor.tenant_id,
        status=status_filter,
        cursor=cursor,
        limit=limit,
    )


def _workflow_response(
    orchestration: ResearchOrchestration,
) -> ConfirmResearchWorkflowResponse:
    next_action = None
    if any(
        value is not None
        for value in (
            orchestration.next_action_kind,
            orchestration.next_action_label,
            orchestration.next_action_payload,
        )
    ):
        next_action = WorkflowNextActionDTO(
            kind=orchestration.next_action_kind,
            label=orchestration.next_action_label,
            payload=orchestration.next_action_payload,
        )
    return ConfirmResearchWorkflowResponse(
        orchestration_id=orchestration.id,
        case_id=orchestration.research_case_id,
        scope_version_id=orchestration.current_scope_version_id,
        research_run_id=orchestration.current_research_run_id,
        state=orchestration.state,
        user_stage=orchestration.user_stage,
        current_system_action=orchestration.current_system_action,
        system_action_reason=orchestration.system_action_reason,
        next_action=next_action,
        version=orchestration.version,
    )


@router.post(
    "/event-research/{case_id}/workflow/confirm",
    response_model=ConfirmResearchWorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_research_workflow(
    case_id: uuid.UUID,
    payload: ConfirmResearchWorkflowRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ConfirmResearchWorkflowResponse:
    principal = OrchestrationPrincipal(
        tenant_id=actor.tenant_id,
        actor=actor.server_actor,
    )
    try:
        orchestration = ResearchOrchestrationService(db).confirm_scope(
            case_id,
            principal,
            payload.idempotency_key,
            scope_version_id=payload.scope_version_id,
            expected_scope_version=payload.scope_version,
        )
        response = _workflow_response(orchestration)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return response


@router.post(
    "/event-research/{case_id}/workflow/decision",
    response_model=ConfirmResearchWorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def decide_research_workflow(
    case_id: uuid.UUID,
    payload: DecideResearchWorkflowRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ConfirmResearchWorkflowResponse:
    principal = OrchestrationPrincipal(
        tenant_id=actor.tenant_id,
        actor=actor.server_actor,
    )
    try:
        orchestration = ResearchOrchestrationService(db).decide_scope(
            case_id,
            principal,
            ScopeDecision(kind=payload.kind, reason=payload.reason),
            payload.idempotency_key,
            expected_version=payload.expected_version,
        )
        response = _workflow_response(orchestration)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return response


@router.post(
    "/event-research/{case_id}/workflow/resume-protocol",
    response_model=ConfirmResearchWorkflowResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def resume_protocol_workflow(
    case_id: uuid.UUID,
    payload: ResumeProtocolWorkflowRequest,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> ConfirmResearchWorkflowResponse:
    principal = OrchestrationPrincipal(
        tenant_id=actor.tenant_id,
        actor=actor.server_actor,
    )
    try:
        orchestration = ResearchOrchestrationService(
            db
        ).resume_after_protocol_completion(
            case_id,
            principal,
            payload.scope_version_id,
            payload.idempotency_key,
            expected_scope_version=payload.scope_version,
        )
        response = _workflow_response(orchestration)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return response
