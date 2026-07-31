"""v1 API router. Combines all versioned read routes."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health_v1() -> dict[str, str]:
    return {
        "service": "industry-evidence-workspace",
        "status": "ok",
        "schema_version": "v1",
    }
