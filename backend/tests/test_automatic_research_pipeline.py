from __future__ import annotations

import uuid
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.acquisition.policy import B_SCOPE_POLICY
from app.acquisition.sources import (
    RetrievedEnvelope,
    RetrievedSearchResult,
    SourceAdapter,
    SourceDescriptor,
    SourceReferenceValue,
)
from app.errors import ValidationFailedError
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    AIAssessment,
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseTenantAdmission,
    DocumentVersion,
    EvidenceLink,
    EvidenceSnapshot,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import (
    EventResearchLifecycle,
    Job,
    JobEvent,
    ResearchRun,
    ResearchTask,
    TaskItem,
)
from app.models.research_monitor import ResearchRunEvent
from app.models.proposals import Proposal
from app.repositories.auto_research import AutoResearchRepository
from app.services.case_monitor import ResearchRunEventRepository
from app.services.event_extraction import EventExtraction


class _MaterialIntakeExtractor:
    def extract(self, *, raw_input: str, source_url: str | None) -> EventExtraction:
        return EventExtraction(
            event_title="用户材料",
            company_name=None,
            ticker=None,
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question="材料说明了什么？",
            candidate_factors=("收入", "利润", "订单"),
            input_kind="material",
        )


def test_material_jobs_dispatch_before_external_jobs_without_spending_budget(
    session,
) -> None:
    from app.services.automatic_research_intake import AutomaticResearchIntakeService
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    started = AutomaticResearchIntakeService(
        session, extractor=_MaterialIntakeExtractor()
    ).start("收入 100，利润 20，订单 30。", tenant_id="team-a")
    run = session.get(ResearchRun, uuid.UUID(started.run_id))
    assert run is not None

    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"
    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"

    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    assert len(jobs) == 3
    assert {job.request_snapshot["acquisition_kind"] for job in jobs} == {
        "intake_material"
    }
    assert all(
        job.request_snapshot["document_version_id"]
        for job in jobs
    )
    assert run.budget_used == 0
    assert not any(
        job.request_snapshot.get("acquisition_kind") == "external_gap"
        for job in jobs
    )


def test_material_acquisition_reuses_frozen_original_with_durable_lineage(
    session,
) -> None:
    from app.repositories.acquisition import AcquisitionRepository
    from app.services.acquisition_runner import AcquisitionRunner
    from app.services.automatic_research_intake import AutomaticResearchIntakeService
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    class NoClaimsClient:
        model_version = "no-claims-v1"

        def chat_json(self, messages, schema_hint=""):
            assert schema_hint == "extract"
            return {"statements": []}

    started = AutomaticResearchIntakeService(
        session, extractor=_MaterialIntakeExtractor()
    ).start("收入 100，利润 20，订单 30。", tenant_id="team-a")
    run = session.get(ResearchRun, uuid.UUID(started.run_id))
    assert run is not None
    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"
    session.commit()

    session_factory = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )
    runner = AcquisitionRunner(
        session_factory, adapters={}, llm_client=NoClaimsClient()
    )
    for index in range(3):
        with session_factory() as claim_session:
            claim = AcquisitionRepository(claim_session).claim_next(
                worker_id=f"system:acquisition-worker@test#material-{index}",
                lease_for=timedelta(minutes=5),
            )
            assert claim is not None
            claim_session.commit()
        runner.run_claim(claim)

    session.expire_all()
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    assert all(job.status in {"partial", "succeeded"} for job in jobs)
    for job in jobs:
        references = list(
            session.scalars(
                select(SourceReference).where(SourceReference.job_id == job.id)
            )
        )
        attempts = list(
            session.scalars(
                select(AcquisitionAttempt).where(AcquisitionAttempt.job_id == job.id)
            )
        )
        artifacts = list(
            session.scalars(
                select(RetrievalArtifact)
                .join(
                    SourceReference,
                    SourceReference.id == RetrievalArtifact.source_reference_id,
                )
                .where(SourceReference.job_id == job.id)
            )
        )
        bindings = list(
            session.scalars(
                select(RetrievalArtifactDocument)
                .join(
                    RetrievalArtifact,
                    RetrievalArtifact.id
                    == RetrievalArtifactDocument.retrieval_artifact_id,
                )
                .join(
                    SourceReference,
                    SourceReference.id == RetrievalArtifact.source_reference_id,
                )
                .where(SourceReference.job_id == job.id)
            )
        )
        assert len(references) == len(attempts) == len(artifacts) == len(bindings) == 1
        assert attempts[0].operation == "fetch"
        assert bindings[0].document_version_id == uuid.UUID(
            job.request_snapshot["document_version_id"]
        )


def test_material_acquisition_passes_governed_gate_and_publishes_machine_evidence(
    session,
) -> None:
    from app.repositories.acquisition import AcquisitionRepository
    from app.services.acquisition_runner import AcquisitionRunner
    from app.services.automatic_research_intake import AutomaticResearchIntakeService
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    class MaterialExtractor:
        def extract(
            self, *, raw_input: str, source_url: str | None
        ) -> EventExtraction:
            return EventExtraction(
                event_title="Example Corp material",
                company_name="Example Corp",
                ticker="600001",
                event_at=None,
                market_reaction=None,
                summary=None,
                research_question="What does the material establish?",
                candidate_factors=("Revenue", "Margin", "Orders"),
                input_kind="material",
            )

    class MaterialClaimsClient:
        model_version = "material-claims-v1"

        def chat_json(self, messages, schema_hint=""):
            assert schema_hint == "extract"
            payload = json.loads(messages[-1]["content"])
            statements = []
            for span in payload["spans"]:
                quote = span["verbatim_text"]
                statements.append(
                    {
                        "span_id": span["span_id"],
                        "kind": "reported_claim",
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
                        "scope": {
                            "company": "Example Corp",
                            "metric": "Revenue",
                        },
                    }
                )
            return {"statements": statements}

    started = AutomaticResearchIntakeService(
        session, extractor=MaterialExtractor()
    ).start(
        "Example Corp 2026-08-12 Revenue was 100 USD.",
        tenant_id="team-a",
    )
    run = session.get(ResearchRun, uuid.UUID(started.run_id))
    assert run is not None
    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"
    session.commit()

    session_factory = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )
    runner = AcquisitionRunner(
        session_factory, adapters={}, llm_client=MaterialClaimsClient()
    )
    for index in range(3):
        with session_factory() as claim_session:
            claim = AcquisitionRepository(claim_session).claim_next(
                worker_id=f"system:acquisition-worker@test#material-gate-{index}",
                lease_for=timedelta(minutes=5),
            )
            assert claim is not None
            claim_session.commit()
        runner.run_claim(claim)

    session.expire_all()
    links = list(
        session.scalars(
            select(EvidenceLink).where(
                EvidenceLink.review_state == "automatically_admitted"
            )
        )
    )
    assert len(links) == 1
    decision = session.get(
        AutomaticAdmissionDecision, links[0].automatic_admission_decision_id
    )
    assert decision is not None and decision.outcome == "admitted"
    source = session.get(SourceStatement, links[0].source_statement_id)
    assert source is not None
    source_span = session.get(SourceSpan, source.source_span_id)
    assert source_span is not None
    assert source_span.document_version_id == uuid.UUID(
        next(
            job.request_snapshot["document_version_id"]
            for job in session.scalars(
                select(AcquisitionJob).where(
                    AcquisitionJob.research_run_id == run.id
                )
            )
        )
    )

    from app.models.ledger import AIRun
    audits = list(session.scalars(select(AIRun).where(AIRun.kind == "extract")))
    assert audits
    for audit in audits:
        assert audit.input_ref["research_run_id"] == str(run.id)
        assert audit.input_ref["research_case_id"] == str(run.research_case_id)


def test_material_contract_rejection_is_durable_and_produces_no_evidence(
    session,
) -> None:
    from app.repositories.acquisition import AcquisitionRepository
    from app.schemas.v1.event_research import CreateEventResearchRequest
    from app.services.acquisition_runner import AcquisitionRunner
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_research import EventResearchService

    class NoClaimsClient:
        model_version = "no-claims-v1"

        def chat_json(self, messages, schema_hint=""):
            return {"statements": []}

    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="收入 100，利润 20，订单 30。",
            source_type="pasted_snapshot",
            source_metadata={
                "authority_level": "user_supplied",
                "intake_role": "provided_material",
                "input_kind": "material",
                "permissions": {
                    "ai_processing": True,
                    "display": False,
                    "export": False,
                    "api": False,
                },
            },
            event_title="用户材料",
            research_question="材料说明了什么？",
            candidate_factors=["收入", "利润", "订单"],
            research_protocol_required=False,
            created_by="tenant:team-a",
        ),
        tenant_id="team-a",
        workflow_mode="automatic",
    )
    assert created.run_id is not None
    run = session.get(ResearchRun, uuid.UUID(created.run_id))
    assert run is not None
    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"
    material_job = session.scalar(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    )
    assert material_job is not None
    session.commit()

    session_factory = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )
    with session_factory() as claim_session:
        claim = AcquisitionRepository(claim_session).claim_next(
            worker_id="system:acquisition-worker@test#material-contract-reject",
            lease_for=timedelta(minutes=5),
        )
        assert claim is not None
        claim_session.commit()
    AcquisitionRunner(
        session_factory, adapters={}, llm_client=NoClaimsClient()
    ).run_claim(claim)

    session.expire_all()
    rejected_job = session.get(AcquisitionJob, claim.job_id)
    assert rejected_job is not None
    assert rejected_job.status == "failed"
    exception = session.scalar(
        select(AcquisitionException).where(
            AcquisitionException.job_id == claim.job_id
        )
    )
    assert exception is not None
    assert exception.reason_code == "intake_material_contract_rejected"
    assert session.scalar(
        select(func.count()).select_from(EvidenceLink)
    ) == 0


