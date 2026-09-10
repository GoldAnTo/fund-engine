from __future__ import annotations

import hashlib
import importlib
import traceback
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.acquisition.policy import B_SCOPE_POLICY, SourcePolicy
from app.acquisition.sources import (
    RetrievedEnvelope,
    RetrievedSearchResult,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
)
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    AcquisitionJobEvent,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import DocumentVersion, EvidenceLink, SourceStatement
from app.models.ledger import AIRun, AtomicClaimCandidate
from app.repositories.acquisition import AcquisitionRepository, StaleLeaseError
from app.services.acquisition_runner import AcquisitionRunner
from app.services.atomic_claims import AtomicClaimService
from app.services.automatic_admission import (
    B_SCOPE_GATE_VERSION,
    AdmissionContext,
    AutomaticAdmissionGate,
)
from app.services.retrieved_documents import RetrievedDocumentFreezer
from tests.test_acquisition_runner import (
    CUTOFF,
    FakeExtractionClient,
    FakeSSEAdapter,
    MutableClock,
    SSE_ONLY_POLICY,
    make_request,
    make_session_factory,
)
from tests.tenant_admission import admit_case
from app.domain.acquisition import AcquisitionPrincipal
from app.services.acquisition import AcquisitionModule


class InjectedWorkerCrash(BaseException):
    pass


def test_run_once_survives_stale_lease_and_keeps_polling(monkeypatch):
    """Losing a lease must never kill the worker process.

    Regression: the acquisition workers crash-looped for a whole night
    (attempt hit 87) because ``StaleLeaseError`` escaped ``run_once``; the
    container restarted, re-claimed, and lost the lease again.
    """
    import uuid
    from datetime import UTC, datetime

    from app.repositories.acquisition import AcquisitionClaim

    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    claim = AcquisitionClaim(
        job_id=uuid.uuid4(),
        lease_token="stale-token",
        lease_owner="system:acquisition-worker@t#worker-a",
        lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        attempt=87,
    )

    class FakeRepository:
        def __init__(self, session):
            pass

        def claim_next(self, *, worker_id, lease_for):
            return claim

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def commit(self):
            return None

    class LeaseLosingRunner:
        def run_claim(self, claim):
            raise StaleLeaseError("acquisition lease is stale")

    monkeypatch.setattr(worker, "AcquisitionRepository", FakeRepository)

    # Must return True (work was found) WITHOUT raising: the job stays
    # durable and a fresh claim resumes it from its checkpoints.
    assert (
        worker.run_once(
            runner=LeaseLosingRunner(),
            session_factory=lambda: FakeSession(),
            worker_id="system:acquisition-worker@t#worker-a",
            lease_for=timedelta(seconds=30),
        )
        is True
    )


def _enqueue_worker_job(session, research_case, thesis, *, key):
    job = AcquisitionModule(session, policy=SSE_ONLY_POLICY).request(
        make_request(research_case, thesis, idempotency_key=key),
        principal=AcquisitionPrincipal("team-a", "system:task8-requester"),
    )
    session.commit()
    return job.id


def _freeze_worker_clock(monkeypatch, worker, clock):
    class ClockedRepository(AcquisitionRepository):
        def __init__(self, session):
            super().__init__(session, clock=clock)

    class ClockedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock().astimezone(tz)

    monkeypatch.setattr(worker, "AcquisitionRepository", ClockedRepository)
    monkeypatch.setattr(worker, "datetime", ClockedDatetime, raising=False)


