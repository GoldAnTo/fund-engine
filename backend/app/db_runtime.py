"""Runtime configuration for application database engines."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from sqlalchemy.engine import make_url


_BOUNDS = {
    "DATABASE_POOL_SIZE": (2, 1, 10),
    "DATABASE_MAX_OVERFLOW": (2, 0, 10),
    "DATABASE_POOL_TIMEOUT_SECONDS": (30, 1, 120),
    "DATABASE_POOL_RECYCLE_SECONDS": (300, 30, 3600),
}
_CANONICAL_INTEGER = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _bounded_integer(
    environment: Mapping[str, object],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = environment.get(name, str(default))
    if not isinstance(value, str) or not _CANONICAL_INTEGER.fullmatch(value):
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
    integer = int(value)
    if not minimum <= integer <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} through {maximum}")
    return integer


def database_engine_kwargs(
    database_url: str, environment: Mapping[str, object] | None = None
) -> dict[str, object]:
    """Return safe SQLAlchemy engine options for the configured database URL."""
    options: dict[str, object] = {"future": True}
    if make_url(database_url).get_backend_name() != "postgresql":
        return options

    values = os.environ if environment is None else environment
    pool_size_default, pool_size_min, pool_size_max = _BOUNDS["DATABASE_POOL_SIZE"]
    overflow_default, overflow_min, overflow_max = _BOUNDS["DATABASE_MAX_OVERFLOW"]
    timeout_default, timeout_min, timeout_max = _BOUNDS["DATABASE_POOL_TIMEOUT_SECONDS"]
    recycle_default, recycle_min, recycle_max = _BOUNDS["DATABASE_POOL_RECYCLE_SECONDS"]
    options.update(
        pool_size=_bounded_integer(
            values,
            "DATABASE_POOL_SIZE",
            pool_size_default,
            pool_size_min,
            pool_size_max,
        ),
        max_overflow=_bounded_integer(
            values,
            "DATABASE_MAX_OVERFLOW",
            overflow_default,
            overflow_min,
            overflow_max,
        ),
        pool_timeout=_bounded_integer(
            values,
            "DATABASE_POOL_TIMEOUT_SECONDS",
            timeout_default,
            timeout_min,
            timeout_max,
        ),
        pool_recycle=_bounded_integer(
            values,
            "DATABASE_POOL_RECYCLE_SECONDS",
            recycle_default,
            recycle_min,
            recycle_max,
        ),
        pool_pre_ping=True,
        pool_use_lifo=True,
    )
    return options
