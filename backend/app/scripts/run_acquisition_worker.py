"""Run lease-fenced governed acquisition jobs.

Use ``--once`` for a scheduler invocation or ``--loop`` for a supervised
worker. Provider and model initialization is explicit and fails closed.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import math
import os
import re
import signal
import socket
import sys
import threading
from collections.abc import Mapping, Sequence
from datetime import timedelta

from app.acquisition.sources import SourceAdapter
from app.ai.client import LLMClient, LLMProviderError
from app.datasources.exchanges.sse import SSEAnnouncementSource
from app.datasources.exchanges.szse import SZSEAnnouncementSource
from app.datasources.gildata.client import GildataMCPClient
from app.datasources.gildata.research_source import GildataResearchSource
from app.db import SessionLocal
from app.repositories.acquisition import (
    AcquisitionRepository,
    StaleLeaseError,
)
from app.services.acquisition_runner import AcquisitionRunner
from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.worker_heartbeat_publisher import WorkerHeartbeatPublisher


_KNOWN_ADAPTERS = frozenset({"gildata", "sse", "szse"})
_IDENTITY_PART = re.compile(r"^[A-Za-z0-9_.-]+$")


def _production() -> bool:
    return os.getenv("APP_ENV", "development").strip().casefold() in {
        "production",
        "prod",
    }


def _configured_adapter_keys() -> tuple[str, ...]:
    raw = os.getenv("ACQUISITION_ENABLED_ADAPTERS", "").strip()
    if not raw:
        if _production():
            raise RuntimeError(
                "ACQUISITION_ENABLED_ADAPTERS is required in production"
            )
        raw = "sse,szse"
    keys = tuple(part.strip().casefold() for part in raw.split(",") if part.strip())
    if not keys or len(keys) != len(set(keys)):
        raise RuntimeError("configured acquisition adapters must be unique")
    unknown = set(keys) - _KNOWN_ADAPTERS
    if unknown:
        raise RuntimeError(
            f"unknown configured acquisition adapters: {sorted(unknown)!r}"
        )
    return keys


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
    for key in _configured_adapter_keys():
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


class _UnconfiguredLLMClient(LLMClient):
    """Keep an idle worker healthy while failing any AI operation closed."""

    def __init__(self) -> None:
        super().__init__(model_version="unconfigured")

    def chat_json(self, messages: list[dict], schema_hint: str = "") -> dict:
        del messages, schema_hint
        raise LLMProviderError("LLM provider is not configured")


def build_llm_client() -> LLMClient:
    """Build a live extraction client or a fail-closed unconfigured boundary."""
    if not os.getenv("LLM_API_KEY", "").strip():
        return _UnconfiguredLLMClient()
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


def _touch(*, mode: str, state: str) -> None:
    with SessionLocal() as session:
        WorkerHeartbeatService(session).touch(
            worker_id=socket.gethostname()[:128],
            mode=mode,
            state=state,
            worker_kind="acquisition",
        )
        session.commit()


def run_once(
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
    try:
        runner.run_claim(claim)
    except StaleLeaseError:
        # Losing a lease is normal operation (expiry mid-stage, or another
        # worker re-claimed after expiry).  The job is durable and will be
        # re-claimed from its checkpoints; the worker must keep polling.
        print(
            f"acquisition lease lost for job {claim.job_id}; "
            "it will be re-claimed from its checkpoints",
            file=sys.stderr,
            flush=True,
        )
    return True


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
    parser.add_argument("--lease-seconds", type=_positive_float, default=1800.0)
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
            lease_for=timedelta(seconds=args.lease_seconds),
        )
        common = {
            "runner": runner,
            "session_factory": SessionLocal,
            "worker_id": worker_id_from_env(),
            "lease_for": timedelta(seconds=args.lease_seconds),
        }
        if args.once:
            _touch(mode="once", state="executing")
            run_once(**common)
            _touch(mode="once", state="idle")
            return
        publisher = WorkerHeartbeatPublisher(
            session_factory=SessionLocal,
            worker_id=socket.gethostname()[:128],
            worker_kind="acquisition",
        )
        publisher.start()
        try:
            while not stop.is_set():
                found = run_once(**common)
                if not found:
                    stop.wait(args.poll_seconds)
        finally:
            publisher.stop()
    except KeyboardInterrupt:
        stop.set()
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        _close_adapters(adapters)


if __name__ == "__main__":
    main()