@pytest.mark.parametrize("stage", ["searching", "extracting", "admitting"])
def test_run_once_bounds_unexpected_job_failures_without_stopping_other_work(
    session, research_case, thesis, document, monkeypatch, capsys, stage
):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    clock = MutableClock()
    _freeze_worker_clock(monkeypatch, worker, clock)
    sessions = make_session_factory(session)
    admit_case(session, research_case.id, tenant_id="team-a", document_version_id=document.id)
    broken_id = _enqueue_worker_job(
        session, research_case, thesis, key=f"broken-worker-{stage}"
    )
    healthy_id = _enqueue_worker_job(
        session, research_case, thesis, key=f"healthy-worker-{stage}"
    )
    marker = "private-source-and-provider-credential-marker"

    class OneBrokenJobRunner(AcquisitionRunner):
        def run_claim(self, claim):
            if claim.job_id == broken_id:
                self._advance(claim, stage)
                raise RuntimeError(marker)
            super().run_claim(claim)

    common = {
        "runner": OneBrokenJobRunner(
            sessions,
            adapters={"sse": FakeSSEAdapter()},
            llm_client=FakeExtractionClient(),
            clock=clock,
        ),
        "session_factory": sessions,
        "worker_id": "system:acquisition-worker@test#bounded-recovery",
        "lease_for": timedelta(minutes=5),
    }

    assert worker.run_once(**common) is True
    with sessions() as check:
        broken = check.get(AcquisitionJob, broken_id)
        assert (broken.status, broken.stage, broken.attempt) == ("retry_wait", stage, 1)
        assert broken.retry_at.replace(tzinfo=UTC) > clock()
        assert broken.lease_token is None
        assert broken.exception_count == 1
        assert broken.error_code == "acquisition_execution_failed"

    # A delayed failed job must not starve another queued job in the same loop.
    assert worker.run_once(**common) is True
    with sessions() as check:
        assert check.get(AcquisitionJob, healthy_id).status == "succeeded"
    assert worker.run_once(**common) is False

    for attempt in (2, 3):
        with sessions() as check:
            clock.now = check.get(AcquisitionJob, broken_id).retry_at.replace(tzinfo=UTC)
        assert worker.run_once(**common) is True
        with sessions() as check:
            broken = check.get(AcquisitionJob, broken_id)
            assert broken.attempt == attempt
            assert broken.exception_count == attempt
            assert broken.lease_token is None
            if attempt < 3:
                assert (broken.status, broken.stage) == ("retry_wait", stage)
                assert broken.retry_at.replace(tzinfo=UTC) > clock()
            else:
                assert (broken.status, broken.stage) == ("failed", "failed")
                assert broken.retry_at is None
                assert broken.finished_at is not None
    assert worker.run_once(**common) is False

    with sessions() as check:
        exceptions = check.scalars(select(AcquisitionException).where(
            AcquisitionException.job_id == broken_id
        )).all()
        events = check.scalars(select(AcquisitionJobEvent).where(
            AcquisitionJobEvent.job_id == broken_id
        )).all()
        assert len(exceptions) == 3
        assert {row.reason_code for row in exceptions} == {"acquisition_execution_failed"}
        assert {row.detail_json["claim_attempt"] for row in exceptions} == {1, 2, 3}
        assert all(set(row.detail_json) == {"claim_attempt"} for row in exceptions)
        assert any(row.status == "retry_wait" for row in events)
        assert any(row.status == "failed" for row in events)
        assert marker not in repr([row.__dict__ for row in (*exceptions, *events)])
        assert check.get(AcquisitionJob, broken_id).error_detail is None
    output = capsys.readouterr()
    assert marker not in output.out + output.err
    assert "retry_wait" in output.err
    assert "failed" in output.err


def test_run_once_does_not_overwrite_new_owner_after_unexpected_failure(
    session, research_case, thesis, document, monkeypatch, capsys
):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    clock = MutableClock()
    _freeze_worker_clock(monkeypatch, worker, clock)
    sessions = make_session_factory(session)
    admit_case(session, research_case.id, tenant_id="team-a", document_version_id=document.id)
    job_id = _enqueue_worker_job(
        session, research_case, thesis, key="rotated-failed-worker"
    )
    new_owner = "system:acquisition-worker@test#new-owner"
    marker = "private-failed-request-marker"

    class ReclaimedRunner:
        def run_claim(self, claim):
            clock.advance(timedelta(minutes=6))
            with sessions() as recovery:
                replacement = AcquisitionRepository(recovery, clock=clock).claim_next(
                    worker_id=new_owner, lease_for=timedelta(minutes=5)
                )
                assert replacement is not None
                assert replacement.job_id == claim.job_id
                recovery.commit()
            raise RuntimeError(marker)

    assert worker.run_once(
        runner=ReclaimedRunner(),
        session_factory=sessions,
        worker_id="system:acquisition-worker@test#old-owner",
        lease_for=timedelta(minutes=5),
    ) is True
    with sessions() as check:
        job = check.get(AcquisitionJob, job_id)
        assert (job.status, job.stage, job.attempt) == ("running", "searching", 2)
        assert job.lease_owner == new_owner
        assert job.exception_count == 0
        assert job.error_code is None
        assert check.scalar(select(func.count()).select_from(AcquisitionException)) == 0
    output = capsys.readouterr()
    assert marker not in output.out + output.err


def test_run_once_reports_safe_failure_when_recovery_cannot_be_persisted(
    session, research_case, thesis, document, monkeypatch
):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    clock = MutableClock()
    _freeze_worker_clock(monkeypatch, worker, clock)
    sessions = make_session_factory(session)
    admit_case(session, research_case.id, tenant_id="team-a", document_version_id=document.id)
    job_id = _enqueue_worker_job(session, research_case, thesis, key="recovery-write-failure")
    runner_marker = "private-provider-execution-marker"
    recovery_marker = "private-recovery-sql-marker"

    class FailedRecoveryRepository(worker.AcquisitionRepository):
        def record_exception(self, *args, **kwargs):
            super().record_exception(*args, **kwargs)
            raise RuntimeError(recovery_marker)

    class FailingRunner:
        def run_claim(self, claim):
            raise RuntimeError(runner_marker)

    monkeypatch.setattr(worker, "AcquisitionRepository", FailedRecoveryRepository)
    with pytest.raises(RuntimeError, match="acquisition failure recovery could not be persisted") as failure:
        worker.run_once(
            runner=FailingRunner(),
            session_factory=sessions,
            worker_id="system:acquisition-worker@test#failed-recovery",
            lease_for=timedelta(minutes=5),
        )
    reported = "".join(traceback.format_exception(failure.value))
    assert runner_marker not in reported
    assert recovery_marker not in reported
    with sessions() as check:
        job = check.get(AcquisitionJob, job_id)
        assert (job.status, job.stage, job.exception_count) == ("running", "searching", 0)
        assert check.scalar(select(func.count()).select_from(AcquisitionException)) == 0


