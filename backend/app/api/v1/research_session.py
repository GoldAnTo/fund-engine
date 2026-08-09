"""Small authenticated session projection for role-gated Research OS navigation."""
from fastapi import APIRouter, Depends

from app.api.v1.tenant_context import ResearchActor, require_research_actor
from app.schemas.v1.common import V1Model


class ResearchSessionDTO(V1Model):
    tenant_id: str
    roles: list[str]


router = APIRouter(tags=["research-session-v1"])


@router.get("/research-session", response_model=ResearchSessionDTO)
def research_session(
    actor: ResearchActor = Depends(require_research_actor),
) -> ResearchSessionDTO:
    """Expose only the already-authenticated tenant and configured roles."""
    return ResearchSessionDTO(tenant_id=actor.tenant_id, roles=sorted(actor.roles))
