"""Execute persisted professional roles after native evidence is authorized.

Run a scheduler job with ``python -m app.scripts.run_professional_worker --once``
or a dedicated supervised process with ``--loop``. Multiple processes can claim
independent industry/finance tasks. Never run the loop inside an API process.
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import time

from app.env import load_local_env

load_local_env()

from app.ai.llm_config import bounded_number, env_number
from app.db import SessionLocal
from app.services.research_team_worker import ProfessionalWorker
from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.worker_heartbeat_publisher import WorkerHeartbeatPublisher

PROFESSIONAL_WORKER_KIND = "professional_team"


def _worker_id() -> str:
    return os.getenv("PROFESSIONAL_WORKER_ID", socket.gethostname())[:128]


def _mark_loop_failed(worker_id: str) -> None:
    with SessionLocal() as session:
        WorkerHeartbeatService(session).touch(
            worker_id=worker_id,
            worker_kind=PROFESSIONAL_WORKER_KIND,
            mode="failed",
            state="failed",
        )
        session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run professional research tasks")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--loop", action="store_true")
    parser.add_argument("--lease-seconds", type=float, default=None)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        lease = args.lease_seconds if args.lease_seconds is not None else env_number("PROFESSIONAL_LEASE_SECONDS", 240)
        bounded_number("lease_seconds", lease, 1, 3600)
        bounded_number("poll_seconds", args.poll_seconds, 0.1, 300)
        bounded_number("max_consecutive_failures", args.max_consecutive_failures, 1, 100)
    except ValueError as exc:
        parser.error(str(exc))
    # Fail-stop belongs to this dedicated process, never the reusable library.
    worker = ProfessionalWorker(SessionLocal, lease_seconds=lease, fatal_handler=os._exit)
    if args.once:
        try:
            worker.run_once()
        except KeyboardInterrupt:
            return 0
        except Exception:  # noqa: BLE001 - fixed operational message, no provider/SQL tracebacks
            logging.getLogger(__name__).error("Professional worker iteration failed")
            return 1
        return 0

    worker_id = _worker_id()
    publisher = WorkerHeartbeatPublisher(
        session_factory=SessionLocal,
        worker_id=worker_id,
        worker_kind=PROFESSIONAL_WORKER_KIND,
    )
    publisher.start()
    consecutive_failures = 0
    mark_failed = False
    try:
        while True:
            try:
                worked = worker.run_once()
            except KeyboardInterrupt:
                return 0
            except Exception:  # noqa: BLE001 - fixed operational message, no provider/SQL tracebacks
                logging.getLogger(__name__).error("Professional worker iteration failed")
                consecutive_failures += 1
                if consecutive_failures >= args.max_consecutive_failures:
                    mark_failed = True
                    return 1
                worked = False
            else:
                consecutive_failures = 0
            if not worked:
                try:
                    time.sleep(args.poll_seconds)
                except KeyboardInterrupt:
                    return 0
    finally:
        publisher.stop()
        if mark_failed:
            try:
                _mark_loop_failed(worker_id)
            except Exception:  # noqa: BLE001 - preserve fail-stop without leaking database diagnostics
                logging.getLogger(__name__).error("Professional worker failure status could not be saved")


if __name__ == "__main__":
    raise SystemExit(main())