class CrashAfterArtifactFreezer(RetrievedDocumentFreezer):
    def _commit_artifact(self, **kwargs):
        super()._commit_artifact(**kwargs)
        raise InjectedWorkerCrash


class CrashBeforeArtifactCheckpointFreezer(RetrievedDocumentFreezer):
    def freeze(self, reference, envelope, request_context):
        raise InjectedWorkerCrash


class CrashDuringFetchAdapter(FakeSSEAdapter):
    def fetch(self, reference):
        self.fetch_calls += 1
        raise InjectedWorkerCrash


GILDATA_ONLY_POLICY = SourcePolicy(
    version=B_SCOPE_POLICY.version,
    enabled_adapter_keys=frozenset({"gildata"}),
    allowed_source_roles=B_SCOPE_POLICY.allowed_source_roles,
    exact_hosts=B_SCOPE_POLICY.exact_hosts,
    suffix_hosts=B_SCOPE_POLICY.suffix_hosts,
    max_response_bytes=B_SCOPE_POLICY.max_response_bytes,
    per_adapter_page_limit=B_SCOPE_POLICY.per_adapter_page_limit,
    permission_declarations=(("gildata", "licensed-provider-contract"),),
)


class FakeGildataInlineAdapter(SourceAdapter):
    descriptor = SourceDescriptor(
        adapter_key="gildata",
        provider_identity="Gildata",
        allowed_schemes=frozenset({"gildata"}),
        allowed_hosts=frozenset({"research-report"}),
        allowed_source_roles=frozenset({"licensed_provider"}),
    )

    def __init__(self, *, search_enabled: bool = True) -> None:
        self.search_enabled = search_enabled
        self.search_calls = 0
        self.fetch_calls = 0
        self.restore_calls = 0

    def search(self, query, cutoff):
        self.search_calls += 1
        if not self.search_enabled:
            raise AssertionError("recovery must not call Gildata search")
        reference = SourceReferenceValue(
            adapter_key="gildata",
            external_record_id="report:600001:2026-08-13:task8",
            external_version="published:2026-08-13",
            canonical_url=(
                "gildata://research-report/"
                "report:600001:2026-08-13:task8"
            ),
            title="Example Corp annual report",
            published_at=CUTOFF - timedelta(hours=1),
            source_role="licensed_provider",
            fetch_locator={"record_id": "report:600001:2026-08-13:task8"},
            metadata={
                "source_type": "research_report",
                "security_code": "600001",
                "publisher": "Task8 Research",
            },
        )
        return (
            RetrievedSearchResult(
                reference=reference,
                envelope=RetrievedEnvelope(
                    content=b"Example Corp 2026-08-12 Revenue was 100 USD.",
                    mime_type="text/plain; charset=utf-8",
                    final_url=reference.canonical_url,
                    etag=None,
                    last_modified=None,
                    provider_request_id=None,
                    metadata={
                        "adapter_key": "gildata",
                        "external_record_id": reference.external_record_id,
                        "provider_identity": "Task8 Research",
                    },
                ),
            ),
        )

    def restore_reference(self, reference):
        self.restore_calls += 1
        raise AssertionError("inline Gildata artifact must not need restore")

    def fetch(self, reference):
        self.fetch_calls += 1
        raise AssertionError("inline Gildata artifact must not need fetch")

    def close(self):
        return None


class CrashBeforeFetchRunner(AcquisitionRunner):
    def _fetch(self, claim, request):
        raise InjectedWorkerCrash


