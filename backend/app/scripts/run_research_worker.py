"""Durable worker for automatic research runs.

Run once in a scheduler/worker container::

    python -m app.scripts.run_research_worker --once

or keep one supervised worker polling locally::

    python -m app.scripts.run_research_worker --loop
"""

from __future__ import annotations

import argparse
import math
import time
from datetime import datetime, timedelta, timezone

from app.env import load_local_env

load_local_env()  # backend/.env (gitignored); exported process env still wins

from app.ai.error_safety import AI_OPERATION_ERROR_MESSAGE
from app.db import SessionLocal
from app.models.operational import ResearchRun
from app.repositories.auto_research import LostResearchRunLeaseError
from app.services.auto_research import AutoResearchService
from app.services.runtime_heartbeat import RuntimeHeartbeat


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _hold_claim_for_observation(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _run_once(
    *,
    recover_after_minutes: float = 30,
    claim_observation_seconds: float = 0,
) -> bool:
    """Claim and execute one persisted run; return whether work was found."""
    with SessionLocal() as session:
        service = AutoResearchService(session)
        service.recover_stale_run_jobs(
            before=datetime.now(timezone.utc) - timedelta(minutes=recover_after_minutes)
        )
        claim = service.claim_next_run_job()
        if claim is None:
            session.commit()
            return False
        run = session.get(ResearchRun, claim.research_run_id)
        session.commit()  # publish the claim before provider work begins
        if run is None:
            service.record_job_completion(
                claim,
                status="failed",
                step="failed",
                error="run missing",
            )
            session.commit()
            return True
        _hold_claim_for_observation(claim_observation_seconds)
        try:
            service.execute(run, claim=claim)
            completed = service.record_job_completion(
                claim,
                status="cancelled" if run.status == "cancelled" else run.status,
                step=run.stage,
                run=run,
            )
            if not completed:
                session.rollback()
                return True
            service.reconcile_orchestration_after_worker(
                run.id,
                actor="worker:research-run",
            )
            session.commit()
        except LostResearchRunLeaseError:
            session.rollback()
            return True
        except Exception:
            # The orchestration/report callback runs after the terminal Job
            # update but before the outer commit.  Discard that uncommitted
            # success first, then reacquire the same claim in a clean
            # transaction so a deterministic callback failure consumes the
            # bounded failure budget instead of degrading into stale-lease
            # recovery forever.
            session.rollback()
            service = AutoResearchService(session)
            failed_run = session.get(ResearchRun, claim.research_run_id)
            if failed_run is None:
                session.rollback()
                raise
            completed = service.record_execution_failure(
                claim,
                run=failed_run,
                error=AI_OPERATION_ERROR_MESSAGE,
            )
            if not completed:
                session.rollback()
                return True
            session.commit()
            raise
        return True


def run_once(
    *,
    recover_after_minutes: float = 30,
    mode: str = "loop",
    claim_observation_seconds: float = 0,
) -> bool:
    """Execute one research job while continuously publishing liveness."""
    with RuntimeHeartbeat(
        SessionLocal,
        service="research-worker",
        mode=mode,
    ):
        return _run_once(
            recover_after_minutes=recover_after_minutes,
            claim_observation_seconds=claim_observation_seconds,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute persisted research-run jobs")
    parser.add_argument(
        "--once", action="store_true", help="claim at most one run and exit"
    )
    parser.add_argument("--loop", action="store_true", help="poll until interrupted")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--recover-after-minutes", type=_positive_float, default=30.0)
    parser.add_argument(
        "--claim-observation-seconds",
        type=_nonnegative_float,
        default=0.0,
        help="hold a committed claim before execution for live crash acceptance",
    )
    args = parser.parse_args()
    if not args.once and not args.loop:
        parser.error("choose --once or --loop")
    if args.once:
        run_once(
            mode="once",
            recover_after_minutes=args.recover_after_minutes,
            claim_observation_seconds=args.claim_observation_seconds,
        )
        return
    with RuntimeHeartbeat(
        SessionLocal,
        service="research-worker",
        mode="loop",
        state="polling",
    ) as heartbeat:
        while True:
            heartbeat.set_state("executing")
            found = run_once(
                mode="loop",
                recover_after_minutes=args.recover_after_minutes,
                claim_observation_seconds=args.claim_observation_seconds,
            )
            heartbeat.set_state("polling")
            if not found:
                time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    main()