def test_material_dispatch_rejects_cross_case_task_before_creating_lineage(
    session,
) -> None:
    from app.services.automatic_research_intake import AutomaticResearchIntakeService
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    started = AutomaticResearchIntakeService(
        session, extractor=_MaterialIntakeExtractor()
    ).start("收入 100，利润 20，订单 30。", tenant_id="team-a")
    run = session.get(ResearchRun, uuid.UUID(started.run_id))
    assert run is not None
    other_case = ResearchCase(
        title="other",
        industry_topic="other",
        created_by="tenant:team-a",
        created_at=datetime.now(timezone.utc),
    )
    session.add(other_case)
    session.flush()
    material_task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "intake_material",
        )
    )
    assert material_task is not None
    material_task.research_case_id = other_case.id

    with pytest.raises(ValueError, match="task matrix|run case"):
        AutomaticResearchPipeline(session).advance(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_uploaded_material_runner_reads_the_frozen_original_not_the_brief(
    session,
) -> None:
    from app.repositories.acquisition import AcquisitionRepository
    from app.schemas.v1.event_research import CreateEventResearchRequest
    from app.services.acquisition_runner import AcquisitionRunner
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_research import (
        EventResearchService,
        InitialUploadedOriginal,
    )

    class NoClaimsClient:
        model_version = "no-claims-v1"

        def chat_json(self, messages, schema_hint=""):
            return {"statements": []}

    original = b"Example Corp 2026-08-12 Revenue was 100 USD."
    created = EventResearchService(session).create(
        CreateEventResearchRequest(
            raw_input="This is only the upload summary.",
            source_type="uploaded_file",
            source_metadata={
                "authority_level": "user_supplied",
                "intake_role": "provided_material",
                "input_kind": "material",
                "permissions": {"ai_processing": True, "display": True},
            },
            event_title="Uploaded material",
            company_name="Example Corp",
            research_question="What does the upload establish?",
            candidate_factors=["Revenue", "Margin", "Orders"],
            research_protocol_required=False,
            created_by="tenant:team-a",
        ),
        tenant_id="team-a",
        workflow_mode="automatic",
        initial_uploaded_original=InitialUploadedOriginal(
            raw=original,
            file_name="material.txt",
            mime_type="text/plain",
            source_metadata={
                "authority_level": "user_supplied",
                "permissions": {"ai_processing": True, "display": True},
            },
        ),
    )
    assert created.run_id is not None
    run = session.get(ResearchRun, uuid.UUID(created.run_id))
    assert run is not None
    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"
    session.commit()

    session_factory = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )
    with session_factory() as claim_session:
        claim = AcquisitionRepository(claim_session).claim_next(
            worker_id="system:acquisition-worker@test#uploaded-material",
            lease_for=timedelta(minutes=5),
        )
        assert claim is not None
        claim_session.commit()
    AcquisitionRunner(
        session_factory, adapters={}, llm_client=NoClaimsClient()
    ).run_claim(claim)

    session.expire_all()
    artifact = session.scalar(
        select(RetrievalArtifact)
        .join(
            SourceReference,
            SourceReference.id == RetrievalArtifact.source_reference_id,
        )
        .where(SourceReference.job_id == claim.job_id)
    )
    assert artifact is not None
    assert artifact.raw_bytes == original
    assert artifact.mime_type == "text/plain"


def test_material_prepare_checkpoint_is_reused_after_worker_restart(session) -> None:
    from app.repositories.acquisition import AcquisitionRepository
    from app.services.acquisition_runner import AcquisitionRunner
    from app.services.automatic_research_intake import AutomaticResearchIntakeService
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    class NoClaimsClient:
        model_version = "no-claims-v1"

        def chat_json(self, messages, schema_hint=""):
            return {"statements": []}

    started = AutomaticResearchIntakeService(
        session, extractor=_MaterialIntakeExtractor()
    ).start("收入 100，利润 20，订单 30。", tenant_id="team-a")
    run = session.get(ResearchRun, uuid.UUID(started.run_id))
    assert run is not None
    assert AutomaticResearchPipeline(session).advance(run) == "waiting_for_sources"
    session.commit()
    session_factory = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )
    runner = AcquisitionRunner(
        session_factory, adapters={}, llm_client=NoClaimsClient()
    )
    with session_factory() as claim_session:
        claim = AcquisitionRepository(claim_session).claim_next(
            worker_id="system:acquisition-worker@test#material-checkpoint",
            lease_for=timedelta(minutes=5),
        )
        assert claim is not None
        claim_session.commit()
    contract = runner._load_contract(claim)
    assert contract is not None
    request, _policy, stage = contract
    assert stage == "searching"

    assert runner._prepare_intake_material(claim, request)
    assert runner._prepare_intake_material(claim, request)

    session.expire_all()
    assert session.scalar(
        select(func.count())
        .select_from(AcquisitionAttempt)
        .where(AcquisitionAttempt.job_id == claim.job_id)
    ) == 1
    assert session.scalar(
        select(func.count())
        .select_from(RetrievalArtifact)
        .join(
            SourceReference,
            SourceReference.id == RetrievalArtifact.source_reference_id,
        )
        .where(SourceReference.job_id == claim.job_id)
    ) == 1


class _WorkerChainExtractor:
    def extract(self, *, raw_input: str, source_url: str | None) -> EventExtraction:
        assert source_url is None
        return EventExtraction(
            event_title=raw_input,
            company_name="Example Corp",
            ticker="600001",
            event_at=None,
            market_reaction=None,
            summary=None,
            research_question="Example Corp Revenue 是否继续增长？",
            candidate_factors=("Revenue", "Margin", "Orders"),
            input_kind="material",
        )


class _WorkerChainExtractionClient:
    model_version = "fake-worker-chain-extractor-v1"

    def chat_json(self, messages, schema_hint=""):
        assert schema_hint == "extract"
        payload = json.loads(messages[-1]["content"])
        statements = []
        for span in payload["spans"]:
            quote = span["verbatim_text"]
            metric = next(
                (
                    value
                    for value in ("Revenue", "Margin", "Orders")
                    if value in quote
                ),
                None,
            )
            if metric is None:
                continue
            statements.append(
                {
                    "span_id": span["span_id"],
                    "kind": "disclosed_fact",
                    "quote": quote,
                    "quote_start": 0,
                    "quote_end": len(quote),
                    "normalized_text": quote,
                    "assertion_actor": "Example Corp",
                    "subject": "Example Corp",
                    "predicate": metric,
                    "object_text": "100 USD",
                    "numeric_value": "100",
                    "unit": "USD",
                    "observed_period": "2026-08-12",
                    "scope": {"company": "Example Corp", "metric": metric},
                }
            )
        return {"statements": statements}


class _WorkerChainAdapter(SourceAdapter):
    def __init__(self, adapter_key: str) -> None:
        self._descriptor = SourceDescriptor(
            adapter_key=adapter_key,
            provider_identity=f"Worker chain {adapter_key}",
            allowed_schemes=frozenset({"https"}),
            allowed_hosts=frozenset(
                {
                    "www.sse.com.cn"
                    if adapter_key == "sse"
                    else "www.szse.cn"
                    if adapter_key == "szse"
                    else "licensed.example.test"
                }
            ),
            allowed_source_roles=frozenset({"company_disclosure"}),
        )

    @property
    def descriptor(self) -> SourceDescriptor:
        return self._descriptor

    def search(self, query: str, cutoff: datetime):
        if self.descriptor.adapter_key != "sse" or "支持 增长 改善" not in query:
            return ()
        metric = next(
            value for value in ("Revenue", "Margin", "Orders") if value in query
        )
        reference = SourceReferenceValue(
            adapter_key="sse",
            external_record_id=f"worker-chain-{metric.casefold()}",
            external_version="v1",
            canonical_url="https://www.sse.com.cn/disclosure.txt",
            title=f"Example Corp {metric} disclosure",
            published_at=datetime(2026, 8, 13, 8, tzinfo=timezone.utc),
            source_role="company_disclosure",
            fetch_locator={
                "canonical_url": "https://www.sse.com.cn/disclosure.txt"
            },
            metadata={
                "provider_identity": "Shanghai Stock Exchange",
                "security_code": "600001",
            },
        )
        if metric == "Orders":
            return (RetrievedSearchResult(reference, self._envelope(reference)),)
        return (reference,)

    def fetch(self, reference: SourceReferenceValue) -> RetrievedEnvelope:
        assert self.descriptor.adapter_key == "sse"
        return self._envelope(reference)

    @staticmethod
    def _envelope(reference: SourceReferenceValue) -> RetrievedEnvelope:
        metric = reference.external_record_id.removeprefix("worker-chain-").title()
        assert metric in {"Revenue", "Margin", "Orders"}
        content = f"Example Corp 2026-08-12 {metric} was 100 USD.".encode()
        return RetrievedEnvelope(
            content=content,
            mime_type="text/plain; charset=utf-8",
            final_url="https://www.sse.com.cn/disclosure.txt",
            etag='"worker-chain-v1"',
            last_modified="Thu, 13 Aug 2026 08:00:00 GMT",
            provider_request_id="worker-chain-request-1",
            metadata={
                "adapter_key": "sse",
                "external_record_id": reference.external_record_id,
                "provider_identity": "Shanghai Stock Exchange",
            },
        )

    def restore_reference(self, reference: SourceReferenceValue) -> None:
        self.descriptor.validate_reference(reference)

    def close(self) -> None:
        return None


