"""v1 API router. Combines all versioned read routes."""

from fastapi import APIRouter

from app.api.v1.cases import router as cases_router
from app.api.v1.documents import router as documents_router
from app.schemas.v1.common import HealthResponse

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
def health_v1() -> HealthResponse:
    return HealthResponse(
        service="industry-evidence-workspace",
        status="ok",
    )


router.include_router(cases_router)
router.include_router(documents_router)
