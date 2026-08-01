"""v1 API router. Combines all versioned read routes."""

from fastapi import APIRouter

from app.api.v1.cases import router as cases_router
from app.api.v1.commands.cases import router as case_commands_router
from app.api.v1.commands.reviews import router as review_commands_router
from app.api.v1.compare import router as compare_router
from app.api.v1.documents import router as documents_router
from app.api.v1.graph import router as graph_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.overview import router as overview_router
from app.api.v1.penetration import router as penetration_router
from app.api.v1.search import router as search_router
from app.schemas.v1.common import HealthResponse

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
def health_v1() -> HealthResponse:
    return HealthResponse(
        service="industry-evidence-workspace",
        status="ok",
    )


router.include_router(cases_router)
router.include_router(compare_router)
router.include_router(graph_router)
router.include_router(documents_router)
router.include_router(search_router)
router.include_router(overview_router)
router.include_router(penetration_router)
router.include_router(metrics_router)
# Command (write) routes live in app/api/v1/commands/, decoupled from reads.
router.include_router(case_commands_router)
router.include_router(review_commands_router)
