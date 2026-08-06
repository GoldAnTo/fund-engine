"""v1 API router. Combines all versioned read routes."""

from fastapi import APIRouter

from app.api.v1.cases import router as cases_router
from app.api.v1.conclusion import router as conclusion_router
from app.api.v1.commands.cases import router as case_commands_router
from app.api.v1.commands.causal import router as causal_commands_router
from app.api.v1.commands.engine import documents_router as engine_doc_commands_router
from app.api.v1.commands.engine import router as engine_commands_router
from app.api.v1.commands.ingest import router as ingest_commands_router
from app.api.v1.commands.instruments import router as instrument_commands_router
from app.api.v1.commands.reviews import router as review_commands_router
from app.api.v1.commands.themes import router as theme_commands_router
from app.api.v1.companies import router as companies_router
from app.api.v1.compare import router as compare_router
from app.api.v1.documents import router as documents_router
from app.api.v1.graph import router as graph_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.metrics import router as metrics_router
from app.api.v1.overview import router as overview_router
from app.api.v1.penetration import router as penetration_router
from app.api.v1.provider_runs import router as provider_runs_router
from app.api.v1.research_ops import router as research_ops_router
from app.api.v1.search import router as search_router
from app.api.v1.themes import router as themes_router
from app.api.v1.commands.proposals import router as review_proposals_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.activity import router as activity_router
from app.api.v1.auto_research import router as auto_research_router
from app.schemas.v1.common import HealthResponse

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=HealthResponse)
def health_v1() -> HealthResponse:
    return HealthResponse(
        service="industry-evidence-workspace",
        status="ok",
    )


router.include_router(cases_router)
router.include_router(conclusion_router)
router.include_router(compare_router)
router.include_router(graph_router)
router.include_router(documents_router)
router.include_router(search_router)
router.include_router(overview_router)
router.include_router(penetration_router)
router.include_router(companies_router)
router.include_router(themes_router)
router.include_router(metrics_router)
router.include_router(provider_runs_router)
router.include_router(research_ops_router)
router.include_router(knowledge_router)
# Command (write) routes live in app/api/v1/commands/, decoupled from reads.
router.include_router(case_commands_router)
router.include_router(review_commands_router)
router.include_router(engine_commands_router)
router.include_router(engine_doc_commands_router)
router.include_router(ingest_commands_router)
router.include_router(instrument_commands_router)
router.include_router(causal_commands_router)
router.include_router(theme_commands_router)
# Operational / proposal / activity endpoints (jobs, proposals, activity).
router.include_router(jobs_router)
router.include_router(activity_router)
router.include_router(auto_research_router)
router.include_router(review_proposals_router)
