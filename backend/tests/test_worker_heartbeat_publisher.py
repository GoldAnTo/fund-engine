from __future__ import annotations

from datetime import datetime
from threading import Event

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker


def test_heartbeat_publisher_stays_fresh_while_worker_work_is_in_progress(tmp_path) -> None:
    from app.models.ledger import Base
    from app.services.research_worker_heartbeat import WorkerHeartbeatService
    from app.services.worker_heartbeat_publisher import WorkerHeartbeatPublisher

    engine = create_engine(
        f"sqlite:///{tmp_path / 'publisher-heartbeat.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    publisher = WorkerHeartbeatPublisher(
        session_factory=sessions,
        worker_id="worker-container",
        worker_kind="research_run",
        interval_seconds=0.01,
    )

    publisher.start()
    try:
        with sessions() as session:
            first_seen = WorkerHeartbeatService(session).latest(
                worker_kind="research_run", worker_id="worker-container"
            ).last_seen_at
        Event().wait(0.05)
        with sessions() as session:
            latest_seen = WorkerHeartbeatService(session).latest(
                worker_kind="research_run", worker_id="worker-container"
            ).last_seen_at
        assert isinstance(first_seen, datetime)
        assert latest_seen > first_seen
    finally:
        publisher.stop()


def test_heartbeat_publisher_exits_the_worker_when_periodic_persistence_fails() -> None:
    from app.services.worker_heartbeat_publisher import WorkerHeartbeatPublisher

    fatal = Event()
    attempts = 0

    def touch() -> None:
        nonlocal attempts
        attempts += 1
        if attempts > 1:
            raise RuntimeError("database unavailable")

    publisher = WorkerHeartbeatPublisher(
        session_factory=lambda: None,
        worker_id="worker-container",
        worker_kind="research_run",
        interval_seconds=0.01,
        fatal_exit=lambda _code: fatal.set(),
    )
    publisher._touch = touch

    publisher.start()
    try:
        assert fatal.wait(1)
    finally:
        publisher.stop()


def test_heartbeat_publisher_retries_transient_sqlite_writer_contention() -> None:
    from app.services.worker_heartbeat_publisher import WorkerHeartbeatPublisher

    fatal = Event()
    retried = Event()
    attempts = 0

    def touch() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked"))
        if attempts == 3:
            retried.set()

    publisher = WorkerHeartbeatPublisher(
        session_factory=lambda: None,
        worker_id="worker-container",
        worker_kind="company_research",
        interval_seconds=0.01,
        fatal_exit=lambda _code: fatal.set(),
    )
    publisher._touch = touch

    publisher.start()
    try:
        assert retried.wait(1)
        assert not fatal.is_set()
    finally:
        publisher.stop()