@pytest.mark.parametrize(
    ("raw_material", "expected_job_kinds", "expected_links", "expected_decisions"),
    [
        (
            "Example Corp 2026-08-12 Revenue was 100 USD.",
            {"intake_material", "external_gap"},
            3,
            8,
        ),
        (
            "User supplied material without quantified evidence.",
            {"external_gap"},
            3,
            3,
        ),
    ],
)
def test_one_click_worker_chain_resumes_from_persisted_wait_and_completes(
    tmp_path,
    monkeypatch,
    raw_material,
    expected_job_kinds,
    expected_links,
    expected_decisions,
) -> None:
    from app.models.ledger import Base
    from app.models.research_preparation import ResearchPreparation
    from app.repositories.acquisition import AcquisitionRepository
    from app.scripts import run_acquisition_worker, run_research_worker
    from app.services.acquisition_runner import AcquisitionRunner
    from app.services.automatic_research_intake import AutomaticResearchIntakeService

    engine = create_engine(
        f"sqlite:///{tmp_path / 'automatic-worker-chain.db'}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True, expire_on_commit=False)

    with session_local() as intake_session:
        started = AutomaticResearchIntakeService(
            intake_session, extractor=_WorkerChainExtractor()
        ).start(
            raw_material,
            tenant_id="team-a",
        )
        run_id = uuid.UUID(started.run_id)
        case_id = uuid.UUID(started.case_id)
        intake_session.commit()

    monkeypatch.setattr(run_research_worker, "SessionLocal", session_local)
    assert run_research_worker.run_once()

    # A fresh session sees the durable park state before any source worker runs.
    with session_local() as waiting_session:
        waiting_run = waiting_session.get(ResearchRun, run_id)
        waiting_lifecycle = waiting_session.get(EventResearchLifecycle, case_id)
        waiting_job = waiting_session.scalar(
            select(Job).where(
                Job.target_type == "research_run", Job.target_id == run_id
            )
        )
        assert waiting_run is not None
        assert waiting_run.status == "waiting_for_sources"
        assert waiting_lifecycle is not None
        assert waiting_lifecycle.status == "researching"
        assert waiting_lifecycle.next_human_action is None
        assert waiting_job is not None
        assert waiting_job.status == "waiting_for_sources"
        initial_source_jobs = list(
            waiting_session.scalars(
                select(AcquisitionJob).where(
                    AcquisitionJob.research_run_id == run_id
                )
            )
        )
        assert len(initial_source_jobs) == 3
        assert {
            job.request_snapshot["acquisition_kind"]
            for job in initial_source_jobs
        } == {"intake_material"}
        assert waiting_run.budget_used == 0

    runner = AcquisitionRunner(
        session_local,
        adapters={
            key: _WorkerChainAdapter(key) for key in B_SCOPE_POLICY.enabled_adapter_keys
        },
        llm_client=_WorkerChainExtractionClient(),
    )
    material_jobs_processed = 0
    while run_acquisition_worker.run_once(
        runner=runner,
        session_factory=session_local,
        worker_id="system:acquisition-worker@test#worker-chain",
        lease_for=timedelta(minutes=5),
    ):
        material_jobs_processed += 1
    assert material_jobs_processed == 3

    # A fresh research session consumes material results before it opens any
    # external evidence-gap jobs.
    assert run_research_worker.run_once()
    with session_local() as external_wait_session:
        persisted_run = external_wait_session.get(ResearchRun, run_id)
        external_jobs = list(
            external_wait_session.scalars(
                select(AcquisitionJob).where(
                    AcquisitionJob.research_run_id == run_id,
                    AcquisitionJob.request_snapshot["acquisition_kind"].as_string()
                    == "external_gap",
                )
            )
        )
        assert persisted_run is not None and persisted_run.budget_used == 9
        assert len(external_jobs) == 9

    external_jobs_processed = 0
    while run_acquisition_worker.run_once(
        runner=runner,
        session_factory=session_local,
        worker_id="system:acquisition-worker@test#worker-chain",
        lease_for=timedelta(minutes=5),
    ):
        external_jobs_processed += 1
    assert external_jobs_processed == 9

    # A third research-worker call resumes only from all committed source
    # terminal states and writes the final snapshot/conclusion.
    assert run_research_worker.run_once()

    with session_local() as final_session:
        run = final_session.get(ResearchRun, run_id)
        lifecycle = final_session.get(EventResearchLifecycle, case_id)
        conclusion = final_session.scalar(
            select(EventResearchConclusion).where(
                EventResearchConclusion.research_case_id == case_id
            )
        )
        source_jobs = list(
            final_session.scalars(
                select(AcquisitionJob).where(AcquisitionJob.research_run_id == run_id)
            )
        )
        admitted_links = list(
            final_session.scalars(
                select(EvidenceLink).where(
                    EvidenceLink.review_state == "automatically_admitted"
                )
            )
        )
        admission_decisions = list(final_session.scalars(select(AutomaticAdmissionDecision)))
        open_tasks = list(
            final_session.scalars(
                select(TaskItem).where(TaskItem.status.in_(("open", "in_progress")))
            )
        )

        assert run is not None and run.status == "succeeded"
        assert lifecycle is not None and lifecycle.status == "completed"
        assert lifecycle.next_human_action is None
        assert conclusion is not None
        assert conclusion.state == "system_generated"
        assert conclusion.reviewer is None
        assert set(conclusion.evidence_link_ids) == {
            str(link.id) for link in admitted_links
        }
        assert len(source_jobs) == 12
        assert all(
            job.status in {"succeeded", "partial", "failed"}
            for job in source_jobs
        )
        assert len(admitted_links) == expected_links
        admitted_job_kinds = {
            final_session.get(AcquisitionJob, decision.job_id).request_snapshot[
                "acquisition_kind"
            ]
            for link in admitted_links
            if (
                decision := final_session.get(
                    AutomaticAdmissionDecision,
                    link.automatic_admission_decision_id,
                )
            )
            is not None
        }
        assert admitted_job_kinds == expected_job_kinds
        assert all(link.creator_type == "ai" for link in admitted_links)
        assert all(
            link.automatic_admission_decision_id is not None
            for link in admitted_links
        )
        assert len(admission_decisions) == expected_decisions
        expected_outcomes = (
            {"admitted", "quarantined"}
            if "intake_material" in expected_job_kinds
            else {"admitted"}
        )
        assert {decision.outcome for decision in admission_decisions} == expected_outcomes
        assert open_tasks == []
        assert list(final_session.scalars(select(ResearchPreparation))) == []
        assert list(final_session.scalars(select(Proposal))) == []
        assert list(final_session.scalars(select(AtomicClaimReview))) == []
        assert list(final_session.scalars(select(ReviewDecision))) == []



def _automatic_run(
    session,
    *,
    factor: str = "需求增长",
    factors: list[str] | None = None,
    plan_mutation=None,
    max_rounds: int = 3,
    budget: int = 100,
) -> ResearchRun:
    factor_statements = factors or [factor]
    now = datetime.now(timezone.utc)
    case = ResearchCase(
        title="需求事件",
        industry_topic="事件研究",
        created_by="tenant:team-a",
        created_at=now,
    )
    document = DocumentVersion(
        content_sha256=uuid.uuid4().hex,
        source_url="event://automatic-test",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add_all([case, document])
    session.flush()
    session.add_all(
        [
            CaseTenantAdmission(
                research_case_id=case.id,
                tenant_id="team-a",
                initial_document_version_id=document.id,
                admitted_by="tenant:team-a",
                admitted_at=now,
            ),
            EventResearchBrief(
                research_case_id=case.id,
                raw_input="公司披露需求与订单变化。",
                source_type="pasted_snapshot",
                event_title="需求事件",
                company_name="示例公司",
                ticker="600000",
                research_question="需求能否持续增长？",
                workflow_mode="automatic",
                extraction_state="system_generated",
                created_at=now,
            ),
        ]
    )
    theses = [
        Thesis(
            research_case_id=case.id,
            statement=statement,
            created_by="system:automatic-intake",
            creator_type="ai",
            review_state="draft",
            created_at=now,
        )
        for statement in factor_statements
    ]
    session.add_all(theses)
    session.flush()
    repo = AutoResearchRepository(session)
    run = repo.create_run(
        research_case_id=case.id,
        scope_thesis_ids=[str(thesis.id) for thesis in theses],
        max_rounds=max_rounds,
        budget=budget,
    )
    scope = EventResearchScopeVersion(
        research_case_id=case.id,
        version=1,
        changed_by="system:automatic-intake",
        change_summary="automatic scope",
        created_at=now,
    )
    session.add(scope)
    session.flush()
    session.add_all(
        [
            *[
                EventResearchScopeFactor(
                    scope_version_id=scope.id,
                    statement=statement,
                    position=position,
                )
                for position, statement in enumerate(factor_statements)
            ],
            EventResearchLifecycle(
                research_case_id=case.id,
                status="researching",
                active_run_id=run.id,
                current_round=0,
                status_summary="automatic research running",
                current_gap=None,
                next_human_action=None,
                updated_at=now,
            ),
        ]
    )
    scope_payload = {
        "workflow_mode": "automatic",
        "factor_ids": [str(thesis.id) for thesis in theses],
        "factor_statements": factor_statements,
        "automatic_protocol": {
            "generated_by": "system",
            "research_question": case.title,
            "factors": factor_statements,
        },
        "budget": run.budget,
        "automatic_evidence_plan": {
            "max_rounds": run.max_rounds,
            "budget": run.budget,
            "items": [
                {
                    "factor": statement,
                    "objectives": [
                        "support",
                        "contradict",
                        "alternative_explanation",
                    ],
                    "allowed_source_roles": [
                        "company_disclosure",
                        "licensed_provider",
                    ],
                }
                for statement in factor_statements
            ]
        },
        "allowed_source_types": [],
        "monitor_version_id": None,
        "frequency": None,
        "next_verification_event": None,
        "configured_by": None,
        "configuration_change_reason": None,
    }
    if plan_mutation is not None:
        plan_mutation(scope_payload)
    ResearchRunEventRepository(session).append(
        run.id,
        stage="scope",
        status="completed",
        message="scope frozen",
        payload_json=scope_payload,
    )
    for thesis in theses:
        for task_type in ("support", "contradict", "result", "alternative"):
            repo.create_task(
                run_id=run.id,
                research_case_id=case.id,
                thesis_id=thesis.id,
                task_type=task_type,
                query=f"{task_type}: {thesis.statement}",
            )
    repo.enqueue_run_job(run)
    return run


def _admit_link(
    session,
    job: AcquisitionJob,
    *,
    link_thesis_id: uuid.UUID | None = None,
) -> EvidenceLink:
    """Persist one coherent automatic-admission lineage for a source job."""
    now = datetime.now(timezone.utc)
    raw = f"admitted-{job.id}-{uuid.uuid4()}".encode()
    sha = hashlib.sha256(raw).hexdigest()
    adapter_key = f"test-{uuid.uuid4()}"
    document = DocumentVersion(
        content_sha256=sha,
        source_url=f"https://example.test/{job.id}",
        available_at=now,
        acquired_at=now,
        parser_version="test",
    )
    session.add(document)
    session.flush()
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text="自动采集证据",
    )
    attempt = AcquisitionAttempt(
        job_id=job.id,
        adapter_key=adapter_key,
        operation="fetch",
        attempt_no=1,
        started_at=now,
        finished_at=now,
        outcome="succeeded",
        retryable=False,
        safe_metadata={},
    )
    reference = SourceReference(
        job_id=job.id,
        adapter_key=adapter_key,
        external_record_id=str(job.id),
        external_version="v1",
        canonical_url=document.source_url,
        title="自动采集证据",
        published_at=now,
        source_role="company_disclosure",
        metadata_json={},
        created_at=now,
    )
    session.add_all([span, attempt, reference])
    session.flush()
    candidate = AtomicClaimCandidate(
        source_span_id=span.id,
        canonical_key=uuid.uuid4().hex,
        quote="自动采集证据",
        quote_start=0,
        quote_end=6,
        quote_sha256=hashlib.sha256("自动采集证据".encode()).hexdigest(),
        normalized_text="自动采集证据",
        claim_type="reported_claim",
        authority_level="primary",
        structured_fields={},
        validation_result={},
        created_at=now,
    )
    artifact = RetrievalArtifact(
        source_reference_id=reference.id,
        attempt_id=attempt.id,
        content_sha256=sha,
        raw_bytes=raw,
        mime_type="text/plain",
        byte_size=len(raw),
        final_url=document.source_url,
        retrieved_at=now,
    )
    session.add_all([candidate, artifact])
    session.flush()
    session.add(
        RetrievalArtifactDocument(
            retrieval_artifact_id=artifact.id,
            document_version_id=document.id,
            relation="created",
            publication_key=sha,
            created_at=now,
        )
    )
    decision = AutomaticAdmissionDecision(
        job_id=job.id,
        candidate_id=candidate.id,
        retrieval_artifact_id=artifact.id,
        outcome="admitted",
        gate_version="test-v1",
        policy_version=B_SCOPE_POLICY.version,
        gate_results={},
        created_at=now,
    )
    session.add(decision)
    session.flush()
    statement = SourceStatement(
        source_span_id=span.id,
        kind="disclosed_fact",
        normalized_text="自动采集证据",
        atomic_claim_candidate_id=candidate.id,
        automatic_admission_decision_id=decision.id,
        created_at=now,
    )
    session.add(statement)
    session.flush()
    link = EvidenceLink(
        thesis_id=link_thesis_id or job.thesis_id,
        source_statement_id=statement.id,
        role=job.request_snapshot["target_link_role"],
        reason="automatic admission",
        scope={},
        available_at=now,
        creator_type="ai",
        review_state="automatically_admitted",
        automatic_admission_decision_id=decision.id,
        created_at=now,
    )
    session.add(link)
    session.flush()
    return link


class _AssessmentGenerator:
    def __init__(
        self,
        *,
        conclusion="supported",
        gaps=None,
        before_persist_hook=None,
        displayed_as_provisional=True,
        creator_type="ai",
    ):
        self.conclusion = conclusion
        self.gaps = list(gaps or [])
        self.before_persist_hook = before_persist_hook
        self.displayed_as_provisional = displayed_as_provisional
        self.creator_type = creator_type
        self.calls = []

    def generate(
        self,
        thesis_id,
        cutoff,
        session,
        *,
        evidence_link_ids=None,
        before_persist=None,
    ):
        self.calls.append(
            {
                "thesis_id": thesis_id,
                "evidence_link_ids": list(evidence_link_ids or []),
            }
        )
        # Match AssessmentGenerator's provider boundary: reads and any caller
        # work are committed before the external call returns.
        session.commit()
        if self.before_persist_hook is not None:
            self.before_persist_hook(session)
        if before_persist is not None and not before_persist():
            return None
        snapshot = EvidenceSnapshot(
            thesis_id=thesis_id,
            cutoff=cutoff,
            evidence_link_ids=[str(value) for value in (evidence_link_ids or [])],
            created_at=cutoff,
        )
        session.add(snapshot)
        session.flush()
        assessment = AIAssessment(
            snapshot_id=snapshot.id,
            conclusion=self.conclusion,
            rationale="当前证据的暂定判断",
            gaps=self.gaps,
            displayed_as_provisional=self.displayed_as_provisional,
            creator_type=self.creator_type,
            model_version="test",
            created_at=cutoff,
        )
        session.add(assessment)
        session.flush()
        return assessment


def _scope_event(session, run: ResearchRun) -> ResearchRunEvent:
    event = session.scalar(
        select(ResearchRunEvent)
        .where(ResearchRunEvent.run_id == run.id)
        .where(ResearchRunEvent.stage == "scope")
        .order_by(ResearchRunEvent.seq.desc())
        .limit(1)
    )
    assert event is not None
    return event


def test_pipeline_uses_local_protocol_for_assessment_generator_dependency() -> None:
    from typing import get_type_hints

    from app.services import automatic_research_pipeline as pipeline_module

    protocol = getattr(pipeline_module, "_AssessmentGeneratorProtocol", None)
    assert protocol is not None
    constructor_hints = get_type_hints(
        pipeline_module.AutomaticResearchPipeline.__init__
    )
    generator_hints = get_type_hints(
        pipeline_module.AutomaticResearchPipeline._generator
    )
    assert constructor_hints["assessment_generator"] == protocol | None
    assert generator_hints["return"] is protocol


def test_dispatch_sources_queues_three_frozen_idempotent_jobs(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    old_task_time = datetime.now(timezone.utc) - timedelta(days=1)
    for task in session.scalars(
        select(ResearchTask)
        .where(ResearchTask.run_id == run.id)
        .where(ResearchTask.task_type != "result")
    ):
        task.updated_at = old_task_time
    first = AutomaticResearchPipeline(session).dispatch_sources(run)
    second = AutomaticResearchPipeline(session).dispatch_sources(run)

    jobs = list(
        session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type != "result")
        )
    )
    assert first == second == "waiting_for_sources"
    assert run.round == 1
    assert len(jobs) == 3
    assert {job.request_snapshot["objective"] for job in jobs} == {
        "support",
        "contradict",
        "alternative_explanation",
    }
    assert {job.research_run_id for job in jobs} == {run.id}
    assert all(job.request_snapshot["cutoff"] for job in jobs)
    assert all(job.request_snapshot["round"] == 1 for job in jobs)
    assert all(
        job.idempotency_key
        == (
            f"automatic:{run.id}:1:{job.thesis_id}:"
            f"{job.request_snapshot['objective']}"
        )
        for job in jobs
    )
    assert all(job.request_snapshot["entity_names"] == ["示例公司"] for job in jobs)
    assert all(job.request_snapshot["security_codes"] == ["600000"] for job in jobs)
    assert all(job.request_snapshot["metric_terms"] == ["需求增长"] for job in jobs)
    assert all(
        job.request_snapshot["allowed_source_roles"]
        == sorted(B_SCOPE_POLICY.allowed_source_roles)
        for job in jobs
    )
    assert {
        (job.request_snapshot["objective"], job.request_snapshot["target_link_role"])
        for job in jobs
    } == {
        ("support", "supports"),
        ("contradict", "contradicts"),
        ("alternative_explanation", "contextualizes"),
    }
    assert all(task.stage == "acquire" for task in tasks)
    assert all(
        task.updated_at.replace(tzinfo=None) > old_task_time.replace(tzinfo=None)
        for task in tasks
    )
    assert all(task.result and task.result["acquisition_job_id"] for task in tasks)
    assert {task.result["objective"] for task in tasks if task.result} == {
        "support",
        "contradict",
        "alternative_explanation",
    }
    waiting_events = list(
        session.scalars(
            select(ResearchRunEvent).where(
                ResearchRunEvent.run_id == run.id,
                ResearchRunEvent.stage == "retrieve",
                ResearchRunEvent.status == "waiting",
            )
        )
    )
    assert len(waiting_events) == 1


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda scope: scope.pop("automatic_evidence_plan"), "evidence plan"),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"factor": "不匹配因素"}
            ),
            "factor",
        ),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"objectives": ["support", "unknown"]}
            ),
            "objective",
        ),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"objectives": ["support", "contradict"]}
            ),
            "objective missing",
        ),
        (
            lambda scope: scope["automatic_evidence_plan"]["items"][0].update(
                {"allowed_source_roles": ["uploaded_file"]}
            ),
            "source roles",
        ),
    ],
)
def test_dispatch_sources_fails_closed_for_invalid_frozen_plan(
    session, mutation, message: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, plan_mutation=mutation)

    with pytest.raises(ValueError, match=message):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rolls_back_all_requests_when_later_request_fails(
    session, monkeypatch
) -> None:
    from app.services.acquisition import AcquisitionModule
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    original_request = AcquisitionModule.request
    request_count = 0

    def fail_second_request(self, request, *, principal):
        nonlocal request_count
        request_count += 1
        if request_count == 2:
            raise RuntimeError("injected second acquisition failure")
        return original_request(self, request, principal=principal)

    monkeypatch.setattr(AcquisitionModule, "request", fail_second_request)

    with pytest.raises(RuntimeError, match="second acquisition failure"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type != "result")
        )
    )
    assert all(task.result is None and task.stage == "planned" for task in tasks)
    assert session.scalar(
        select(func.count())
        .select_from(ResearchRunEvent)
        .where(
            ResearchRunEvent.run_id == run.id,
            ResearchRunEvent.stage == "retrieve",
            ResearchRunEvent.status == "waiting",
        )
    ) == 0
    assert run.status == "queued" and run.stage == "planning"


