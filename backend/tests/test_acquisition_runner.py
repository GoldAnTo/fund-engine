from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.acquisition.sources import (
    RejectedSearchItem,
    RetrievedEnvelope,
    SourceAdapter,
    SourceReferenceValue,
    SourceUnavailable,
)
from app.acquisition.policy import B_SCOPE_POLICY, SourcePolicy
from app.datasources.exchanges.http import ExchangeSourceDescriptor
from app.domain.acquisition import (
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
)
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    RetrievalArtifactDocument,
)
from app.models.ledger import AtomicClaimReview, EvidenceLink, SourceStatement
from app.repositories.acquisition import AcquisitionRepository, StaleLeaseError
from app.services.acquisition import AcquisitionModule
from app.services.acquisition_runner import AcquisitionRunner
from tests.tenant_admission import admit_case


NOW = datetime(2026, 8, 13, 8, tzinfo=UTC)
CUTOFF = datetime(2026, 8, 13, 16, tzinfo=UTC)
SSE_ONLY_POLICY = SourcePolicy(
    version=B_SCOPE_POLICY.version,
    enabled_adapter_keys=frozenset({"sse"}),
    allowed_source_roles=B_SCOPE_POLICY.allowed_source_roles,
    exact_hosts=B_SCOPE_POLICY.exact_hosts,
    suffix_hosts=B_SCOPE_POLICY.suffix_hosts,
    max_response_bytes=B_SCOPE_POLICY.max_response_bytes,
    per_adapter_page_limit=B_SCOPE_POLICY.per_adapter_page_limit,
    permission_declarations=(("sse", "official-public-disclosure"),),
)


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


class FakeExtractionClient:
    model_version = "fake-task8-extractor-v1"

    def chat_json(self, messages, schema_hint=""):
        assert schema_hint == "extract"
        payload = json.loads(messages[-1]["content"])
        span = next(
            item
            for item in payload["spans"]
            if "Revenue was 100 USD" in item["verbatim_text"]
        )
        quote = span["verbatim_text"]
        return {
            "statements": [
                {
                    "span_id": span["span_id"],
                    "kind": "disclosed_fact",
                    "quote": quote,
                    "quote_start": 0,
                    "quote_end": len(quote),
                    "normalized_text": quote,
                    "assertion_actor": "Example Corp",
                    "subject": "Example Corp",
                    "predicate": "Revenue",
                    "object_text": "100 USD",
                    "numeric_value": "100",
                    "unit": "USD",
                    "observed_period": "2026-08-12",
                    "scope": {"company": "Example Corp", "metric": "Revenue"},
                }
            ]
        }


class FakeSSEAdapter(SourceAdapter):
    descriptor = ExchangeSourceDescriptor(
        adapter_key="sse",
        provider_identity="Shanghai Stock Exchange",
        allowed_schemes=frozenset({"https"}),
        allowed_hosts=frozenset({"www.sse.com.cn", "static.sse.com.cn"}),
        allowed_source_roles=frozenset({"company_disclosure"}),
    )

    def __init__(self) -> None:
        self.search_calls = 0
        self.restore_calls = 0
        self.fetch_calls = 0
        self.closed = False

    def search(self, query: str, cutoff: datetime):
        self.search_calls += 1
        assert "Example Corp" in query
        assert cutoff == CUTOFF
        return (
            SourceReferenceValue(
                adapter_key="sse",
                external_record_id="task8-report-1",
                external_version="v1",
                canonical_url="https://www.sse.com.cn/disclosure/task8-report.txt",
                title="Example Corp annual report",
                published_at=NOW,
                source_role="company_disclosure",
                fetch_locator={
                    "canonical_url": (
                        "https://www.sse.com.cn/disclosure/task8-report.txt"
                    )
                },
                metadata={
                    "provider_identity": "Shanghai Stock Exchange",
                    "security_code": "600001",
                    "source_publication": "2026-08-13",
                },
            ),
        )

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        self.fetch_calls += 1
        assert reference.external_record_id == "task8-report-1"
        return RetrievedEnvelope(
            content=b"Example Corp 2026-08-12 Revenue was 100 USD.",
            mime_type="text/plain; charset=utf-8",
            final_url="https://static.sse.com.cn/disclosure/task8-report.txt",
            etag='"task8-v1"',
            last_modified="Thu, 13 Aug 2026 08:00:00 GMT",
            provider_request_id="task8-request-1",
            metadata={
                "adapter_key": "sse",
                "external_record_id": reference.external_record_id,
                "provider_identity": "Shanghai Stock Exchange",
            },
        )

    def restore_reference(self, reference: SourceReferenceValue) -> None:
        self.restore_calls += 1
        self.descriptor.validate_reference(reference)

    def close(self) -> None:
        self.closed = True