@pytest.mark.parametrize(
    ("request_version", "policy_version"),
    [
        pytest.param("b-scope-v1", "b-scope-v1", id="obsolete-v1"),
        pytest.param(B_SCOPE_POLICY.version, "b-scope-v1", id="mismatched"),
    ],
)
def test_obsolete_or_mismatched_policy_claim_fails_closed_before_adapter_work(
    session,
    research_case,
    thesis,
    document,
    request_version,
    policy_version,
):
    marker = "raw-obsolete-policy-snapshot-marker"
    clock = MutableClock()
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    job_ref = AcquisitionModule(session, policy=SSE_ONLY_POLICY).request(
        make_request(
            research_case,
            thesis,
            idempotency_key=f"task8-policy-rollover-{request_version}-{policy_version}",
        ),
        principal=principal,
    )
    job = session.get(AcquisitionJob, job_ref.id)
    assert job is not None
    job.request_snapshot = {
        **job.request_snapshot,
        "source_policy_version": request_version,
        "raw_marker": marker,
    }
    job.policy_snapshot = {
        **job.policy_snapshot,
        "version": policy_version,
        "raw_marker": marker,
    }
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#policy-rollover",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()
    adapter = FakeSSEAdapter()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job_ref.id)
    exception = session.scalar(
        select(AcquisitionException).where(
            AcquisitionException.job_id == job_ref.id
        )
    )
    assert persisted is not None
    assert (persisted.status, persisted.stage) == ("failed", "failed")
    assert persisted.error_code == "unsupported_source_policy_version"
    assert persisted.exception_count == 1
    assert exception is not None
    assert exception.reason_code == "unsupported_source_policy_version"
    assert exception.detail_json == {
        "active_policy_version": B_SCOPE_POLICY.version
    }
    assert marker not in repr(exception.detail_json)
    assert (adapter.search_calls, adapter.restore_calls, adapter.fetch_calls) == (
        0,
        0,
        0,
    )


def test_gildata_inline_artifact_recovers_without_provider_research(
    session, research_case, thesis, document
):
    clock = MutableClock()
    sessions = make_session_factory(session)
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    request = replace(
        make_request(
            research_case,
            thesis,
            idempotency_key="task8-gildata-inline-recovery",
        ),
        allowed_source_roles=frozenset({"licensed_provider"}),
    )
    job = AcquisitionModule(session, policy=GILDATA_ONLY_POLICY).request(
        request, principal=principal
    )
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim_a = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim_a is not None
    session.commit()
    original = FakeGildataInlineAdapter()

    with pytest.raises(InjectedWorkerCrash):
        CrashBeforeFetchRunner(
            sessions,
            adapters={"gildata": original},
            llm_client=FakeExtractionClient(),
            clock=clock,
        ).run_claim(claim_a)

    assert original.search_calls == 1
    assert original.fetch_calls == 0
    assert session.scalar(select(func.count()).select_from(SourceReference)) == 1
    assert session.scalar(select(func.count()).select_from(RetrievalArtifact)) == 1
    reference = session.scalar(select(SourceReference))
    assert reference is not None
    assert "Revenue was 100 USD" not in repr(reference.metadata_json)
    # Worker A's _renew calls pushed the lease past the original 30s window,
    # so a takeover must outlive the renewed lease (default 1800s).
    clock.advance(timedelta(seconds=1801))
    claim_b = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-b",
        lease_for=timedelta(minutes=5),
    )
    assert claim_b is not None
    session.commit()
    recovered = FakeGildataInlineAdapter(search_enabled=False)

    AcquisitionRunner(
        sessions,
        adapters={"gildata": recovered},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim_b)

    assert recovered.search_calls == 0
    assert recovered.restore_calls == 0
    assert recovered.fetch_calls == 0
    assert session.get(AcquisitionJob, job.id).status in {"succeeded", "partial"}
    assert session.scalar(select(func.count()).select_from(RetrievalArtifactDocument)) == 1


def test_persisted_official_reference_recovers_without_live_search(
    session, research_case, thesis, document
):
    clock = MutableClock()
    sessions = make_session_factory(session)
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    AcquisitionModule(session, policy=SSE_ONLY_POLICY).request(
        make_request(
            research_case,
            thesis,
            idempotency_key="task8-reference-restore",
        ),
        principal=principal,
    )
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim_a = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim_a is not None
    session.commit()

    with pytest.raises(InjectedWorkerCrash):
        AcquisitionRunner(
            sessions,
            adapters={"sse": CrashDuringFetchAdapter()},
            llm_client=FakeExtractionClient(),
            clock=clock,
        ).run_claim(claim_a)

    assert session.scalar(select(func.count()).select_from(SourceReference)) == 1
    assert session.scalar(select(func.count()).select_from(RetrievalArtifact)) == 0
    # Worker A's _renew calls pushed the lease past the original 30s window,
    # so a takeover must outlive the renewed lease (default 1800s).
    clock.advance(timedelta(seconds=1801))
    claim_b = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-b",
        lease_for=timedelta(minutes=5),
    )
    assert claim_b is not None
    session.commit()
    recovered = FakeSSEAdapter()

    AcquisitionRunner(
        sessions,
        adapters={"sse": recovered},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim_b)

    assert recovered.search_calls == 0
    assert recovered.restore_calls == 1
    assert recovered.fetch_calls == 1
    assert session.scalar(select(func.count()).select_from(RetrievalArtifact)) == 1


