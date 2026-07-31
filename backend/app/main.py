import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.cases import router as cases_router
from app.api.v1.router import router as v1_router
from app.errors import NotFoundError

logger = logging.getLogger("industry_evidence_workspace")

app = FastAPI(title="Industry Evidence Workspace")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(cases_router)
app.include_router(v1_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    except Exception:
        # Unified internal-error boundary: any exception that escapes the
        # inner ExceptionMiddleware is converted here to a stable 500 envelope
        # carrying the request-id, so no 500 ever lacks the correlation header.
        # Internal text/stack must not leak to the client (design 7.3).
        logger.exception("Unhandled exception (request_id=%s)", request_id)
        response = JSONResponse(
            status_code=500,
            content={
                "schema_version": "v1",
                "error": {
                    "code": "internal_error",
                    "message": "internal error",
                    "request_id": request_id,
                    "details": {},
                },
            },
            headers={"x-request-id": request_id},
        )
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=404,
        content={
            "schema_version": "v1",
            "error": {
                "code": "not_found",
                "message": str(exc) or "not found",
                "request_id": request_id,
                "details": {},
            },
        },
        headers={"x-request-id": request_id},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "industry-evidence-workspace", "status": "ok"}
