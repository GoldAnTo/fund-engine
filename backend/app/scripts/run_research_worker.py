"""Durable worker for automatic research runs.

Run once in a scheduler/worker container::

    python -m app.scripts.run_research_worker --once

or keep one supervised worker polling locally::

    python -m app.scripts.run_research_worker --loop
"""
from __future__ import annotations

import argparse
import os
import socket
import time
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models.operational import ResearchRun
from app.services.auto_research import AutoResearchService
from app.services.monitor_scheduler import MonitorScheduler
from app.services.fund_disclosure_sync_scheduler import FundDisclosureSyncScheduler
from app.services.research_worker_heartbeat import WorkerHeartbeatService


def _worker_id() -> str:
    return os.getenv("RESEARCH_WORKER_ID", socket.gethostname())[:128]


def _touch(*, mode: str, state: str) -> None:
    with SessionLocal() as session:
        WorkerHeartbeatService(session).touch(
            worker_id=_worker_id(), mode=mode, state=state
        )
        session.commit()


def run_once(*, recover_after_minutes: int = 30) -> bool:
    """Claim and execute one persisted run; return whether work was found."""
    with SessionLocal() as session:
        MonitorScheduler(session).dispatch_due()
        scheduled_fund_runs = FundDisclosureSyncScheduler(session).dispatch_due()
        session.commit()
        service = AutoResearchService(session)
        service.repo.recover_stale_run_jobs(
            before=datetime.now(timezone.utc) - timedelta(minutes=recover_after_minutes)
        )
        job = service.repo.claim_next_run_job()
        if job is None:
            session.commit()
            return bool(scheduled_fund_runs)
        run = session.get(ResearchRun, job.target_id)
        session.commit()  # publish the claim before provider work begins
        if run is None:
            service.repo.record_job_completion(job, status="failed", step="failed", error="run missing")
            session.commit()
            return True
        try:
            service.execute(run)
            session.refresh(run)
            service.repo.record_job_completion(
                job,
                status="cancelled" if run.status == "cancelled" else run.status,
                step=run.stage,
            )
            session.commit()
        except Exception as exc:
            service.repo.update_run(run, status="failed", stage="failed", stop_reason="execution_failed")
            service.repo.record_job_completion(job, status="failed", step="failed", error=str(exc))
            session.commit()
            raise
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute persisted research-run jobs")
    parser.add_argument("--once", action="store_true", help="claim at most one run and exit")
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
    while True:
        _touch(mode="loop", state="polling")
        found = run_once()
        _touch(mode="loop", state="polling")
        if not found:
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    main()