def test_fetch_success_checkpoint_never_commits_attempt_without_artifact(
    session, research_case, thesis, document
):
    clock = MutableClock()
    sessions = make_session_factory(session)
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(
        make_request(
            research_case,
            thesis,
            idempotency_key="task8-atomic-fetch-checkpoint",
        ),
        principal=principal,
    )
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()

    with pytest.raises(InjectedWorkerCrash):
        AcquisitionRunner(
            sessions,
            adapters={"sse": FakeSSEAdapter()},
            llm_client=FakeExtractionClient(),
            freezer=CrashBeforeArtifactCheckpointFreezer(sessions, clock=clock),
            clock=clock,
        ).run_claim(claim)

    session.expire_all()
    succeeded_fetches_without_artifacts = session.scalar(
        select(func.count())
        .select_from(AcquisitionAttempt)
        .outerjoin(
            RetrievalArtifact,
            RetrievalArtifact.attempt_id == AcquisitionAttempt.id,
        )
        .where(
            AcquisitionAttempt.job_id == job.id,
            AcquisitionAttempt.operation == "fetch",
            AcquisitionAttempt.outcome == "succeeded",
            RetrievalArtifact.id.is_(None),
        )
    )
    assert succeeded_fetches_without_artifacts == 0


def test_runner_fails_closed_on_succeeded_fetch_without_artifact(
    session, research_case, thesis, document
):
    clock = MutableClock()
    sessions = make_session_factory(session)
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(
        make_request(
            research_case,
            thesis,
            idempotency_key="task8-invalid-fetch-checkpoint",
        ),
        principal=principal,
    )
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()
    reference = repository.create_or_get_reference(
        job.id,
        lease_token=claim.lease_token,
        adapter_key="sse",
        external_record_id="task8-invalid-fetch",
        external_version="v1",
        canonical_url="https://www.sse.com.cn/disclosure/task8-invalid.txt",
        title="Invalid fetch checkpoint fixture",
        published_at=clock.now,
        source_role="company_disclosure",
        metadata_json={"provider_identity": "Shanghai Stock Exchange"},
    )
    session.commit()
    repository.advance(
        job.id,
        lease_token=claim.lease_token,
        stage="fetching",
    )
    session.commit()
    repository.record_attempt(
        job.id,
        lease_token=claim.lease_token,
        adapter_key="sse",
        operation="fetch",
        started_at=clock.now,
        finished_at=clock.now,
        outcome="succeeded",
        retryable=False,
        safe_metadata={
            "source_reference_id": str(reference.id),
            "claim_attempt": claim.attempt,
        },
    )
    session.commit()
    adapter = FakeSSEAdapter()

    AcquisitionRunner(
        sessions,
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert (persisted.status, persisted.stage) == ("failed", "failed")
    exception = session.scalar(
        select(AcquisitionException).where(
            AcquisitionException.job_id == job.id,
            AcquisitionException.reason_code == "invalid_fetch_checkpoint",
        )
    )
    assert exception is not None
    assert exception.source_reference_id == reference.id
    assert (adapter.search_calls, adapter.fetch_calls) == (0, 0)


def test_extractor_internal_commit_cannot_publish_after_lease_rotation(
    session, research_case, thesis, document, document_service
):
    clock = MutableClock()
    sessions = make_session_factory(session)
    table_text = (
        "主要会计数据 单位：千元\n"
        "指标 2025年 2024年\n"
        "营业收入 50,000,000 40,000,000\n"
    )
    document_service.add_span(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text=table_text,
    )
    document_service.add_span(
        document_version_id=document.id,
        locator={"page": 2},
        verbatim_text="管理层表示订单能见度良好",
    )
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(
        make_request(
            research_case,
            thesis,
            idempotency_key="task8-stale-extraction-commit",
        ),
        principal=principal,
    )
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim_a = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim_a is not None
    session.commit()

    reference = repository.create_or_get_reference(
        job.id,
        lease_token=claim_a.lease_token,
        adapter_key="sse",
        external_record_id="task8-extraction-source",
        external_version="v1",
        canonical_url="https://www.sse.com.cn/disclosure/task8-extraction.txt",
        title="Task8 extraction fixture",
        published_at=clock.now,
        source_role="company_disclosure",
        metadata_json={
            "provider_identity": "Shanghai Stock Exchange",
            "security_code": "600001",
            "source_publication": "2026-08-13",
        },
    )
    session.commit()
    repository.advance(
        job.id,
        lease_token=claim_a.lease_token,
        stage="fetching",
    )
    session.commit()
    attempt = repository.record_attempt(
        job.id,
        lease_token=claim_a.lease_token,
        adapter_key="sse",
        operation="fetch",
        started_at=clock.now,
        finished_at=clock.now,
        outcome="succeeded",
        retryable=False,
        safe_metadata={"source_reference_id": str(reference.id)},
    )
    raw_bytes = b"durable extraction fixture"
    artifact = RetrievalArtifact(
        source_reference_id=reference.id,
        attempt_id=attempt.id,
        content_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        raw_bytes=raw_bytes,
        mime_type="text/plain; charset=utf-8",
        byte_size=len(raw_bytes),
        final_url="https://static.sse.com.cn/disclosure/task8-extraction.txt",
        etag=None,
        last_modified=None,
        provider_request_id="task8-extraction-request",
        retrieved_at=clock.now,
    )
    session.add(artifact)
    session.flush()
    session.add(
        RetrievalArtifactDocument(
            retrieval_artifact_id=artifact.id,
            document_version_id=document.id,
            relation="created",
            publication_key="a" * 64,
            created_at=clock.now,
        )
    )
    session.commit()
    repository.advance(
        job.id,
        lease_token=claim_a.lease_token,
        stage="freezing",
    )
    session.commit()
    repository.advance(
        job.id,
        lease_token=claim_a.lease_token,
        stage="extracting",
    )
    session.commit()

    class LeaseRotatingExtractionClient:
        model_version = "fake-task8-lease-rotation-v1"

        def __init__(self) -> None:
            self.claim_b = None

        def chat_json(self, messages, schema_hint=""):
            assert schema_hint == "extract"
            # The runner renews the lease to its own budget (default 1800s)
            # before each document, so the rotation must outlive the RENEWED
            # lease — not the original 30s claim — to take the job over.
            clock.advance(timedelta(seconds=1831))
            with sessions() as takeover:
                self.claim_b = AcquisitionRepository(
                    takeover, clock=clock
                ).claim_next(
                    worker_id="system:acquisition-worker@task8-v1#worker-b",
                    lease_for=timedelta(minutes=5),
                )
                assert self.claim_b is not None
                takeover.commit()
            return {"statements": []}

    client = LeaseRotatingExtractionClient()
    with pytest.raises(StaleLeaseError):
        AcquisitionRunner(
            sessions,
            adapters={"sse": FakeSSEAdapter()},
            llm_client=client,
            clock=clock,
        ).run_claim(claim_a)

    assert client.claim_b is not None
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(AtomicClaimCandidate)) == 0
    assert session.scalar(select(func.count()).select_from(AIRun)) == 0
    assert (
        session.scalar(select(func.count()).select_from(AutomaticAdmissionDecision))
        == 0
    )

    class RecoveryExtractionClient:
        model_version = "fake-task8-recovery-extraction-v1"

        def chat_json(self, messages, schema_hint=""):
            assert schema_hint == "extract"
            return {"statements": []}

    AcquisitionRunner(
        sessions,
        adapters={"sse": FakeSSEAdapter()},
        llm_client=RecoveryExtractionClient(),
        clock=clock,
    ).run_claim(client.claim_b)

    session.expire_all()
    assert session.scalar(select(func.count()).select_from(AtomicClaimCandidate)) == 2
    assert session.scalar(
        select(func.count()).select_from(AIRun).where(AIRun.status == "success")
    ) == 1


