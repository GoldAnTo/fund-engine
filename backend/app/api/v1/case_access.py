"""Authenticated HTTP management for explicit Case collaborators."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback
from app.api.v1.dependencies import RequireCaseRoute
from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.db import get_db
from app.models.identity import CaseAccessGrant, ResearchUser
from app.schemas.v1.case_access import (
    CaseAccessGrantDTO,
    CaseAccessGrantListDTO,
    ChangeCaseAccessRoleRequest,
    GrantCaseAccessRequest,
    RevokeCaseAccessRequest,
)
from app.services.case_authorization import CaseAuthorizationService


router = APIRouter(tags=["case-access-v1"])


def _dto(grant: CaseAccessGrant, user: ResearchUser) -> CaseAccessGrantDTO:
    return CaseAccessGrantDTO(
        user_id=user.id,
        display_name=user.display_name,
        normalized_email=user.normalized_email,
        role=grant.role,
        reason=grant.reason,
    )


@router.get(
    "/event-research/{case_id}/access-grants",
    response_model=CaseAccessGrantListDTO,
)
def list_case_access_grants(
    case_id: uuid.UUID,
    _case_policy: RequireCaseRoute,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> CaseAccessGrantListDTO:
    rows = CaseAuthorizationService(db).list_grants(case_id, actor)
    return CaseAccessGrantListDTO(items=[_dto(grant, user) for grant, user in rows])


@router.post(
    "/event-research/{case_id}/access-grants",
    response_model=CaseAccessGrantDTO,
    status_code=status.HTTP_201_CREATED,
)
def grant_case_access(
    case_id: uuid.UUID,
    payload: GrantCaseAccessRequest,
    _case_policy: RequireCaseRoute,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> CaseAccessGrantDTO:
    service = CaseAuthorizationService(db)
    grant = service.grant(
        case_id,
        actor,
        payload.target_user_id,
        payload.role,
        reason=payload.reason,
    )
    user = db.get(ResearchUser, payload.target_user_id)
    assert user is not None
    result = _dto(grant, user)
    commit_or_rollback(db)
    return result


@router.patch(
    "/event-research/{case_id}/access-grants/{user_id}",
    response_model=CaseAccessGrantDTO,
)
def change_case_access_role(
    case_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: ChangeCaseAccessRoleRequest,
    _case_policy: RequireCaseRoute,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> CaseAccessGrantDTO:
    service = CaseAuthorizationService(db)
    grant = service.change_role(
        case_id,
        actor,
        user_id,
        payload.role,
        reason=payload.reason,
    )
    user = db.get(ResearchUser, user_id)
    assert user is not None
    result = _dto(grant, user)
    commit_or_rollback(db)
    return result


@router.delete(
    "/event-research/{case_id}/access-grants/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_case_access(
    case_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: RevokeCaseAccessRequest,
    _case_policy: RequireCaseRoute,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> Response:
    CaseAuthorizationService(db).revoke(
        case_id,
        actor,
        user_id,
        reason=payload.reason,
    )
    commit_or_rollback(db)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
