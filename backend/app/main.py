import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.api.legacy import router as cases_router
from app.api.v1.router import router as v1_router
from app.db import SessionLocal, get_db
from app.env import load_local_env
from app.errors import (
    ConflictError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationFailedError,
)
from app.schemas.v1.common import ErrorEnvelope
from app.services.embed_access import EmbedAccessService

load_local_env()  # backend/.env (gitignored) -> os.environ, env vars win

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


_EMBED_PREFLIGHT_PREFIX = "/api/v1/report-research/"
_EMBED_PREFLIGHT_SUFFIX = "/embed/wiki"
_EMBED_PREFLIGHT_ALLOWED_HEADERS = frozenset({"x-embed-token"})


def _embed_case_id_for_preflight(request: Request) -> uuid.UUID | None:
    """Return the case only for the exact external embed preflight route."""
    if request.method != "OPTIONS":
        return None
    path = request.url.path
    if not path.startswith(_EMBED_PREFLIGHT_PREFIX) or not path.endswith(
        _EMBED_PREFLIGHT_SUFFIX
    ):
        return None
    raw_case_id = path[
        len(_EMBED_PREFLIGHT_PREFIX) : -len(_EMBED_PREFLIGHT_SUFFIX)
    ]
    if not raw_case_id or "/" in raw_case_id:
        return None
    try:
        return uuid.UUID(raw_case_id)
    except ValueError:
        return None


def _embed_preflight_headers_are_safe(request: Request) -> bool:
    requested_method = request.headers.get("access-control-request-method", "").upper()
    if requested_method not in {"GET", "HEAD"}:
        return False
    requested_headers = request.headers.get("access-control-request-headers", "")
    headers = {
        value.strip().lower()
        for value in requested_headers.split(",")
        if value.strip()
    }
    return headers == _EMBED_PREFLIGHT_ALLOWED_HEADERS


@contextmanager
def _embed_preflight_session() -> Iterator[Session]:
    """Open one short read session, honoring test dependency overrides."""
    override = app.dependency_overrides.get(get_db)
    if override is None:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()
        return
    dependency = override()
    session = next(dependency)
    try:
        yield session
    finally:
        try:
            next(dependency)
        except StopIteration:
            pass


@app.middleware("http")
async def embed_preflight_middleware(request: Request, call_next):
    """Handle grant-aware external embed preflight before global CORS.

    This declaration is intentionally after the request-id middleware and is
    therefore the outermost application middleware.  Returning directly
    prevents the broad development ``CORSMiddleware`` from answering embed
    preflights with its unrelated local-origin policy.
    """
    case_id = _embed_case_id_for_preflight(request)
    if case_id is None:
        return await call_next(request)
    origin = request.headers.get("origin")
    allowed = False
    if origin is not None and _embed_preflight_headers_are_safe(request):
        with _embed_preflight_session() as session:
            allowed = EmbedAccessService(session).allows_preflight(case_id, origin)
    if not allowed:
        return Response(status_code=403)
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, HEAD",
            "Access-Control-Allow-Headers": "X-Embed-Token",
            "Vary": "Origin, Access-Control-Request-Method, Access-Control-Request-Headers",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _v1_error_response(
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
    status_code: int = 500,
) -> JSONResponse:
    """Build the v1 error envelope by serializing the Pydantic ErrorEnvelope
    model directly. Using the model as the single source of truth (instead
    of constructing a raw dict) ensures the runtime response cannot drift
    from the schema declared in OpenAPI.
    """
    envelope = ErrorEnvelope(
        error={"code": code, "message": message, "request_id": request_id, "details": details or {}}
    ).model_dump(mode="json")
    return JSONResponse(
        status_code=status_code,
        content=envelope,
        headers={"x-request-id": request_id},
    )


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
        response = _v1_error_response(
            "internal_error",
            "internal error",
            request_id,
            status_code=500,
        )
    # The application-wide development CORS allow-list is intentionally not
    # an embed permission system.  A rejected bearer grant must not inherit a
    # permissive CORS header merely because its Origin happens to be one of
    # the local frontend origins.  Successful embed reads set their own exact
    # grant-origin header in the route.
    if (
        request.url.path.endswith("/embed/wiki")
        and response.status_code in {401, 403}
    ):
        for header in tuple(response.headers):
            if header.lower().startswith("access-control-"):
                del response.headers[header]
    response.headers["x-request-id"] = request_id
    return response


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    request_id = getattr(request.state, "request_id", "")
    return _v1_error_response(
        "not_found",
        str(exc) or "not found",
        request_id,
        status_code=404,
    )


@app.exception_handler(ValidationFailedError)
async def validation_failed_error_handler(request: Request, exc: ValidationFailedError):
    request_id = getattr(request.state, "request_id", "")
    return _v1_error_response(
        "validation_failed",
        str(exc) or "validation failed",
        request_id,
        status_code=422,
    )


@app.exception_handler(ConflictError)
async def conflict_error_handler(request: Request, exc: ConflictError):
    request_id = getattr(request.state, "request_id", "")
    return _v1_error_response(
        "conflict",
        str(exc) or "resource already exists",
        request_id,
        status_code=409,
    )


@app.exception_handler(UpstreamUnavailableError)
async def upstream_unavailable_error_handler(
    request: Request, exc: UpstreamUnavailableError
):
    request_id = getattr(request.state, "request_id", "")
    return _v1_error_response(
        "upstream_unavailable",
        str(exc) or "upstream datasource unavailable",
        request_id,
        status_code=503,
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
    return _v1_error_response(
        "validation_failed",
        "request validation failed",
        request_id,
        details={"errors": jsonable_encoder(exc.errors())},
        status_code=422,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"service": "industry-evidence-workspace", "status": "ok"}


def _register_error_envelope(openapi_schema: dict[str, Any]) -> None:
    """Inject the ErrorEnvelope schema into OpenAPI components.

    The error envelope is produced by global exception handlers, so FastAPI
    does not auto-document it. Declaring it here keeps the frontend contract
    (openapi-typescript) as the single source of truth, including errors.
    """
    components = openapi_schema.setdefault("components", {}).setdefault(
        "schemas", {}
    )
    if "ErrorEnvelope" in components:
        return
    schema = ErrorEnvelope.model_json_schema(
        ref_template="#/components/schemas/{model}"
    )
    defs = schema.pop("$defs", {})
    components["ErrorEnvelope"] = schema
    components.update(defs)


def _custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title, version=app.version, routes=app.routes
    )
    _register_error_envelope(openapi_schema)
    app.openapi_schema = openapi_schema
    return openapi_schema


app.openapi = _custom_openapi
