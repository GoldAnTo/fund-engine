"""Private Gateway entry point: dedicated database and narrow HTTP surface.

Run this API and its workers with an isolated GATEWAY_DATABASE_URL. Never point
the legacy application at this database: its global ledger APIs have a wider
trust model. This module deliberately mounts no legacy readers or job controls.
"""
from __future__ import annotations

import os

# This must precede every import of app.db (including indirect route imports).
gateway_database_url = os.getenv("GATEWAY_DATABASE_URL", "").strip()
if os.getenv("APP_ENV", "").lower() != "test" and not gateway_database_url:
    raise RuntimeError("GATEWAY_DATABASE_URL must identify an isolated Gateway database")
if gateway_database_url:
    os.environ["DATABASE_URL"] = gateway_database_url

from fastapi import APIRouter, FastAPI, Request

from app.api.v1.company_study import router as company_study_router
from app.api.v1.research_gateway import router as gateway_router
from app.api.v1.research_gateway_trace import router as gateway_trace_router
from app.api.v1.research_session import router as session_router
from app.api.v1.research_team import router as team_router
from app.db import DATABASE_URL
from app.main import app as legacy_app
from app.main import request_id_middleware
from app.schemas.v1.common import HealthResponse

if gateway_database_url and DATABASE_URL != gateway_database_url:
    raise RuntimeError("Gateway database was already initialized with another configuration")

app = FastAPI(title="FundClaw Private Research Gateway")
app.state.gateway_isolated = True
app.exception_handlers.update(legacy_app.exception_handlers)
app.middleware("http")(request_id_middleware)


@app.middleware("http")
async def prohibit_private_response_storage(request: Request, call_next):
    """Apply one cache contract to private JSON, receipts, errors and streams."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


router = APIRouter(prefix="/api/v1")
router.include_router(gateway_router)
router.include_router(gateway_trace_router)
router.include_router(session_router)
router.include_router(team_router)
router.include_router(company_study_router)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(service="fundclaw-private-gateway", status="ok")


app.include_router(router)