class PartialSSEAdapter(FakeSSEAdapter):
    def search(self, query: str, cutoff: datetime):
        accepted = super().search(query, cutoff)
        return accepted + (
            RejectedSearchItem(
                adapter_key="sse",
                reason="outside_cutoff",
                external_record_id="task8-rejected-1",
                title="Post-cutoff filing",
                published_at=CUTOFF + timedelta(seconds=1),
                metadata={"provider_identity": "Shanghai Stock Exchange"},
            ),
        )


class RetryableSearchAdapter(FakeSSEAdapter):
    def __init__(self, *, retry_after_seconds=None) -> None:
        super().__init__()
        self._retry_after_seconds = retry_after_seconds

    def search(self, query: str, cutoff: datetime):
        self.search_calls += 1
        diagnostics = {"provider_status": "temporarily_unavailable"}
        if self._retry_after_seconds is not None:
            diagnostics["retry_after_seconds"] = self._retry_after_seconds
        raise SourceUnavailable(
            "official source temporarily unavailable",
            retryable=True,
            diagnostics=diagnostics,
        )


class EmptySearchAdapter(FakeSSEAdapter):
    def search(self, query: str, cutoff: datetime):
        self.search_calls += 1
        return ()


class TrackingSessionFactory:
    def __init__(self, session) -> None:
        self._factory = make_session_factory(session)
        self.sessions = []

    def __call__(self):
        created = self._factory()
        self.sessions.append(created)
        return created

    def assert_no_transaction(self) -> None:
        assert all(not value.in_transaction() for value in self.sessions)


class TransactionAssertingAdapter(FakeSSEAdapter):
    def __init__(self, tracker: TrackingSessionFactory) -> None:
        super().__init__()
        self._tracker = tracker

    def search(self, query: str, cutoff: datetime):
        self._tracker.assert_no_transaction()
        return super().search(query, cutoff)

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        self._tracker.assert_no_transaction()
        return super().fetch(reference)


def make_session_factory(session):
    return sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )


def make_request(research_case, thesis, *, idempotency_key=None):
    return AcquisitionRequest(
        tenant_id="team-a",
        case_id=research_case.id,
        thesis_id=thesis.id,
        research_run_id=None,
        round=1,
        objective=EvidenceObjective.SUPPORT,
        target_link_role="supports",
        thesis_statement="Example Corp revenue is 100 USD",
        entity_names=("Example Corp",),
        security_codes=("600001",),
        metric_terms=("Revenue",),
        period_start="2026-01-01",
        period_end="2026-12-31",
        cutoff=CUTOFF,
        allowed_source_roles=frozenset({"company_disclosure"}),
        source_policy_version=B_SCOPE_POLICY.version,
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )


def enqueue_and_claim(session, research_case, thesis, clock, *, request=None):
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=session.scalar(
            select(RetrievalArtifactDocument.document_version_id).limit(1)
        ),
    )
    principal = AcquisitionPrincipal(
        tenant_id="team-a", actor="system:task8-requester"
    )
    request = request or make_request(research_case, thesis)
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(request, principal=principal)
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()
    return module, principal, request, job, claim


