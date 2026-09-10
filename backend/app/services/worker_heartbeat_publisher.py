"""Independent durable worker heartbeats for long-running job execution."""
from __future__ import annotations

import os
from collections.abc import Callable
from threading import Event, Thread, current_thread

from app.services.research_worker_heartbeat import WorkerHeartbeatService
from sqlalchemy.exc import OperationalError


def _is_transient_sqlite_writer_contention(error: Exception) -> bool:
    return isinstance(error, OperationalError) and "database is locked" in str(error).lower()


class WorkerHeartbeatPublisher:
    """Publish liveness without waiting for a potentially slow provider call.

    A failed periodic write terminates the worker process so Docker's
    ``restart: unless-stopped`` policy can recover it. The initial write is
    synchronous: an uninitialized database never appears healthy.
    """

    def __init__(
        self,
        *,
        session_factory,
        worker_id: str,
        worker_kind: str,
        interval_seconds: float = 15.0,
        fatal_exit: Callable[[int], None] = os._exit,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        self._session_factory = session_factory
        self._worker_id = worker_id
        self._worker_kind = worker_kind
        self._interval_seconds = interval_seconds
        self._fatal_exit = fatal_exit
        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        self._touch()
        self._thread = Thread(
            target=self._run,
            name=f"heartbeat-{self._worker_kind}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not current_thread():
            self._thread.join(timeout=self._interval_seconds + 1)

    def _touch(self) -> None:
        with self._session_factory() as session:
            WorkerHeartbeatService(session).touch(
                worker_id=self._worker_id,
                worker_kind=self._worker_kind,
                mode="loop",
                state="polling",
            )
            session.commit()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._touch()
            except Exception as error:
                if _is_transient_sqlite_writer_contention(error):
                    continue
                self._fatal_exit(1)
                return
