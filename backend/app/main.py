import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api.legacy import router as cases_router
from app.api.v1.router import router as v1_router
from app.env import load_local_env
from app.errors import (
    AuthenticationRequiredError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    UpstreamUnavailableError,
    ValidationFailedError,
)
from app.schemas.v1.common import ErrorEnvelope

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
    # Each isolated frontend worktree may choose a different Vite port.  Keep
    # local live-read verification usable without granting access to nonlocal
    # origins or widening the deployed same-origin surface.
    allow_origin_regex=r"^https?://(?:localhost|127\.0\.0\.1):\d+$",
    # Both authenticated browser adapters intentionally use credentials:
    # include.  The local cross-origin Vite workflow therefore needs the
    # explicit credential response header; origins remain restricted above.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(cases_router)
app.include_router(v1_router)


MAX_AUTOMATIC_RESEARCH_UPLOAD_REQUEST_BYTES = 20 * 1024 * 1024


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


class AutomaticResearchUploadBodyLimitMiddleware:
    """Bound one upload route before FastAPI can parse multipart form data.

    The wrapper consumes the ASGI receive stream itself and only replays a
    body that fits within the bound.  This deliberately does not use
    ``Content-Length``: clients may omit or forge that header, while the
    receive stream is the actual request payload.
    """

    def __init__(self, app, *, max_request_bytes: int) -> None:
        self.app = app
        self._max_request_bytes = max_request_bytes

    async def __call__(self, scope, receive, send) -> None:
        if not (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] == "/api/v1/automatic-research/uploaded"
        ):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body = message.get("body", b"")
            received_bytes += len(body)
            if received_bytes > self._max_request_bytes:
                request_id = scope.get("state", {}).get("request_id") or str(
                    uuid.uuid4()
                )
                response = _v1_error_response(
                    "request_too_large",
                    "uploaded request must not exceed 20 MiB",
                    request_id,
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {
                "type": "http.request",
                "body": b"".join(chunks),
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)


# Added before the request-id decorator, so its 413 response keeps the normal
# v1 envelope and correlation header while multipart parsing remains inside the
# route-specific body cap.
app.add_middleware(
    AutomaticResearchUploadBodyLimitMiddleware,
    max_request_bytes=MAX_AUTOMATIC_RESEARCH_UPLOAD_REQUEST_BYTES,
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


@app.exception_handler(AuthenticationRequiredError)
async def authentication_required_error_handler(
    request: Request, exc: AuthenticationRequiredError
):
    request_id = getattr(request.state, "request_id", "")
    return _v1_error_response(
        "authentication_required",
        str(exc) or "authentication required",
        request_id,
        status_code=401,
    )


@app.exception_handler(PermissionDeniedError)
async def permission_denied_error_handler(request: Request, exc: PermissionDeniedError):
    request_id = getattr(request.state, "request_id", "")
    return _v1_error_response(
        "permission_denied",
        str(exc) or "permission denied",
        request_id,
        status_code=403,
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
