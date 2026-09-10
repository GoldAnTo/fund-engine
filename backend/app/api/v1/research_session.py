"""Small authenticated session projection for role-gated Research OS navigation."""

from datetime import datetime

from fastapi import APIRouter, Depends

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.schemas.v1.common import V1Model


class ResearchSessionDTO(V1Model):
    user_id: str
    display_name: str
    tenant_id: str
    roles: list[str]
    issuer: str
    expires_at: datetime


router = APIRouter(tags=["research-session-v1"])


@router.get("/research-session", response_model=ResearchSessionDTO)
def research_session(
    actor: ResearchActor = Depends(require_research_actor),
) -> ResearchSessionDTO:
    """Expose a safe projection of the already-authenticated principal."""
    return ResearchSessionDTO(
        user_id=str(actor.user_id),
        display_name=actor.display_name,
        tenant_id=actor.tenant_id,
        roles=sorted(actor.roles),
        issuer=actor.issuer,
        expires_at=actor.expires_at,
    )
