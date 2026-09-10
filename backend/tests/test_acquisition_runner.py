from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import object_session, sessionmaker

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
    ACQUISITION_PLANNER_VERSION,
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
    PlannedQuery,
    QueryPlanExpansion,
)
from app.models.event_research import EventResearchScopeVersion
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    RetrievalArtifactDocument,
)
from app.ai.prompts import EXTRACT_PROMPT_VERSION
from app.models.ledger import (
    AIRun,
    AtomicClaimCandidate,
    AtomicClaimReview,
    EvidenceLink,
    SourceStatement,
)
from app.models.operational import ResearchRun
from app.models.research_orchestration import AcquisitionQueryPlan
from app.repositories.acquisition import AcquisitionRepository
from app.services.acquisition import AcquisitionModule
from app.services.acquisition_runner import AcquisitionRunner, _thaw
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


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_thaw_normalizes_non_finite_provider_diagnostics(value):
    assert _thaw({"diagnostic": value}) == {"diagnostic": None}


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


class ManySSEAdapter(FakeSSEAdapter):
    def search(self, query: str, cutoff: datetime):
        reference = super().search(query, cutoff)[0]
        return tuple(
            replace(
                reference,
                external_record_id=f"task8-report-{index}",
                canonical_url=(
                    f"https://www.sse.com.cn/disclosure/task8-report-{index}.txt"
                ),
                fetch_locator={
                    "canonical_url": (
                        f"https://www.sse.com.cn/disclosure/task8-report-{index}.txt"
                    )
                },
            )
            for index in range(1, 7)
        )

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        self.fetch_calls += 1
        return RetrievedEnvelope(
            content=(
                f"Example Corp 2026-08-12 Revenue was 100 USD. "
                f"{reference.external_record_id}"
            ).encode(),
            mime_type="text/plain; charset=utf-8",
            final_url=reference.canonical_url.replace(
                "www.sse.com.cn", "static.sse.com.cn"
            ),
            etag=f'"{reference.external_record_id}"',
            last_modified="Thu, 13 Aug 2026 08:00:00 GMT",
            provider_request_id=f"request-{reference.external_record_id}",
            metadata={
                "adapter_key": "sse",
                "external_record_id": reference.external_record_id,
                "provider_identity": "Shanghai Stock Exchange",
            },
        )


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


class RetryableFetchAdapter(FakeSSEAdapter):
    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        self.fetch_calls += 1
        raise SourceUnavailable(
            "official source temporarily unavailable",
            retryable=True,
            diagnostics={
                "provider_status": "temporarily_unavailable",
                "retry_after_seconds": 30,
                "debug_value": "internal-marker-must-not-leak",
            },
        )


class EmptySearchAdapter(FakeSSEAdapter):
    def search(self, query: str, cutoff: datetime):
        self.search_calls += 1
        return ()


class RetryThenEmptySearchAdapter(FakeSSEAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.queries: list[str] = []

    def search(self, query: str, cutoff: datetime):
        self.search_calls += 1
        self.queries.append(query)
        if self.search_calls == 1:
            raise SourceUnavailable(
                "official source temporarily unavailable",
                retryable=True,
                diagnostics={"provider_status": "temporarily_unavailable"},
            )
        return ()


class CountingSSEPlanner:
    version = ACQUISITION_PLANNER_VERSION

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, request, policy):
        self.calls += 1
        return (
            PlannedQuery(
                adapter_key="sse",
                objective=request.objective,
                query="Example Corp Revenue 600001 2026-01-01 2026-12-31",
            ),
        )


class OutOfPolicyPlanner:
    version = ACQUISITION_PLANNER_VERSION

    def plan(self, request, policy):
        return (
            PlannedQuery(
                adapter_key="szse",
                objective=request.objective,
                query="Example Corp out-of-policy query",
            ),
        )


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