def test_runner_executes_real_freeze_extract_gate_and_publish_chain(
    session, research_case, thesis, document
):
    clock = MutableClock()
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal(
        tenant_id="team-a", actor="system:task8-requester"
    )
    request = make_request(research_case, thesis)
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(request, principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
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
    events = module.events(job.id, principal=principal)
    view = module.get(job.id, principal=principal)
    assert [event.stage for event in events] == [
        "queued",
        "searching",
        "fetching",
        "freezing",
        "extracting",
        "admitting",
        "succeeded",
    ]
    assert view.status == "succeeded"
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert persisted.policy_snapshot["enabled_adapter_keys"] == ["sse"]
    assert persisted.frozen_count == 1
    assert len(module.admitted_evidence(job.id, principal=principal)) == 1
    assert session.scalar(select(func.count()).select_from(SourceStatement)) == 1
    assert session.scalar(select(func.count()).select_from(EvidenceLink)) == 1
    assert session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0
    assert (adapter.search_calls, adapter.fetch_calls) == (1, 1)


def _run_with_adapter(session, research_case, thesis, document, adapter, clock):
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal(
        tenant_id="team-a", actor="system:task8-requester"
    )
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()
    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
        retry_delay=timedelta(seconds=45),
        jitter_source=lambda: 0.0,
    ).run_claim(claim)
    session.expire_all()
    return module, principal, job


def test_runner_completes_partial_when_one_search_item_is_rejected(
    session, research_case, thesis, document
):
    module, principal, job = _run_with_adapter(
        session,
        research_case,
        thesis,
        document,
        PartialSSEAdapter(),
        MutableClock(),
    )

    view = module.get(job.id, principal=principal)
    assert (view.status, view.stage) == ("partial", "partial")
    assert len(module.admitted_evidence(job.id, principal=principal)) == 1


def test_retryable_provider_failure_without_success_enters_retry_wait(
    session, research_case, thesis, document
):
    clock = MutableClock()
    module, principal, job = _run_with_adapter(
        session,
        research_case,
        thesis,
        document,
        RetryableSearchAdapter(),
        clock,
    )

    view = module.get(job.id, principal=principal)
    persisted = session.get(AcquisitionJob, job.id)
    assert (view.status, view.stage) == ("retry_wait", "searching")
    assert persisted is not None
    assert persisted.retry_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=45)
    assert persisted.lease_token is None


def test_provider_retry_after_is_a_lower_bound_for_retry_at(
    session, research_case, thesis, document
):
    clock = MutableClock()
    module, principal, job = _run_with_adapter(
        session,
        research_case,
        thesis,
        document,
        RetryableSearchAdapter(retry_after_seconds=120),
        clock,
    )

    persisted = session.get(AcquisitionJob, job.id)
    assert module.get(job.id, principal=principal).status == "retry_wait"
    assert persisted is not None
    assert persisted.retry_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=120)
    attempt = session.scalar(
        select(AcquisitionAttempt).where(AcquisitionAttempt.job_id == job.id)
    )
    assert attempt is not None
    assert attempt.safe_metadata["diagnostics"]["retry_after_seconds"] == 120


def test_retry_backoff_adds_injected_bounded_jitter(
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
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": RetryableSearchAdapter()},
        llm_client=FakeExtractionClient(),
        clock=clock,
        retry_delay=timedelta(seconds=100),
        max_retry_delay=timedelta(seconds=110),
        retry_jitter_ratio=0.2,
        jitter_source=lambda: 0.75,
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert persisted.retry_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=107.5)


