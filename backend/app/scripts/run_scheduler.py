"""Run all periodic research dispatch and orchestration recovery under one fence."""
from __future__ import annotations

import argparse
import math
import signal
import threading
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services.fund_disclosure_sync_scheduler import FundDisclosureSyncScheduler
from app.services.monitor_scheduler import MonitorScheduler
from app.services.research_orchestration import ResearchOrchestrationService
from app.services.runtime_heartbeat import RuntimeHeartbeat


SCHEDULER_ADVISORY_LOCK_KEY = 7_332_461_079


def try_acquire_scheduler_lease(session: Session) -> bool:
    """Acquire a crash-safe, session-scoped PostgreSQL scheduler fence."""
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return True
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_lock(:lock_key)"),
            {"lock_key": SCHEDULER_ADVISORY_LOCK_KEY},
        ).scalar_one()
    )


def release_scheduler_lease(session: Session) -> bool:
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        return bool(session.execute(
            text("SELECT pg_advisory_unlock(:lock_key)"),
            {"lock_key": SCHEDULER_ADVISORY_LOCK_KEY},
        ).scalar_one())
    return True


def run_once(
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    now: datetime | None = None,
    recover_after_minutes: float = 30,
    mode: str = "loop",
) -> bool:
    """Run one fenced scheduler iteration and return whether work was found."""
    if recover_after_minutes <= 0:
        raise ValueError("recover_after_minutes must be greater than zero")
    observed_at = now or datetime.now(timezone.utc)
    with session_factory() as lease_session:
        if not try_acquire_scheduler_lease(lease_session):
            return False
        try:
            with RuntimeHeartbeat(
                session_factory,
                service="scheduler",
                mode=mode,
                state="executing",
            ):
                with session_factory() as session:
                    monitor_runs = MonitorScheduler(session).dispatch_due(now=observed_at)
                    fund_runs = FundDisclosureSyncScheduler(session).dispatch_due(now=observed_at)
                    session.commit()

                    orchestration = ResearchOrchestrationService(session)
                    stale_before = observed_at - timedelta(minutes=recover_after_minutes)
                    started = orchestration.start_stale_acquisition_recoveries(
                        now=observed_at,
                        stale_before=stale_before,
                        limit=10,
                    )
                    session.commit()
                    completed = orchestration.complete_acquisition_recoveries(
                        now=observed_at,
                        limit=10,
                    )
                    session.commit()
                    reconciled = orchestration.reconcile_batch(
                        now=observed_at,
                        stale_before=stale_before,
                        limit=10,
                    )
                    session.commit()
                    return bool(
                        monitor_runs or fund_runs or started or completed or reconciled
                    )
        finally:
            try:
                if not release_scheduler_lease(lease_session):
                    raise RuntimeError("scheduler advisory lock was not held at release")
                lease_session.commit()
            except Exception:
                # Invalidate the physical connection so PostgreSQL releases
                # every session-level lock even when explicit unlock fails.
                lease_session.invalidate()
                raise


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run periodic research scheduling")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true")
    modes.add_argument("--loop", action="store_true")
    parser.add_argument("--poll-seconds", type=_positive_float, default=30.0)
    parser.add_argument("--recover-after-minutes", type=_positive_float, default=30.0)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    stop = threading.Event()
    previous_handler = signal.getsignal(signal.SIGTERM)

    def request_stop(_signum, _frame) -> None:
        stop.set()

    try:
        signal.signal(signal.SIGTERM, request_stop)
        if args.once:
            run_once(recover_after_minutes=args.recover_after_minutes, mode="once")
            return
        with RuntimeHeartbeat(
            SessionLocal,
            service="scheduler",
            mode="loop",
            state="polling",
        ) as heartbeat:
            while not stop.is_set():
                heartbeat.set_state("executing")
                run_once(recover_after_minutes=args.recover_after_minutes, mode="loop")
                heartbeat.set_state("polling")
                stop.wait(args.poll_seconds)
    except KeyboardInterrupt:
        stop.set()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    main()
