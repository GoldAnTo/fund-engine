"""Exit successfully only when this container's worker heartbeat is fresh."""
from __future__ import annotations

import argparse
from datetime import timedelta

from app.db import SessionLocal
from app.services.research_worker_heartbeat import WorkerHeartbeatService


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a container-scoped worker heartbeat")
    parser.add_argument("--worker-kind", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--max-age-seconds", type=_positive_seconds, default=300.0)
    args = parser.parse_args(argv)

    with SessionLocal() as session:
        status = WorkerHeartbeatService(session).status(
            worker_kind=args.worker_kind,
            worker_id=args.worker_id,
            stale_after=timedelta(seconds=args.max_age_seconds),
        )
    if status["status"] == "available":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
