"""Do not add private Gateway records through the legacy global API surface."""
from fastapi import Request

from app.errors import UpstreamUnavailableError


def require_gateway_runtime(request: Request) -> None:
    if not getattr(request.app.state, "gateway_isolated", False):
        raise UpstreamUnavailableError("private research requires the isolated Gateway runtime")
