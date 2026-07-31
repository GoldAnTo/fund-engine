import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.cases import router as cases_router
from app.api.v1.router import router as v1_router
from app.errors import NotFoundError, ValidationFailedError

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


def _v1_error_envelope(
    code: str, message: str, request_id: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "v1",
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details or {},
        },
    }


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
            content=_v1_error_envelope("internal_error", "internal error", request_id),
            headers={"x-request-id": request_id},
        )
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=404,
        content=_v1_error_envelope("not_found", str(exc) or "not found", request_id),
        headers={"x-request-id": request_id},
    )


@app.exception_handler(ValidationFailedError)
async def validation_failed_error_handler(request: Request, exc: ValidationFailedError):
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=422,
        content=_v1_error_envelope(
            "validation_failed", str(exc) or "validation failed", request_id
        ),
        headers={"x-request-id": request_id},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(
    request: Request, exc: RequestValidationError
):
    request_id = getattr(request.state, "request_id", "")
    # The v1 error envelope applies only to /api/v1; legacy routes keep
    # FastAPI's default {"detail": [...]} 422 format for compatibility.
    if not request.url.path.startswith("/api/v1"):
        return JSONResponse(
            status_code=422,
            content={"detail": jsonable_encoder(exc.errors())},
        )
    return JSONResponse(
        status_code=422,
        content=_v1_error_envelope(
            "validation_failed",
            "request validation failed",
            request_id,
            {"errors": jsonable_encoder(exc.errors())},
        ),
        headers={"x-request-id": request_id},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "industry-evidence-workspace", "status": "ok"}
