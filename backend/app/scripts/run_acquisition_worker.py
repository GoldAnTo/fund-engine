"""Run lease-fenced governed acquisition jobs.

Use ``--once`` for a scheduler invocation or ``--loop`` for a supervised
worker. Provider and model initialization is explicit and fails closed.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import logging
import math
import os
import re
import signal
import socket
import threading
from collections.abc import Mapping, Sequence
from datetime import timedelta

from app.acquisition.sources import SourceAdapter
from app.ai.client import LLMClient
from app.datasources.exchanges.sse import SSEAnnouncementSource
from app.datasources.exchanges.szse import SZSEAnnouncementSource
from app.datasources.gildata.client import GildataMCPClient
from app.datasources.gildata.research_source import GildataResearchSource
from app.db import SessionLocal
from app.repositories.acquisition import AcquisitionRepository
from app.repositories.acquisition import StaleLeaseError
from app.services.acquisition_runner import AcquisitionRunner
from app.services.runtime_heartbeat import RuntimeHeartbeat
from app.services.runtime_configuration import validate_adapter_configuration


_IDENTITY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")
logger = logging.getLogger(__name__)


class _ClaimLeaseHeartbeat:
    """Keep one claimed job fenced while adapters or the LLM are blocking."""

    def __init__(self, session_factory, *, claim, lease_for: timedelta) -> None:
        self._session_factory = session_factory
        self._claim = claim
        self._lease_for = lease_for
        self._interval_seconds = max(
            0.01,
            min(30.0, lease_for.total_seconds() / 3.0),
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self._thread = threading.Thread(
            target=self._pulse,
            name=f"acquisition-lease-{self._claim.job_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1.0)

    def _pulse(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                with self._session_factory() as session:
                    AcquisitionRepository(session).renew_lease(
                        self._claim.job_id,
                        lease_token=self._claim.lease_token,
                        lease_for=self._lease_for,
                    )
                    session.commit()
            except StaleLeaseError:
                return
            except Exception:
                logger.exception("acquisition lease heartbeat failed")


def _production(environment: Mapping[str, str] | None = None) -> bool:
    env = environment if environment is not None else os.environ
    return env.get("APP_ENV", "development").strip().casefold() in {
        "production",
        "prod",
    }


def _close_adapters(adapters: Mapping[str, object]) -> None:
    for adapter in reversed(tuple(adapters.values())):
        try:
            adapter.close()
        except Exception:
            # Shutdown must continue so every independently owned transport is
            # offered a close, while preserving the original startup/runtime error.
            continue


def build_adapters_from_env() -> dict[str, SourceAdapter]:
    """Initialize exactly the configured governed adapters or fail closed."""
    adapters: dict[str, SourceAdapter] = {}
    for key in validate_adapter_configuration(os.environ):
        try:
            if key == "gildata":
                adapters[key] = GildataResearchSource(GildataMCPClient.from_env())
            elif key == "sse":
                adapters[key] = SSEAnnouncementSource()
            else:
                adapters[key] = SZSEAnnouncementSource()
        except Exception as exc:
            _close_adapters(adapters)
            raise RuntimeError(
                f"configured adapter {key} failed to initialize"
            ) from exc
    return adapters


def build_llm_client() -> LLMClient:
    """Build the extraction client and forbid mock identity in production."""
    client = LLMClient.from_env()
    if _production() and (
        bool(getattr(client, "_mock", False))
        or str(client.model_version).startswith("mock-")
        or str(client.model_version).startswith("fake-")
    ):
        raise RuntimeError("production acquisition worker requires a real LLM")
    return client


def _identity_part(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized or _IDENTITY_PART.fullmatch(normalized) is None:
        raise RuntimeError(f"{field_name} must use letters, digits, dot, dash or underscore")
    return normalized


def worker_id_from_env() -> str:
    try:
        default_version = importlib.metadata.version("industry-evidence-workspace")
    except importlib.metadata.PackageNotFoundError:
        default_version = "0.1.0"
    version = _identity_part(
        os.getenv("ACQUISITION_WORKER_VERSION", default_version),
        "ACQUISITION_WORKER_VERSION",
    )
    default_instance = re.sub(
        r"[^A-Za-z0-9_.-]", "-", f"{socket.gethostname()}-{os.getpid()}"
    )
    instance = _identity_part(
        os.getenv("ACQUISITION_WORKER_INSTANCE", default_instance),
        "ACQUISITION_WORKER_INSTANCE",
    )
    value = f"system:acquisition-worker@{version}#{instance}"
    if len(value) > 256:
        raise RuntimeError("acquisition worker identity exceeds 256 characters")
    return value


def _run_once(
    *,
    runner: AcquisitionRunner,
    session_factory=SessionLocal,
    worker_id: str,
    lease_for: timedelta,
) -> bool:
    """Claim, commit, and execute at most one job."""
    with session_factory() as session:
        claim = AcquisitionRepository(session).claim_next(
            worker_id=worker_id, lease_for=lease_for
        )
        session.commit()
    if claim is None:
        return False
    with _ClaimLeaseHeartbeat(
        session_factory,
        claim=claim,
        lease_for=lease_for,
    ):
        runner.run_claim(claim)
    return True


def run_once(
    *,
    runner: AcquisitionRunner,
    session_factory=SessionLocal,
    worker_id: str,
    lease_for: timedelta,
    mode: str = "loop",
) -> bool:
    """Execute one acquisition job while continuously publishing liveness."""
    with RuntimeHeartbeat(
        session_factory,
        service="acquisition-worker",
        mode=mode,
    ):
        return _run_once(
            runner=runner,
            session_factory=session_factory,
            worker_id=worker_id,
            lease_for=lease_for,
        )


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute persisted governed-acquisition jobs"
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--once", action="store_true", help="claim at most one job")
    modes.add_argument("--loop", action="store_true", help="poll until interrupted")
    parser.add_argument("--lease-seconds", type=_positive_float, default=300.0)
    parser.add_argument("--retry-seconds", type=_positive_float, default=60.0)
    parser.add_argument("--poll-seconds", type=_positive_float, default=1.0)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    adapters = build_adapters_from_env()
    stop = threading.Event()
    previous_handler = signal.getsignal(signal.SIGTERM)

    def request_stop(_signum, _frame) -> None:
        stop.set()

    try:
        signal.signal(signal.SIGTERM, request_stop)
        runner = AcquisitionRunner(
            SessionLocal,
            adapters=adapters,
            llm_client=build_llm_client(),
            retry_delay=timedelta(seconds=args.retry_seconds),
        )
        common = {
            "runner": runner,
            "session_factory": SessionLocal,
            "worker_id": worker_id_from_env(),
            "lease_for": timedelta(seconds=args.lease_seconds),
        }
        if args.once:
            run_once(**common, mode="once")
            return
        with RuntimeHeartbeat(
            SessionLocal,
            service="acquisition-worker",
            mode="loop",
            state="polling",
        ) as heartbeat:
            while not stop.is_set():
                heartbeat.set_state("executing")
                found = run_once(**common, mode="loop")
                heartbeat.set_state("polling")
                if not found:
                    stop.wait(args.poll_seconds)
    except KeyboardInterrupt:
        stop.set()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        _close_adapters(adapters)


if __name__ == "__main__":
    main()