def test_artifact_commit_survives_crash_and_stale_worker_is_fenced(
    session, research_case, thesis, document
):
    clock = MutableClock()
    sessions = make_session_factory(session)
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal(
        tenant_id="team-a", actor="system:task8-requester"
    )
    request = make_request(research_case, thesis, idempotency_key="task8-recovery")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    first = module.request(request, principal=principal)
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim_a = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim_a is not None
    session.commit()
    adapter_a = FakeSSEAdapter()
    crashing = AcquisitionRunner(
        sessions,
        adapters={"sse": adapter_a},
        llm_client=FakeExtractionClient(),
        freezer=CrashAfterArtifactFreezer(sessions, clock=clock),
        clock=clock,
    )

    with pytest.raises(InjectedWorkerCrash):
        crashing.run_claim(claim_a)

    session.expire_all()
    assert session.scalar(select(func.count()).select_from(RetrievalArtifact)) == 1
    assert session.scalar(select(func.count()).select_from(RetrievalArtifactDocument)) == 0
    # Worker A's _renew calls pushed the lease past the original 30s window,
    # so a takeover must outlive the renewed lease (default 1800s).
    clock.advance(timedelta(seconds=1801))
    claim_b = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-b",
        lease_for=timedelta(minutes=5),
    )
    assert claim_b is not None
    assert claim_b.attempt == 2
    session.commit()

    with pytest.raises(StaleLeaseError):
        repository.advance(
            first.id,
            lease_token=claim_a.lease_token,
            stage="failed",
            status="failed",
        )

    adapter_b = FakeSSEAdapter()
    AcquisitionRunner(
        sessions,
        adapters={"sse": adapter_b},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim_b)

    session.expire_all()
    decision = session.scalar(select(AutomaticAdmissionDecision))
    assert decision is not None
    with pytest.raises(StaleLeaseError):
        AutomaticAdmissionGate(session, clock=clock).evaluate(
            decision.candidate_id,
            AdmissionContext(
                job_id=first.id,
                retrieval_artifact_id=decision.retrieval_artifact_id,
                thesis_id=thesis.id,
                cutoff=CUTOFF,
                objective="support",
                target_link_role="supports",
                gate_version=B_SCOPE_GATE_VERSION,
                policy_version=B_SCOPE_POLICY.version,
                allowed_source_roles=frozenset({"company_disclosure"}),
                metric_terms=("Revenue",),
                expected_subject="Example Corp",
                lease_token=claim_a.lease_token,
            ),
        )
    session.rollback()
    with pytest.raises(StaleLeaseError):
        AtomicClaimService(session, clock=clock).publish_automatically(
            decision.candidate_id,
            decision.id,
            lease_token=claim_a.lease_token,
        )
    session.rollback()

    assert session.scalar(select(func.count()).select_from(RetrievalArtifactDocument)) == 1
    assert session.scalar(
        select(func.count(DocumentVersion.id))
        .join(
            RetrievalArtifactDocument,
            RetrievalArtifactDocument.document_version_id == DocumentVersion.id,
        )
    ) == 1
    assert session.scalar(select(func.count()).select_from(SourceStatement)) == 1
    assert session.scalar(select(func.count()).select_from(EvidenceLink)) == 1
    assert session.scalar(select(func.count()).select_from(AcquisitionAttempt)) == 2
    events = tuple(
        session.scalars(
            select(AcquisitionJobEvent)
            .where(AcquisitionJobEvent.job_id == first.id)
            .order_by(AcquisitionJobEvent.seq)
        )
    )
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert (adapter_b.search_calls, adapter_b.fetch_calls) == (0, 0)

    same = AcquisitionModule(session, policy=SSE_ONLY_POLICY).request(
        request, principal=principal
    )
    assert same.id == first.id


