"""Real-PostgreSQL proof of the governed event-research mainline."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.acquisition.sources import (
    RetrievedEnvelope,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
)
from app.ai.client import LLMClient
from app.domain.acquisition import AcquisitionPrincipal
from app.models.acquisition import AcquisitionJob, AutomaticAdmissionDecision
from app.models.event_research import (
    EventResearchConclusion,
    EventResearchScopeVersion,
)
from app.models.ledger import EvidenceLink, Thesis
from app.models.operational import Job, ResearchRun, ResearchTask
from app.models.research_monitor import ResearchRunEvent
from app.models.research_orchestration import (
    AcquisitionGoalCoverage,
    AcquisitionQueryPlan,
    ResearchOrchestration,
    ResearchOrchestrationEvent,
)
from app.schemas.v1.event_research import CreateEventResearchRequest
from app.scripts import run_research_worker
from app.scripts.run_acquisition_worker import run_once as run_acquisition_once
from app.services.acquisition import AcquisitionModule
from app.services.acquisition_runner import AcquisitionRunner
from app.services.event_research import EventResearchService
from app.services.research_orchestration import (
    OrchestrationPrincipal,
    ResearchOrchestrationService,
)
from app.services.research_protocol import (
    MetricDefinitionInput,
    OutcomeBindingInput,
    ResearchProtocolService,
)
from tests.research_identity import persist_research_principal


TENANT = "test-team"
ACTOR = "user:test-team"
WORKER = "system:acquisition-worker@mainline-v1#worker-a"
EVENT_AT = datetime(2026, 8, 13, 8, tzinfo=UTC)
FACTS = (("Revenue", "100"), ("Orders", "200"), ("Inventory", "300"))
PROVIDER_IDENTITIES = {
    "gildata": "Gildata",
    "sse": "Shanghai Stock Exchange",
    "szse": "Shenzhen Stock Exchange",
}
SOURCE_ROLES = {
    "gildata": "licensed_provider",
    "sse": "company_disclosure",
    "szse": "company_disclosure",
}


def _source_url(adapter_key: str, record_id: str, *, final: bool) -> str:
    if adapter_key == "gildata":
        return f"gildata://research-report/{record_id}"
    if adapter_key == "szse":
        return f"https://disc.static.szse.cn/{record_id}.txt"
    host = "static.sse.com.cn" if final else "www.sse.com.cn"
    return f"https://{host}/{record_id}.txt"


class DeterministicMainlineSource(SourceAdapter):
    """The only fake at the external-source boundary in this test."""

    def __init__(self, adapter_key: str) -> None:
        self.adapter_key = adapter_key
        self.search_count = 0
        self.fetch_count = 0
        schemes = frozenset({"gildata" if adapter_key == "gildata" else "https"})
        hosts = {
            "gildata": frozenset({"research-report"}),
            "sse": frozenset({"www.sse.com.cn"}),
            "szse": frozenset({"disc.static.szse.cn"}),
        }[adapter_key]
        self._descriptor = SourceDescriptor(
            adapter_key=adapter_key,
            provider_identity=PROVIDER_IDENTITIES[adapter_key],
            allowed_schemes=schemes,
            allowed_hosts=hosts,
            allowed_source_roles=frozenset({SOURCE_ROLES[adapter_key]}),
        )

    @property
    def descriptor(self) -> SourceDescriptor:
        return self._descriptor

    def search(self, query: str, cutoff: datetime):
        self.search_count += 1
        assert cutoff == datetime(2026, 8, 13, 23, 59, 59, 999999, tzinfo=UTC)
        digest = hashlib.sha256(query.encode()).hexdigest()[:20]
        record_id = f"mainline-{self.adapter_key}-{digest}"
        return (
            SourceReferenceValue(
                adapter_key=self.adapter_key,
                external_record_id=record_id,
                external_version="published:2026-08-13",
                canonical_url=_source_url(self.adapter_key, record_id, final=False),
                title=f"Example Corp governed disclosure {digest}",
                published_at=EVENT_AT,
                source_role=SOURCE_ROLES[self.adapter_key],
                fetch_locator={"record_id": record_id},
                metadata={
                    "provider_identity": PROVIDER_IDENTITIES[self.adapter_key],
                    "security_code": "600001",
                },
            ),
        )

    def restore_reference(self, reference: SourceReferenceValue) -> None:
        self.descriptor.validate_reference(reference)

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        self.fetch_count += 1
        content = "\n".join(
            [f"source {self.adapter_key} {reference.external_record_id}"]
            + [
                f"Example Corp 2026-08-13 {metric} was {value} USD."
                for metric, value in FACTS
            ]
        ).encode()
        return RetrievedEnvelope(
            content=content,
            mime_type="text/plain; charset=utf-8",
            final_url=_source_url(
                self.adapter_key,
                reference.external_record_id,
                final=True,
            ),
            etag=f'"{reference.external_record_id}"',
            last_modified="Thu, 13 Aug 2026 08:00:00 GMT",
            provider_request_id=f"request-{reference.external_record_id}",
            metadata={
                "adapter_key": self.adapter_key,
                "external_record_id": reference.external_record_id,
                "provider_identity": PROVIDER_IDENTITIES[self.adapter_key],
            },
        )

    def close(self) -> None:
        return None


class DeterministicExtractionClient:
    model_version = "test-mainline-extractor-v1"

    def chat_json(self, messages, schema_hint=""):
        assert schema_hint == "extract"
        payload = json.loads(messages[-1]["content"])
        statements = []
        for span in payload["spans"]:
            text = span["verbatim_text"]
            for metric, value in FACTS:
                quote = f"Example Corp 2026-08-13 {metric} was {value} USD."
                if quote not in text:
                    continue
                start = text.index(quote)
                statements.append(
                    {
                        "span_id": span["span_id"],
                        "kind": "disclosed_fact",
                        "quote": quote,
                        "quote_start": start,
                        "quote_end": start + len(quote),
                        "normalized_text": quote,
                        "assertion_actor": "Example Corp",
                        "subject": "Example Corp",
                        "predicate": metric,
                        "object_text": f"{value} USD",
                        "numeric_value": value,
                        "unit": "USD",
                        "observed_period": "2026-08-13",
                        "scope": {
                            "company": "Example Corp",
                            "metric": metric,
                        },
                    }
                )
        return {"statements": statements}


class DeterministicResearchClient:
    """A deterministic model-provider double injected through the worker factory."""

    model_version = "test-mainline-research-v1"

    def __init__(self) -> None:
        self.schema_hints: list[str] = []

    def chat_json(self, messages, schema_hint=""):
        self.schema_hints.append(schema_hint)
        if schema_hint == "propose":
            return {"links": []}
        if schema_hint == "assess":
            return {
                "conclusion": "supported",
                "rationale": "当前冻结证据支持该命题，且未发现相反证据。",
                "gaps": [],
            }
        if schema_hint == "extract":
            return {"statements": []}
        if schema_hint == "rewrite":
            payload = json.loads(messages[-1]["content"])
            return {"texts": list(payload.get("texts", []))}
        raise AssertionError(f"unexpected research schema hint: {schema_hint}")


def _seed_confirmed_scope(session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="Example Corp disclosed Revenue, Orders and Inventory.",
            event_title="Example Corp operating update",
            company_name="Example Corp",
            ticker="600001",
            event_at=EVENT_AT,
            research_question="Are operating gains supported by primary evidence?",
            candidate_factors=[metric for metric, _value in FACTS],
            research_protocol_required=False,
        ),
        principal=persist_research_principal(
            session, tenant_id=TENANT, label="event-mainline"
        ),
    )
    case_id = uuid.UUID(created.case_id)
    scope = session.scalar(
        select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
    )
    assert scope is not None
    theses = list(
        session.scalars(
            select(Thesis)
            .where(Thesis.research_case_id == case_id)
            .order_by(Thesis.statement)
        )
    )
    assert {thesis.statement for thesis in theses} == {
        metric for metric, _value in FACTS
    }
    protocol = ResearchProtocolService(session)
    for thesis in theses:
        metric = protocol.add_metric_version(
            MetricDefinitionInput(
                metric_id=f"mainline_{thesis.statement.casefold()}_{case_id.hex}",
                display_name=thesis.statement,
                canonical_definition=f"Frozen {thesis.statement} outcome",
                entity_scope="company",
                unit="USD",
                frequency="event",
                period_semantics="period end",
                allowed_source_roles=["company_disclosure"],
                role_eligibility=["outcome"],
            ),
            approved_by="system:test-fixture",
            reason="deterministic PostgreSQL mainline",
        )
        draft = protocol.create_outcome_binding(
            thesis.id,
            OutcomeBindingInput(
                metric_definition_id=metric.id,
                entity_scope={
                    "company_id": "example-corp",
                    "company": "Example Corp",
                },
                direction="increase",
                baseline={
                    "source_ref": "test:mainline",
                    "value": "0",
                    "unit": "USD",
                    "observed_period": "2026-08-13",
                    "available_at": EVENT_AT.isoformat(),
                },
                horizon_start=date(2026, 8, 13),
                horizon_end=date(2026, 8, 13),
                reviewer="system:test-fixture",
                reason="bind coverage semantics",
            ),
        )
        protocol.approve_outcome_binding(
            draft.id,
            reviewer="system:test-fixture",
            reason="approve deterministic coverage semantics",
        )
    principal = OrchestrationPrincipal(TENANT, ACTOR)
    orchestration = ResearchOrchestrationService(session).confirm_scope(
        case_id,
        principal,
        f"confirm:{scope.id}",
        scope_version_id=scope.id,
        expected_scope_version=scope.version,
    )
    assert orchestration.state == "planning_acquisition"
    assert orchestration.current_research_run_id is not None
    session.commit()
    return case_id, scope.id, orchestration.current_research_run_id


def _plan_snapshot(session, case_id: uuid.UUID) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            plan.id,
            plan.acquisition_job_id,
            plan.series_id,
            plan.goal_id,
            plan.acquisition_round,
            json.dumps(plan.frozen_inputs_json, sort_keys=True),
            json.dumps(plan.ordered_queries_json, sort_keys=True),
        )
        for plan in session.scalars(
            select(AcquisitionQueryPlan)
            .join(
                AcquisitionJob,
                AcquisitionJob.id == AcquisitionQueryPlan.acquisition_job_id,
            )
            .where(AcquisitionJob.research_case_id == case_id)
            .order_by(AcquisitionQueryPlan.goal_id, AcquisitionQueryPlan.id)
        )
    )


@pytest.mark.pg_only
def test_event_research_mainline_is_durable_idempotent_and_traceable(
    engine,
    session,
    monkeypatch,
) -> None:
    del session  # fixture teardown truncates the dedicated PostgreSQL database
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    principal = OrchestrationPrincipal(TENANT, "worker:research-orchestration")

    with sessions() as command:
        case_id, scope_id, run_id = _seed_confirmed_scope(command)

    with sessions() as orchestration_worker:
        current = ResearchOrchestrationService(orchestration_worker).reconcile(
            case_id, principal
        )
        assert current.state == "acquiring"
        orchestration_worker.commit()

    adapters = {
        key: DeterministicMainlineSource(key) for key in ("gildata", "sse", "szse")
    }
    runner = AcquisitionRunner(
        sessions,
        adapters=adapters,
        llm_client=DeterministicExtractionClient(),
    )
    completed_jobs = 0
    while run_acquisition_once(
        runner=runner,
        session_factory=sessions,
        worker_id=WORKER,
        lease_for=timedelta(minutes=5),
    ):
        completed_jobs += 1
        assert completed_jobs <= 7
    assert completed_jobs == 7

    with sessions() as orchestration_worker:
        service = ResearchOrchestrationService(orchestration_worker)
        assessing = service.reconcile(case_id, principal)
        assert assessing.state == "assessing_coverage"
        orchestration_worker.commit()
        ready = service.reconcile(case_id, principal)
        coverage_debug = [
            (
                row.goal_id,
                row.status,
                row.reason_codes_json,
                row.evidence_link_ids_json,
            )
            for row in orchestration_worker.scalars(
                select(AcquisitionGoalCoverage).where(
                    AcquisitionGoalCoverage.research_run_id == run_id
                )
            )
        ]
        assert ready.state == "assessing_coverage", json.dumps(coverage_debug)
        assert ready.checkpoint_json["coverage_decision"]["decision"] == "ready"
        orchestration_worker.commit()
        synthesizing = service.reconcile(case_id, principal)
        assert synthesizing.state == "synthesizing_evidence"
        orchestration_worker.commit()

    with sessions() as verification:
        plans_before_replay = _plan_snapshot(verification, case_id)
        evidence_before_replay = tuple(
            sorted(
                (str(link.id), str(link.automatic_admission_decision_id))
                for link in verification.scalars(
                    select(EvidenceLink)
                    .join(
                        AutomaticAdmissionDecision,
                        AutomaticAdmissionDecision.id
                        == EvidenceLink.automatic_admission_decision_id,
                    )
                    .join(
                        AcquisitionJob,
                        AcquisitionJob.id == AutomaticAdmissionDecision.job_id,
                    )
                    .where(AcquisitionJob.research_case_id == case_id)
                )
            )
        )
        assert len(plans_before_replay) == 7
        assert evidence_before_replay

    research_client = DeterministicResearchClient()
    monkeypatch.setattr(run_research_worker, "SessionLocal", sessions)
    monkeypatch.setattr(
        LLMClient,
        "from_env",
        classmethod(lambda _cls: research_client),
    )

    # Exercise the production research worker entrypoint: reconcile, claim,
    # synthesize the already frozen and automatically admitted scope evidence,
    # lease-fenced completion, and orchestration callback all run through the
    # deployed worker path. The empty schema-hint log below proves this mode
    # did not invoke the legacy extraction/proposal model pipeline.
    assert run_research_worker.run_once()
    with sessions() as worker_check:
        worker_run = worker_check.get(ResearchRun, run_id)
        worker_jobs = list(
            worker_check.execute(
                select(Job.status, Job.step, Job.error).where(Job.target_id == run_id)
            )
        )
        worker_tasks = list(
            worker_check.execute(
                select(ResearchTask.task_type, ResearchTask.status).where(
                    ResearchTask.run_id == run_id
                )
            )
        )
        worker_debug = {
            "run": (worker_run.status, worker_run.stage) if worker_run else None,
            "jobs": worker_jobs,
            "tasks": worker_tasks,
        }
    assert worker_debug["run"] == ("succeeded", "complete")
    assert worker_debug["jobs"] == [("succeeded", "complete", None)]
    assert {status for _task_type, status in worker_debug["tasks"]} == {"done"}
    assert research_client.schema_hints == []

    with sessions() as replay_worker:
        service = ResearchOrchestrationService(replay_worker)
        for _ in range(3):
            current = service.reconcile(case_id, principal)
        assert current.state == "monitoring"
        replay_worker.commit()

    assert not run_acquisition_once(
        runner=runner,
        session_factory=sessions,
        worker_id=WORKER,
        lease_for=timedelta(minutes=5),
    )

    with sessions() as verification:
        orchestration = verification.scalar(
            select(ResearchOrchestration).where(
                ResearchOrchestration.research_case_id == case_id
            )
        )
        assert orchestration is not None and orchestration.state == "monitoring"
        transitions = list(
            verification.scalars(
                select(ResearchOrchestrationEvent.transition)
                .where(ResearchOrchestrationEvent.orchestration_id == orchestration.id)
                .order_by(ResearchOrchestrationEvent.sequence)
            )
        )
        state_transitions = {
            "scope_confirmed": "planning_acquisition",
            "acquisition_goals_dispatched": "acquiring",
            "acquisition_round_completed": "assessing_coverage",
            "evidence_synthesis_started": "synthesizing_evidence",
            "thesis_adjudicated": "generating_report",
            "monitoring_started": "monitoring",
        }
        assert [
            state_transitions[item] for item in transitions if item in state_transitions
        ] == [
            "planning_acquisition",
            "acquiring",
            "assessing_coverage",
            "synthesizing_evidence",
            "generating_report",
            "monitoring",
        ]

        assert _plan_snapshot(verification, case_id) == plans_before_replay
        evidence_after_replay = tuple(
            sorted(
                (str(link.id), str(link.automatic_admission_decision_id))
                for link in verification.scalars(
                    select(EvidenceLink)
                    .join(
                        AutomaticAdmissionDecision,
                        AutomaticAdmissionDecision.id
                        == EvidenceLink.automatic_admission_decision_id,
                    )
                    .join(
                        AcquisitionJob,
                        AcquisitionJob.id == AutomaticAdmissionDecision.job_id,
                    )
                    .where(AcquisitionJob.research_case_id == case_id)
                )
            )
        )
        assert evidence_after_replay == evidence_before_replay
        assert (
            verification.scalar(
                select(func.count())
                .select_from(AcquisitionJob)
                .where(AcquisitionJob.research_case_id == case_id)
            )
            == 7
        )

        module = AcquisitionModule(verification)
        acquisition_principal = AcquisitionPrincipal(
            TENANT, "system:mainline-verification"
        )
        ledger = module.workflow_ledger(
            case_id=case_id,
            scope_version_id=scope_id,
            principal=acquisition_principal,
        )
        admitted = [item for item in ledger if item.status == "admitted"]
        assert admitted
        assert all(
            item.final_url
            and item.retrieved_at
            and item.content_sha256
            and len(item.content_sha256) == 64
            and item.review_state == "automatically_admitted"
            and item.evidence_link_id is not None
            for item in admitted
        )
        draft = verification.scalar(
            select(EventResearchConclusion).where(
                EventResearchConclusion.research_case_id == case_id,
                EventResearchConclusion.state == "ai_draft",
            )
        )
        assert draft is not None
        assert draft.reviewer is None
        assert draft.text.startswith("系统生成，未经人工审核：")
        completion = verification.scalar(
            select(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == run_id,
                ResearchRunEvent.stage == "complete",
                ResearchRunEvent.status == "completed",
            )
            .order_by(ResearchRunEvent.seq.desc())
            .limit(1)
        )
        assert completion is not None
        receipt_ids = set(completion.payload_json["evidence_link_ids"])
        assert set(draft.evidence_link_ids) == receipt_ids
        assert receipt_ids <= {
            evidence_id for evidence_id, _decision_id in evidence_after_replay
        }
