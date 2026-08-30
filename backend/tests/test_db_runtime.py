from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.db_runtime import database_engine_kwargs


POSTGRES_URL = "postgresql+psycopg://user:secret@postgres:5432/evidence"


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
