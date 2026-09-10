"""PostgreSQL serialization proofs for Case grant mutations."""

from __future__ import annotations

from dataclasses import replace
from queue import Empty, Queue
from threading import Event, Thread

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.errors import PermissionDeniedError
from app.models.events import DomainEvent
from app.models.identity import CaseAccessGrant
from app.models.ledger import CaseTenantAdmission
from app.services.case_authorization import CaseAuthorizationService
from tests.research_identity import persist_research_principal


def _admit(session, research_case, document, *, tenant_id: str) -> None:
    session.add(
        CaseTenantAdmission(
            research_case_id=research_case.id,
            tenant_id=tenant_id,
            initial_document_version_id=document.id,
            admitted_by="test:fixture",
            admitted_at=research_case.created_at,
        )
    )
    session.flush()


def _grant(session, case_id, principal, role: str) -> CaseAccessGrant:
    grant = CaseAccessGrant(
        research_case_id=case_id,
        user_id=principal.user_id,
        role=role,
        granted_by_principal_id="test:fixture",
        reason="concurrency fixture",
        created_at=principal.expires_at,
        updated_at=principal.expires_at,
    )
    session.add(grant)
    session.flush()
    return grant


def _set_bounded_timeouts(session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '1500ms'"))
    session.execute(text("SET LOCAL statement_timeout = '4000ms'"))


def _bounded_outcome(thread: Thread, outcomes: Queue[object]) -> object:
    thread.join(timeout=5)
    assert not thread.is_alive(), "bounded PostgreSQL worker did not stop"
    try:
        outcome = outcomes.get(timeout=0.25)
    except Empty as exc:
        raise AssertionError("bounded PostgreSQL worker returned no outcome") from exc
    if isinstance(outcome, BaseException):
        raise outcome
    return outcome


def _assert_backend_waiting_on_lock(session, backend_pid: int) -> None:
    for _ in range(100):
        wait_event_type = session.scalar(
            text(
                "SELECT wait_event_type FROM pg_stat_activity "
                "WHERE pid = :backend_pid"
            ),
            {"backend_pid": backend_pid},
        )
        if wait_event_type == "Lock":
            return
        Event().wait(0.01)
    raise AssertionError("worker backend never entered a PostgreSQL lock wait")


@pytest.mark.pg_only
def test_preloaded_admin_is_refreshed_after_waiting_for_case_lock(
    engine, session, research_case, document
) -> None:
    tenant_id = "tenant-case-auth-recheck"
    _admit(session, research_case, document, tenant_id=tenant_id)
    stale_admin = persist_research_principal(
        session, tenant_id=tenant_id, label="stale-admin"
    )
    remaining_owner = persist_research_principal(
        session, tenant_id=tenant_id, label="remaining-owner"
    )
    tenant_admin = replace(
        persist_research_principal(
            session, tenant_id=tenant_id, label="tenant-admin"
        ),
        roles=frozenset({"tenant_administrator"}),
    )
    target = persist_research_principal(
        session, tenant_id=tenant_id, label="target"
    )
    stale_grant = _grant(session, research_case.id, stale_admin, "owner")
    _grant(session, research_case.id, remaining_owner, "owner")
    _grant(session, research_case.id, tenant_admin, "owner")
    session.commit()

    SessionLocal = sessionmaker(bind=engine, future=True)
    controlling = SessionLocal()
    preloaded = Event()
    proceed = Event()
    outcomes: Queue[object] = Queue()
    worker_pids: Queue[int] = Queue()

    def grant_after_wait() -> None:
        worker = SessionLocal()
        try:
            _set_bounded_timeouts(worker)
            worker_pid = worker.scalar(text("SELECT pg_backend_pid()"))
            assert worker_pid is not None
            worker_pids.put(worker_pid)
            cached = worker.get(CaseAccessGrant, stale_grant.id)
            assert cached is not None and cached.role == "owner"
            preloaded.set()
            assert proceed.wait(timeout=2)
            CaseAuthorizationService(worker).grant(
                research_case.id,
                stale_admin,
                target.user_id,
                "viewer",
                reason="must recheck after wait",
            )
            worker.commit()
            outcomes.put("granted")
        except PermissionDeniedError:
            worker.rollback()
            outcomes.put("permission_denied")
        except BaseException as exc:
            worker.rollback()
            outcomes.put(exc)
        finally:
            worker.close()

    worker_thread = Thread(target=grant_after_wait, daemon=True)
    worker_thread.start()
    try:
        assert preloaded.wait(timeout=2)
        worker_pid = worker_pids.get(timeout=0.25)
        _set_bounded_timeouts(controlling)
        controlling.scalar(
            select(CaseTenantAdmission)
            .where(CaseTenantAdmission.research_case_id == research_case.id)
            .with_for_update()
        )
        CaseAuthorizationService(controlling).change_role(
            research_case.id,
            tenant_admin,
            stale_admin.user_id,
            "viewer",
            reason="administrator downgraded",
        )
        proceed.set()
        _assert_backend_waiting_on_lock(controlling, worker_pid)
        controlling.commit()
        assert _bounded_outcome(worker_thread, outcomes) == "permission_denied"
    finally:
        proceed.set()
        controlling.rollback()
        controlling.close()
        worker_thread.join(timeout=5)


@pytest.mark.pg_only
def test_preloaded_grant_refreshes_old_role_after_waiting_for_case_lock(
    engine, session, research_case, document
) -> None:
    tenant_id = "tenant-case-auth-old-role"
    _admit(session, research_case, document, tenant_id=tenant_id)
    first_admin = replace(
        persist_research_principal(
            session, tenant_id=tenant_id, label="first-admin"
        ),
        roles=frozenset({"tenant_administrator"}),
    )
    second_admin = replace(
        persist_research_principal(
            session, tenant_id=tenant_id, label="second-admin"
        ),
        roles=frozenset({"tenant_administrator"}),
    )
    target = persist_research_principal(
        session, tenant_id=tenant_id, label="target"
    )
    target_grant = _grant(session, research_case.id, target, "viewer")
    _grant(session, research_case.id, first_admin, "owner")
    _grant(session, research_case.id, second_admin, "owner")
    session.commit()

    SessionLocal = sessionmaker(bind=engine, future=True)
    controlling = SessionLocal()
    preloaded = Event()
    proceed = Event()
    outcomes: Queue[object] = Queue()
    worker_pids: Queue[int] = Queue()

    def second_change() -> None:
        worker = SessionLocal()
        try:
            _set_bounded_timeouts(worker)
            worker_pid = worker.scalar(text("SELECT pg_backend_pid()"))
            assert worker_pid is not None
            worker_pids.put(worker_pid)
            cached = worker.get(CaseAccessGrant, target_grant.id)
            assert cached is not None and cached.role == "viewer"
            preloaded.set()
            assert proceed.wait(timeout=2)
            CaseAuthorizationService(worker).change_role(
                research_case.id,
                second_admin,
                target.user_id,
                "reviewer",
                reason="second serialized change",
            )
            worker.commit()
            outcomes.put("changed")
        except BaseException as exc:
            worker.rollback()
            outcomes.put(exc)
        finally:
            worker.close()

    worker_thread = Thread(target=second_change, daemon=True)
    worker_thread.start()
    try:
        assert preloaded.wait(timeout=2)
        worker_pid = worker_pids.get(timeout=0.25)
        _set_bounded_timeouts(controlling)
        controlling.scalar(
            select(CaseTenantAdmission)
            .where(CaseTenantAdmission.research_case_id == research_case.id)
            .with_for_update()
        )
        CaseAuthorizationService(controlling).change_role(
            research_case.id,
            first_admin,
            target.user_id,
            "editor",
            reason="first serialized change",
        )
        proceed.set()
        _assert_backend_waiting_on_lock(controlling, worker_pid)
        controlling.commit()
        assert _bounded_outcome(worker_thread, outcomes) == "changed"
    finally:
        proceed.set()
        controlling.rollback()
        controlling.close()
        worker_thread.join(timeout=5)

    events = list(
        session.scalars(
            select(DomainEvent)
            .where(DomainEvent.type == "case_access_role_changed")
            .order_by(DomainEvent.seq)
        )
    )
    assert [
        (event.payload["old_role"], event.payload["new_role"])
        for event in events
    ] == [("viewer", "editor"), ("editor", "reviewer")]
