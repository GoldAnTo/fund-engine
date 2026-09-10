"""Continuously publish one runtime process's state during long work."""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.services.research_worker_heartbeat import WorkerHeartbeatService
from app.services.runtime_identity import runtime_heartbeat_id
from app.services.runtime_configuration import provider_configuration_status


logger = logging.getLogger(__name__)


class RuntimeHeartbeat:
    """A context-managed heartbeat with periodic refresh on a separate session."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        service: str,
        mode: str,
        state: str = "executing",
        interval_seconds: float = 30.0,
    ) -> None:
        self._session_factory = session_factory
        self._worker_id = runtime_heartbeat_id(service)
        self._service = service
        self._mode = mode
        self._state = state
        self._interval_seconds = max(0.01, interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> RuntimeHeartbeat:
        self._touch()
        self._thread = threading.Thread(
            target=self._pulse,
            name=f"{self._worker_id}-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return self

    def set_state(self, state: str) -> None:
        with self._lock:
            self._state = state
        self._touch()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1.0)
        self.set_state("failed" if exc_type is not None else "idle")

    def _pulse(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._touch()
            except Exception:
                logger.exception("runtime heartbeat refresh failed")

    def _touch(self) -> None:
        with self._lock:
            state = self._state
        with self._session_factory() as session:
            configuration = provider_configuration_status(self._service, os.environ)
            WorkerHeartbeatService(session).touch(
                worker_id=self._worker_id,
                mode=self._mode,
                state=state,
                configuration_status=(
                    "configured"
                    if configuration["status"] == "healthy"
                    else "misconfigured"
                ),
                configuration_issues={
                    "missing": list(configuration.get("missing", [])),
                    "invalid": list(configuration.get("invalid", [])),
                },
                seen_at=datetime.now(timezone.utc),
            )
            session.commit()
