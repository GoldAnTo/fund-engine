"""Read one durable heartbeat without loading the application/ORM model graph."""
from __future__ import annotations

import argparse
import os
from contextlib import closing
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, unquote, urlsplit


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return seconds


def _connect(database_url: str):
    if database_url.startswith("sqlite:///"):
        import sqlite3

        path = unquote(urlsplit(database_url).path[1:])
        # A liveness check must not create a missing database.
        return sqlite3.connect(f"file:{quote(path, safe='/')}?mode=ro", uri=True, timeout=2), "?"

    import psycopg

    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    return psycopg.connect(
        url,
        autocommit=True,
        connect_timeout=2,
        options="-c statement_timeout=2000 -c default_transaction_read_only=on",
    ), "%s"


def _available(worker_kind: str, worker_id: str, max_age_seconds: float) -> bool:
    database_url = os.getenv(
        "DATABASE_URL", "postgresql+psycopg://evidence:evidence@localhost:5432/evidence"
    )
    connection, placeholder = _connect(database_url)
    with closing(connection):
        query = (
            "SELECT mode, last_seen_at FROM research_worker_heartbeats "
            f"WHERE worker_id = {placeholder}"
        )
        storage_id = f"{worker_kind}:{worker_id}"[:128]
        row = connection.execute(query, (storage_id,)).fetchone()
        # Preserve pre-0053 raw research IDs, but never bypass an existing
        # namespaced stale/once row or reuse another worker kind's raw ID.
        if row is None and worker_kind == "research_run":
            row = connection.execute(query, (worker_id,)).fetchone()
    if row is None or row[0] != "loop":
        return False
    last_seen_at = row[1]
    if isinstance(last_seen_at, str):
        last_seen_at = datetime.fromisoformat(last_seen_at)
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)
    return last_seen_at >= datetime.now(UTC) - timedelta(seconds=max_age_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a container-scoped worker heartbeat")
    parser.add_argument("--worker-kind", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--max-age-seconds", type=_positive_seconds, default=300.0)
    args = parser.parse_args(argv)

    try:
        return 0 if _available(args.worker_kind, args.worker_id, args.max_age_seconds) else 1
    except Exception:  # noqa: BLE001 -- CLI health boundary must not leak DSNs.
        # Connection errors can embed credentials/DSNs. The health exit code
        # is sufficient; leave detailed diagnosis to the application logs.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