def test_repository_runner_units_are_lease_fenced_and_caller_transactional(
    session, research_case, thesis, document
):
    clock = MutableClock()
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    job = AcquisitionModule(session, policy=SSE_ONLY_POLICY).request(
        make_request(research_case, thesis), principal=principal
    )
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim_a = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim_a is not None
    session.commit()
    # Worker A's _renew calls pushed the lease past the original 30s window,
    # so a takeover must outlive the renewed lease (default 1800s).
    clock.advance(timedelta(seconds=1801))
    claim_b = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-b",
        lease_for=timedelta(minutes=5),
    )
    assert claim_b is not None
    session.commit()

    stale_calls = (
        lambda: repository.record_attempt(
            job.id,
            lease_token=claim_a.lease_token,
            adapter_key="sse",
            operation="search",
            started_at=clock.now,
            finished_at=clock.now,
            outcome="succeeded",
            retryable=False,
        ),
        lambda: repository.create_or_get_reference(
            job.id,
            lease_token=claim_a.lease_token,
            adapter_key="sse",
            external_record_id="stale-reference",
            external_version="v1",
            canonical_url="https://www.sse.com.cn/stale.pdf",
            title="stale",
            published_at=clock.now,
            source_role="company_disclosure",
            metadata_json={"provider_identity": "Shanghai Stock Exchange"},
        ),
        lambda: repository.record_exception(
            job.id,
            lease_token=claim_a.lease_token,
            reason_code="stale_exception",
            detail_json={"error_code": "stale"},
        ),
    )
    for call in stale_calls:
        with pytest.raises(StaleLeaseError):
            call()
        session.rollback()

    attempt = repository.record_attempt(
        job.id,
        lease_token=claim_b.lease_token,
        adapter_key="sse",
        operation="search",
        started_at=clock.now,
        finished_at=clock.now,
        outcome="succeeded",
        retryable=False,
    )
    assert attempt.id is not None
    session.rollback()
    assert session.scalar(select(func.count()).select_from(AcquisitionAttempt)) == 0

    reference = repository.create_or_get_reference(
        job.id,
        lease_token=claim_b.lease_token,
        adapter_key="sse",
        external_record_id="rollback-reference",
        external_version="v1",
        canonical_url="https://www.sse.com.cn/rollback.pdf",
        title="rollback",
        published_at=clock.now,
        source_role="company_disclosure",
        metadata_json={"provider_identity": "Shanghai Stock Exchange"},
    )
    assert reference.id is not None
    session.rollback()
    assert session.scalar(select(func.count()).select_from(SourceReference)) == 0

    exception = repository.record_exception(
        job.id,
        lease_token=claim_b.lease_token,
        reason_code="rollback_exception",
        detail_json={"error_code": "rollback"},
    )
    assert exception.id is not None
    session.rollback()
    assert session.scalar(select(func.count()).select_from(AcquisitionException)) == 0


