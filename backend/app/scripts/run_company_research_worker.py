"""Run the recoverable, review-gated company-research preparation worker."""

from __future__ import annotations

import argparse
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from app.env import load_local_env

load_local_env()

from app.db import SessionLocal  # noqa: E402
from app.services.research_worker_heartbeat import WorkerHeartbeatService  # noqa: E402
from app.services.worker_heartbeat_publisher import WorkerHeartbeatPublisher  # noqa: E402
from app.underwriting.services.company_research_preparation import (  # noqa: E402
    CompanyResearchPreparationWorker,
)


def _worker_id() -> str:
    return os.getenv("COMPANY_RESEARCH_WORKER_ID", socket.gethostname())[:128]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _touch(*, mode: str, state: str) -> None:
    with SessionLocal() as session:
        WorkerHeartbeatService(session).touch(
            worker_id=_worker_id(),
            mode=mode,
            state=state,
            worker_kind="company_research",
        )
        session.commit()


def run_once(*, recover_after_minutes: int = 30, session_factory=SessionLocal) -> bool:
    """Claim and execute one source or model-bundle stage when work is due."""
    with session_factory() as session:
        worker = CompanyResearchPreparationWorker(session, now=_utcnow)
        recovered = worker.recover_stale_claims(
            before=_utcnow() - timedelta(minutes=recover_after_minutes)
        )
        cancelled = worker.cancel_queued_claims()
        claim = worker.claim_next()
        session.commit()
    if claim is None:
        return bool(recovered or cancelled)
    with session_factory() as session:
        live_runtime = None
        if claim.step == "evidence_index" and os.getenv("COMPANY_RESEARCH_LIVE") == "1":
            from app.underwriting.services.company_research_live_runtime import CompanyResearchLiveRuntime
            live_runtime = CompanyResearchLiveRuntime.from_env(now=_utcnow)
        worker = CompanyResearchPreparationWorker(session, now=_utcnow, live_runtime=live_runtime)
        worker.run_claim(claim)
        session.commit()
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute company-research jobs")
    parser.add_argument("--once", action="store_true", help="claim at most one job")
    parser.add_argument("--loop", action="store_true", help="poll until interrupted")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    if not args.once and not args.loop:
        parser.error("choose --once or --loop")
    if args.once:
        _touch(mode="once", state="executing")
        run_once()
        _touch(mode="once", state="idle")
        return
    publisher = WorkerHeartbeatPublisher(
        session_factory=SessionLocal,
        worker_id=_worker_id(),
        worker_kind="company_research",
    )
    publisher.start()
    try:
        while True:
            if not run_once():
                time.sleep(max(args.poll_seconds, 0.1))
    finally:
        publisher.stop()


if __name__ == "__main__":
    main()
