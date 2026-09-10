"""Read-only runtime readiness probe; never calls external providers."""
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.operational import ResearchWorkerHeartbeat
from app.services.runtime_configuration import (
    BASE_REQUIRED,
    provider_configuration_status,
)
from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.runtime_identity import runtime_heartbeat_id


def expected_migration_heads() -> set[str]:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    return set(ScriptDirectory.from_config(config).get_heads())


def inspect_runtime(
    service: str,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    environment: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if service not in BASE_REQUIRED:
        raise ValueError(f"unsupported runtime service: {service}")
    env = environment if environment is not None else os.environ
    observed_at = now or datetime.now(timezone.utc)
    checks: dict[str, Any] = {}

    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
            checks["database"] = {"status": "healthy"}
            current = {
                str(row[0])
                for row in session.execute(text("SELECT version_num FROM alembic_version"))
            }
            expected = expected_migration_heads()
            checks["migration"] = {
                "status": "healthy" if current == expected else "unhealthy",
                "current": sorted(current),
                "expected": sorted(expected),
            }
            if service != "api":
                heartbeat = session.get(
                    ResearchWorkerHeartbeat,
                    runtime_heartbeat_id(service, environment=env),
                )
                if heartbeat is None:
                    checks["heartbeat"] = {"status": "missing"}
                else:
                    seen_at = heartbeat.last_seen_at
                    if seen_at.tzinfo is None:
                        seen_at = seen_at.replace(tzinfo=timezone.utc)
                    age = observed_at - seen_at
                    fresh = (
                        age >= -WorkerHeartbeatService.max_clock_skew
                        and age <= WorkerHeartbeatService.stale_after
                    )
                    checks["heartbeat"] = {
                        "status": "healthy"
                        if (
                            fresh
                            and heartbeat.mode == "loop"
                            and heartbeat.state in WorkerHeartbeatService.healthy_states
                        )
                        else "stale",
                        "last_seen_at": seen_at.isoformat(),
                        "mode": heartbeat.mode,
                        "state": heartbeat.state,
                    }
    except Exception as exc:
        checks.setdefault(
            "database",
            {"status": "unhealthy", "error": type(exc).__name__},
        )
        checks.setdefault("migration", {"status": "unknown"})
        if service != "api":
            checks.setdefault("heartbeat", {"status": "unknown"})

    checks["providers"] = provider_configuration_status(service, env)
    healthy = all(check.get("status") == "healthy" for check in checks.values())
    return {"service": service, "status": "healthy" if healthy else "unhealthy", "checks": checks}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect runtime readiness")
    parser.add_argument("--service", choices=tuple(BASE_REQUIRED), required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = inspect_runtime(args.service)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