def test_dispatch_sources_rejects_missing_required_task_before_writes(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "contradict",
        )
    )
    assert task is not None
    task.status = "cancelled"

    with pytest.raises(ValueError, match="task matrix"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_extra_objective_before_writes(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    thesis_id = uuid.UUID((run.scope_thesis_ids or [])[0])
    AutoResearchRepository(session).create_task(
        run_id=run.id,
        research_case_id=run.research_case_id,
        thesis_id=thesis_id,
        task_type="verify_rule",
        query="unexpected objective",
    )

    with pytest.raises(ValueError, match="task matrix"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_thesis_outside_frozen_scope(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    outside = Thesis(
        research_case_id=run.research_case_id,
        statement="范围外因素",
        created_by="test",
        created_at=datetime.now(timezone.utc),
    )
    session.add(outside)
    session.flush()
    AutoResearchRepository(session).create_task(
        run_id=run.id,
        research_case_id=run.research_case_id,
        thesis_id=outside.id,
        task_type="support",
        query="outside frozen scope",
    )

    with pytest.raises(ValueError, match="task matrix|frozen scope"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_cross_case_task_before_writes(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    other_case = ResearchCase(
        title="other",
        industry_topic="other",
        created_by="test",
        created_at=datetime.now(timezone.utc),
    )
    session.add(other_case)
    session.flush()
    task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "support",
        )
    )
    assert task is not None
    task.research_case_id = other_case.id

    with pytest.raises(ValueError, match="task matrix|run case"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_rejects_zero_queued_source_tasks(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    for task in session.scalars(
        select(ResearchTask)
        .where(ResearchTask.run_id == run.id)
        .where(ResearchTask.task_type != "result")
    ):
        task.status = "cancelled"

    with pytest.raises(ValueError, match="task matrix|no acquisition"):
        AutomaticResearchPipeline(session).dispatch_sources(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_dispatch_sources_does_not_resurrect_a_stale_cancelled_run(
    tmp_path,
) -> None:
    from app.models.ledger import Base
    from app.services.auto_research import AutoResearchService

    engine = create_engine(f"sqlite:///{tmp_path / 'cancel-before-dispatch.db'}", future=True)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, future=True)
    with session_local() as setup:
        run = _automatic_run(setup)
        run_id = run.id
        setup.commit()

    worker = session_local()
    try:
        stale_run = worker.get(ResearchRun, run_id)
        assert stale_run is not None and stale_run.status == "queued"
        with session_local() as cancelling:
            AutoResearchService(cancelling).cancel_run(
                run_id,
                actor="human:test",
                change_reason="cancel before dispatch lock",
            )

        with pytest.raises(ValueError, match="no longer dispatchable"):
            AutoResearchService(worker).execute(stale_run)
        worker.rollback()
    finally:
        worker.close()

    with Session(engine) as check:
        persisted = check.get(ResearchRun, run_id)
        assert persisted is not None and persisted.status == "cancelled"
        persisted_job = check.scalar(
            select(Job).where(Job.target_type == "research_run", Job.target_id == run_id)
        )
        assert persisted_job is not None and persisted_job.status == "cancelled"
        assert check.scalar(select(func.count()).select_from(AcquisitionJob)) == 0
        cancelled_tasks = list(
            check.scalars(select(ResearchTask).where(ResearchTask.run_id == run_id))
        )
        assert all(task.result is None for task in cancelled_tasks)
        assert all(task.stage == "stopped" for task in cancelled_tasks)
        assert check.scalar(
            select(func.count())
            .select_from(ResearchRunEvent)
            .where(
                ResearchRunEvent.run_id == run_id,
                ResearchRunEvent.stage == "retrieve",
                ResearchRunEvent.status == "waiting",
            )
        ) == 0


@pytest.mark.pg_only
def test_postgres_cancel_committed_in_other_session_wins_before_dispatch_lock(
    session,
) -> None:
    from app.services.auto_research import AutoResearchService

    run = _automatic_run(session)
    run_id = run.id
    session.commit()
    session_local = sessionmaker(bind=session.get_bind(), future=True)
    worker = session_local()
    try:
        stale_run = worker.get(ResearchRun, run_id)
        assert stale_run is not None and stale_run.status == "queued"
        with session_local() as cancelling:
            AutoResearchService(cancelling).cancel_run(
                run_id,
                actor="human:pg-test",
                change_reason="committed before dispatch lock",
            )
        with pytest.raises(ValueError, match="no longer dispatchable"):
            AutoResearchService(worker).execute(stale_run)
        worker.rollback()
    finally:
        worker.close()

    assert session.get(ResearchRun, run_id).status == "cancelled"
    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


@pytest.mark.parametrize(
    "last_terminal_status",
    ["succeeded", "partial", "failed", "cancelled"],
)
def test_wait_for_sources_and_requeue_only_after_all_linked_jobs_terminal(
    session, last_terminal_status: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    AutomaticResearchPipeline(session).dispatch_sources(run)
    repo = AutoResearchRepository(session)
    research_job = repo.job_for_run(run.id)
    assert research_job is not None

    repo.wait_for_sources(run, research_job)
    source_jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    assert run.status == "waiting_for_sources"
    assert run.stage == "retrieve"
    assert research_job.status == "waiting_for_sources"
    assert research_job.step == "retrieve"
    assert research_job.finished_at is None
    parked_event = session.scalar(
        select(JobEvent)
        .where(JobEvent.job_id == research_job.id)
        .order_by(JobEvent.seq.desc())
        .limit(1)
    )
    assert parked_event is not None
    assert parked_event.message == "waiting for governed acquisition jobs"
    assert repo.requeue_source_ready_runs() == 0
    assert research_job.attempt == 1

    for source_job in source_jobs[:-1]:
        source_job.status = "succeeded"
    source_jobs[-1].status = "running"
    assert repo.requeue_source_ready_runs() == 0
    assert research_job.attempt == 1

    source_jobs[-1].status = last_terminal_status
    assert repo.requeue_source_ready_runs() == 1
    assert run.status == "queued" and run.stage == "analyze"
    assert research_job.status == "queued" and research_job.step == "analyze"
    assert research_job.attempt == 2
    assert repo.requeue_source_ready_runs() == 0
    assert research_job.attempt == 2
    latest = session.scalar(
        select(JobEvent)
        .where(JobEvent.job_id == research_job.id)
        .order_by(JobEvent.seq.desc())
        .limit(1)
    )
    assert latest is not None
    assert latest.status == "queued" and latest.step == "analyze"
    assert latest.message == "sources ready"


def test_requeue_source_ready_runs_keeps_job_waiting_when_no_sources_exist(session) -> None:
    run = _automatic_run(session)
    repo = AutoResearchRepository(session)
    research_job = repo.job_for_run(run.id)
    assert research_job is not None
    repo.wait_for_sources(run, research_job)

    assert repo.requeue_source_ready_runs() == 0
    assert run.status == "waiting_for_sources"
    assert research_job.status == "waiting_for_sources"
    assert research_job.attempt == 1


def test_requeued_source_ready_job_gets_a_fresh_stale_recovery_clock(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session)
    AutomaticResearchPipeline(session).dispatch_sources(run)
    repo = AutoResearchRepository(session)
    research_job = repo.job_for_run(run.id)
    assert research_job is not None
    old_started_at = datetime.now(timezone.utc) - timedelta(hours=2)
    research_job.started_at = old_started_at
    repo.wait_for_sources(run, research_job)
    for source_job in session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        source_job.status = "succeeded"

    assert repo.requeue_source_ready_runs() == 1
    assert research_job.started_at is None

    claimed = repo.claim_next_run_job()
    assert claimed is not None and claimed.id == research_job.id
    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert claimed.started_at is not None
    assert claimed.started_at > stale_cutoff
    assert repo.recover_stale_run_jobs(before=stale_cutoff) == 0
    assert claimed.status == "running"
    assert claimed.attempt == 2


def test_recovered_research_job_gets_a_new_claim_fence(session) -> None:
    run = _automatic_run(session)
    repo = AutoResearchRepository(session)
    first = repo.claim_next_run_job()
    assert first is not None
    first_token = first.claim_token
    assert first_token
    first.started_at = datetime.now(timezone.utc) - timedelta(hours=2)

    assert repo.recover_stale_run_jobs(
        before=datetime.now(timezone.utc) - timedelta(minutes=30)
    ) == 1
    second = repo.claim_next_run_job()
    assert second is not None and second.id == first.id
    assert second.claim_token
    assert second.claim_token != first_token
    second_id = second.id
    second_token = second.claim_token
    session.commit()

    repo.record_job_completion(
        second,
        status="failed",
        step="failed",
        error="stale worker",
        expected_claim_token=first_token,
    )
    persisted = session.get(Job, second_id)
    assert persisted is not None and persisted.status == "running"
    assert persisted.claim_token == second_token


@pytest.mark.parametrize("replacement_status", ["running", "succeeded", "failed"])
def test_stale_worker_cannot_park_reclaimed_or_finished_job(
    session, replacement_status: str
) -> None:
    run = _automatic_run(session)
    repo = AutoResearchRepository(session)
    job = repo.claim_next_run_job()
    assert job is not None and job.claim_token
    original_token = job.claim_token
    job_id, run_id = job.id, run.id
    job.claim_token = "replacement-worker"
    job.status = replacement_status
    run.status = replacement_status
    session.commit()

    # Work staged by the expired worker must be discarded along with its
    # attempt to replace the newer worker's status and claim.
    run.status = "waiting_for_sources"
    repo.wait_for_sources(run, job, expected_claim_token=original_token)
    session.commit()

    persisted_job = session.get(Job, job_id)
    persisted_run = session.get(ResearchRun, run_id)
    assert persisted_job.status == replacement_status
    assert persisted_job.claim_token == "replacement-worker"
    assert persisted_run.status == replacement_status


def test_advance_completes_from_one_admitted_partial_source_without_human_gates(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    admitted = _admit_link(session, jobs[0])
    jobs[0].status, jobs[0].stage, jobs[0].admitted_count = "partial", "partial", 1
    jobs[0].exception_count = 2
    jobs[1].status, jobs[1].stage = "failed", "failed"
    jobs[2].status, jobs[2].stage = "cancelled", "cancelled"

    assert pipeline.advance(run) == "completed"
    assert run.status == "succeeded"
    assert run.stage == "complete"
    assert run.stop_reason == "automatic_completed"
    conclusion = session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == run.research_case_id
        )
    )
    assert conclusion is not None
    assert conclusion.state == "system_generated"
    assert conclusion.evidence_link_ids == [str(admitted.id)]
    assert "失败 1" in conclusion.text
    assert "取消 1" in conclusion.text
    assert "未执行 0" in conclusion.text
    assert "跳过/异常条目 2" in conclusion.text
    lifecycle = session.get(EventResearchLifecycle, run.research_case_id)
    assert lifecycle is not None
    assert lifecycle.status == "completed"
    assert lifecycle.next_human_action is None
    assert [call["thesis_id"] for call in generator.calls] == [jobs[0].thesis_id]
    assert generator.calls[0]["evidence_link_ids"] == [admitted.id]
    assert session.scalar(select(func.count()).select_from(TaskItem)) == 0

    source_tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type != "result")
        )
    )
    assert {(task.status, task.stage) for task in source_tasks} == {
        ("done", "completed"),
        ("failed", "failed"),
    }
    assert all(
        task.result
        and set(
            (
                "acquisition_job_id",
                "objective",
                "acquisition_status",
                "reference_count",
                "frozen_count",
                "admitted_count",
                "exception_count",
            )
        ).issubset(task.result)
        for task in source_tasks
    )
    assignment = session.scalar(
        select(EventResearchScopeEvidenceAssignment).where(
            EventResearchScopeEvidenceAssignment.evidence_link_id == admitted.id
        )
    )
    assert assignment is not None and assignment.disposition == "mapped"
    assert pipeline.advance(run) == "completed"
    assert session.scalar(
        select(func.count()).select_from(EventResearchConclusion)
    ) == 1
    assert session.scalar(select(func.count()).select_from(Proposal)) == 0
    assert session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0


def test_automatic_conclusion_reports_partial_source_without_admitted_evidence(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator(gaps=[])
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob)
            .where(AcquisitionJob.research_run_id == run.id)
            .order_by(AcquisitionJob.idempotency_key)
        )
    )
    _admit_link(session, jobs[0])
    jobs[0].status, jobs[0].stage, jobs[0].admitted_count = (
        "succeeded",
        "succeeded",
        1,
    )
    jobs[1].status, jobs[1].stage = "partial", "partial"
    jobs[1].admitted_count = 0
    jobs[1].exception_count = 0
    jobs[2].status, jobs[2].stage = "succeeded", "succeeded"

    assert pipeline.advance(run) == "completed"
    conclusion = session.scalar(select(EventResearchConclusion))
    assert conclusion is not None
    assert "部分完成 1" in conclusion.text
    assert "部分完成但无准入证据 1" in conclusion.text


def test_advance_fails_final_round_with_no_usable_evidence(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    for job in session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        job.status, job.stage = "failed", "failed"

    assert pipeline.advance(run) == "failed"
    assert run.status == "failed"
    assert run.stage == "failed"
    assert run.stop_reason == "no_usable_evidence"
    assert generator.calls == []
    assert session.scalar(
        select(func.count()).select_from(EventResearchConclusion)
    ) == 0


def test_advance_replenishes_once_then_fails_when_all_rounds_have_zero_evidence(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=2, budget=6)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            conclusion="insufficient_evidence", gaps=["尚无可用证据"]
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    for job in session.scalars(
        select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
    ):
        job.status, job.stage = "failed", "failed"
    assert pipeline.advance(run) == "waiting_for_sources"
    assert run.round == 2
    round_two = [
        job
        for job in session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
        if job.request_snapshot["round"] == 2
    ]
    assert len(round_two) == 3
    for job in round_two:
        job.status, job.stage = "failed", "failed"

    assert pipeline.advance(run) == "failed"
    assert run.stop_reason == "no_usable_evidence"
    assert run.budget_used == 6
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_advance_finalizes_gapped_assessment_at_round_limit(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            conclusion="insufficient_evidence", gaps=["缺少量化验证"]
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "completed"
    conclusion = session.scalar(select(EventResearchConclusion))
    assert conclusion is not None
    assert "证据不足" in conclusion.text
    assert "缺少量化验证" in conclusion.text


def test_advance_creates_exact_next_round_for_gaps_and_is_idempotent(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=2, budget=6)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            conclusion="insufficient_evidence", gaps=["缺少行业对照"]
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    round_one_jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, round_one_jobs[0])
    for index, job in enumerate(round_one_jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    round_one_jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "waiting_for_sources"
    assert run.round == 2
    assert run.status == "waiting_for_sources"
    tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .order_by(ResearchTask.round, ResearchTask.task_type)
        )
    )
    assert {task.round for task in tasks} == {1, 2}
    assert {task.task_type for task in tasks if task.round == 2} == {
        "support",
        "contradict",
        "alternative",
        "result",
    }
    assert all(
        "缺少行业对照" in task.query for task in tasks if task.round == 2
    )
    assert session.scalar(
        select(func.count())
        .select_from(AcquisitionJob)
        .where(AcquisitionJob.research_run_id == run.id)
    ) == 6

    assert pipeline.advance(run) == "waiting_for_sources"
    assert session.scalar(
        select(func.count())
        .select_from(AcquisitionJob)
        .where(AcquisitionJob.research_run_id == run.id)
    ) == 6
    assert session.scalar(
        select(func.count())
        .select_from(ResearchTask)
        .where(ResearchTask.run_id == run.id)
    ) == 8


def _advance_to_round_two(session, pipeline, run):
    assert pipeline.advance(run) == "waiting_for_sources"
    round_one_jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    round_one_link = _admit_link(session, round_one_jobs[0])
    for index, job in enumerate(round_one_jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    round_one_jobs[0].admitted_count = 1
    assert pipeline.advance(run) == "waiting_for_sources"
    assert run.round == 2
    return round_one_jobs, round_one_link


@pytest.mark.parametrize("mutation", ["binding", "request", "idempotency"])
def test_advance_round_two_revalidates_prior_round_source_provenance(
    session, mutation: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=2, budget=6)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            conclusion="insufficient_evidence", gaps=["缺少行业对照"]
        ),
    )
    round_one_jobs, _link = _advance_to_round_two(session, pipeline, run)
    prior_tasks = list(
        session.scalars(
            select(ResearchTask).where(
                ResearchTask.run_id == run.id,
                ResearchTask.round == 1,
                ResearchTask.task_type != "result",
            )
        )
    )
    if mutation == "binding":
        prior_tasks[0].result = {
            **(prior_tasks[0].result or {}),
            "acquisition_job_id": prior_tasks[1].result["acquisition_job_id"],
        }
    elif mutation == "request":
        round_one_jobs[0].request_snapshot = {
            **round_one_jobs[0].request_snapshot,
            "target_link_role": "tampered",
        }
    else:
        round_one_jobs[0].idempotency_key = f"tampered:{uuid.uuid4()}"

    with pytest.raises(ValueError, match="source.*binding|source task matrix"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_advance_rejects_extra_unbound_run_job_and_its_mapped_evidence(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_research_scope_evidence import (
        append_current_scope_evidence_assignment,
    )

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator(gaps=[])
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    admitted = _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    extra_key = f"automatic-extra:{uuid.uuid4()}"
    extra_snapshot = dict(jobs[0].request_snapshot)
    extra_snapshot["idempotency_key"] = extra_key
    extra_job = AcquisitionJob(
        tenant_id=jobs[0].tenant_id,
        research_case_id=run.research_case_id,
        thesis_id=jobs[0].thesis_id,
        research_run_id=run.id,
        idempotency_key=extra_key,
        request_snapshot=extra_snapshot,
        policy_snapshot=dict(jobs[0].policy_snapshot),
        status="succeeded",
        stage="succeeded",
        attempt=1,
        reference_count=1,
        fetched_count=1,
        frozen_count=1,
        admitted_count=1,
        exception_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(extra_job)
    session.flush()
    extra_link = _admit_link(session, extra_job)
    thesis = session.get(Thesis, extra_job.thesis_id)
    assert thesis is not None
    append_current_scope_evidence_assignment(
        session,
        case_id=run.research_case_id,
        evidence_link_id=extra_link.id,
        factor_statement=thesis.statement,
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValueError, match="source.*binding|unbound"):
        pipeline.advance(run)
    assert generator.calls == []
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0
    assert admitted.id != extra_link.id


def test_automatic_conclusion_independently_rejects_extra_unbound_run_job(
    session, monkeypatch
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_conclusion import EventConclusionService

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator(gaps=[])
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    with monkeypatch.context() as patch:
        patch.setattr(
            EventConclusionService,
            "create_automatic_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("stop before conclusion")
            ),
        )
        with pytest.raises(RuntimeError, match="stop before conclusion"):
            pipeline.advance(run)

    extra_key = f"automatic-extra:{uuid.uuid4()}"
    extra_snapshot = dict(jobs[0].request_snapshot)
    extra_snapshot["idempotency_key"] = extra_key
    session.add(
        AcquisitionJob(
            tenant_id=jobs[0].tenant_id,
            research_case_id=run.research_case_id,
            thesis_id=jobs[0].thesis_id,
            research_run_id=run.id,
            idempotency_key=extra_key,
            request_snapshot=extra_snapshot,
            policy_snapshot=dict(jobs[0].policy_snapshot),
            status="succeeded",
            stage="succeeded",
            attempt=1,
            reference_count=0,
            fetched_count=0,
            frozen_count=0,
            admitted_count=0,
            exception_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    )
    session.flush()

    with pytest.raises(ValidationFailedError, match="source binding"):
        EventConclusionService(session).create_automatic_result(
            run.research_case_id, run.id
        )
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_automatic_conclusion_rejects_cross_thesis_link_from_valid_bound_job(
    session, monkeypatch
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_conclusion import EventConclusionService
    from app.services.event_research_scope_evidence import (
        append_current_scope_evidence_assignment,
    )

    run = _automatic_run(
        session, factors=["需求增长", "利润改善"], max_rounds=1, budget=6
    )
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator(gaps=[])
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    thesis_ids = [uuid.UUID(value) for value in run.scope_thesis_ids or []]
    admitted_jobs = {
        thesis_id: next(job for job in jobs if job.thesis_id == thesis_id)
        for thesis_id in thesis_ids
    }
    for job in jobs:
        job.status, job.stage = "failed", "failed"
    for job in admitted_jobs.values():
        _admit_link(session, job)
        job.status, job.stage, job.admitted_count = "succeeded", "succeeded", 1
    with monkeypatch.context() as patch:
        patch.setattr(
            EventConclusionService,
            "create_automatic_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("stop before conclusion")
            ),
        )
        with pytest.raises(RuntimeError, match="stop before conclusion"):
            pipeline.advance(run)

    first_job = admitted_jobs[thesis_ids[0]]
    second_thesis = session.get(Thesis, thesis_ids[1])
    assert second_thesis is not None
    cross_link = _admit_link(
        session,
        first_job,
        link_thesis_id=second_thesis.id,
    )
    append_current_scope_evidence_assignment(
        session,
        case_id=run.research_case_id,
        evidence_link_id=cross_link.id,
        factor_statement=second_thesis.statement,
        created_at=datetime.now(timezone.utc),
    )
    second_result = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
            ResearchTask.thesis_id == second_thesis.id,
        )
    )
    assert second_result is not None and isinstance(second_result.result, dict)
    original_assessment = session.get(
        AIAssessment, uuid.UUID(second_result.result["assessment_id"])
    )
    assert original_assessment is not None
    original_snapshot = session.get(EvidenceSnapshot, original_assessment.snapshot_id)
    assert original_snapshot is not None
    tampered_snapshot = EvidenceSnapshot(
        thesis_id=second_thesis.id,
        cutoff=datetime.now(timezone.utc),
        evidence_link_ids=[*original_snapshot.evidence_link_ids, str(cross_link.id)],
        created_at=datetime.now(timezone.utc),
    )
    session.add(tampered_snapshot)
    session.flush()
    tampered_assessment = AIAssessment(
        snapshot_id=tampered_snapshot.id,
        conclusion=original_assessment.conclusion,
        rationale=original_assessment.rationale,
        gaps=list(original_assessment.gaps or []),
        displayed_as_provisional=True,
        creator_type="ai",
        model_version="test",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tampered_assessment)
    session.flush()
    second_result.result = {
        **second_result.result,
        "assessment_id": str(tampered_assessment.id),
    }

    with pytest.raises(ValidationFailedError, match="evidence snapshot differs"):
        EventConclusionService(session).create_automatic_result(
            run.research_case_id, run.id
        )
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_automatic_conclusion_limitations_use_validated_source_jobs(
    session, monkeypatch
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_conclusion import EventConclusionService

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator(gaps=[])
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    jobs[0].status, jobs[0].stage = "partial", "partial"
    jobs[0].admitted_count = 1
    jobs[0].exception_count = 2
    for job in jobs[1:]:
        job.status, job.stage = "failed", "failed"
    with monkeypatch.context() as patch:
        patch.setattr(
            EventConclusionService,
            "create_automatic_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("stop before conclusion")
            ),
        )
        with pytest.raises(RuntimeError, match="stop before conclusion"):
            pipeline.advance(run)

    for task in session.scalars(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type != "result",
        )
    ):
        task.result = {
            **(task.result or {}),
            "acquisition_status": "succeeded",
            "admitted_count": 99,
            "exception_count": 0,
        }

    conclusion = EventConclusionService(session).create_automatic_result(
        run.research_case_id, run.id
    )
    assert "失败 2" in conclusion.text
    assert "部分完成 1" in conclusion.text
    assert "跳过/异常条目 2" in conclusion.text


def test_assessment_output_slot_rejects_source_binding_mutation_during_provider(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)

    def mutate_source_binding(provider_session) -> None:
        source_tasks = list(
            provider_session.scalars(
                select(ResearchTask)
                .where(ResearchTask.run_id == run.id)
                .where(ResearchTask.task_type != "result")
                .order_by(ResearchTask.task_type)
            )
        )
        source_tasks[0].result = {
            **(source_tasks[0].result or {}),
            "acquisition_job_id": source_tasks[1].result["acquisition_job_id"],
        }
        provider_session.commit()

    generator = _AssessmentGenerator(
        gaps=[], before_persist_hook=mutate_source_binding
    )
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    with pytest.raises(ValueError, match="cancelled or stale"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(AIAssessment)) == 0
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_valid_multi_round_run_uses_all_validly_bound_evidence_idempotently(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=2, budget=6)
    generator = _AssessmentGenerator(
        conclusion="insufficient_evidence", gaps=["缺少行业对照"]
    )
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    _round_one_jobs, round_one_link = _advance_to_round_two(session, pipeline, run)
    round_two_jobs = list(
        session.scalars(
            select(AcquisitionJob).where(
                AcquisitionJob.research_run_id == run.id,
                AcquisitionJob.request_snapshot["round"].as_integer() == 2,
            )
        )
    )
    assert len(round_two_jobs) == 3
    round_two_link = _admit_link(session, round_two_jobs[0])
    for index, job in enumerate(round_two_jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    round_two_jobs[0].admitted_count = 1
    generator.gaps = []

    assert pipeline.advance(run) == "completed"
    conclusion = session.scalar(select(EventResearchConclusion))
    assert conclusion is not None
    assert conclusion.evidence_link_ids == [
        str(round_one_link.id),
        str(round_two_link.id),
    ]
    assert pipeline.advance(run) == "completed"
    assert session.scalar(
        select(func.count()).select_from(EventResearchConclusion)
    ) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_source_task",
        "all_missing_bindings",
        "duplicate_job_binding",
        "swapped_job_binding",
        "task_objective",
        "task_round",
        "request_objective",
        "request_target_role",
        "request_round",
        "idempotency_key",
    ],
)
def test_advance_revalidates_resumed_source_matrix_and_job_bindings(
    session, mutation: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    tasks = {
        task.task_type: task
        for task in session.scalars(
            select(ResearchTask).where(
                ResearchTask.run_id == run.id,
                ResearchTask.task_type != "result",
            )
        )
    }
    jobs = {
        task_type: session.get(
            AcquisitionJob,
            uuid.UUID(str(task.result["acquisition_job_id"])),
        )
        for task_type, task in tasks.items()
        if isinstance(task.result, dict)
    }
    assert set(tasks) == {"support", "contradict", "alternative"}
    assert all(job is not None for job in jobs.values())
    for job in jobs.values():
        assert job is not None
        job.status, job.stage = "failed", "failed"

    if mutation == "duplicate_source_task":
        duplicate = AutoResearchRepository(session).create_task(
            run_id=run.id,
            research_case_id=run.research_case_id,
            thesis_id=tasks["support"].thesis_id,
            task_type="support",
            query="duplicate resumed support task",
            round=run.round,
        )
        duplicate.result = dict(tasks["support"].result or {})
        duplicate.stage = "acquire"
    elif mutation == "all_missing_bindings":
        for task in tasks.values():
            task.result = None
    elif mutation == "duplicate_job_binding":
        tasks["contradict"].result = {
            **(tasks["contradict"].result or {}),
            "acquisition_job_id": tasks["support"].result["acquisition_job_id"],
        }
    elif mutation == "swapped_job_binding":
        support_job_id = tasks["support"].result["acquisition_job_id"]
        contradict_job_id = tasks["contradict"].result["acquisition_job_id"]
        tasks["support"].result = {
            **(tasks["support"].result or {}),
            "acquisition_job_id": contradict_job_id,
        }
        tasks["contradict"].result = {
            **(tasks["contradict"].result or {}),
            "acquisition_job_id": support_job_id,
        }
    elif mutation == "task_objective":
        tasks["support"].result = {
            **(tasks["support"].result or {}),
            "objective": "contradict",
        }
    elif mutation == "task_round":
        tasks["support"].round = run.round + 1
    elif mutation == "request_objective":
        jobs["support"].request_snapshot = {
            **jobs["support"].request_snapshot,
            "objective": "contradict",
        }
    elif mutation == "request_target_role":
        jobs["support"].request_snapshot = {
            **jobs["support"].request_snapshot,
            "target_link_role": "contradicts",
        }
    elif mutation == "request_round":
        jobs["support"].request_snapshot = {
            **jobs["support"].request_snapshot,
            "round": run.round + 1,
        }
    else:
        jobs["support"].idempotency_key = f"tampered:{uuid.uuid4()}"

    with pytest.raises(
        ValueError, match="source task matrix|acquisition binding|unbound job"
    ):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0
    assert all(
        not isinstance(task.result, dict)
        or "acquisition_status" not in task.result
        for task in tasks.values()
    )


@pytest.mark.parametrize("mutation", ["missing", "duplicate"])
def test_advance_rejects_non_exact_multi_thesis_result_task_matrix(
    session, mutation: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, factors=["需求增长", "利润改善"])
    result_tasks = list(
        session.scalars(
            select(ResearchTask)
            .where(ResearchTask.run_id == run.id)
            .where(ResearchTask.task_type == "result")
            .order_by(ResearchTask.id)
        )
    )
    if mutation == "missing":
        session.delete(result_tasks[0])
        session.flush()
    else:
        AutoResearchRepository(session).create_task(
            run_id=run.id,
            research_case_id=run.research_case_id,
            thesis_id=result_tasks[0].thesis_id,
            task_type="result",
            query="duplicate result",
        )

    with pytest.raises(ValueError, match="result task matrix"):
        AutomaticResearchPipeline(
            session, assessment_generator=_AssessmentGenerator()
        ).advance(run)

    assert session.scalar(select(func.count()).select_from(AcquisitionJob)) == 0


def test_advance_rejects_reused_assessment_bound_to_the_wrong_scoped_thesis(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(
        session, factors=["需求增长", "利润改善"], max_rounds=1
    )
    second = session.get(Thesis, uuid.UUID((run.scope_thesis_ids or [])[1]))
    assert second is not None
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    first_thesis_id = uuid.UUID((run.scope_thesis_ids or [])[0])
    admitted_job = next(job for job in jobs if job.thesis_id == first_thesis_id)
    admitted = _admit_link(session, admitted_job)
    for job in jobs:
        job.status, job.stage = "failed", "failed"
    admitted_job.status, admitted_job.stage, admitted_job.admitted_count = (
        "succeeded",
        "succeeded",
        1,
    )
    wrong_snapshot = EvidenceSnapshot(
        thesis_id=second.id,
        cutoff=datetime.now(timezone.utc),
        evidence_link_ids=[],
        created_at=datetime.now(timezone.utc),
    )
    session.add(wrong_snapshot)
    session.flush()
    wrong_assessment = AIAssessment(
        snapshot_id=wrong_snapshot.id,
        conclusion="supported",
        rationale="wrong thesis",
        gaps=[],
        displayed_as_provisional=True,
        creator_type="ai",
        created_at=datetime.now(timezone.utc),
    )
    session.add(wrong_assessment)
    session.flush()
    first_result = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
            ResearchTask.thesis_id == first_thesis_id,
        )
    )
    assert first_result is not None
    first_result.status, first_result.stage = "done", "completed"
    first_result.result = {
        "task_type": "result",
        "assessment_id": str(wrong_assessment.id),
        "conclusion": wrong_assessment.conclusion,
        "gaps": [],
    }

    with pytest.raises(ValueError, match="assessment.*thesis|evidence scope"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0
    assert admitted.id is not None


def test_advance_rejects_generated_assessment_not_marked_provisional(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session,
        assessment_generator=_AssessmentGenerator(
            displayed_as_provisional=False,
        ),
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    with pytest.raises(ValueError, match="provenance"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_advance_rejects_reused_non_ai_assessment(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    admitted = _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    snapshot = EvidenceSnapshot(
        thesis_id=jobs[0].thesis_id,
        cutoff=datetime.now(timezone.utc),
        evidence_link_ids=[str(admitted.id)],
        created_at=datetime.now(timezone.utc),
    )
    session.add(snapshot)
    session.flush()
    assessment = AIAssessment(
        snapshot_id=snapshot.id,
        conclusion="supported",
        rationale="not automatic AI provenance",
        gaps=[],
        displayed_as_provisional=True,
        creator_type="human",
        created_at=datetime.now(timezone.utc),
    )
    session.add(assessment)
    session.flush()
    result_task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
        )
    )
    assert result_task is not None
    result_task.status, result_task.stage = "done", "completed"
    result_task.result = {
        "task_type": "result",
        "assessment_id": str(assessment.id),
        "conclusion": assessment.conclusion,
        "gaps": [],
    }

    with pytest.raises(ValueError, match="provenance"):
        pipeline.advance(run)
    assert session.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0


def test_advance_assessment_excludes_prior_run_and_reviewed_visible_evidence(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    current = _admit_link(session, jobs[0])
    prior_run = AutoResearchRepository(session).create_run(
        research_case_id=run.research_case_id,
        scope_thesis_ids=run.scope_thesis_ids,
    )
    prior_job = AcquisitionJob(
        tenant_id=jobs[0].tenant_id,
        research_case_id=run.research_case_id,
        thesis_id=jobs[0].thesis_id,
        research_run_id=prior_run.id,
        idempotency_key=f"prior:{uuid.uuid4()}",
        request_snapshot=dict(jobs[0].request_snapshot),
        policy_snapshot=dict(jobs[0].policy_snapshot),
        status="succeeded",
        stage="succeeded",
        attempt=1,
        reference_count=1,
        fetched_count=1,
        frozen_count=1,
        admitted_count=1,
        exception_count=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    session.add(prior_job)
    session.flush()
    prior = _admit_link(session, prior_job)
    reviewed = EvidenceLink(
        thesis_id=jobs[0].thesis_id,
        source_statement_id=current.source_statement_id,
        role="supports",
        reason="reviewed evidence outside automatic run",
        scope={"source": "reviewed"},
        available_at=datetime.now(timezone.utc),
        creator_type="human",
        review_state="reviewed",
        created_at=datetime.now(timezone.utc),
    )
    session.add(reviewed)
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "completed"
    result_task = session.scalar(
        select(ResearchTask).where(
            ResearchTask.run_id == run.id,
            ResearchTask.task_type == "result",
        )
    )
    assert result_task is not None and result_task.result is not None
    assessment = session.get(
        AIAssessment, uuid.UUID(result_task.result["assessment_id"])
    )
    assert assessment is not None
    snapshot = session.get(EvidenceSnapshot, assessment.snapshot_id)
    assert snapshot is not None
    assert snapshot.evidence_link_ids == [str(current.id)]
    assert str(prior.id) not in snapshot.evidence_link_ids
    assert str(reviewed.id) not in snapshot.evidence_link_ids
    conclusion = session.scalar(
        select(EventResearchConclusion).where(
            EventResearchConclusion.research_case_id == run.research_case_id
        )
    )
    assert conclusion is not None
    assert conclusion.evidence_link_ids == snapshot.evidence_link_ids


def test_advance_rejects_replaced_current_scope_before_mapping_or_assessment(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    generator = _AssessmentGenerator()
    pipeline = AutomaticResearchPipeline(session, assessment_generator=generator)
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    current_scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == run.research_case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    assert current_scope is not None
    replacement = EventResearchScopeVersion(
        research_case_id=run.research_case_id,
        version=current_scope.version + 1,
        changed_by="human:reviewer",
        change_summary="replace frozen automatic scope",
        created_at=datetime.now(timezone.utc),
    )
    session.add(replacement)
    session.flush()
    session.add(
        EventResearchScopeFactor(
            scope_version_id=replacement.id,
            statement="替换后的范围",
            position=0,
        )
    )

    with pytest.raises(ValueError, match="current scope"):
        pipeline.advance(run)
    assert generator.calls == []
    assert session.scalar(
        select(func.count()).select_from(EventResearchScopeEvidenceAssignment)
    ) == 0


def test_automatic_conclusion_id_is_run_derived_and_ignores_overlapping_result(
    session,
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_conclusion import EventConclusionService

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    assert pipeline.advance(run) == "completed"
    original = session.scalar(select(EventResearchConclusion))
    assert original is not None
    overlapping = EventResearchConclusion(
        research_case_id=original.research_case_id,
        scope_version_id=original.scope_version_id,
        state="system_generated",
        text=original.text,
        primary_factor=original.primary_factor,
        evidence_link_ids=original.evidence_link_ids,
        based_on_conclusion_id=None,
        reviewer=None,
        created_at=original.created_at + timedelta(seconds=1),
    )
    session.add(overlapping)
    session.flush()

    retried = EventConclusionService(session).create_automatic_result(
        run.research_case_id, run.id
    )
    expected_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"fund-engine:event-research:automatic:{run.id}",
    )
    assert retried.id == expected_id
    assert retried.id == original.id
    assert retried.id != overlapping.id


@pytest.mark.parametrize("drift", ["primary_factor", "reviewer", "based_on"])
def test_automatic_conclusion_retry_rejects_immutable_identity_drift(
    session, monkeypatch, drift: str
) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    from app.services.event_conclusion import EventConclusionService

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator()
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    admitted = _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1

    with monkeypatch.context() as patch:
        patch.setattr(
            EventConclusionService,
            "create_automatic_result",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("stop before conclusion")
            ),
        )
        with pytest.raises(RuntimeError, match="stop before conclusion"):
            pipeline.advance(run)

    scope = session.scalar(
        select(EventResearchScopeVersion)
        .where(EventResearchScopeVersion.research_case_id == run.research_case_id)
        .order_by(EventResearchScopeVersion.version.desc())
        .limit(1)
    )
    assert scope is not None
    based_on_id = None
    if drift == "based_on":
        seed = EventResearchConclusion(
            research_case_id=run.research_case_id,
            scope_version_id=scope.id,
            state="system_generated",
            text="unrelated seed",
            primary_factor=None,
            evidence_link_ids=[],
            based_on_conclusion_id=None,
            reviewer=None,
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        session.add(seed)
        session.flush()
        based_on_id = seed.id
    expected_text = (
        "需求增长：得到当前证据支持。当前证据的暂定判断\n"
        "局限：结论仅基于本次冻结范围内自动准入且映射到当前范围的证据。"
        "采集任务：失败 2，取消 0，部分完成 0，部分完成但无准入证据 0，"
        "未执行 0，跳过/异常条目 0。"
    )
    conflict = EventResearchConclusion(
        id=uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"fund-engine:event-research:automatic:{run.id}",
        ),
        research_case_id=run.research_case_id,
        scope_version_id=scope.id,
        state="system_generated",
        text=expected_text,
        primary_factor="错误主因素" if drift == "primary_factor" else "需求增长",
        evidence_link_ids=[str(admitted.id)],
        based_on_conclusion_id=based_on_id,
        reviewer="human:reviewer" if drift == "reviewer" else None,
        created_at=datetime.now(timezone.utc),
    )
    session.add(conflict)
    session.flush()

    with pytest.raises(ValidationFailedError, match="immutable"):
        EventConclusionService(session).create_automatic_result(
            run.research_case_id, run.id
        )


def test_automatic_clean_success_still_reports_evidence_boundary(session) -> None:
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(
        session, assessment_generator=_AssessmentGenerator(gaps=[])
    )
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(
        session.scalars(
            select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
        )
    )
    _admit_link(session, jobs[0])
    for job in jobs:
        job.status, job.stage = "succeeded", "succeeded"
    jobs[0].admitted_count = 1

    assert pipeline.advance(run) == "completed"
    conclusion = session.scalar(select(EventResearchConclusion))
    assert conclusion is not None
    assert "局限" in conclusion.text
    assert "冻结范围" in conclusion.text


def test_cancel_after_provider_prevents_assessment_attachment_and_conclusion(
    tmp_path,
) -> None:
    from app.models.ledger import Base
    from app.services.auto_research import AutoResearchService
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline

    engine = create_engine(
        f"sqlite:///{tmp_path / 'cancel-automatic-assessment.db'}", future=True
    )
    session_local = sessionmaker(bind=engine, future=True)
    Base.metadata.create_all(engine)
    worker = session_local()
    try:
        run = _automatic_run(worker, max_rounds=1)
        pipeline = AutomaticResearchPipeline(
            worker, assessment_generator=_AssessmentGenerator()
        )
        assert pipeline.advance(run) == "waiting_for_sources"
        jobs = list(
            worker.scalars(
                select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)
            )
        )
        _admit_link(worker, jobs[0])
        for index, job in enumerate(jobs):
            job.status = "succeeded" if index == 0 else "failed"
            job.stage = job.status
        jobs[0].admitted_count = 1
        run_id, case_id = run.id, run.research_case_id
        worker.commit()

        def cancel_in_other_session(_worker_session):
            with session_local() as cancelling:
                AutoResearchService(cancelling).cancel_run(
                    run_id,
                    actor="human:test",
                    change_reason="cancel after provider",
                )

        pipeline = AutomaticResearchPipeline(
            worker,
            assessment_generator=_AssessmentGenerator(
                before_persist_hook=cancel_in_other_session
            ),
        )
        stale = worker.get(ResearchRun, run_id)
        assert stale is not None
        with pytest.raises(ValueError, match="cancelled|stale"):
            pipeline.advance(stale)
        worker.rollback()
    finally:
        worker.close()

    with Session(engine) as check:
        persisted = check.get(ResearchRun, run_id)
        assert persisted is not None and persisted.status == "cancelled"
        assert check.scalar(select(func.count()).select_from(AIAssessment)) == 0
        assert check.scalar(select(func.count()).select_from(EventResearchConclusion)) == 0
        result_task = check.scalar(
            select(ResearchTask).where(
                ResearchTask.run_id == run_id,
                ResearchTask.task_type == "result",
            )
        )
        assert result_task is not None
        assert result_task.status == "cancelled"
        assert result_task.result is None


def test_automatic_assessment_audit_has_exact_task_ownership(session):
    from app.ai.runs import record_run
    from app.models.ledger import AIRun
    from app.services.automatic_research_pipeline import AutomaticResearchPipeline
    class AuditedGenerator(_AssessmentGenerator):
        def generate(self, thesis_id, cutoff, session, **kwargs):
            result = super().generate(thesis_id, cutoff, session, **kwargs)
            record_run(session, kind="assess", model_version="fixture", prompt_version="fixture",
                       input_ref={"thesis_id": str(thesis_id)}, output_summary="fixture", status="success",
                       started_at=cutoff)
            return result
    run = _automatic_run(session, max_rounds=1)
    pipeline = AutomaticResearchPipeline(session, assessment_generator=AuditedGenerator())
    assert pipeline.advance(run) == "waiting_for_sources"
    jobs = list(session.scalars(select(AcquisitionJob).where(AcquisitionJob.research_run_id == run.id)))
    _admit_link(session, jobs[0])
    for index, job in enumerate(jobs):
        job.status = "succeeded" if index == 0 else "failed"
        job.stage = job.status
    jobs[0].admitted_count = 1
    assert pipeline.advance(run) == "completed"
    audits = list(session.scalars(select(AIRun).where(AIRun.kind == "assess")))
    assert audits
    for audit in audits:
        assert audit.input_ref["research_run_id"] == str(run.id)
        assert audit.input_ref["research_case_id"] == str(run.research_case_id)
        task = session.get(ResearchTask, uuid.UUID(audit.input_ref["research_task_id"]))
        assert task.run_id == run.id
        assert str(task.thesis_id) == audit.input_ref["thesis_id"]