@pytest.mark.parametrize(
    ("retry_after_seconds", "expected_seconds"),
    [
        (float("nan"), 45),
        (float("inf"), 45),
        (-1, 45),
        (True, 45),
        ("120", 45),
        (9999, 90),
    ],
)
def test_invalid_retry_after_is_ignored_and_oversized_value_is_clamped(
    session,
    research_case,
    thesis,
    document,
    retry_after_seconds,
    expected_seconds,
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
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={
            "sse": RetryableSearchAdapter(
                retry_after_seconds=retry_after_seconds
            )
        },
        llm_client=FakeExtractionClient(),
        clock=clock,
        retry_delay=timedelta(seconds=45),
        max_retry_delay=timedelta(seconds=90),
        jitter_source=lambda: 0.0,
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert persisted.retry_at.replace(tzinfo=UTC) == NOW + timedelta(
        seconds=expected_seconds
    )


def test_empty_nonretryable_search_fails_closed(
    session, research_case, thesis, document
):
    module, principal, job = _run_with_adapter(
        session,
        research_case,
        thesis,
        document,
        EmptySearchAdapter(),
        MutableClock(),
    )

    assert module.get(job.id, principal=principal).status == "failed"


def test_historical_retryable_failure_does_not_pollute_current_claim(
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
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)
    claim_a = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim_a is not None
    session.commit()
    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": RetryableSearchAdapter()},
        llm_client=FakeExtractionClient(),
        clock=clock,
        retry_delay=timedelta(seconds=45),
        jitter_source=lambda: 0.0,
    ).run_claim(claim_a)

    clock.advance(timedelta(seconds=45))
    claim_b = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-b",
        lease_for=timedelta(minutes=5),
    )
    assert claim_b is not None
    assert claim_b.attempt == 2
    session.commit()
    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": EmptySearchAdapter()},
        llm_client=FakeExtractionClient(),
        clock=clock,
        retry_delay=timedelta(seconds=45),
        jitter_source=lambda: 0.0,
    ).run_claim(claim_b)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert (persisted.status, persisted.stage, persisted.retry_at) == (
        "failed",
        "failed",
        None,
    )
    attempts = tuple(
        session.scalars(
            select(AcquisitionAttempt)
            .where(AcquisitionAttempt.job_id == job.id)
            .order_by(AcquisitionAttempt.attempt_no)
        )
    )
    assert [attempt.safe_metadata["claim_attempt"] for attempt in attempts] == [1, 2]


def test_retry_backoff_is_bounded_and_last_claim_attempt_fails(
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
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    repository = AcquisitionRepository(session, clock=clock)

    for claim_attempt, expected_delay in ((1, 45), (2, 90), (3, None)):
        claim = repository.claim_next(
            worker_id=(
                f"system:acquisition-worker@task8-v1#worker-{claim_attempt}"
            ),
            lease_for=timedelta(minutes=5),
        )
        assert claim is not None
        assert claim.attempt == claim_attempt
        session.commit()
        AcquisitionRunner(
            make_session_factory(session),
            adapters={"sse": RetryableSearchAdapter()},
            llm_client=FakeExtractionClient(),
            clock=clock,
            retry_delay=timedelta(seconds=45),
            max_retry_delay=timedelta(seconds=90),
            max_attempts=3,
            jitter_source=lambda: 0.0,
        ).run_claim(claim)
        session.expire_all()
        persisted = session.get(AcquisitionJob, job.id)
        assert persisted is not None
        if expected_delay is None:
            assert (persisted.status, persisted.stage, persisted.retry_at) == (
                "failed",
                "failed",
                None,
            )
        else:
            assert persisted.status == "retry_wait"
            assert persisted.retry_at.replace(tzinfo=UTC) == clock.now + timedelta(
                seconds=expected_delay
            )
            clock.advance(timedelta(seconds=expected_delay))

    attempts = tuple(
        session.scalars(
            select(AcquisitionAttempt)
            .where(AcquisitionAttempt.job_id == job.id)
            .order_by(AcquisitionAttempt.attempt_no)
        )
    )
    assert [attempt.safe_metadata["claim_attempt"] for attempt in attempts] == [
        1,
        2,
        3,
    ]


def test_external_search_and_fetch_run_without_a_database_transaction(
    session, research_case, thesis, document
):
    tracker = TrackingSessionFactory(session)
    adapter = TransactionAssertingAdapter(tracker)
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=MutableClock()).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()

    AcquisitionRunner(
        tracker,
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=MutableClock(),
    ).run_claim(claim)

    assert module.get(job.id, principal=principal).status == "succeeded"


def test_missing_policy_enabled_adapter_fails_before_external_work(
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
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim)

    session.expire_all()
    assert module.get(job.id, principal=principal).status == "failed"
    exception = session.scalar(
        select(AcquisitionException).where(AcquisitionException.job_id == job.id)
    )
    assert exception is not None
    assert exception.reason_code == "configured_adapter_unavailable"
    assert exception.detail_json == {"adapter_key": "sse"}


