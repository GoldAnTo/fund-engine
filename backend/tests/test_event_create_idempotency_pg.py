"""Real PostgreSQL contention at the event-create idempotency transaction."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic, sleep
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.v1.event_research import create_event_research
from app.errors import ConflictError, ValidationFailedError
from app.models.ledger import CaseTenantAdmission, ResearchCase
from app.models.operational import IdempotencyKey, Job
from app.models.research_preparation import ResearchPreparation
from app.repositories.operational import IdempotencyRepository
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.services.research_preparation import ResearchPreparationService
from tests.test_event_research_api import _confirmed_event


pytestmark = pytest.mark.pg_only


def _assert_blocked_by(engine, blocked_pid: int, winner_pid: int) -> None:
    """Prove overlap through the server's lock graph, not scheduler timing."""
    deadline = monotonic() + 5
    with engine.connect() as observer:
        while monotonic() < deadline:
            blockers = observer.scalar(
                text("SELECT pg_blocking_pids(:pid)"), {"pid": blocked_pid}
            )
            if winner_pid in blockers:
                return
            sleep(0.01)
    pytest.fail("the second independent connection never waited on the winning transaction")


@pytest.mark.parametrize("scenario", ["same_payload", "changed_payload", "winner_rollback"])
def test_event_creation_contends_atomically_on_postgres(
    engine, session, monkeypatch, scenario
):
    # The session fixture owns post-test cleanup; all commands below instead
    # use independent physical connections and commit their own transactions.
    assert engine.dialect.name == "postgresql"
    held = Event()
    release = Event()
    follower_connected = Event()
    pids = {}
    preparation_attempts = []
    original_acquire = IdempotencyRepository.acquire
    original_prepare = ResearchPreparationService.create_for_case

    def hold_winner(repository, **kwargs):
        result = original_acquire(repository, **kwargs)
        if repository._session.info["role"] == "winner":
            assert result[1] is True
            held.set()
            assert release.wait(10), "test never released the winning transaction"
        return result

    def prepare_or_fail(service, *args, **kwargs):
        preparation = original_prepare(service, *args, **kwargs)
        preparation_attempts.append(service._session.info["role"])
        if scenario == "winner_rollback" and service._session.info["role"] == "winner":
            # Fail after the real Case, admission, preparation and queued Job
            # have been flushed, so retry must recover the entire transaction.
            raise ValueError("injected failure after preparation was queued")
        return preparation

    monkeypatch.setattr(IdempotencyRepository, "acquire", hold_winner)
    monkeypatch.setattr(ResearchPreparationService, "create_for_case", prepare_or_fail)
    original_payload = CreateEventResearchRequest.model_validate(_confirmed_event())
    follower_payload = original_payload
    if scenario == "changed_payload":
        follower_payload = original_payload.model_copy(update={"event_title": "different event"})
    key = f"pg-event-create-{uuid.uuid4()}"

    def invoke(role, payload):
        with Session(engine) as command_session:
            command_session.info["role"] = role
            command_session.execute(text("SET LOCAL lock_timeout = '10s'"))
            command_session.execute(text("SET LOCAL statement_timeout = '15s'"))
            pids[role] = command_session.scalar(text("SELECT pg_backend_pid()"))
            if role == "follower":
                follower_connected.set()
            return create_event_research(
                payload, db=command_session, tenant_id="pg-test-team", idempotency_key=key
            ).model_dump(mode="json")

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner = pool.submit(invoke, "winner", original_payload)
        try:
            assert held.wait(5), "winner never acquired the idempotency slot"
            follower = pool.submit(invoke, "follower", follower_payload)
            assert follower_connected.wait(5)
            assert pids["winner"] != pids["follower"]
            _assert_blocked_by(engine, pids["follower"], pids["winner"])
        finally:
            release.set()

        if scenario == "winner_rollback":
            with pytest.raises(ValidationFailedError, match="injected failure"):
                winner.result(timeout=15)
            response = follower.result(timeout=15)
            assert preparation_attempts == ["winner", "follower"]
        else:
            response = winner.result(timeout=15)
            if scenario == "changed_payload":
                with pytest.raises(ConflictError, match="idempotency_conflict"):
                    follower.result(timeout=15)
            else:
                assert follower.result(timeout=15) == response
            assert preparation_attempts == ["winner"]

    # A later retry must replay the committed winner (including after the
    # original winner rolled back) without queuing another preparation Job.
    assert invoke("retry", original_payload) == response
    with Session(engine) as verify:
        for model in (ResearchCase, CaseTenantAdmission, ResearchPreparation, Job, IdempotencyKey):
            assert verify.scalar(select(func.count()).select_from(model)) == 1
        slot = verify.scalar(select(IdempotencyKey))
        assert slot.status == "completed"
        assert slot.response_status == 201
        assert slot.response_payload == response