def test_successful_extraction_checkpoint_is_scoped_to_prompt_and_grounding_context(
    session,
):
    document_id = uuid.uuid4()
    expected_context = {
        "entity_names": ["皇马科技"],
        "metric_terms": ["营业收入"],
        "period_start": "2025-08-17",
        "period_end": "2026-08-17",
    }
    session.add_all(
        [
            AIRun(
                kind="extract",
                model_version="fixture-model-v1",
                prompt_version="extract-v2",
                input_ref={
                    "document_version_id": str(document_id),
                    "grounding_context": expected_context,
                },
                output_summary="old prompt",
                status="success",
                error=None,
                started_at=NOW,
                finished_at=NOW,
            ),
            AIRun(
                kind="extract",
                model_version="fixture-model-v1",
                prompt_version=EXTRACT_PROMPT_VERSION,
                input_ref={
                    "document_version_id": str(document_id),
                    "grounding_context": {
                        **expected_context,
                        "entity_names": ["另一家公司"],
                    },
                },
                output_summary="different case context",
                status="success",
                error=None,
                started_at=NOW,
                finished_at=NOW,
            ),
        ]
    )
    session.commit()

    runner = AcquisitionRunner(
        make_session_factory(session),
        adapters={},
        llm_client=FakeExtractionClient(),
    )

    assert not runner._has_successful_extraction(document_id, expected_context)

    session.add(
        AIRun(
            kind="extract",
            model_version="fixture-model-v1",
            prompt_version=EXTRACT_PROMPT_VERSION,
            input_ref={
                "document_version_id": str(document_id),
                "grounding_context": expected_context,
            },
            output_summary="matching case context",
            status="success",
            error=None,
            started_at=NOW,
            finished_at=NOW,
        )
    )
    session.commit()

    assert runner._has_successful_extraction(document_id, expected_context)


def make_request(research_case, thesis, *, idempotency_key=None):
    session = object_session(research_case)
    assert session is not None
    scope_version = (
        session.scalar(
            select(func.max(EventResearchScopeVersion.version)).where(
                EventResearchScopeVersion.research_case_id == research_case.id
            )
        )
        or 0
    ) + 1
    scope = EventResearchScopeVersion(
        research_case_id=research_case.id,
        version=scope_version,
        changed_by="system:test",
        change_summary="runner test scope",
        created_at=NOW,
    )
    run = ResearchRun(
        research_case_id=research_case.id,
        status="queued",
        stage="planning",
        round=0,
        max_rounds=3,
        budget=100,
        budget_used=0,
        scope_thesis_ids=[str(thesis.id)],
        created_at=NOW,
        updated_at=NOW,
    )
    session.add_all((scope, run))
    session.flush()
    goal_id = f"thesis:{thesis.id}:support"
    return AcquisitionRequest(
        tenant_id="team-a",
        case_id=research_case.id,
        thesis_id=thesis.id,
        research_run_id=run.id,
        scope_version_id=scope.id,
        goal_id=goal_id,
        round=1,
        objective=EvidenceObjective.SUPPORT,
        target_link_role="supports",
        thesis_statement="Example Corp revenue is 100 USD",
        entity_names=("Example Corp",),
        security_codes=("600001",),
        metric_terms=("Revenue",),
        metric_periods=("2026-Q2",),
        metric_units=("USD",),
        period_start="2026-01-01",
        period_end="2026-12-31",
        cutoff=CUTOFF,
        allowed_source_roles=frozenset({"company_disclosure"}),
        source_policy_version=B_SCOPE_POLICY.version,
        planner_version=ACQUISITION_PLANNER_VERSION,
        previous_query_plan_id=None,
        expansion=None,
        # Historical callers pass a descriptive test label here. The governed
        # contract no longer permits that label to replace the canonical job
        # identity, and each helper call already owns a distinct real run/scope.
        idempotency_key=f"run:{run.id}:scope:{scope.id}:goal:{goal_id}:round:1",
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
        "queued",
        "searching",
        "searching",
        "fetching",
        "freezing",
        "extracting",
        "admitting",
        "succeeded",
    ]
    search_page = next(event for event in events if event.message == "search_result_page")
    assert search_page.payload_json == {
        "adapter_key": "sse",
        "goal_id": request.goal_id,
        "policy_version": B_SCOPE_POLICY.version,
        "query_index": 0,
        "result_count": 1,
    }
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


