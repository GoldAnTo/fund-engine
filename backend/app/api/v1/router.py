"""v1 API router. Combines all versioned read routes."""

from fastapi import APIRouter

from app.schemas.v1.common import HealthResponse

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
def health_v1() -> HealthResponse:
    return HealthResponse(
        service="industry-evidence-workspace",
        status="ok",
    )
