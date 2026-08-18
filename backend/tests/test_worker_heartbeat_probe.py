from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_heartbeat_probe_requires_a_fresh_matching_loop_worker(tmp_path, monkeypatch) -> None:
    from app.models.ledger import Base
    from app.services.research_worker_heartbeat import WorkerHeartbeatService
    from app.scripts import check_worker_heartbeat

    engine = create_engine(f"sqlite:///{tmp_path / 'worker-heartbeat.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(check_worker_heartbeat, "SessionLocal", sessions)

    arguments = [
        "--worker-kind",
        "acquisition",
        "--worker-id",
        "acquisition-container",
        "--max-age-seconds",
        "30",
    ]
    assert check_worker_heartbeat.main(arguments) == 1

    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="acquisition-container",
            worker_kind="acquisition",
            mode="loop",
            state="polling",
            seen_at=datetime.now(timezone.utc),
        )
        session.commit()

    assert check_worker_heartbeat.main(arguments) == 0

    with sessions() as session:
        WorkerHeartbeatService(session).touch(
            worker_id="acquisition-container",
            worker_kind="acquisition",
            mode="loop",
            state="polling",
            seen_at=datetime.now(timezone.utc) - timedelta(seconds=31),
        )
        session.commit()

    assert check_worker_heartbeat.main(arguments) == 1