def test_runner_bounds_provider_results_before_fetch_and_extraction(
    session, research_case, thesis, document
):
    clock = MutableClock()
    adapter = ManySSEAdapter()
    module, principal, job = _run_with_adapter(
        session, research_case, thesis, document, adapter, clock
    )

    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert persisted.reference_count == SSE_ONLY_POLICY.per_adapter_page_limit
    assert persisted.frozen_count <= SSE_ONLY_POLICY.per_adapter_page_limit
    assert 0 < adapter.fetch_calls <= SSE_ONLY_POLICY.per_adapter_page_limit
    bounded = next(
        event
        for event in module.events(job.id, principal=principal)
        if event.message == "search_results_bounded"
    )
    assert bounded.payload_json["accepted_count"] == 3
    assert bounded.payload_json["omitted_count"] == 3


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


def test_candidate_lineages_ignore_an_extraction_from_another_grounding_context(
    session, research_case, thesis, document
) -> None:
    clock = MutableClock()
    _module, _principal, job = _run_with_adapter(
        session,
        research_case,
        thesis,
        document,
        FakeSSEAdapter(),
        clock,
    )
    extraction_run = session.scalar(select(AIRun).where(AIRun.kind == "extract"))
    original = session.scalar(select(AtomicClaimCandidate))
    assert extraction_run is not None and original is not None
    unrelated_run = AIRun(
        kind="extract",
        model_version=extraction_run.model_version,
        prompt_version=extraction_run.prompt_version,
        input_ref={
            **extraction_run.input_ref,
            "grounding_context": {
                **extraction_run.input_ref["grounding_context"],
                "metric_terms": ["Operating margin"],
            },
        },
        output_summary="unrelated extraction",
        status="success",
        error=None,
        started_at=NOW,
        finished_at=NOW,
    )
    session.add(unrelated_run)
    session.flush()
    unrelated = AtomicClaimCandidate(
        source_span_id=original.source_span_id,
        canonical_key=uuid.uuid4().hex,
        quote=original.quote,
        quote_start=original.quote_start,
        quote_end=original.quote_end,
        quote_sha256=original.quote_sha256,
        normalized_text=original.normalized_text,
        claim_type=original.claim_type,
        assertion_actor=original.assertion_actor,
        authority_level=original.authority_level,
        structured_fields={
            **original.structured_fields,
            "run_ref": f"extract:{unrelated_run.id}",
        },
        validation_result=original.validation_result,
        created_at=NOW + timedelta(seconds=1),
    )
    deterministic = AtomicClaimCandidate(
        source_span_id=original.source_span_id,
        canonical_key=uuid.uuid4().hex,
        quote=original.quote,
        quote_start=original.quote_start,
        quote_end=original.quote_end,
        quote_sha256=original.quote_sha256,
        normalized_text=f"{original.normalized_text} table fact",
        claim_type=original.claim_type,
        assertion_actor=original.assertion_actor,
        authority_level=original.authority_level,
        structured_fields={
            **original.structured_fields,
            "run_ref": f"extract:{unrelated_run.id}",
            "scope": {
                **original.structured_fields.get("scope", {}),
                "extraction_method": "financial_table_v1",
            },
        },
        validation_result=original.validation_result,
        created_at=NOW + timedelta(seconds=2),
    )
    session.add_all((unrelated, deterministic))
    session.commit()
    runner = AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": FakeSSEAdapter()},
        llm_client=FakeExtractionClient(),
        clock=clock,
    )

    lineages = runner._candidate_lineages(job.id)
    assert {candidate_id for candidate_id, _artifact_id in lineages} == {
        original.id,
        deterministic.id,
    }


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
    retry_events = [
        event
        for event in module.events(job.id, principal=principal)
        if event.message == "retry_source_switch"
    ]
    assert retry_events
    assert retry_events[-1].payload_json == {
        "adapter_key": "sse",
        "goal_id": session.get(AcquisitionJob, job.id).request_snapshot["goal_id"],
        "operation": "search",
        "policy_version": B_SCOPE_POLICY.version,
        "query_index": 0,
        "reason_code": "source_unavailable",
        "retry_delay_seconds": 45.0,
    }
    assert all(
        {
            "adapter_key",
            "goal_id",
            "operation",
            "policy_version",
            "reason_code",
            "retry_delay_seconds",
        }
        <= event.payload_json.keys()
        for event in retry_events
    )


