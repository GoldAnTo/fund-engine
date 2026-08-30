from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.db_runtime import database_engine_kwargs


POSTGRES_URL = "postgresql+psycopg://user:secret@postgres:5432/evidence"
POOL_NAMES = (
    "DATABASE_POOL_SIZE",
    "DATABASE_MAX_OVERFLOW",
    "DATABASE_POOL_TIMEOUT_SECONDS",
    "DATABASE_POOL_RECYCLE_SECONDS",
)


def test_postgres_defaults_are_bounded() -> None:
    assert database_engine_kwargs(POSTGRES_URL, {}) == {
        "future": True,
        "pool_size": 2,
        "max_overflow": 2,
        "pool_timeout": 30,
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_use_lifo": True,
    }


def test_postgres_pool_values_can_be_overridden() -> None:
    environment = {
        "DATABASE_POOL_SIZE": "4",
        "DATABASE_MAX_OVERFLOW": "1",
        "DATABASE_POOL_TIMEOUT_SECONDS": "12",
        "DATABASE_POOL_RECYCLE_SECONDS": "90",
    }
    assert database_engine_kwargs(POSTGRES_URL, environment) == {
        "future": True,
        "pool_size": 4,
        "max_overflow": 1,
        "pool_timeout": 12,
        "pool_recycle": 90,
        "pool_pre_ping": True,
        "pool_use_lifo": True,
    }


@pytest.mark.parametrize(
    ("name", "value", "minimum", "maximum"),
    [
        ("DATABASE_POOL_SIZE", "0", 1, 10),
        ("DATABASE_POOL_SIZE", "11", 1, 10),
        ("DATABASE_POOL_SIZE", "not-an-integer", 1, 10),
        ("DATABASE_MAX_OVERFLOW", "-1", 0, 10),
        ("DATABASE_MAX_OVERFLOW", "11", 0, 10),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "0", 1, 120),
        ("DATABASE_POOL_TIMEOUT_SECONDS", "121", 1, 120),
        ("DATABASE_POOL_RECYCLE_SECONDS", "29", 30, 3600),
        ("DATABASE_POOL_RECYCLE_SECONDS", "3601", 30, 3600),
    ],
)
def test_invalid_postgres_pool_values_are_rejected(
    name: str, value: str, minimum: int, maximum: int
) -> None:
    with pytest.raises(
        ValueError,
        match=rf"^{name} must be an integer from {minimum} through {maximum}$",
    ) as error:
        database_engine_kwargs(POSTGRES_URL, {name: value})
    assert POSTGRES_URL not in str(error.value)
    assert "secret" not in str(error.value)


def test_sqlite_ignores_postgres_pool_environment_and_executes() -> None:
    options = database_engine_kwargs(
        "sqlite://",
        {
            "DATABASE_POOL_SIZE": "not-an-integer",
            "DATABASE_MAX_OVERFLOW": "-1",
            "DATABASE_POOL_TIMEOUT_SECONDS": "0",
            "DATABASE_POOL_RECYCLE_SECONDS": "3601",
        },
    )
    assert options == {"future": True}
    engine = create_engine("sqlite://", **options)
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1


def test_oversized_pool_value_uses_bounded_error() -> None:
    value = "1" * 4301
    with pytest.raises(
        ValueError,
        match=r"^DATABASE_POOL_SIZE must be an integer from 1 through 10$",
    ):
        database_engine_kwargs(POSTGRES_URL, {"DATABASE_POOL_SIZE": value})


def test_app_db_wires_bounded_postgres_pool_without_connecting() -> None:
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_URL": POSTGRES_URL,
            "DATABASE_POOL_SIZE": "4",
            "DATABASE_MAX_OVERFLOW": "1",
            "DATABASE_POOL_TIMEOUT_SECONDS": "12",
            "DATABASE_POOL_RECYCLE_SECONDS": "90",
        }
    )
    script = """
import json
from app.db import SessionLocal, engine
pool = engine.pool
print(json.dumps({
    "pool_type": type(pool).__name__,
    "size": pool.size(),
    "max_overflow": pool._max_overflow,
    "timeout": pool.timeout(),
    "recycle": pool._recycle,
    "pre_ping": pool._pre_ping,
    "use_lifo": pool._pool.use_lifo,
    "checkedout": pool.checkedout(),
    "session_bound": SessionLocal.kw["bind"] is engine,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == {
        "pool_type": "QueuePool",
        "size": 4,
        "max_overflow": 1,
        "timeout": 12,
        "recycle": 90,
        "pre_ping": True,
        "use_lifo": True,
        "checkedout": 0,
        "session_bound": True,
    }
    assert "secret" not in result.stdout


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (name, value)
        for name in POOL_NAMES
        for value in ("01", "+1", " 1", "1 ", "", 1)
    ],
)
def test_noncanonical_pool_values_are_rejected(name: str, value: object) -> None:
    minimum, maximum = {
        "DATABASE_POOL_SIZE": (1, 10),
        "DATABASE_MAX_OVERFLOW": (0, 10),
        "DATABASE_POOL_TIMEOUT_SECONDS": (1, 120),
        "DATABASE_POOL_RECYCLE_SECONDS": (30, 3600),
    }[name]
    with pytest.raises(
        ValueError,
        match=rf"^{name} must be an integer from {minimum} through {maximum}$",
    ):
        database_engine_kwargs(POSTGRES_URL, {name: value})


@pytest.mark.parametrize(
    ("name", "minimum", "maximum"),
    [
        ("DATABASE_POOL_SIZE", 1, 10),
        ("DATABASE_MAX_OVERFLOW", 0, 10),
        ("DATABASE_POOL_TIMEOUT_SECONDS", 1, 120),
        ("DATABASE_POOL_RECYCLE_SECONDS", 30, 3600),
    ],
)
def test_pool_settings_accept_exact_bounds(name: str, minimum: int, maximum: int) -> None:
    assert database_engine_kwargs(POSTGRES_URL, {name: str(minimum)})[name_to_option(name)] == minimum
    assert database_engine_kwargs(POSTGRES_URL, {name: str(maximum)})[name_to_option(name)] == maximum


def name_to_option(name: str) -> str:
    return {
        "DATABASE_POOL_SIZE": "pool_size",
        "DATABASE_MAX_OVERFLOW": "max_overflow",
        "DATABASE_POOL_TIMEOUT_SECONDS": "pool_timeout",
        "DATABASE_POOL_RECYCLE_SECONDS": "pool_recycle",
    }[name]
