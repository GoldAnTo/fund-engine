"""Fail deployment startup closed on an incompatible event hash schema."""

from __future__ import annotations

from app.env import load_local_env

load_local_env()

from app.db import DATABASE_URL  # noqa: E402
from app.db_migrations import require_company_research_event_schema  # noqa: E402


def main() -> None:
    require_company_research_event_schema(DATABASE_URL)


if __name__ == "__main__":
    main()