def test_retryable_fetch_failure_records_safe_observable_retry(
    session, research_case, thesis, document
):
    clock = MutableClock()
    module, principal, job = _run_with_adapter(
        session,
        research_case,
        thesis,
        document,
        RetryableFetchAdapter(),
        clock,
    )

    view = module.get(job.id, principal=principal)
    persisted = session.get(AcquisitionJob, job.id)
    assert (view.status, view.stage) == ("retry_wait", "fetching")
    assert persisted is not None
    assert persisted.retry_at.replace(tzinfo=UTC) == NOW + timedelta(seconds=45)
    retry_event = [
        event
        for event in module.events(job.id, principal=principal)
        if event.message == "retry_source_switch"
    ][-1]
    assert retry_event.payload_json == {
        "adapter_key": "sse",
        "goal_id": persisted.request_snapshot["goal_id"],
        "operation": "fetch",
        "policy_version": B_SCOPE_POLICY.version,
        "query_index": 0,
        "reason_code": "source_unavailable",
        "retry_delay_seconds": 45.0,
    }
    serialized = json.dumps(retry_event.payload_json).casefold()
    assert "authorization" not in serialized
    assert "cookie" not in serialized
    assert "internal-marker-must-not-leak" not in serialized


def test_retry_reloads_byte_identical_frozen_plan_without_invoking_planner(
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
    planner = CountingSSEPlanner()
    module = AcquisitionModule(
        session, policy=SSE_ONLY_POLICY, planner=planner
    )
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    frozen_plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == job.id
        )
    )
    assert frozen_plan is not None
    frozen_bytes = json.dumps(
        frozen_plan.ordered_queries_json,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    adapter = RetryThenEmptySearchAdapter()
    repository = AcquisitionRepository(session, clock=clock)

    first_claim = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert first_claim is not None
    session.commit()
    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
        retry_delay=timedelta(seconds=45),
        jitter_source=lambda: 0.0,
    ).run_claim(first_claim)

    clock.advance(timedelta(seconds=45))
    second_claim = repository.claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-b",
        lease_for=timedelta(minutes=5),
    )
    assert second_claim is not None
    session.commit()
    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
        retry_delay=timedelta(seconds=45),
        jitter_source=lambda: 0.0,
    ).run_claim(second_claim)

    session.expire_all()
    persisted_plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == job.id
        )
    )
    assert persisted_plan is not None
    assert planner.calls == 1
    assert adapter.queries == [adapter.queries[0], adapter.queries[0]]
    assert json.dumps(
        persisted_plan.ordered_queries_json,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode() == frozen_bytes
    assert session.scalar(
        select(func.count()).select_from(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == job.id
        )
    ) == 1


def test_runner_fails_closed_when_frozen_plan_exceeds_policy(
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
    module = AcquisitionModule(
        session, policy=SSE_ONLY_POLICY, planner=OutOfPolicyPlanner()
    )
    job = module.request(make_request(research_case, thesis), principal=principal)
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()
    adapter = EmptySearchAdapter()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job.id)
    assert persisted is not None
    assert (persisted.status, persisted.error_code) == ("failed", "invalid_query_plan")
    assert adapter.search_calls == 0


@pytest.mark.parametrize(
    ("mismatch", "expected_error"),
    (
        ("snapshot", "invalid_query_plan"),
        ("policy", "unsupported_source_policy_version"),
        ("plan", "invalid_query_plan"),
    ),
)
def test_runner_fails_closed_on_snapshot_policy_or_plan_mismatch(
    session, research_case, thesis, document, mismatch, expected_error
):
    clock = MutableClock()
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    job_ref = AcquisitionModule(session, policy=SSE_ONLY_POLICY).request(
        make_request(research_case, thesis), principal=principal
    )
    job = session.get(AcquisitionJob, job_ref.id)
    plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == job_ref.id
        )
    )
    assert job is not None and plan is not None
    if mismatch == "snapshot":
        job.request_snapshot = {
            **job.request_snapshot,
            "query_plan_id": str(uuid.uuid4()),
        }
    elif mismatch == "policy":
        job.policy_snapshot = {**job.policy_snapshot, "version": "b-scope-v0"}
    else:
        job.request_snapshot = {
            **job.request_snapshot,
            "goal_id": "different-goal",
        }
    session.commit()
    claim = AcquisitionRepository(session, clock=clock).claim_next(
        worker_id="system:acquisition-worker@task8-v1#worker-a",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None
    session.commit()
    adapter = EmptySearchAdapter()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job_ref.id)
    assert persisted is not None
    assert (persisted.status, persisted.error_code) == ("failed", expected_error)
    assert adapter.search_calls == 0