def _expire_claim_for_job(session_factory, job_id) -> None:
    """Rotate the live token so the holder's lease becomes stale immediately.

    Mimics what another worker does after the lease window elapses — the
    regression target — without needing real time to advance.  Accepts a
    sessionmaker so it can be passed the same factory the runner uses.
    """
    with session_factory() as rotate_session:
        AcquisitionRepository(rotate_session)._session.execute(  # noqa: SLF001
            AcquisitionJob.__table__.update()
            .where(AcquisitionJob.id == job_id)
            .values(
                lease_token="rotated-by-takeover",
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
        rotate_session.commit()


def test_search_returns_quietly_when_renew_sees_a_rotated_lease(
    session, research_case, thesis, document
):
    """Regression: a stale lease mid-search must not crash the worker loop.

    Without the try/except the worker would die, restart, re-claim, and crash
    again — the same crash-loop that pinned the page on "执行补证" all night.
    """
    clock = MutableClock()
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim is not None
    session.commit()

    class RenewRep:
        """Forces the very next ``renew`` to see a rotated lease."""

        def __init__(
            self,
            session_factory,
            clock,
        ) -> None:
            self._session_factory = session_factory
            self._clock = clock
            self._triggered = False

        def renew(self, job_id, *, lease_token, lease_for):  # noqa: D401
            if not self._triggered:
                self._triggered = True
                _expire_claim_for_job(self._session_factory, job_id)
            with self._session_factory() as session:
                return AcquisitionRepository(session, clock=self._clock).renew(
                    job_id, lease_token=lease_token, lease_for=lease_for
                )

    captured: dict[str, object] = {}

    class FakeRunner(AcquisitionRunner):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._renew_proxy = RenewRep(args[0], clock)

        def _renew(self, claim):  # noqa: D401
            return self._renew_proxy.renew(
                claim.job_id,
                lease_token=claim.lease_token,
                lease_for=self._lease_for,
            )

    adapter = FakeSSEAdapter()
    runner = FakeRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
    )
    # Must surface as StaleLeaseError so the worker entry-point (run_once)
    # can swallow it and keep polling — without this the worker loop
    # crashes on the first rotation and the page pins on "执行补证".
    with pytest.raises(StaleLeaseError):
        runner.run_claim(claim)

    session.expire_all()
    # The job is still 'running' because the renew guarded the first search
    # call.  The stage never advances to 'fetching' — a fresh worker will
    # resume from its durable checkpoints on the next claim.
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.stage == "searching"
    # No 'succeeded' event was published — the run was abandoned cleanly.
    assert module.get(job.id, principal=principal).status == "running"


def test_fetch_returns_quietly_when_renew_sees_a_rotated_lease(
    session, research_case, thesis, document
):
    """Regression: stale lease in the fetch stage must not crash the worker.

    Sets up a job that has already produced references and advances the
    stage to ``fetching`` so the ``_fetch`` branch is exercised.
    """
    clock = MutableClock()
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim is not None
    # Move the job past searching by recording a reference + advancing stage.
    repository = AcquisitionRepository(session, clock=clock)
    reference = repository.create_or_get_reference(
        job_id=job.id,
        lease_token=claim.lease_token,
        adapter_key="sse",
        external_record_id="task8-fetch-regression",
        external_version="v1",
        canonical_url="https://www.sse.com.cn/disclosure/task8-fetch.txt",
        title="Example Corp announcement",
        published_at=NOW,
        source_role="company_disclosure",
        metadata_json={"retrieval_locator": {"canonical_url": "https://x"}},
    )
    repository.advance(
        job.id,
        lease_token=claim.lease_token,
        stage="fetching",
        status="running",
    )
    session.commit()

    class RenewRep:
        def __init__(
            self,
            session_factory,
            clock,
        ) -> None:
            self._session_factory = session_factory
            self._clock = clock
            self._triggered = False

        def renew(self, job_id, *, lease_token, lease_for):
            if not self._triggered:
                self._triggered = True
                _expire_claim_for_job(self._session_factory, job_id)
            with self._session_factory() as session:
                return AcquisitionRepository(session, clock=self._clock).renew(
                    job_id, lease_token=lease_token, lease_for=lease_for
                )

    class FakeRunner(AcquisitionRunner):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._renew_proxy = RenewRep(args[0], clock)

        def _renew(self, claim):
            return self._renew_proxy.renew(
                claim.job_id,
                lease_token=claim.lease_token,
                lease_for=self._lease_for,
            )

    adapter = FakeSSEAdapter()
    runner = FakeRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
    )
    # Must surface as StaleLeaseError so the worker entry-point can swallow
    # it and keep polling — see the test above for context.
    with pytest.raises(StaleLeaseError):
        runner.run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.stage == "fetching"
    assert module.get(job.id, principal=principal).status == "running"
    assert reference.id is not None  # fixture sanity


