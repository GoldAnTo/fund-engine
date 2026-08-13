"""Lease fencing and provenance reads for governed acquisition jobs."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, Event
from time import sleep

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import sessionmaker

from app.domain.acquisition import AdmittedEvidenceRef
from app.errors import ConflictError
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionJob,
    AcquisitionJobEvent,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    AtomicClaimCandidate,
    Base,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.repositories.acquisition import (
    AcquisitionClaim,
    AcquisitionRepository,
    StaleLeaseError,
    TerminalJobError,
)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> Clock:
    return Clock(datetime(2026, 8, 12, 8, tzinfo=UTC))


@pytest.fixture
def repo(session, clock) -> AcquisitionRepository:
    return AcquisitionRepository(session, clock=clock)


def _create_job(repo, research_case, thesis, **overrides):
    values = {
        "tenant_id": "team-a",
        "research_case_id": research_case.id,
        "thesis_id": thesis.id,
        "research_run_id": None,
        "idempotency_key": uuid.uuid4().hex,
        "request_snapshot": {"objective": "support", "cutoff": "2026-08-12T08:00:00Z"},
        "policy_snapshot": {"version": "b-scope-v1"},
        "creation_payload": {"actor": "system:test"},
    }
    values.update(overrides)
    return repo.create_or_get(**values)


def test_create_or_get_is_idempotent_and_starts_event_sequence(repo, research_case, thesis):
    key = uuid.uuid4().hex
    first = _create_job(repo, research_case, thesis, idempotency_key=key)
    second = _create_job(repo, research_case, thesis, idempotency_key=key)

    assert second == first
    assert first.status == "queued"
    events = repo.events(first.id)
    assert [(event.seq, event.status, event.stage) for event in events] == [
        (1, "queued", "queued")
    ]
    assert events[0].payload_json == {"actor": "system:test"}


def test_same_idempotency_key_rejects_a_different_frozen_request(repo, research_case, thesis):
    key = uuid.uuid4().hex
    _create_job(repo, research_case, thesis, idempotency_key=key)

    with pytest.raises(ConflictError, match="idempotency"):
        _create_job(
            repo,
            research_case,
            thesis,
            idempotency_key=key,
            request_snapshot={"objective": "contradict"},
        )


@pytest.mark.parametrize(
    "unsafe",
    [
        {"AuthorizationHeader": "Bearer value"},
        {"nested": [{"api-key-name": "value"}]},
        {"url": "https://user:pass@example.test/report"},
        {"url": "https://example.test/report?access_token=value"},
    ],
)
def test_persisted_json_rejects_credentials_and_sensitive_urls(
    repo, research_case, thesis, unsafe
):
    with pytest.raises(ValueError, match="sensitive|userinfo"):
        _create_job(repo, research_case, thesis, request_snapshot=unsafe)


def test_claim_is_frozen_random_fenced_and_transactionally_coherent(
    repo, session, clock, research_case, thesis
):
    queued = _create_job(repo, research_case, thesis)

    claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))

    assert isinstance(claim, AcquisitionClaim)
    assert claim.job_id == queued.id
    assert claim.lease_owner == "worker-a"
    assert claim.lease_token
    assert claim.lease_expires_at == clock.now + timedelta(seconds=30)
    assert claim.attempt == 1
    with pytest.raises(FrozenInstanceError):
        claim.attempt = 2
    job = session.get(AcquisitionJob, queued.id)
    assert job is not None
    assert (job.status, job.stage, job.attempt) == ("running", "searching", 1)
    assert job.started_at is not None
    assert job.lease_token == claim.lease_token
    assert repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30)) is None
    assert [event.seq for event in repo.events(queued.id)] == [1, 2]

    with pytest.raises(StaleLeaseError):
        repo.advance(
            queued.id,
            lease_token="wrong-token",
            stage="fetching",
            message="must be fenced",
        )


def test_file_sqlite_atomic_claim_race_has_one_winner_and_one_none(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'claim-race.sqlite3'}",
        future=True,
        connect_args={"timeout": 5},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime(2026, 8, 12, 8, tzinfo=UTC)
    with sessions() as seed:
        case = ResearchCase(
            title="race",
            industry_topic="test",
            created_by="test",
            created_at=now,
        )
        seed.add(case)
        seed.flush()
        thesis = Thesis(
            research_case_id=case.id,
            statement="race thesis",
            created_by="test",
            created_at=now,
        )
        seed.add(thesis)
        seed.flush()
        queued = _create_job(
            AcquisitionRepository(seed, clock=lambda: now), case, thesis
        )
        seed.commit()

    winner_session = sessions()
    loser_session = sessions()
    try:
        winner_repo = AcquisitionRepository(winner_session, clock=lambda: now)
        loser_repo = AcquisitionRepository(loser_session, clock=lambda: now)
        winner = winner_repo.claim_next(
            worker_id="worker-a", lease_for=timedelta(seconds=30)
        )
        assert winner is not None

        loser_started = Event()

        def claim_loser():
            loser_started.set()
            return loser_repo.claim_next(
                worker_id="worker-b", lease_for=timedelta(seconds=30)
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            loser_future = pool.submit(claim_loser)
            assert loser_started.wait(timeout=1)
            # Give the second connection time to evaluate the still-visible
            # queued row and contend on SQLite's writer lock.
            sleep(0.1)
            winner_session.commit()
            loser = loser_future.result(timeout=5)
        loser_session.commit()

        assert loser is None
        with sessions() as verify:
            stored = verify.get(AcquisitionJob, queued.id)
            assert stored is not None
            assert stored.lease_token == winner.lease_token
            assert stored.attempt == 1
            assert [
                event.seq
                for event in verify.scalars(
                    select(AcquisitionJobEvent)
                    .where(AcquisitionJobEvent.job_id == queued.id)
                    .order_by(AcquisitionJobEvent.seq)
                )
            ] == [1, 2]
    finally:
        winner_session.close()
        loser_session.close()
        engine.dispose()


@pytest.mark.pg_only
def test_postgres_skip_locked_takeover_and_stale_cas_use_independent_sessions(
    engine, session, research_case, thesis
):
    now = datetime(2026, 8, 12, 8, tzinfo=UTC)
    seed_repo = AcquisitionRepository(session, clock=lambda: now)
    first_job = _create_job(seed_repo, research_case, thesis)
    second_job = _create_job(seed_repo, research_case, thesis)
    session.commit()
    sessions = sessionmaker(bind=engine, future=True)
    start = Barrier(2)
    both_claimed = Barrier(2)

    def claim(worker_id):
        with sessions() as worker_session:
            start.wait(timeout=5)
            value = AcquisitionRepository(
                worker_session, clock=lambda: now
            ).claim_next(worker_id=worker_id, lease_for=timedelta(seconds=30))
            assert value is not None
            both_claimed.wait(timeout=5)
            worker_session.commit()
            return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = tuple(
            future.result(timeout=10)
            for future in (
                pool.submit(claim, "worker-pg-a"),
                pool.submit(claim, "worker-pg-b"),
            )
        )

    assert {claim.job_id for claim in claims} == {first_job.id, second_job.id}
    stale = claims[0]
    with sessions() as cancel_session:
        AcquisitionRepository(cancel_session, clock=lambda: now).cancel(
            claims[1].job_id, lease_token=claims[1].lease_token
        )
        cancel_session.commit()
    takeover_now = now + timedelta(seconds=31)
    with sessions() as takeover_session:
        replacement = AcquisitionRepository(
            takeover_session, clock=lambda: takeover_now
        ).claim_next(
            worker_id="worker-pg-takeover", lease_for=timedelta(seconds=30)
        )
        assert replacement is not None
        takeover_session.commit()
    with sessions() as stale_session:
        with pytest.raises(StaleLeaseError):
            AcquisitionRepository(stale_session, clock=lambda: takeover_now).advance(
                stale.job_id,
                lease_token=stale.lease_token,
                stage="fetching",
            )


def test_expired_lease_reclaim_rotates_token_and_fences_old_worker(
    repo, clock, research_case, thesis
):
    job = _create_job(repo, research_case, thesis)
    old = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))
    assert old is not None
    clock.advance(timedelta(seconds=31))

    new = repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30))

    assert new is not None
    assert new.job_id == job.id
    assert new.lease_token != old.lease_token
    assert new.attempt == 2
    with pytest.raises(StaleLeaseError):
        repo.advance(job.id, lease_token=old.lease_token, stage="fetching")
    repo.advance(job.id, lease_token=new.lease_token, stage="fetching")
    assert [event.seq for event in repo.events(job.id)] == [1, 2, 3, 4]


def test_advance_validates_stages_updates_counters_and_freezes_terminal_job(
    repo, research_case, thesis
):
    job = _create_job(repo, research_case, thesis)
    claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))
    assert claim is not None

    with pytest.raises(ValueError, match="stage"):
        repo.advance(job.id, lease_token=claim.lease_token, stage="queued")
    with pytest.raises(ValueError, match="counter"):
        repo.advance(
            job.id,
            lease_token=claim.lease_token,
            stage="fetching",
            counters={"unknown_count": 1},
        )

    view = repo.advance(
        job.id,
        lease_token=claim.lease_token,
        stage="admitting",
        counters={"reference_count": 2, "admitted_count": 1},
        message="admitted evidence",
        payload={"count": 1},
    )
    assert (view.status, view.stage, view.attempt) == ("running", "admitting", 1)
    terminal = repo.advance(
        job.id,
        lease_token=claim.lease_token,
        stage="succeeded",
        status="succeeded",
        message="complete",
    )
    assert (terminal.status, terminal.stage) == ("succeeded", "succeeded")
    assert [event.seq for event in repo.events(job.id)] == [1, 2, 3, 4]
    with pytest.raises(TerminalJobError):
        repo.advance(job.id, lease_token=claim.lease_token, stage="failed", status="failed")
    assert repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30)) is None


def test_advance_rejects_stage_regression_without_appending_event(
    repo, session, research_case, thesis
):
    job = _create_job(repo, research_case, thesis)
    claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))
    assert claim is not None
    repo.advance(job.id, lease_token=claim.lease_token, stage="admitting")
    event_ids_before = tuple(event.id for event in repo.events(job.id))

    with pytest.raises(ValueError, match="regress"):
        repo.advance(job.id, lease_token=claim.lease_token, stage="searching")

    session.expire_all()
    stored = session.get(AcquisitionJob, job.id)
    assert stored is not None
    assert (stored.status, stored.stage) == ("running", "admitting")
    assert tuple(event.id for event in repo.events(job.id)) == event_ids_before


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_advance_stage_cas_predicate_has_same_contract_on_sqlite_and_postgres(
    dialect,
):
    predicate = AcquisitionRepository._advance_stage_predicate(
        status="running", stage="extracting"
    )
    compiled = str(
        select(AcquisitionJob.id)
        .where(predicate)
        .compile(dialect=dialect, compile_kwargs={"literal_binds": True})
    ).casefold()

    assert "acquisition_jobs.stage in" in compiled
    for allowed in ("searching", "fetching", "freezing", "extracting"):
        assert allowed in compiled
    assert "admitting" not in compiled


def test_retry_wait_releases_lease_and_is_not_claimable_until_due(
    repo, clock, research_case, thesis
):
    job = _create_job(repo, research_case, thesis)
    claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))
    assert claim is not None
    china_standard_time = timezone(timedelta(hours=8))
    retry_at = (clock.now + timedelta(minutes=5)).astimezone(china_standard_time)

    retry = repo.advance(
        job.id,
        lease_token=claim.lease_token,
        stage="fetching",
        status="retry_wait",
        retry_at=retry_at,
        message="provider unavailable",
    )

    assert retry.status == "retry_wait"
    stored = repo.get_record(job.id)
    assert stored is not None
    assert stored.lease_token is None
    assert stored.lease_owner is None
    assert stored.lease_expires_at is None
    assert repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30)) is None
    clock.advance(timedelta(minutes=5))
    reclaimed = repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30))
    assert reclaimed is not None
    assert reclaimed.job_id == job.id
    assert reclaimed.attempt == 2


@pytest.mark.parametrize("initial", ["queued", "retry_wait", "running"])
def test_cancel_supports_all_nonterminal_statuses_and_terminal_jobs_are_immutable(
    repo, clock, research_case, thesis, initial
):
    job = _create_job(repo, research_case, thesis)
    token = None
    if initial in {"retry_wait", "running"}:
        claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))
        assert claim is not None
        token = claim.lease_token
    if initial == "retry_wait":
        repo.advance(
            job.id,
            lease_token=token,
            stage="searching",
            status="retry_wait",
            retry_at=clock.now + timedelta(minutes=1),
        )
        token = None

    cancelled = repo.cancel(job.id, lease_token=token, payload={"reason": "user"})

    assert (cancelled.status, cancelled.stage) == ("cancelled", "cancelled")
    with pytest.raises(TerminalJobError):
        repo.cancel(job.id, lease_token=None)
    assert repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30)) is None


def test_fenced_update_rejects_old_token_even_when_job_is_still_running(
    repo, clock, research_case, thesis
):
    job = _create_job(repo, research_case, thesis)
    first = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=1))
    assert first is not None
    clock.advance(timedelta(seconds=2))
    second = repo.claim_next(worker_id="worker-b", lease_for=timedelta(seconds=30))
    assert second is not None

    with pytest.raises(StaleLeaseError):
        repo.advance(job.id, lease_token=first.lease_token, stage="fetching")


def test_expired_token_cannot_advance_terminate_or_cancel_before_reclaim(
    repo, clock, research_case, thesis
):
    job = _create_job(repo, research_case, thesis)
    claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=1))
    assert claim is not None
    clock.advance(timedelta(seconds=2))

    with pytest.raises(StaleLeaseError):
        repo.advance(job.id, lease_token=claim.lease_token, stage="fetching")
    with pytest.raises(StaleLeaseError):
        repo.advance(
            job.id,
            lease_token=claim.lease_token,
            stage="succeeded",
            status="succeeded",
        )
    with pytest.raises(StaleLeaseError):
        repo.cancel(job.id, lease_token=claim.lease_token)

    stored = repo.get_record(job.id)
    assert stored is not None
    assert (stored.status, stored.stage, stored.lease_token) == (
        "running",
        "searching",
        claim.lease_token,
    )
    assert [event.seq for event in repo.events(job.id)] == [1, 2]


@pytest.mark.parametrize(
    "worker_id",
    [
        "Authorization: Basic abc",
        "Cookie=session-id",
        "Bearer abc.def",
        "password=hunter2",
        "X-Api-Key: value",
    ],
)
def test_claim_rejects_credential_shaped_worker_id_before_mutation(
    repo, research_case, thesis, worker_id
):
    job = _create_job(repo, research_case, thesis)

    with pytest.raises(ValueError, match="credential|sensitive"):
        repo.claim_next(worker_id=worker_id, lease_for=timedelta(seconds=30))

    stored = repo.get_record(job.id)
    assert stored is not None
    assert (stored.status, stored.stage, stored.attempt) == ("queued", "queued", 0)
    assert [event.seq for event in repo.events(job.id)] == [1]


@pytest.mark.parametrize(
    ("field_name", "unsafe"),
    [
        ("message", "Authorization: Bearer abc"),
        ("message", "https://user:pass@example.test/report"),
        ("error_code", "token=abc"),
        ("error_detail", "Cookie: session=abc"),
        ("error_detail", "https://example.test/report?api_key=abc"),
        ("error_detail", "Bearer abc.def"),
    ],
)
def test_advance_rejects_credential_shaped_diagnostics_before_mutation(
    repo, research_case, thesis, field_name, unsafe
):
    job = _create_job(repo, research_case, thesis)
    claim = repo.claim_next(worker_id="worker-a", lease_for=timedelta(seconds=30))
    assert claim is not None

    with pytest.raises(ValueError, match="credential|sensitive|userinfo"):
        repo.advance(
            job.id,
            lease_token=claim.lease_token,
            stage="fetching",
            **{field_name: unsafe},
        )

    stored = repo.get_record(job.id)
    assert stored is not None
    assert (stored.status, stored.stage) == ("running", "searching")
    assert stored.error_code is None
    assert stored.error_detail is None
    assert [event.seq for event in repo.events(job.id)] == [1, 2]


@pytest.mark.parametrize(
    "mismatch",
    [
        "decision_job",
        "link_thesis",
        "statement_candidate",
        "candidate_span",
        "artifact_document",
        "reference_job",
        "attempt_job",
    ],
)
def test_admitted_evidence_returns_only_fully_coherent_lineage(
    repo,
    session,
    research_case,
    thesis,
    document,
    span,
    document_service,
    mismatch,
):
    other_thesis = Thesis(
        research_case_id=research_case.id,
        statement="other",
        created_by="test",
        created_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    session.add(other_thesis)
    session.flush()
    target = _create_job(repo, research_case, thesis)
    other = _create_job(repo, research_case, other_thesis)
    alternate_document = document_service.freeze(
        raw=f"alternate {uuid.uuid4().hex}".encode(),
        source_url=f"https://example.test/{uuid.uuid4().hex}",
    )
    alternate_span = document_service.add_span(
        document_version_id=alternate_document.id,
        locator={"page": 1, "paragraph": 0},
        verbatim_text="alternate span",
    )

    def add_chain(*, mismatch_kind=None):
        suffix = uuid.uuid4().hex
        attempt = AcquisitionAttempt(
            job_id=other.id if mismatch_kind == "attempt_job" else target.id,
            adapter_key=f"test-{suffix[:8]}",
            operation="fetch",
            attempt_no=1,
            started_at=datetime(2026, 8, 12, tzinfo=UTC),
            outcome="succeeded",
            retryable=False,
            safe_metadata={},
        )
        reference = SourceReference(
            job_id=other.id if mismatch_kind == "reference_job" else target.id,
            adapter_key=attempt.adapter_key,
            external_record_id=suffix,
            external_version="v1",
            canonical_url=f"https://example.test/{suffix}",
            title="test source",
            source_role="company_disclosure",
            metadata_json={},
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        candidate = AtomicClaimCandidate(
            source_span_id=(
                alternate_span.id if mismatch_kind == "candidate_span" else span.id
            ),
            canonical_key=suffix,
            quote="claim",
            quote_start=0,
            quote_end=5,
            quote_sha256="a" * 64,
            normalized_text="claim",
            claim_type="reported_claim",
            authority_level="primary",
            structured_fields={},
            validation_result={},
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        session.add_all([attempt, reference, candidate])
        session.flush()
        artifact = RetrievalArtifact(
            source_reference_id=reference.id,
            attempt_id=attempt.id,
            content_sha256=suffix + suffix,
            raw_bytes=b"x",
            mime_type="text/plain",
            byte_size=1,
            final_url=reference.canonical_url,
            retrieved_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        session.add(artifact)
        session.flush()
        binding = RetrievalArtifactDocument(
            retrieval_artifact_id=artifact.id,
            document_version_id=(
                alternate_document.id
                if mismatch_kind == "artifact_document"
                else document.id
            ),
            relation="created",
            publication_key=suffix,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        session.add(binding)
        session.flush()
        decision = AutomaticAdmissionDecision(
            job_id=other.id if mismatch_kind == "decision_job" else target.id,
            candidate_id=candidate.id,
            retrieval_artifact_id=artifact.id,
            outcome="admitted",
            gate_version=uuid.uuid4().hex,
            policy_version="b-scope-v1",
            gate_results={},
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        session.add(decision)
        session.flush()
        statement_candidate_id = candidate.id
        if mismatch_kind == "statement_candidate":
            statement_candidate = AtomicClaimCandidate(
                source_span_id=span.id,
                canonical_key=uuid.uuid4().hex,
                quote="other claim",
                quote_start=0,
                quote_end=11,
                quote_sha256="b" * 64,
                normalized_text="other claim",
                claim_type="reported_claim",
                authority_level="primary",
                structured_fields={},
                validation_result={},
                created_at=datetime(2026, 8, 12, tzinfo=UTC),
            )
            session.add(statement_candidate)
            session.flush()
            statement_candidate_id = statement_candidate.id
        statement = SourceStatement(
            source_span_id=span.id,
            kind="disclosed_fact",
            normalized_text=uuid.uuid4().hex,
            atomic_claim_candidate_id=statement_candidate_id,
            automatic_admission_decision_id=decision.id,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        session.add(statement)
        session.flush()
        link = EvidenceLink(
            thesis_id=(
                other_thesis.id if mismatch_kind == "link_thesis" else thesis.id
            ),
            source_statement_id=statement.id,
            role="supports",
            reason="automatic",
            scope={},
            available_at=datetime(2026, 8, 12, tzinfo=UTC),
            creator_type="ai",
            review_state="automatically_admitted",
            automatic_admission_decision_id=decision.id,
            created_at=datetime(2026, 8, 12, tzinfo=UTC),
        )
        session.add(link)
        session.flush()
        return decision, link, statement

    _admitted, good_link, good_statement = add_chain()
    add_chain(mismatch_kind=mismatch)

    assert repo.admitted_evidence(target.id) == (
        AdmittedEvidenceRef(
            evidence_link_id=good_link.id,
            source_statement_id=good_statement.id,
            document_version_id=document.id,
        ),
    )


def test_transition_state_and_event_share_caller_owned_transaction(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'transaction.sqlite3'}", future=True
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    now = datetime(2026, 8, 12, 8, tzinfo=UTC)
    with sessions() as seed:
        case = ResearchCase(
            title="transaction",
            industry_topic="test",
            created_by="test",
            created_at=now,
        )
        seed.add(case)
        seed.flush()
        thesis = Thesis(
            research_case_id=case.id,
            statement="transaction thesis",
            created_by="test",
            created_at=now,
        )
        seed.add(thesis)
        seed.flush()
        job = _create_job(AcquisitionRepository(seed, clock=lambda: now), case, thesis)
        seed.commit()

    writer = sessions()
    try:
        repo = AcquisitionRepository(writer, clock=lambda: now)
        first = repo.claim_next(
            worker_id="worker-a", lease_for=timedelta(seconds=30)
        )
        assert first is not None
        writer.rollback()

        with sessions() as reader:
            rolled_back = reader.get(AcquisitionJob, job.id)
            assert rolled_back is not None
            assert (rolled_back.status, rolled_back.attempt) == ("queued", 0)
            assert len(AcquisitionRepository(reader).events(job.id)) == 1

        second = repo.claim_next(
            worker_id="worker-b", lease_for=timedelta(seconds=30)
        )
        assert second is not None
        writer.commit()

        with sessions() as reader:
            committed = reader.get(AcquisitionJob, job.id)
            assert committed is not None
            assert (committed.status, committed.attempt) == ("running", 1)
            assert [
                event.seq
                for event in AcquisitionRepository(reader).events(job.id)
            ] == [1, 2]
    finally:
        writer.close()
        engine.dispose()


def test_repository_documents_caller_owned_transaction_boundary():
    docs = AcquisitionRepository.__doc__ or ""
    assert "caller" in docs.casefold()
    assert "commit" in docs.casefold()
    assert "external work" in docs.casefold()


def test_postgresql_claim_statement_uses_skip_locked(repo):
    statement = repo._claim_statement(datetime(2026, 8, 12, tzinfo=UTC))
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY acquisition_jobs.created_at, acquisition_jobs.id" in sql