@pytest.mark.parametrize(
    ("snapshot_field", "malformed_root"),
    (
        pytest.param("request_snapshot", "root-marker", id="request-string"),
        pytest.param("request_snapshot", 7, id="request-number"),
        pytest.param("request_snapshot", ["root-marker"], id="request-list"),
        pytest.param("request_snapshot", None, id="request-null"),
        pytest.param("policy_snapshot", "root-marker", id="policy-string"),
        pytest.param("policy_snapshot", 7, id="policy-number"),
        pytest.param("policy_snapshot", ["root-marker"], id="policy-list"),
        pytest.param("policy_snapshot", None, id="policy-null"),
    ),
)
def test_runner_contains_malformed_snapshot_roots_before_adapter_work(
    session,
    research_case,
    thesis,
    document,
    snapshot_field,
    malformed_root,
):
    clock = MutableClock()
    module, principal, _request, job_ref, claim = enqueue_and_claim(
        session, research_case, thesis, clock
    )
    job = session.get(AcquisitionJob, job_ref.id)
    assert job is not None
    setattr(job, snapshot_field, malformed_root)
    session.commit()
    adapter = EmptySearchAdapter()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=clock,
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job_ref.id)
    assert persisted is not None
    assert (persisted.status, persisted.stage, persisted.error_code) == (
        "failed",
        "failed",
        "invalid_query_plan",
    )
    assert (
        persisted.lease_owner,
        persisted.lease_token,
        persisted.lease_expires_at,
    ) == (None, None, None)
    assert adapter.search_calls == 0
    failure_event = module.events(job_ref.id, principal=principal)[-1]
    assert failure_event.message == "acquisition query plan failed closed"
    assert failure_event.payload_json == {
        "reason_code": "invalid_query_plan",
        "validation_error": f"invalid_{snapshot_field}_root",
    }
    assert "root-marker" not in repr(failure_event.payload_json)


def _corrupt_query_plan(session, plan_id, **assignments) -> None:
    if session.get_bind().dialect.name != "sqlite":
        pytest.skip("runner corruption tests use the disposable SQLite ledger")
    cursor = session.connection().connection.cursor()
    foreign_keys_enabled = cursor.execute("PRAGMA foreign_keys").fetchone()[0]
    cursor.execute("PRAGMA foreign_keys = OFF")
    cursor.execute("PRAGMA ignore_check_constraints = ON")
    clauses = [f"{column} = ?" for column in assignments]
    values = [
        json.dumps(value) if isinstance(value, (dict, list)) else value
        for value in assignments.values()
    ]
    values.append(plan_id.hex)
    cursor.execute(
        f"UPDATE acquisition_query_plans SET {', '.join(clauses)} WHERE id = ?",
        tuple(values),
    )
    assert cursor.rowcount == 1
    cursor.execute("PRAGMA ignore_check_constraints = OFF")
    session.commit()
    cursor.execute(f"PRAGMA foreign_keys = {foreign_keys_enabled}")
    session.expire_all()


