"""Recoverable company activity dispatch with real Gateway persistence and offline intake."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.models.company_study import (
    CompanyStudy,
    CompanyStudyActivity,
)
from app.models.ledger import Base
from app.models.research_gateway import ResearchConversation, ResearchRunSpec
from app.models.research_team import ProfessionalTask
from app.services.company_study import CompanyStudyService
from tests.test_company_study import BODY
from tests.test_research_gateway_service import ALICE, _Runtime


@pytest.fixture
def db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'company.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 9, 11, tzinfo=UTC)
    with factory() as session:
        service = CompanyStudyService(session, now=lambda: now)
        study = service.create(ALICE, **BODY, idempotency_key="study")
        activity = service.add_activity(
            ALICE,
            UUID(study["id"]),
            kind="event",
            text="核验新订单是否已经形成收入",
            idempotency_key="event",
        )
    yield SimpleNamespace(
        factory=factory,
        engine=engine,
        now=now,
        study_id=UUID(study["id"]),
        activity_id=UUID(activity["id"]),
    )
    engine.dispose()


def worker(db, **kwargs):
    from app.services.company_study_worker import CompanyStudyWorker

    # The native intake is real SQL; only the provider extraction is offline.
    from app.services.research_gateway import ResearchGateway

    return CompanyStudyWorker(
        db.factory,
        now=kwargs.pop("now", lambda: db.now),
        gateway_factory=lambda session: ResearchGateway(
            session, runtime=_Runtime(session)
        ),
        **kwargs,
    )


def test_worker_dispatches_one_real_native_and_four_roles(db):
    runner = worker(db)
    assert runner.run_once() is True
    assert runner.run_once() is False
    with db.factory() as session:
        activity = session.get(CompanyStudyActivity, db.activity_id)
        assert activity.status == "running"
        assert activity.conversation_id is not None and activity.run_spec_id is not None
        assert (
            session.scalar(select(func.count()).select_from(ResearchConversation)) == 1
        )
        assert session.scalar(select(func.count()).select_from(ProfessionalTask)) == 4
        spec = session.get(ResearchRunSpec, activity.run_spec_id)
        assert spec.tenant_id == ALICE.tenant_id and spec.subject_id == ALICE.subject_id
        assert "不是已核验事实" in activity.prompt and BODY["focus"] in activity.prompt
        assert "event" not in activity.title


def test_claim_lease_excludes_other_workers_and_fences_stale_dispatch(db):
    first = worker(db, lease_seconds=60)
    claim = first.claim_next()
    assert claim is not None
    assert worker(db).claim_next() is None
    later = worker(db, now=lambda: db.now + timedelta(seconds=61))
    replacement = later.claim_next()
    assert replacement is not None and replacement.token != claim.token
    assert first.run_claim(claim) is False
    assert later.run_claim(replacement) is True
    with db.factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ResearchConversation)) == 1
        )


def test_lost_receipt_recovers_same_gateway_intent_without_duplicate_native(db):
    runner = worker(db, lease_seconds=60)
    assert runner.claim_next() is not None
    from app.services.company_study_worker import activity_intent_key
    from app.services.research_gateway import ResearchGateway

    with db.factory() as session:
        activity = session.get(CompanyStudyActivity, db.activity_id)
        receipt = ResearchGateway(session, runtime=_Runtime(session)).send_message(
            actor=ALICE,
            text=activity.prompt,
            idempotency_key=activity_intent_key(activity.id),
        )
        # Crash after Gateway committed but before the activity receipt is saved.
        conversation_id = receipt.conversation_id
    later = worker(db, now=lambda: db.now + timedelta(seconds=61))
    assert later.run_once() is True
    with db.factory() as session:
        activity = session.get(CompanyStudyActivity, db.activity_id)
        assert activity.conversation_id == conversation_id
        assert (
            session.scalar(select(func.count()).select_from(ResearchConversation)) == 1
        )
        assert session.scalar(select(func.count()).select_from(ResearchRunSpec)) == 1


def test_failure_is_bounded_and_explicit_retry_reuses_same_activity(db):
    from app.services.company_study_worker import CompanyStudyWorker

    class Unavailable:
        def send_message(self, **kwargs):
            raise RuntimeError(
                "private provider response and sensitive query must not be retained"
            )

    runner = CompanyStudyWorker(
        db.factory, now=lambda: db.now, gateway_factory=lambda session: Unavailable()
    )
    assert runner.run_once() is True
    with db.factory() as session:
        activity = session.get(CompanyStudyActivity, db.activity_id)
        assert (
            activity.status == "failed"
            and activity.error_code == "research_start_unavailable"
        )
        service = CompanyStudyService(session)
        retry = service.retry(
            ALICE, db.study_id, db.activity_id, idempotency_key="retry"
        )
        assert retry["id"] == str(db.activity_id) and retry["status"] == "queued"
        assert (
            service.retry(ALICE, db.study_id, db.activity_id, idempotency_key="retry")
            == retry
        )
    assert worker(db).run_once() is True
    with db.factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ResearchConversation)) == 1
        )


def test_due_monitor_deduplicates_caps_outstanding_and_pause_stops_dispatch(db):
    with db.factory() as session:
        CompanyStudyService(session, now=lambda: db.now).configure_monitor(
            ALICE,
            db.study_id,
            status="active",
            frequency="daily",
            focus="只研究已公布财报的变化",
            idempotency_key="monitor",
        )
    due = db.now.replace(hour=12)
    runner = worker(db, now=lambda: due)
    assert runner.schedule_due() == 1
    assert runner.schedule_due() == 0
    # A still outstanding automatic activity consumes the dossier's one slot.
    assert worker(db, now=lambda: due + timedelta(days=1)).schedule_due() == 0
    with db.factory() as session:
        auto = session.scalar(
            select(CompanyStudyActivity).where(
                CompanyStudyActivity.schedule_key.is_not(None)
            )
        )
        assert auto.kind == "refresh" and "已公布财报" in auto.text
        assert "2026-09-09" in auto.schedule_key
        auto.status = "failed"
        session.commit()
    assert worker(db, now=lambda: due + timedelta(days=2)).schedule_due() == 1
    with db.factory() as session:
        CompanyStudyService(
            session, now=lambda: due + timedelta(days=2, hours=1)
        ).configure_monitor(
            ALICE,
            db.study_id,
            status="paused",
            frequency="daily",
            focus="暂停",
            idempotency_key="pause",
        )
    assert worker(db, now=lambda: due + timedelta(days=3)).schedule_due() == 0


def test_pause_reactivate_same_window_cannot_create_duplicate(db):
    with db.factory() as session:
        service = CompanyStudyService(session, now=lambda: db.now)
        service.configure_monitor(
            ALICE,
            db.study_id,
            status="active",
            frequency="daily",
            focus="变化",
            idempotency_key="active",
        )
    due = db.now.replace(hour=12)
    assert worker(db, now=lambda: due).schedule_due() == 1
    with db.factory() as session:
        activity = session.scalar(
            select(CompanyStudyActivity).where(
                CompanyStudyActivity.schedule_key.is_not(None)
            )
        )
        activity.status = "failed"
        # Simulate a restarted scheduler whose due pointer was not advanced.
        session.get(CompanyStudy, db.study_id).monitor_next_due_at = due
        session.commit()
    assert worker(db, now=lambda: due + timedelta(minutes=1)).schedule_due() == 0


def test_concurrent_workers_claim_and_schedule_each_window_once(db):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    def concurrent(call):
        barrier = Barrier(2)

        def run(_):
            barrier.wait()
            return call()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, range(2)))

    claims = concurrent(lambda: worker(db).claim_next())
    assert sum(claim is not None for claim in claims) == 1
    with db.factory() as session:
        CompanyStudyService(session, now=lambda: db.now).configure_monitor(
            ALICE,
            db.study_id,
            status="active",
            frequency="daily",
            focus="财报更新",
            idempotency_key="monitor",
        )
    counts = concurrent(
        lambda: worker(db, now=lambda: db.now.replace(hour=12)).schedule_due()
    )
    assert sum(counts) == 1


def test_monitor_persists_blocked_activity_when_adopted_sources_unavailable(db):
    from sqlalchemy import text

    from tests.test_research_gateway_content import _contract
    from tests.test_research_team_read import complete_team

    with db.factory() as session:
        service = CompanyStudyService(session, now=lambda: db.now)
        spec, links, _ = complete_team(session)
        linked = service.link(
            ALICE,
            db.study_id,
            conversation_id=spec.conversation_id,
            idempotency_key="link",
        )
        service.adopt(
            ALICE,
            db.study_id,
            activity_id=UUID(linked["id"]),
            expected_revision=0,
            note="采用有据可查的判断",
            idempotency_key="adopt",
        )
        service.configure_monitor(
            ALICE,
            db.study_id,
            status="active",
            frequency="daily",
            focus="跟踪变化",
            idempotency_key="monitor",
        )
        contract = _contract(session, links[0])
        contract_id = contract.id.hex
        session.execute(
            text("UPDATE source_contracts SET allow_display=0 WHERE id=:id"),
            {"id": contract_id},
        )
        session.commit()
    due = db.now.replace(hour=12)
    assert worker(db, now=lambda: due).schedule_due() == 1
    with db.factory() as session:
        activity = session.scalar(
            select(CompanyStudyActivity).where(
                CompanyStudyActivity.schedule_key.is_not(None)
            )
        )
        assert (
            activity.status == "blocked"
            and activity.error_code == "adopted_research_unavailable"
        )
        assert activity.prompt is None and activity.context_revision == 1
        activity_id = activity.id
        session.execute(
            text("UPDATE source_contracts SET allow_display=1 WHERE id=:id"),
            {"id": contract_id},
        )
        session.get(CompanyStudyActivity, db.activity_id).status = "failed"
        session.commit()
        CompanyStudyService(session).retry(
            ALICE, db.study_id, activity_id, idempotency_key="retry"
        )
    assert worker(db, now=lambda: due).run_once() is True
    with db.factory() as session:
        activity = session.get(CompanyStudyActivity, activity_id)
        assert activity.status == "running" and "的证据判断" not in activity.prompt
        assert activity.run_spec_id is not None


def test_gateway_in_progress_lease_recovers_automatically_after_both_leases_expire(
    db, monkeypatch
):
    from app.repositories import research_gateway as repository_module
    from app.services import research_gateway as gateway_module
    from app.services.company_study_worker import activity_intent_key
    from app.services.research_gateway import ResearchGateway

    current = [db.now]
    monkeypatch.setattr(gateway_module, "_utcnow", lambda: current[0])
    monkeypatch.setattr(repository_module, "_utcnow", lambda: current[0])
    runner = worker(db, now=lambda: current[0], lease_seconds=240)
    assert runner.claim_next() is not None
    with db.factory() as session:
        activity = session.get(CompanyStudyActivity, db.activity_id)
        ResearchGateway(session)._acquire_request(
            ALICE,
            operation="send_message",
            idempotency_key=activity_intent_key(activity.id),
            payload={"conversation_id": None, "text": activity.prompt},
        )
    current[0] += timedelta(seconds=241)
    assert runner.run_once()
    with db.factory() as session:
        assert session.get(CompanyStudyActivity, db.activity_id).status == "starting"
    current[0] = db.now + timedelta(seconds=302)
    assert runner.run_once()
    with db.factory() as session:
        assert session.get(CompanyStudyActivity, db.activity_id).run_spec_id is not None
        assert (
            session.scalar(select(func.count()).select_from(ResearchConversation)) == 1
        )