def test_freeze_returns_quietly_when_renew_sees_a_rotated_lease(
    session, research_case, thesis, document
):
    """Regression: stale lease in the freeze stage must not crash the worker.

    Seeds an artifact so the ``_freeze`` branch sees a real unbound artifact,
    then rotates the lease so the very first renew raises.
    """
    from app.models.acquisition import RetrievalArtifact
    from app.documents.locators import compute_text_sha256

    clock = MutableClock()
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(seconds=30),
    )
    assert claim is not None
    repository = AcquisitionRepository(session, clock=clock)
    reference = repository.create_or_get_reference(
        job_id=job.id,
        lease_token=claim.lease_token,
        adapter_key="sse",
        external_record_id="task8-freeze-regression",
        external_version="v1",
        canonical_url="https://www.sse.com.cn/disclosure/task8-freeze.txt",
        title="Example Corp announcement",
        published_at=NOW,
        source_role="company_disclosure",
        metadata_json={"retrieval_locator": {"canonical_url": "https://x"}},
    )
    repository.record_attempt(
        job_id=job.id,
        lease_token=claim.lease_token,
        adapter_key="sse",
        operation="fetch",
        started_at=NOW,
        finished_at=NOW,
        outcome="succeeded",
        retryable=False,
    )
    attempt = session.scalar(
        select(AcquisitionAttempt)
        .where(AcquisitionAttempt.job_id == job.id)
        .order_by(AcquisitionAttempt.started_at.desc())
    )
    assert attempt is not None
    session.add(
        RetrievalArtifact(
            source_reference_id=reference.id,
            attempt_id=attempt.id,
            content_sha256=compute_text_sha256(
                "Example Corp 2026-08-12 Revenue was 100 USD."
            ),
            raw_bytes=b"Example Corp 2026-08-12 Revenue was 100 USD.",
            mime_type="text/plain; charset=utf-8",
            byte_size=len(b"Example Corp 2026-08-12 Revenue was 100 USD."),
            final_url="https://www.sse.com.cn/disclosure/task8-freeze.txt",
            etag='"task8-v1"',
            last_modified="Thu, 13 Aug 2026 08:00:00 GMT",
            provider_request_id="task8-freeze-request",
            retrieved_at=NOW,
        )
    )
    repository.advance(
        job.id,
        lease_token=claim.lease_token,
        stage="freezing",
        status="running",
    )
    session.commit()

    class RenewRep:
        def __init__(
            self,
            session_factory,
            clock,
        ) -> None:
            self._session_factory = session_factory
            self._clock = clock
            self._triggered = False

        def renew(self, job_id, *, lease_token, lease_for):
            if not self._triggered:
                self._triggered = True
                _expire_claim_for_job(self._session_factory, job_id)
            with self._session_factory() as session:
                return AcquisitionRepository(session, clock=self._clock).renew(
                    job_id, lease_token=lease_token, lease_for=lease_for
                )

    class FakeRunner(AcquisitionRunner):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self._renew_proxy = RenewRep(args[0], clock)

        def _renew(self, claim):
            return self._renew_proxy.renew(
                claim.job_id,
                lease_token=claim.lease_token,
                lease_for=self._lease_for,
            )

    runner = FakeRunner(
        make_session_factory(session),
        adapters={"sse": FakeSSEAdapter()},
        llm_client=FakeExtractionClient(),
        clock=clock,
    )
    # Must surface as StaleLeaseError so the worker entry-point can swallow
    # it and keep polling — see the test above for context.
    with pytest.raises(StaleLeaseError):
        runner.run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert persisted.status == "running"
    assert persisted.stage == "freezing"
    assert module.get(job.id, principal=principal).status == "running"