def _round_two_job(session, research_case, thesis, document):
    admit_case(
        session,
        research_case.id,
        tenant_id="team-a",
        document_version_id=document.id,
    )
    principal = AcquisitionPrincipal("team-a", "system:task8-requester")
    module = AcquisitionModule(session, policy=SSE_ONLY_POLICY)
    first_request = make_request(research_case, thesis)
    first_job = module.request(first_request, principal=principal)
    first_plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == first_job.id
        )
    )
    assert first_plan is not None
    second_request = replace(
        first_request,
        round=2,
        metric_terms=("Revenue", "Operating margin"),
        metric_periods=("2026-Q2",),
        metric_units=("USD",),
        previous_query_plan_id=first_plan.id,
        expansion=QueryPlanExpansion(
            trigger="coverage_gap",
            reason="Operating margin evidence remains missing",
        ),
        idempotency_key=(
            f"run:{first_request.research_run_id}:"
            f"scope:{first_request.scope_version_id}:"
            f"goal:{first_request.goal_id}:round:2"
        ),
    )
    second_job = module.request(second_request, principal=principal)
    second_plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == second_job.id
        )
    )
    assert second_plan is not None
    other_goal = "thesis:other-goal:support"
    other_request = replace(
        first_request,
        goal_id=other_goal,
        idempotency_key=(
            f"run:{first_request.research_run_id}:"
            f"scope:{first_request.scope_version_id}:"
            f"goal:{other_goal}:round:1"
        ),
    )
    other_job = module.request(other_request, principal=principal)
    other_plan = session.scalar(
        select(AcquisitionQueryPlan).where(
            AcquisitionQueryPlan.acquisition_job_id == other_job.id
        )
    )
    assert other_plan is not None
    for job_id in (first_job.id, other_job.id):
        job = session.get(AcquisitionJob, job_id)
        assert job is not None
        job.status = "succeeded"
        job.stage = "succeeded"
        job.finished_at = NOW
    session.commit()
    return module, principal, second_job, second_plan, other_plan


@pytest.mark.parametrize(
    ("tamper", "expected_validation_error"),
    (
        ("missing_predecessor", "previous_plan_missing"),
        ("cross_series_predecessor", "previous_plan_series_mismatch"),
        ("wrong_previous_round", "previous_plan_round_mismatch"),
        ("diff", "query_plan_diff_mismatch"),
        ("trigger", "expansion_trigger_missing"),
        ("reason", "expansion_reason_missing"),
    ),
)
def test_round_two_runner_fails_closed_on_tampered_lineage_or_expansion(
    session,
    research_case,
    thesis,
    document,
    tamper,
    expected_validation_error,
):
    module, principal, job_ref, plan, other_plan = _round_two_job(
        session, research_case, thesis, document
    )
    job = session.get(AcquisitionJob, job_ref.id)
    assert job is not None
    plan_inputs = dict(plan.frozen_inputs_json)
    request_snapshot = dict(job.request_snapshot)
    assignments = {}
    if tamper in {"missing_predecessor", "cross_series_predecessor"}:
        predecessor_id = (
            uuid.uuid4()
            if tamper == "missing_predecessor"
            else other_plan.id
        )
        assignments["previous_query_plan_id"] = predecessor_id.hex
        plan_inputs["previous_query_plan_id"] = str(predecessor_id)
        request_snapshot["previous_query_plan_id"] = str(predecessor_id)
        assignments["frozen_inputs_json"] = plan_inputs
        job.request_snapshot = request_snapshot
    elif tamper == "wrong_previous_round":
        assignments["previous_acquisition_round"] = 0
    elif tamper == "diff":
        assignments["diff_json"] = {
            **plan.diff_json,
            "added_queries": [],
        }
    elif tamper == "trigger":
        assignments["expansion_trigger"] = " "
    else:
        assignments["diff_json"] = {**plan.diff_json, "reason": " "}
    session.flush()
    _corrupt_query_plan(session, plan.id, **assignments)
    claim = AcquisitionRepository(session, clock=MutableClock()).claim_next(
        worker_id="system:acquisition-worker@task8-v1#lineage-test",
        lease_for=timedelta(minutes=5),
    )
    assert claim is not None and claim.job_id == job_ref.id
    session.commit()
    adapter = EmptySearchAdapter()

    AcquisitionRunner(
        make_session_factory(session),
        adapters={"sse": adapter},
        llm_client=FakeExtractionClient(),
        clock=MutableClock(),
    ).run_claim(claim)

    session.expire_all()
    persisted = session.get(AcquisitionJob, job_ref.id)
    assert persisted is not None
    assert (persisted.status, persisted.error_code) == (
        "failed",
        "invalid_query_plan",
    )
    assert adapter.search_calls == 0
    failure_event = module.events(job_ref.id, principal=principal)[-1]
    assert failure_event.message == "acquisition query plan failed closed"
    assert failure_event.payload_json == {
        "reason_code": "invalid_query_plan",
        "validation_error": expected_validation_error,
    }


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