def test_reclaimed_fetch_restores_reference_without_repeating_search(
    session, research_case, thesis, document
):
    clock = MutableClock()
    sessions = make_session_factory(session)
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(
        make_request(research_case, thesis, idempotency_key="task8-ref-recovery"),
        principal=principal,
    )
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim_a = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim_a is not None
    session.commit()

    with pytest.raises(InjectedWorkerCrash):
        AcquisitionRunner(
            sessions,
            adapters={"sse": CrashDuringFetchAdapter()},
            llm_client=FakeExtractionClient(),
            clock=clock,
        ).run_claim(claim_a)

    # Worker A's _renew calls pushed the lease past the original 30s window,
    # so a takeover must outlive the renewed lease (default 1800s).
    clock.advance(timedelta(seconds=1801))
    claim_b = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-b",
        lease_for=timedelta(minutes=5),
    )
    assert claim_b is not None
    session.commit()
    recovered = FakeSSEAdapter()
    AcquisitionRunner(
        sessions,
        adapters={"sse": recovered},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim_b)

    session.expire_all()
    attempts = tuple(
        session.scalars(
            select(AcquisitionAttempt)
            .where(AcquisitionAttempt.job_id == job.id)
            .order_by(
                AcquisitionAttempt.operation, AcquisitionAttempt.attempt_no
            )
        )
    )
    assert [attempt.attempt_no for attempt in attempts if attempt.operation == "search"] == [1]
    assert [attempt.attempt_no for attempt in attempts if attempt.operation == "fetch"] == [1]
    assert (recovered.search_calls, recovered.restore_calls, recovered.fetch_calls) == (
        0,
        1,
        1,
    )
    assert session.scalar(select(func.count()).select_from(SourceReference)) == 1
    assert module.get(job.id, principal=principal).status == "succeeded"


class ClosableAdapter:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_configured_adapter_initialization_failure_closes_prior_adapters(
    monkeypatch,
):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    initialized = ClosableAdapter()
    monkeypatch.setenv("ACQUISITION_ENABLED_ADAPTERS", "sse,gildata")
    monkeypatch.setattr(worker, "SSEAnnouncementSource", lambda: initialized)

    def fail_gildata():
        raise RuntimeError("configured provider unavailable")

    monkeypatch.setattr(worker.GildataMCPClient, "from_env", fail_gildata)

    with pytest.raises(RuntimeError, match="gildata.*initialize"):
        worker.build_adapters_from_env()

    assert initialized.closed is True


def test_production_worker_rejects_a_mock_llm(monkeypatch):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LLM_API_KEY", "configured-for-test")
    monkeypatch.setattr(worker.LLMClient, "from_env", FakeExtractionClient)

    with pytest.raises(RuntimeError, match="real LLM"):
        worker.build_llm_client()


def test_production_worker_starts_without_optional_llm_and_fails_provider_calls_closed(
    monkeypatch,
):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    client = worker.build_llm_client()

    assert client.model_version == "unconfigured"
    with pytest.raises(worker.LLMProviderError, match="not configured"):
        client.chat_json([], "extract")


@pytest.mark.parametrize(
    "arguments",
    [
        ["--once", "--lease-seconds", "0"],
        ["--once", "--retry-seconds", "0"],
        ["--loop", "--poll-seconds", "0"],
        ["--once", "--lease-seconds", "nan"],
        ["--once", "--retry-seconds", "inf"],
        ["--once", "--loop"],
    ],
)
def test_worker_rejects_invalid_modes_and_timing(monkeypatch, arguments):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")

    with pytest.raises(SystemExit):
        worker.main(arguments)


def test_once_entry_builds_identity_runs_one_claim_and_closes_adapters(monkeypatch):
    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    adapter = FakeSSEAdapter()
    calls = []
    monkeypatch.setenv("ACQUISITION_WORKER_VERSION", "task8-v1")
    monkeypatch.setenv("ACQUISITION_WORKER_INSTANCE", "test-instance")
    monkeypatch.setattr(
        worker, "build_adapters_from_env", lambda: {"sse": adapter}
    )
    monkeypatch.setattr(worker, "build_llm_client", FakeExtractionClient)
    monkeypatch.setattr(worker, "run_once", lambda **kwargs: calls.append(kwargs) or False)
    monkeypatch.setattr(worker, "_touch", lambda **_kwargs: None)

    worker.main(
        [
            "--once",
            "--lease-seconds",
            "30",
            "--retry-seconds",
            "15",
            "--poll-seconds",
            "0.25",
        ]
    )

    assert len(calls) == 1
    assert calls[0]["worker_id"] == (
        "system:acquisition-worker@task8-v1#test-instance"
    )
    assert calls[0]["lease_for"] == timedelta(seconds=30)
    assert adapter.closed is True


def test_acquisition_worker_records_a_container_scoped_loop_heartbeat(tmp_path, monkeypatch):
    from app.models.ledger import Base
    from app.services.research_worker_heartbeat import WorkerHeartbeatService

    worker = importlib.import_module("app.scripts.run_acquisition_worker")
    engine = create_engine(f"sqlite:///{tmp_path / 'acquisition-heartbeat.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    monkeypatch.setattr(worker.socket, "gethostname", lambda: "acquisition-container")

    worker._touch(mode="loop", state="polling")

    with sessions() as session:
        status = WorkerHeartbeatService(session).status(
            worker_kind="acquisition", worker_id="acquisition-container"
        )

    assert status["status"] == "available"
    assert status["mode"] == "loop"
