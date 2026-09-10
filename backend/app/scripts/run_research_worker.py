"""Durable worker for automatic research runs.

Run once in a scheduler/worker container::

    python -m app.scripts.run_research_worker --once

or keep one supervised worker polling locally::

    python -m app.scripts.run_research_worker --loop
"""
from __future__ import annotations

import argparse
import logging
import os
import socket
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select

from app.env import load_local_env

load_local_env()  # backend/.env (gitignored); exported process env still wins

from app.ai.error_safety import AI_OPERATION_ERROR_MESSAGE
from app.db import SessionLocal
from app.models.operational import ResearchRun
from app.models.research_gateway import ResearchRunSpec
from app.services.auto_research import AutoResearchService
from app.services.fund_disclosure_sync_scheduler import FundDisclosureSyncScheduler
from app.services.monitor_scheduler import MonitorScheduler
from app.services.research_gateway_automatic_adapter import (
    GatewayAutomaticResearchAdapter,
)
from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.worker_heartbeat_publisher import WorkerHeartbeatPublisher


def _sync_gateway_run(run_id) -> None:
    """Catch-up reads can recover if this post-commit projection is unavailable."""
    try:
        with SessionLocal() as projection_session:
            GatewayAutomaticResearchAdapter(projection_session).sync_native_run(run_id)
            projection_session.commit()
    except Exception:  # noqa: BLE001 - optional projection must never roll back native worker progress
        # Never copy provider/SQL/source details into an operational log/event.
        logging.getLogger(__name__).warning("Gateway projection deferred to catch-up")


def _begin_sqlite_worker_transaction(_session, transaction, connection) -> None:
    """Reserve the worker's SQLite writer before any snapshot/savepoint reads.

    Automatic provider adapters commit before network work and reopen transactions
    afterwards. Fence each root transaction, not just the first execution step;
    a DEFERRED read-to-write upgrade can otherwise deadlock with Gateway polling.
    This listener belongs only to the worker Session, never the shared engine.
    """
    if transaction.parent is None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _worker_id() -> str:
    return os.getenv("RESEARCH_WORKER_ID", socket.gethostname())[:128]


def _touch(*, mode: str, state: str) -> None:
    with SessionLocal() as session:
        WorkerHeartbeatService(session).touch(
            worker_id=_worker_id(), mode=mode, state=state, worker_kind="research_run"
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
            before=datetime.now(UTC) - timedelta(minutes=recover_after_minutes)
        )
        service.repo.requeue_source_ready_runs()
        job = service.repo.claim_next_run_job()
        if job is None:
            session.commit()
            return bool(scheduled_fund_runs)
        claim_token = job.claim_token
        run_id = job.target_id
        run = session.get(ResearchRun, run_id)
        gateway_automatic_run = (
            run is not None
            and service._is_automatic_workflow(run)
            and session.scalar(select(ResearchRunSpec.id).where(
                ResearchRunSpec.native_run_id == run_id,
                ResearchRunSpec.native_case_id == run.research_case_id,
            ).limit(1)) is not None
        )
        session.commit()  # publish the claim before provider work begins
        if run is not None:
            # Reading an expired ORM id after commit would open a worker
            # transaction while the separate projector needs its own writer.
            _sync_gateway_run(run_id)
        if gateway_automatic_run and session.get_bind().dialect.name == "sqlite":
            # Existing non-Gateway workflows retain their public cancellation
            # and provider concurrency contract; fence only Gateway dispatch.
            event.listen(session, "after_begin", _begin_sqlite_worker_transaction)
        if run is None:
            service.repo.record_job_completion(
                job,
                status="failed",
                step="failed",
                error="run missing",
                expected_claim_token=claim_token,
            )
            session.commit()
            return True
        try:
            service.execute(run)
            if run.status == "waiting_for_sources":
                service.repo.wait_for_sources(run, job)
            else:
                service.repo.record_job_completion(
                    job,
                    status="cancelled" if run.status == "cancelled" else run.status,
                    step=run.stage,
                    run=run,
                    expected_claim_token=claim_token,
                )
            session.commit()
            _sync_gateway_run(run_id)
        except Exception:
            # A failed flush disables the Session. Preserve an already-clean
            # native failure UoW (including its pending failed AIRun), but reset
            # an invalid transaction before querying/committing terminal rows.
            if not session.is_active:
                session.rollback()
            service.repo.update_run(run, status="failed", stage="failed", stop_reason="execution_failed")
            service.repo.record_job_completion(
                job,
                status="failed",
                step="failed",
                error=AI_OPERATION_ERROR_MESSAGE,
                run=run,
                expected_claim_token=claim_token,
            )
            session.commit()
            _sync_gateway_run(run_id)
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
    publisher = WorkerHeartbeatPublisher(
        session_factory=SessionLocal,
        worker_id=_worker_id(),
        worker_kind="research_run",
    )
    publisher.start()
    try:
        while True:
            found = run_once()
            if not found:
                time.sleep(max(args.poll_seconds, 0.1))
    finally:
        publisher.stop()


if __name__ == "__main__":
    main()
