from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import uuid
from dataclasses import FrozenInstanceError, dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker
from reportlab.pdfgen import canvas as rl_canvas

from app.acquisition.policy import B_SCOPE_POLICY
from app.acquisition.sources import RetrievedEnvelope
from app.ai.extraction import StatementExtractor
from app.datasources.docling import PARSER_VERSION_PYPDF, ParsedSpan, PypdfAdapter
from app.documents.locators import compute_text_sha256, validate_locator_v1
from app.errors import ConflictError
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    AIRun,
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseDocumentVersion,
    DocumentVersion,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Thesis,
    ValidationError,
)
from app.models.operational import ResearchRun
from app.models.source_governance import SourceContract
from app.repositories.acquisition import StaleLeaseError
from app.repositories.research import ResearchRepository
from app.services.atomic_claims import AtomicClaimService
from app.services.automatic_admission import (
    B_SCOPE_GATE_VERSION,
    AdmissionContext,
    AutomaticAdmissionGate,
    GateResult,
)
from app.services.retrieved_documents import (
    FrozenRequestContext,
    RetrievedDocumentFreezer,
)


NOW = datetime(2026, 8, 12, 8, tzinfo=UTC)
CUTOFF = datetime(2026, 8, 12, 16, tzinfo=UTC)
LEASE_TOKEN = "automatic-admission-test-lease"
TEXT = "示例公司2026年12月31日营业收入为100亿元。"
DOCUMENT_BYTES = TEXT.encode("utf-8")
DOCUMENT_SHA256 = hashlib.sha256(DOCUMENT_BYTES).hexdigest()


@dataclass(frozen=True)
class AdmissionFixture:
    case: ResearchCase
    thesis: Thesis
    job: AcquisitionJob
    reference: SourceReference
    artifact: RetrievalArtifact
    document: DocumentVersion
    span: SourceSpan
    candidate: AtomicClaimCandidate
    context: AdmissionContext


class _FixturePdfParser:
    parser_version = "fixture-pdf-v1"

    def __init__(
        self, spans: list[ParsedSpan] | None = None, error: Exception | None = None
    ):
        self.spans = spans or []
        self.error = error

    def extract_spans(self, raw: bytes, *, document_sha256: str) -> list[ParsedSpan]:
        if self.error is not None:
            raise self.error
        return self.spans


class _SteppingClock:
    def __init__(self, *values: datetime):
        self._values = iter(values)
        self._last = values[-1]

    def __call__(self) -> datetime:
        return next(self._values, self._last)


def _seed(
    session,
    *,
    source_role: str = "company_disclosure",
    allowed_source_roles: tuple[str, ...] = ("company_disclosure",),
    contract_active: bool = True,
    contract_effective_until: datetime | None = None,
    contract_source_type: str | None = None,
    published_at: datetime | None = NOW,
    reference_published_at: datetime | None = NOW,
    reference_created_at: datetime = NOW,
    available_at: datetime = NOW,
    acquired_at: datetime = NOW,
    retrieved_at: datetime = NOW,
    adapter_key: str = "sse",
    attempt_adapter_key: str | None = None,
    attempt_operation: str = "fetch",
    attempt_outcome: str = "succeeded",
    attempt_started_at: datetime = NOW,
    attempt_finished_at: datetime | None = NOW,
    attempt_reference_id: uuid.UUID | None = None,
    canonical_url: str = "https://www.sse.com.cn/report.txt",
    final_url: str = "https://static.sse.com.cn/report.txt",
    provider_identity: str | None = "Shanghai Stock Exchange",
    policy_enabled_adapter_keys: tuple[str, ...] = ("gildata", "sse", "szse"),
    entity_names: tuple[str, ...] = ("示例公司",),
    security_codes: tuple[str, ...] = (),
    metric_terms: tuple[str, ...] = ("营业收入",),
    raw_bytes: bytes = DOCUMENT_BYTES,
    text: str = TEXT,
    mime_type: str = "text/plain",
    parser_version: str = "fixture-v1",
    locator_v1: dict | None = None,
    span_context_hash: str | None = None,
    document_source_url: str | None = None,
    binding_relation: str = "created",
    quote_sha256: str | None = None,
    span_text_sha256: str | None = None,
    subject: str | None = "示例公司",
    predicate: str | None = "营业收入",
    numeric_value: str | None = "100",
    unit: str | None = "亿元",
    observed_period: str | None = "2026-12-31",
    normalized_text: str = "示例公司2026年12月31日营业收入为100亿元",
    request_extras: dict | None = None,
    policy_extras: dict | None = None,
) -> AdmissionFixture:
    case = ResearchCase(
        title=f"case-{uuid.uuid4().hex}",
        industry_topic="test",
        created_by="tester",
        created_at=NOW,
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="示例公司的营业收入增长",
        created_by="tester",
        created_at=NOW,
    )
    session.add(thesis)
    session.flush()
    request_snapshot = {
        "allowed_source_roles": list(allowed_source_roles),
        "case_id": str(case.id),
        "cutoff": "2026-08-12T16:00:00Z",
        "entity_names": list(entity_names),
        "metric_terms": list(metric_terms),
        "objective": "support",
        "period_end": "2026-12-31",
        "period_start": "2026-01-01",
        "security_codes": list(security_codes),
        "source_policy_version": B_SCOPE_POLICY.version,
        "target_link_role": "supports",
        "thesis_id": str(thesis.id),
    }
    request_snapshot.update(request_extras or {})
    policy_snapshot = {
        "version": B_SCOPE_POLICY.version,
        "allowed_source_roles": list(allowed_source_roles),
        "enabled_adapter_keys": list(policy_enabled_adapter_keys),
    }
    policy_snapshot.update(policy_extras or {})
    job = AcquisitionJob(
        tenant_id="team-a",
        research_case_id=case.id,
        thesis_id=thesis.id,
        research_run_id=None,
        idempotency_key=uuid.uuid4().hex,
        request_snapshot=request_snapshot,
        policy_snapshot=policy_snapshot,
        status="running",
        stage="admitting",
        attempt=1,
        reference_count=1,
        fetched_count=1,
        frozen_count=1,
        admitted_count=0,
        exception_count=0,
        lease_owner="system:acquisition-worker@task7-test-v1#worker-a",
        lease_token=LEASE_TOKEN,
        lease_expires_at=CUTOFF + timedelta(days=1),
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(job)
    session.flush()
    reference = SourceReference(
        job_id=job.id,
        adapter_key=adapter_key,
        external_record_id=uuid.uuid4().hex,
        external_version="v1",
        canonical_url=canonical_url,
        title="示例公司公告",
        published_at=reference_published_at,
        source_role=source_role,
        metadata_json={"provider_identity": provider_identity},
        created_at=reference_created_at,
    )
    session.add(reference)
    session.flush()
    attempt = AcquisitionAttempt(
        job_id=job.id,
        adapter_key=attempt_adapter_key or adapter_key,
        operation=attempt_operation,
        attempt_no=1,
        started_at=attempt_started_at,
        finished_at=attempt_finished_at,
        outcome=attempt_outcome,
        retryable=False,
        safe_metadata={
            "source_reference_id": str(attempt_reference_id or reference.id)
        },
    )
    session.add(attempt)
    session.flush()
    artifact_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    artifact = RetrievalArtifact(
        source_reference_id=reference.id,
        attempt_id=attempt.id,
        content_sha256=artifact_sha256,
        raw_bytes=raw_bytes,
        mime_type=mime_type,
        byte_size=len(raw_bytes),
        final_url=final_url,
        retrieved_at=retrieved_at,
    )
    document = DocumentVersion(
        content_sha256=artifact_sha256,
        source_url=document_source_url or reference.canonical_url,
        published_at=published_at,
        available_at=available_at,
        acquired_at=acquired_at,
        parser_version=parser_version,
        source_authority=(
            "primary_disclosure"
            if source_role == "company_disclosure"
            else "licensed_research"
        ),
    )
    session.add_all([artifact, document])
    session.flush()
    binding = RetrievalArtifactDocument(
        retrieval_artifact_id=artifact.id,
        document_version_id=document.id,
        relation=binding_relation,
        publication_key=hashlib.sha256(b"publication").hexdigest(),
        created_at=NOW,
    )
    span = SourceSpan(
        document_version_id=document.id,
        locator={"page": 1},
        verbatim_text=text,
        text_sha256=span_text_sha256 or compute_text_sha256(text),
        context_hash=span_context_hash or hashlib.sha256(b"context").hexdigest(),
        locator_v1=locator_v1
        or {
            "schema": "source-locator/v1",
            "document_sha256": artifact_sha256,
            "page": 1,
            "parser_version": parser_version,
            "text_position": {"start": 0, "end": len(text)},
            "text_quote": {"exact": text, "prefix": "", "suffix": ""},
        },
    )
    attachment = CaseDocumentVersion(
        research_case_id=case.id,
        document_version_id=document.id,
        linked_at=NOW,
    )
    contract = SourceContract(
        document_version_id=document.id,
        source_type=contract_source_type or source_role,
        research_source_type=source_role,
        provider_or_tenant="issuer",
        allow_ai_processing=True,
        allow_display=True,
        allow_export=False,
        allow_api=False,
        region="CN",
        effective_from=None,
        effective_until=(
            NOW - timedelta(seconds=1)
            if not contract_active
            else contract_effective_until
        ),
        retention_policy="case_retained",
        deletion_policy="not_recorded",
        downstream_restrictions=[],
        contract_version="v1",
        intake_metadata={},
        declared_by="system:test",
        created_at=NOW,
    )
    session.add_all([binding, span, attachment, contract])
    session.flush()
    ai_run_id = uuid.uuid4()
    ai_run = AIRun(
        id=ai_run_id,
        kind="extract",
        model_version="fixture-model-v1",
        prompt_version="extract-v1",
        input_ref={
            "document_version_id": str(document.id),
            "span_ids": [str(span.id)],
        },
        output_summary="fixture extraction",
        status="success",
        error=None,
        started_at=NOW,
        finished_at=NOW,
    )
    session.add(ai_run)
    session.flush()
    quote_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    candidate = AtomicClaimCandidate(
        source_span_id=span.id,
        canonical_key=uuid.uuid4().hex + uuid.uuid4().hex,
        quote=text,
        quote_start=0,
        quote_end=len(text),
        quote_sha256=quote_sha256 or quote_digest,
        normalized_text=normalized_text,
        claim_type="disclosed_fact",
        assertion_actor="示例公司",
        authority_level=document.source_authority,
        structured_fields={
            "subject": subject,
            "predicate": predicate,
            "object_text": "100亿元",
            "numeric_value": numeric_value,
            "unit": unit,
            "observed_period": observed_period,
            "scope": {"company": "示例公司", "metric": "营业收入"},
            "run_ref": f"extract:{ai_run_id}",
        },
        validation_result={
            "confidence": 0.01,
            "normalizer_version": "atomic-claim-normalizer-v1",
        },
        created_at=NOW,
    )
    session.add(candidate)
    session.flush()
    context = AdmissionContext(
        job_id=job.id,
        retrieval_artifact_id=artifact.id,
        thesis_id=thesis.id,
        cutoff=CUTOFF,
        objective="support",
        target_link_role="supports",
        gate_version=B_SCOPE_GATE_VERSION,
        policy_version=B_SCOPE_POLICY.version,
        allowed_source_roles=frozenset(allowed_source_roles),
        metric_terms=metric_terms,
        expected_subject=entity_names[0] if entity_names else None,
        lease_token=LEASE_TOKEN,
    )
    return AdmissionFixture(
        case, thesis, job, reference, artifact, document, span, candidate, context
    )


def _counts(session) -> tuple[int, int, int, int, int]:
    return tuple(
        session.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            AutomaticAdmissionDecision,
            AcquisitionException,
            SourceStatement,
            EvidenceLink,
            AtomicClaimReview,
        )
    )


def _rotate_token_on_fence(engine, *, job_id: uuid.UUID, fence_number: int):
    calls = 0

    def listener(connection, clauseelement, multiparams, params, execution_options):
        nonlocal calls
        sql = str(clauseelement)
        if not sql.startswith("UPDATE acquisition_jobs SET updated_at="):
            return
        calls += 1
        if calls == fence_number:
            connection.exec_driver_sql(
                "UPDATE acquisition_jobs SET lease_token = ? WHERE id = ?",
                ("rotated-token", job_id.hex),
            )

    event.listen(engine, "before_execute", listener)
    return listener, lambda: calls


def _make_text_pdf(text: str) -> bytes:
    buffer = io.BytesIO()
    canvas = rl_canvas.Canvas(buffer)
    canvas.drawString(50, 750, text)
    canvas.save()
    return buffer.getvalue()


def _make_repeated_text_pdf(text: str) -> bytes:
    buffer = io.BytesIO()
    canvas = rl_canvas.Canvas(buffer)
    for _page in range(2):
        canvas.drawString(50, 750, text)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _make_multiparagraph_pdf(*paragraphs: str) -> bytes:
    buffer = io.BytesIO()
    canvas = rl_canvas.Canvas(buffer)
    for paragraph in paragraphs:
        canvas.drawString(50, 750, paragraph)
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


class _DeterministicExtractionClient:
    model_version = "fake-deterministic-extractor-v1"

    def chat_json(self, messages, schema_hint="", **_kwargs):
        assert schema_hint == "extract"
        payload = json.loads(messages[-1]["content"])
        target = next(
            span for span in payload["spans"] if "Revenue was 100 USD" in span["verbatim_text"]
        )
        quote = target["verbatim_text"]
        return {
            "statements": [
                {
                    "span_id": target["span_id"],
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
                    "observed_period": "2026-12-31",
                    "scope": {"company": "Example Corp", "metric": "Revenue"},
                }
            ]
        }


def test_gate_result_and_context_are_immutable_json_safe_values() -> None:
    mutable = {"values": ["one"]}
    result = GateResult("source", True, "passed", mutable)
    mutable["values"].append("two")

    assert result.as_json() == {
        "passed": True,
        "reason_code": "passed",
        "facts": {"values": ["one"]},
    }
    with pytest.raises(TypeError):
        result.facts["new"] = "value"
    with pytest.raises(FrozenInstanceError):
        result.passed = False


@pytest.mark.parametrize(
    "unsafe_fact",
    [datetime(2026, 8, 12, tzinfo=UTC), "Authorization: Bearer secret-value"],
)
def test_gate_result_rejects_non_json_or_credential_shaped_facts(
    unsafe_fact,
) -> None:
    with pytest.raises(ValueError):
        GateResult("source", False, "unsafe", {"value": unsafe_fact})


def test_fully_structured_numeric_claim_is_admitted_and_published_without_review(
    session,
) -> None:
    seeded = _seed(session)
    gate = AutomaticAdmissionGate(session, clock=lambda: NOW)

    decision = gate.evaluate(seeded.candidate, seeded.context)

    assert decision.outcome == "admitted"
    assert set(decision.gate_results) == {"source", "temporal", "locator", "semantic"}
    assert all(value["passed"] for value in decision.gate_results.values())
    assert decision.retrieval_artifact_id == seeded.artifact.id
    statement, link = AtomicClaimService(
        session, clock=lambda: NOW
    ).publish_automatically(
        seeded.candidate.id,
        decision.id,
        lease_token=LEASE_TOKEN,
    )
    assert statement.atomic_claim_candidate_id == seeded.candidate.id
    assert statement.automatic_admission_decision_id == decision.id
    assert link.thesis_id == seeded.thesis.id
    assert link.role == "supports"
    assert link.creator_type == "ai"
    assert link.review_state == "automatically_admitted"
    assert link.automatic_admission_decision_id == decision.id
    assert link.available_at <= CUTOFF
    assert session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0


def test_automatic_publication_maps_month_candidate_to_month_end(session) -> None:
    text = "示例公司2026年12月营业收入为100亿元。"
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        observed_period="2026-12",
        normalized_text=text.rstrip("。"),
    )
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    statement, _link = AtomicClaimService(
        session, clock=lambda: NOW
    ).publish_automatically(
        seeded.candidate.id,
        decision.id,
        lease_token=LEASE_TOKEN,
    )

    assert decision.outcome == "admitted"
    assert statement.observed_period == date(2026, 12, 31)


def test_real_pdf_freeze_extract_admit_and_automatic_publish_end_to_end(
    session,
) -> None:
    case = ResearchCase(
        title=f"case-{uuid.uuid4().hex}",
        industry_topic="test",
        created_by="tester",
        created_at=NOW,
    )
    session.add(case)
    session.flush()
    thesis = Thesis(
        research_case_id=case.id,
        statement="Example Corp revenue is disclosed",
        created_by="tester",
        created_at=NOW,
    )
    session.add(thesis)
    session.flush()
    job = AcquisitionJob(
        tenant_id="team-a",
        research_case_id=case.id,
        thesis_id=thesis.id,
        research_run_id=None,
        idempotency_key=uuid.uuid4().hex,
        request_snapshot={
            "allowed_source_roles": ["company_disclosure"],
            "case_id": str(case.id),
            "cutoff": "2026-08-12T16:00:00Z",
            "entity_names": ["Example Corp"],
            "metric_terms": ["Revenue"],
            "objective": "support",
            "period_end": "2026-12-31",
            "period_start": "2026-01-01",
            "security_codes": [],
            "source_policy_version": B_SCOPE_POLICY.version,
            "target_link_role": "supports",
            "thesis_id": str(thesis.id),
        },
        policy_snapshot={
            "version": B_SCOPE_POLICY.version,
            "allowed_source_roles": ["company_disclosure"],
            "enabled_adapter_keys": ["gildata", "sse", "szse"],
        },
        status="running",
        stage="freezing",
        attempt=1,
        reference_count=1,
        fetched_count=1,
        frozen_count=0,
        admitted_count=0,
        exception_count=0,
        lease_owner="system:acquisition-worker@task7-e2e-v1#worker-a",
        lease_token=LEASE_TOKEN,
        lease_expires_at=CUTOFF + timedelta(days=1),
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(job)
    session.flush()
    reference = SourceReference(
        job_id=job.id,
        adapter_key="sse",
        external_record_id=uuid.uuid4().hex,
        external_version="v1",
        canonical_url="https://www.sse.com.cn/disclosure/task7-e2e.pdf",
        title="Example Corp annual report",
        published_at=NOW,
        source_role="company_disclosure",
        metadata_json={
            "provider_identity": "Shanghai Stock Exchange",
            "security_code": "600001",
            "source_publication": "2026-08-12",
        },
        created_at=NOW,
    )
    session.add(reference)
    session.flush()
    attempt = AcquisitionAttempt(
        job_id=job.id,
        adapter_key="sse",
        operation="fetch",
        attempt_no=1,
        started_at=NOW,
        finished_at=NOW,
        outcome="succeeded",
        retryable=False,
        safe_metadata={"source_reference_id": str(reference.id)},
    )
    session.add(attempt)
    session.commit()

    raw_pdf = _make_multiparagraph_pdf(
        "Example Corp annual report context paragraph.",
        "Example Corp 2026-12-31 Revenue was 100 USD.",
    )
    sessions = sessionmaker(
        bind=session.get_bind(), future=True, expire_on_commit=False
    )
    frozen = RetrievedDocumentFreezer(sessions, clock=lambda: NOW).freeze(
        reference.id,
        RetrievedEnvelope(
            content=raw_pdf,
            mime_type="application/pdf",
            final_url="https://static.sse.com.cn/disclosure/task7-e2e.pdf",
            etag=None,
            last_modified=None,
            provider_request_id="task7-e2e-request",
            metadata={"adapter_key": "sse"},
        ),
        FrozenRequestContext(
            job_id=job.id,
            attempt_id=attempt.id,
            research_case_id=case.id,
            tenant_id="team-a",
            declared_actor="system:acquisition-worker",
            lease_token=LEASE_TOKEN,
            source_metadata={
                "permissions": {"ai_processing": True, "display": True},
                "region": "CN",
                "retention_policy": "case_retained",
                "deletion_policy": "not_recorded",
            },
            retrieved_at=NOW,
        ),
    )
    assert frozen.document is not None

    session.expire_all()
    persisted_job = session.get(AcquisitionJob, job.id)
    assert persisted_job is not None
    persisted_job.stage = "admitting"
    session.commit()
    spans = list(
        session.scalars(
            select(SourceSpan).where(
                SourceSpan.document_version_id == frozen.document.id
            )
        )
    )
    assert len(spans) == 2
    assert frozen.document.parser_version == PARSER_VERSION_PYPDF
    assert all(span.locator_v1["parser_version"] == PARSER_VERSION_PYPDF for span in spans)
    assert session.scalar(
        select(SourceContract).where(
            SourceContract.document_version_id == frozen.document.id
        )
    )

    candidates = StatementExtractor(_DeterministicExtractionClient()).extract(
        frozen.document.id, session
    )
    assert len(candidates) == 1
    run_ref = candidates[0].structured_fields["run_ref"]
    extraction_run = session.get(AIRun, uuid.UUID(run_ref.removeprefix("extract:")))
    assert extraction_run is not None
    assert extraction_run.status == "success"

    context = AdmissionContext(
        job_id=job.id,
        retrieval_artifact_id=frozen.artifact.id,
        thesis_id=thesis.id,
        cutoff=CUTOFF,
        objective="support",
        target_link_role="supports",
        gate_version=B_SCOPE_GATE_VERSION,
        policy_version=B_SCOPE_POLICY.version,
        allowed_source_roles=frozenset({"company_disclosure"}),
        metric_terms=("Revenue",),
        expected_subject="Example Corp",
        lease_token=LEASE_TOKEN,
    )
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        candidates[0], context
    )
    statement, link = AtomicClaimService(
        session, clock=lambda: NOW
    ).publish_automatically(
        candidates[0].id,
        decision.id,
        lease_token=LEASE_TOKEN,
    )

    assert decision.outcome == "admitted"
    assert statement.atomic_claim_candidate_id == candidates[0].id
    assert link.review_state == "automatically_admitted"
    assert link.automatic_admission_decision_id == decision.id
    assert session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0


@pytest.mark.parametrize(
    ("failure", "overrides", "expected_gate"),
    [
        (
            "non_whitelisted_source",
            {
                "source_role": "licensed_provider",
                "allowed_source_roles": ("company_disclosure",),
            },
            "source",
        ),
        ("inactive_contract", {"contract_active": False}, "source"),
        (
            "contract_source_type_mismatch",
            {"contract_source_type": "public_url"},
            "source",
        ),
        (
            "post_cutoff_publication",
            {"published_at": CUTOFF + timedelta(seconds=1)},
            "temporal",
        ),
        (
            "post_cutoff_availability",
            {"available_at": CUTOFF + timedelta(seconds=1)},
            "temporal",
        ),
        (
            "post_cutoff_acquisition",
            {"acquired_at": CUTOFF + timedelta(seconds=1)},
            "temporal",
        ),
        (
            "post_cutoff_artifact",
            {"retrieved_at": CUTOFF + timedelta(seconds=1)},
            "temporal",
        ),
        ("locator_hash_mismatch", {"span_text_sha256": "0" * 64}, "locator"),
        ("locator_quote_mismatch", {"quote_sha256": "0" * 64}, "locator"),
        ("missing_subject", {"subject": "  "}, "semantic"),
        ("missing_period", {"observed_period": None}, "semantic"),
        ("missing_numeric_unit", {"unit": None}, "semantic"),
        ("metric_mismatch", {"predicate": "净利润"}, "semantic"),
    ],
)
def test_gate_failure_quarantines_with_complete_audit_and_no_publication(
    session, failure, overrides, expected_gate
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined", failure
    assert set(decision.gate_results) == {"source", "temporal", "locator", "semantic"}
    assert decision.gate_results[expected_gate]["passed"] is False
    exception = session.scalar(select(AcquisitionException))
    assert exception is not None
    assert exception.job_id == seeded.job.id
    assert exception.retrieval_artifact_id == seeded.artifact.id
    assert exception.candidate_id == seeded.candidate.id
    assert exception.reason_code == "automatic_admission_quarantined"
    assert _counts(session) == (1, 1, 0, 0, 0)


@pytest.mark.parametrize(
    "context_changes",
    [
        {"objective": "contradict"},
        {"target_link_role": "contradicts"},
    ],
)
def test_objective_or_link_role_mismatch_is_quarantined(
    session, context_changes
) -> None:
    seeded = _seed(session)
    mismatched = replace(seeded.context, **context_changes)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, mismatched
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False
    assert _counts(session) == (1, 1, 0, 0, 0)


def test_job_policy_snapshot_mismatch_is_quarantined_by_source_gate(session) -> None:
    seeded = _seed(session)
    changed_request = dict(seeded.job.request_snapshot)
    changed_request["source_policy_version"] = "other-policy"
    seeded.job.request_snapshot = changed_request
    seeded.job.policy_snapshot = {"version": "other-policy"}
    session.flush()

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["source"]["passed"] is False
    assert _counts(session) == (1, 1, 0, 0, 0)


def test_decision_and_exception_retries_are_idempotent(session) -> None:
    seeded = _seed(session, subject=None)
    gate = AutomaticAdmissionGate(session, clock=lambda: NOW)

    first = gate.evaluate(seeded.candidate, seeded.context)
    second = gate.evaluate(seeded.candidate.id, seeded.context)

    assert second.id == first.id
    assert _counts(session) == (1, 1, 0, 0, 0)


def test_quarantine_uniqueness_race_reuses_exact_exception_winner(
    session, monkeypatch
) -> None:
    seeded = _seed(session, subject=None)
    first_decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    assert session.scalar(select(AcquisitionException)) is not None
    racing_gate = AutomaticAdmissionGate(session, clock=lambda: NOW)
    real_get = racing_gate._get_exception
    calls = 0

    def miss_then_find(decision, lineage):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return real_get(decision, lineage)

    monkeypatch.setattr(racing_gate, "_get_exception", miss_then_find)

    raced = racing_gate.evaluate(seeded.candidate, seeded.context)

    assert raced.id == first_decision.id
    assert calls == 2
    assert _counts(session) == (1, 1, 0, 0, 0)


def test_decision_uniqueness_race_returns_the_matching_winner(
    session, monkeypatch
) -> None:
    seeded = _seed(session)
    original = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    gate = AutomaticAdmissionGate(session, clock=lambda: NOW)
    real_get_existing = gate._get_existing
    calls = 0

    def miss_then_find(candidate_id, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return real_get_existing(candidate_id, context)

    monkeypatch.setattr(gate, "_get_existing", miss_then_find)
    raced = gate.evaluate(seeded.candidate, seeded.context)

    assert raced.id == original.id
    assert calls == 2
    assert _counts(session) == (1, 0, 0, 0, 0)


def test_same_decision_key_rejects_changed_effective_context(session) -> None:
    seeded = _seed(session)
    gate = AutomaticAdmissionGate(session, clock=lambda: NOW)
    original = gate.evaluate(seeded.candidate, seeded.context)

    with pytest.raises(ConflictError, match="context"):
        gate.evaluate(
            seeded.candidate,
            replace(seeded.context, objective="contradict"),
        )

    assert session.scalar(select(AutomaticAdmissionDecision)).id == original.id
    assert _counts(session) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize("stale_kind", ["token", "status", "expiry"])
def test_evaluation_requires_the_current_active_lease(session, stale_kind) -> None:
    seeded = _seed(session)
    context = seeded.context
    if stale_kind == "token":
        context = replace(context, lease_token="stale-token")
    elif stale_kind == "status":
        seeded.job.status = "failed"
        seeded.job.stage = "failed"
    else:
        seeded.job.lease_expires_at = NOW
    session.flush()

    with pytest.raises(StaleLeaseError, match="lease"):
        AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
            seeded.candidate, context
        )

    assert _counts(session) == (0, 0, 0, 0, 0)


def test_lineage_mismatch_is_rejected_without_a_partial_decision(session) -> None:
    seeded = _seed(session)

    with pytest.raises(ValidationError, match="lineage"):
        AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
            seeded.candidate,
            replace(seeded.context, retrieval_artifact_id=uuid.uuid4()),
        )

    assert _counts(session) == (0, 0, 0, 0, 0)


def test_sqlite_timezone_round_trip_compares_instants_in_utc(session) -> None:
    seeded = _seed(session)
    shanghai_cutoff = datetime(2026, 8, 13, tzinfo=timezone(timedelta(hours=8)))
    seeded.job.request_snapshot["cutoff"] = "2026-08-12T16:00:00Z"
    session.flush()

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate,
        replace(seeded.context, cutoff=shanghai_cutoff),
    )

    assert decision.outcome == "admitted"
    assert decision.gate_results["temporal"]["passed"] is True


def test_automatic_publication_is_idempotent(session) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    service = AtomicClaimService(session, clock=lambda: NOW)

    first_statement, first_link = service.publish_automatically(
        seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
    )
    second_statement, second_link = service.publish_automatically(
        seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
    )

    assert second_statement.id == first_statement.id
    assert second_link.id == first_link.id
    assert _counts(session) == (1, 0, 1, 1, 0)


def test_automatic_publication_uniqueness_race_reuses_exact_link_winner(
    session, monkeypatch
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    service = AtomicClaimService(session, clock=lambda: NOW)
    statement, winner = service.publish_automatically(
        seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
    )
    real_get = ResearchRepository.get_automatic_evidence_link
    calls = 0

    def miss_then_find(repository, decision_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return real_get(repository, decision_id)

    monkeypatch.setattr(
        ResearchRepository, "get_automatic_evidence_link", miss_then_find
    )

    raced_statement, raced_link = service.publish_automatically(
        seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
    )

    assert raced_statement.id == statement.id
    assert raced_link.id == winner.id
    assert calls == 2
    assert _counts(session) == (1, 0, 1, 1, 0)


def test_automatic_publication_rejects_request_snapshot_changed_after_decision(
    session,
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    changed_snapshot = dict(seeded.job.request_snapshot)
    changed_snapshot["objective"] = "contradict"
    changed_snapshot["target_link_role"] = "contradicts"
    seeded.job.request_snapshot = changed_snapshot
    session.flush()

    with pytest.raises(ValidationError, match="audit"):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id,
            decision.id,
            lease_token=LEASE_TOKEN,
        )

    assert session.scalar(select(func.count()).select_from(SourceStatement)) == 0
    assert session.scalar(select(func.count()).select_from(EvidenceLink)) == 0


def test_automatic_publication_rolls_back_statement_when_link_creation_fails(
    session, monkeypatch
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    def fail_link(*_args, **_kwargs):
        raise RuntimeError("simulated link failure")

    monkeypatch.setattr(ResearchRepository, "link_evidence", fail_link)
    with pytest.raises(RuntimeError, match="link failure"):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id,
            decision.id,
            lease_token=LEASE_TOKEN,
        )

    assert session.scalar(select(func.count()).select_from(SourceStatement)) == 0
    assert session.scalar(select(func.count()).select_from(EvidenceLink)) == 0


@pytest.mark.parametrize("failure", ["quarantined", "candidate", "lease"])
def test_automatic_publication_rejects_invalid_provenance_without_partial_rows(
    session, failure
) -> None:
    seeded = _seed(session, **({"subject": None} if failure == "quarantined" else {}))
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    candidate_id = seeded.candidate.id
    lease_token = LEASE_TOKEN
    if failure == "candidate":
        other = AtomicClaimCandidate(
            source_span_id=seeded.span.id,
            canonical_key=uuid.uuid4().hex + uuid.uuid4().hex,
            quote=TEXT,
            quote_start=0,
            quote_end=len(TEXT),
            quote_sha256=hashlib.sha256(TEXT.encode("utf-8")).hexdigest(),
            normalized_text="另一条候选陈述",
            claim_type="disclosed_fact",
            assertion_actor="示例公司",
            authority_level="primary_disclosure",
            structured_fields=dict(seeded.candidate.structured_fields),
            validation_result={},
            created_at=NOW,
        )
        session.add(other)
        session.flush()
        candidate_id = other.id
    elif failure == "lease":
        lease_token = "stale-token"

    error = ValidationError if failure != "lease" else StaleLeaseError
    with pytest.raises(error):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            candidate_id,
            decision.id,
            lease_token=lease_token,
        )

    assert session.scalar(select(func.count()).select_from(SourceStatement)) == 0
    assert session.scalar(select(func.count()).select_from(EvidenceLink)) == 0
    assert session.scalar(select(func.count()).select_from(AtomicClaimReview)) == 0


def test_research_repository_link_evidence_remains_backward_compatible(session) -> None:
    seeded = _seed(session)
    statement = SourceStatement(
        source_span_id=seeded.span.id,
        kind="disclosed_fact",
        normalized_text="人工路径兼容性",
        observed_period=None,
        created_at=NOW,
    )
    session.add(statement)
    session.flush()

    link = ResearchRepository(session).link_evidence(
        thesis_id=seeded.thesis.id,
        source_statement_id=statement.id,
        role="supports",
        reason="legacy caller",
        scope={"path": "legacy"},
        available_at=NOW,
    )

    assert link.review_state == "machine_generated"
    assert link.automatic_admission_decision_id is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"adapter_key": "evil"},
        {"policy_enabled_adapter_keys": ("gildata", "szse")},
        {"provider_identity": None},
        {"provider_identity": "  "},
        {"canonical_url": "https://www.sse.com.cn.evil.test/report.txt"},
        {"canonical_url": "https://evil.test@www.sse.com.cn/report.txt"},
        {"canonical_url": "https://www.sse.com.cn:443/report.txt"},
        {"canonical_url": "https://www.sse.com.cn/report.txt?next=evil"},
        {"canonical_url": "https://www.sse.com.cn/report.txt#fragment"},
        {"canonical_url": "https://www.sse.com.cn/%2e%2e/report.txt"},
        {"final_url": "https://query.sse.com.cn/report.txt"},
        {"final_url": "https://static.sse.com.cn.evil.test/report.txt"},
    ],
)
def test_source_gate_rejects_unauthorized_adapter_provider_or_url_without_crashing(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["source"]["passed"] is False
    assert set(decision.gate_results) == {"source", "temporal", "locator", "semantic"}
    assert _counts(session) == (1, 1, 0, 0, 0)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "adapter_key": "sse",
            "canonical_url": "https://www.sse.com.cn/report.txt",
            "final_url": "https://static.sse.com.cn/report.txt",
            "provider_identity": "Shanghai Stock Exchange",
        },
        {
            "adapter_key": "szse",
            "canonical_url": "https://disc.static.szse.cn/report.txt",
            "final_url": "https://disc.static.szse.cn/report.txt",
            "provider_identity": "Shenzhen Stock Exchange",
        },
        {
            "adapter_key": "gildata",
            "canonical_url": "gildata://research-report/report-1",
            "final_url": "gildata://research-report/report-1",
            "provider_identity": "Gildata",
            "source_role": "licensed_provider",
            "allowed_source_roles": ("licensed_provider",),
        },
    ],
)
def test_source_gate_accepts_only_exact_governed_adapter_boundaries(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "admitted"
    assert decision.gate_results["source"]["passed"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "adapter_key": "sse",
            "source_role": "licensed_provider",
            "allowed_source_roles": ("licensed_provider",),
            "provider_identity": "Shanghai Stock Exchange",
        },
        {"provider_identity": "SSE"},
        {"provider_identity": "Gildata"},
        {"document_source_url": "https://www.sse.com.cn/other-report.txt"},
        {
            "document_source_url": "https://www.sse.com.cn/original.txt",
            "binding_relation": "content_duplicate",
        },
    ],
)
def test_source_gate_requires_exact_adapter_role_provider_and_document_url_mapping(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["source"]["passed"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt_started_at": NOW + timedelta(seconds=2)},
        {"attempt_finished_at": NOW + timedelta(seconds=2)},
        {"reference_created_at": NOW + timedelta(seconds=1)},
        {"published_at": NOW + timedelta(seconds=1)},
        {"acquired_at": NOW + timedelta(seconds=1)},
    ],
)
def test_temporal_gate_requires_exact_monotonic_fetch_chronology(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["temporal"]["passed"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt_operation": "search"},
        {"attempt_outcome": "failed"},
        {"attempt_finished_at": None},
        {"attempt_adapter_key": "szse"},
        {"attempt_reference_id": uuid.UUID("c5f39b14-b674-4e71-85de-6853f04d0d88")},
    ],
)
def test_source_gate_requires_exact_successful_fetch_attempt_lineage(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["source"]["passed"] is False
    assert set(decision.gate_results) == {"source", "temporal", "locator", "semantic"}


@pytest.mark.parametrize(
    "reference_published_at",
    [None, CUTOFF + timedelta(microseconds=1)],
)
def test_temporal_gate_requires_reference_publication_at_or_before_cutoff(
    session, reference_published_at
) -> None:
    seeded = _seed(session, reference_published_at=reference_published_at)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["temporal"]["passed"] is False


def test_temporal_gate_accepts_post_cutoff_backfill_of_pre_cutoff_announcement(
    session,
) -> None:
    reference_created_at = CUTOFF + timedelta(minutes=1)
    attempt_started_at = CUTOFF + timedelta(minutes=2)
    attempt_finished_at = CUTOFF + timedelta(minutes=3)
    retrieved_at = CUTOFF + timedelta(minutes=4)
    acquired_at = CUTOFF + timedelta(minutes=5)
    evaluation_at = CUTOFF + timedelta(minutes=6)
    seeded = _seed(
        session,
        published_at=NOW,
        reference_published_at=NOW,
        available_at=NOW + timedelta(hours=1),
        reference_created_at=reference_created_at,
        attempt_started_at=attempt_started_at,
        attempt_finished_at=attempt_finished_at,
        retrieved_at=retrieved_at,
        acquired_at=acquired_at,
    )

    decision = AutomaticAdmissionGate(
        session, clock=lambda: evaluation_at
    ).evaluate(seeded.candidate, seeded.context)

    assert decision.outcome == "admitted"
    assert decision.gate_results["temporal"]["passed"] is True


def test_temporal_gate_requires_reference_and_document_publication_to_match(
    session,
) -> None:
    seeded = _seed(
        session,
        published_at=NOW,
        reference_published_at=NOW - timedelta(days=1),
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["temporal"]["reason_code"] == (
        "temporal_publication_mismatch"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "reference_created_at": NOW + timedelta(seconds=2),
            "attempt_started_at": NOW + timedelta(seconds=1),
        },
        {
            "attempt_finished_at": NOW + timedelta(seconds=2),
            "retrieved_at": NOW + timedelta(seconds=1),
            "acquired_at": NOW + timedelta(seconds=3),
        },
        {
            "retrieved_at": NOW + timedelta(seconds=2),
            "acquired_at": NOW + timedelta(seconds=1),
        },
        {"available_at": NOW - timedelta(seconds=1)},
        {
            "reference_created_at": NOW + timedelta(seconds=1),
            "attempt_started_at": NOW + timedelta(seconds=2),
            "attempt_finished_at": NOW + timedelta(seconds=3),
            "retrieved_at": NOW + timedelta(seconds=4),
            "acquired_at": NOW + timedelta(seconds=5),
        },
    ],
)
def test_temporal_gate_rejects_impossible_or_future_chronology(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["temporal"]["passed"] is False


def test_semantic_gate_requires_subject_from_frozen_request_even_without_hint(
    session,
) -> None:
    seeded = _seed(session, subject="另一家公司")
    context = replace(seeded.context, expected_subject=None)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


def test_semantic_metric_match_is_normalized_exact_not_token_subset(session) -> None:
    seeded = _seed(
        session,
        metric_terms=("Revenue",),
        predicate="Revenue adjusted",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "text": "示例公司2026年净利润为100亿元。",
            "raw_bytes": "示例公司2026年净利润为100亿元。".encode(),
        },
        {"numeric_value": "999"},
        {"numeric_value": "NaN"},
        {"numeric_value": "1e2"},
        {"unit": "万元"},
        {"observed_period": "2030-12-31"},
        {"normalized_text": "2026年营业收入为100亿元"},
        {"normalized_text": "示例公司2026年为100亿元"},
        {"normalized_text": "示例公司2026年营业收入"},
    ],
)
def test_semantic_gate_rejects_structured_values_not_grounded_in_quote_and_text(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "text": "示例公司2026年12月31日营业收入为1,000.50亿元。",
            "raw_bytes": "示例公司2026年12月31日营业收入为1,000.50亿元。".encode(),
            "numeric_value": "1000.500",
            "normalized_text": "示例公司2026年12月31日营业收入为1000.500亿元",
        },
        {
            "text": "Example Corp 2026-12-31 Revenue was 1,000.50 USD.",
            "raw_bytes": b"Example Corp 2026-12-31 Revenue was 1,000.50 USD.",
            "entity_names": ("Example Corp",),
            "metric_terms": ("Revenue",),
            "subject": "example corp",
            "predicate": "revenue",
            "numeric_value": "1000.500",
            "unit": "USD",
            "normalized_text": "Example Corp 2026-12-31 Revenue was 1000.500 USD",
        },
        {
            "numeric_value": None,
            "unit": None,
            "normalized_text": "示例公司2026年12月31日营业收入",
        },
    ],
)
def test_semantic_gate_accepts_deterministically_grounded_chinese_english_and_table_claims(
    session, overrides
) -> None:
    seeded = _seed(session, **overrides)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "admitted"
    assert decision.gate_results["semantic"]["passed"] is True


def test_semantic_period_requires_request_window_and_textual_grounding(session) -> None:
    seeded = _seed(
        session,
        text="示例公司营业收入为100亿元。",
        raw_bytes="示例公司营业收入为100亿元。".encode(),
        normalized_text="示例公司营业收入为100亿元",
        request_extras={"period_start": "2025-01-01", "period_end": "2027-12-31"},
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


@pytest.mark.parametrize(
    ("text", "normalized_text", "predicate", "numeric_value"),
    [
        (
            "示例公司2026年12月31日营业收入为200亿元。"
            "另一家公司2026年12月31日净利润为100亿元。",
            "示例公司2026年12月31日净利润为100亿元",
            "净利润",
            "100",
        ),
        (
            "示例公司2026年12月31日营业收入为200亿元；净利润为100亿元。",
            "示例公司2026年12月31日营业收入为100亿元",
            "营业收入",
            "100",
        ),
    ],
)
def test_semantic_gate_rejects_reviewer_cross_segment_token_splicing(
    session, text, normalized_text, predicate, numeric_value
) -> None:
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        metric_terms=(predicate,),
        predicate=predicate,
        numeric_value=numeric_value,
        normalized_text=normalized_text,
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


@pytest.mark.parametrize(
    ("predicate", "numeric_value", "normalized_text", "expected_outcome"),
    [
        ("营业收入", "100", "示例公司2026年营业收入为100亿元", "quarantined"),
        ("营业收入", "200", "示例公司2026年营业收入为200亿元", "admitted"),
        ("净利润", "100", "示例公司2026年净利润为100亿元", "admitted"),
    ],
)
def test_semantic_gate_binds_each_metric_to_its_own_following_value(
    session, predicate, numeric_value, normalized_text, expected_outcome
) -> None:
    text = "示例公司2026年营业收入为200亿元，净利润为100亿元"
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        metric_terms=("营业收入", "净利润"),
        observed_period="2026",
        predicate=predicate,
        numeric_value=numeric_value,
        normalized_text=normalized_text,
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == expected_outcome
    assert decision.gate_results["semantic"]["passed"] is (
        expected_outcome == "admitted"
    )


@pytest.mark.parametrize(
    ("text", "entity_names", "metric_terms", "subject", "predicate", "unit"),
    [
        (
            "示例公司2026年营业收入：200亿元，净利润：100亿元",
            ("示例公司",),
            ("营业收入", "净利润"),
            "示例公司",
            "营业收入",
            "亿元",
        ),
        (
            "Example Corp 2026 Revenue: USD 200, Net profit: USD 100",
            ("Example Corp",),
            ("Revenue", "Net profit"),
            "Example Corp",
            "Revenue",
            "USD",
        ),
        (
            "示例公司\t2026年\t营业收入\t200亿元\t净利润\t100亿元",
            ("示例公司",),
            ("营业收入", "净利润"),
            "示例公司",
            "营业收入",
            "亿元",
        ),
        (
            "示例公司2026年营业收入：200亿元；净利润：100亿元",
            ("示例公司",),
            ("营业收入", "净利润"),
            "示例公司",
            "营业收入",
            "亿元",
        ),
    ],
)
def test_semantic_metric_value_binding_fails_closed_across_punctuation_and_table_rows(
    session, text, entity_names, metric_terms, subject, predicate, unit
) -> None:
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        entity_names=entity_names,
        metric_terms=metric_terms,
        subject=subject,
        predicate=predicate,
        numeric_value="100",
        unit=unit,
        observed_period="2026",
        normalized_text=f"{subject} 2026 {predicate}: 100 {unit}",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


def test_semantic_gate_rejects_exact_day_supported_only_by_bare_year(session) -> None:
    text = "示例公司2026年营业收入为100亿元。"
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        observed_period="2026-12-31",
        normalized_text="示例公司2026年营业收入为100亿元",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


def test_semantic_gate_rejects_bare_year_supported_only_by_exact_day(session) -> None:
    text = "示例公司2026年12月31日营业收入为100亿元。"
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        observed_period="2026",
        normalized_text="示例公司2026年12月31日营业收入为100亿元",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


def test_semantic_gate_rejects_year_supported_only_by_english_exact_day(
    session,
) -> None:
    text = "Example Corp December 31, 2026 Revenue was 100 USD."
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        entity_names=("Example Corp",),
        metric_terms=("Revenue",),
        subject="Example Corp",
        predicate="Revenue",
        numeric_value="100",
        unit="USD",
        observed_period="2026",
        normalized_text=text.rstrip("."),
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


@pytest.mark.parametrize(
    ("observed_period", "text"),
    [
        ("2026", "示例公司2026年营业收入为100亿元。"),
        ("2026-12", "示例公司2026年12月营业收入为100亿元。"),
        ("2026-12", "Example Corp December 2026 Revenue was 100 USD."),
        ("2026-12-31", "示例公司2026年12月31日营业收入为100亿元。"),
        ("2026-12-31", "Example Corp December 31, 2026 Revenue was 100 USD."),
    ],
)
def test_semantic_period_accepts_same_grain_chinese_and_english_equivalents(
    session, observed_period, text
) -> None:
    english = text.startswith("Example")
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        entity_names=("Example Corp",) if english else ("示例公司",),
        metric_terms=("Revenue",) if english else ("营业收入",),
        subject="Example Corp" if english else "示例公司",
        predicate="Revenue" if english else "营业收入",
        unit="USD" if english else "亿元",
        observed_period=observed_period,
        normalized_text=text.rstrip("。."),
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "admitted"
    assert decision.gate_results["semantic"]["passed"] is True


@pytest.mark.parametrize(
    ("observed_period", "text"),
    [
        ("2026", "示例公司2026-12营业收入为100亿元。"),
        ("2026-12", "示例公司2026-12-31营业收入为100亿元。"),
        ("2026-12-31", "示例公司2026-12营业收入为100亿元。"),
    ],
)
def test_semantic_period_rejects_support_from_a_different_date_grain(
    session, observed_period, text
) -> None:
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        observed_period=observed_period,
        normalized_text=text.rstrip("。"),
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


@pytest.mark.parametrize(
    ("text", "normalized_text"),
    [
        (
            "示例公司2026年12月31日营业收入未增长100亿元。",
            "示例公司2026年12月31日营业收入增长100亿元",
        ),
        (
            "示例公司2026年12月31日营业收入下降100亿元。",
            "示例公司2026年12月31日营业收入增长100亿元",
        ),
    ],
)
def test_semantic_gate_fails_closed_on_negation_or_comparison_direction_mismatch(
    session, text, normalized_text
) -> None:
    seeded = _seed(
        session,
        text=text,
        raw_bytes=text.encode(),
        normalized_text=normalized_text,
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["semantic"]["passed"] is False


def test_decision_records_complete_frozen_request_digest_without_lease_token(
    session,
) -> None:
    seeded = _seed(session)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    digests = {
        result["facts"].get("frozen_request_digest")
        for result in decision.gate_results.values()
    }
    assert len(digests) == 1
    assert len(digests.pop()) == 64
    assert LEASE_TOKEN not in str(decision.gate_results)


def test_decision_records_one_canonical_lineage_digest_and_trusted_replay_identity(
    session,
) -> None:
    seeded = _seed(session)

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    digests = {
        result["facts"].get("admission_digest")
        for result in decision.gate_results.values()
    }
    assert len(digests) == 1
    assert len(digests.pop()) == 64
    replay_identities = {
        str(result["facts"].get("replay_identity"))
        for result in decision.gate_results.values()
    }
    assert len(replay_identities) == 1
    identity = decision.gate_results["source"]["facts"]["replay_identity"]
    run_ref = seeded.candidate.structured_fields["run_ref"]
    assert identity["extraction"] == {
        "airun_id": run_ref.removeprefix("extract:"),
        "run_ref": run_ref,
        "model_version": "fixture-model-v1",
        "prompt_version": "extract-v1",
    }
    assert identity["normalizer_version"] == "atomic-claim-normalizer-v1"
    assert identity["worker"] == {
        "actor": "system:acquisition-worker",
        "version": "task7-test-v1",
    }
    assert LEASE_TOKEN not in str(decision.gate_results)


@pytest.mark.parametrize(
    ("table", "assignment"),
    [
        ("atomic_claim_candidates", "normalized_text = 'tampered candidate'"),
        ("source_spans", "verbatim_text = 'tampered span'"),
        ("source_references", "title = 'tampered lineage'"),
    ],
)
def test_publication_rejects_raw_sql_tampering_of_candidate_span_or_lineage(
    session, table, assignment
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    row_id = {
        "atomic_claim_candidates": seeded.candidate.id,
        "source_spans": seeded.span.id,
        "source_references": seeded.reference.id,
    }[table]
    session.connection().exec_driver_sql(
        f"UPDATE {table} SET {assignment} WHERE id = ?", (row_id.hex,)
    )
    session.expire_all()

    with pytest.raises(ValidationError, match="digest"):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("snapshot_name", "field", "changed_value"),
    [
        ("request_snapshot", "period_start", "2025-01-01"),
        ("request_snapshot", "round", 2),
        ("request_snapshot", "thesis_statement", "mutated thesis"),
        ("request_snapshot", "future_security_field", {"mode": "strict"}),
        ("policy_snapshot", "exact_hosts", ["evil.example"]),
        ("policy_snapshot", "max_response_bytes", 1),
        ("policy_snapshot", "permission_declarations", [["sse", "mutated"]]),
    ],
)
def test_complete_digest_blocks_mutation_of_any_snapshot_field(
    session, snapshot_name, field, changed_value
) -> None:
    seeded = _seed(
        session,
        request_extras={
            "round": 1,
            "thesis_statement": "示例公司的营业收入增长",
        },
        policy_extras={
            "exact_hosts": ["www.sse.com.cn"],
            "max_response_bytes": 20 * 1024 * 1024,
            "permission_declarations": [["sse", "official-public-disclosure"]],
        },
    )
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    changed = dict(getattr(seeded.job, snapshot_name))
    changed[field] = changed_value
    setattr(seeded.job, snapshot_name, changed)
    session.flush()

    with pytest.raises(ValidationError, match="digest"):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize("binding", ["tenant_id", "idempotency_key", "research_run_id"])
def test_complete_digest_blocks_mutation_of_job_binding_fields(
    session, binding
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    if binding == "research_run_id":
        run = ResearchRun(
            research_case_id=seeded.case.id,
            status="queued",
            stage="planning",
            round=1,
            max_rounds=3,
            budget=100,
            budget_used=0,
            created_at=NOW,
            updated_at=NOW,
        )
        session.add(run)
        session.flush()
        seeded.job.research_run_id = run.id
    else:
        setattr(seeded.job, binding, f"mutated-{uuid.uuid4().hex}")
    session.flush()

    with pytest.raises(ValidationError, match="digest"):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)


def test_pdf_locator_requires_replayed_parser_span(session) -> None:
    seeded = _seed(
        session,
        raw_bytes=b"%PDF-1.7 invented fixture",
        mime_type="application/pdf",
        parser_version="fixture-pdf-v1",
    )
    replayed = ParsedSpan(
        locator=validate_locator_v1(seeded.span.locator_v1),
        verbatim_text=seeded.span.verbatim_text,
        text_sha256=seeded.span.text_sha256,
        context_hash=seeded.span.context_hash,
    )

    admitted = AutomaticAdmissionGate(
        session,
        clock=lambda: NOW,
        pdf_parser=_FixturePdfParser([replayed]),
    ).evaluate(seeded.candidate, seeded.context)

    assert admitted.outcome == "admitted"
    assert admitted.gate_results["locator"]["passed"] is True


def test_default_pdf_parser_never_accepts_stored_quote_as_replay(session) -> None:
    seeded = _seed(
        session,
        raw_bytes=b"%PDF-1.7 invented fixture",
        mime_type="application/pdf",
        parser_version="fixture-pdf-v1",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["locator"]["passed"] is False


def test_default_pypdf_replays_real_pdf_by_stable_paragraph_identity(session) -> None:
    raw = _make_text_pdf("Example Corp 2026-12-31 Revenue was 1,000.50 USD.")
    digest = hashlib.sha256(raw).hexdigest()
    parsed = PypdfAdapter().extract_spans(raw, document_sha256=digest)[0]
    seeded = _seed(
        session,
        raw_bytes=raw,
        text=parsed.verbatim_text,
        mime_type="application/pdf",
        parser_version=PARSER_VERSION_PYPDF,
        locator_v1=parsed.locator.to_storage_dict(),
        span_text_sha256=parsed.text_sha256,
        span_context_hash=parsed.context_hash,
        entity_names=("Example Corp",),
        metric_terms=("Revenue",),
        subject="example corp",
        predicate="revenue",
        numeric_value="1000.500",
        unit="USD",
        normalized_text="Example Corp 2026-12-31 Revenue was 1000.500 USD",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert parsed.locator.parser_item_ref == "#/legacy-paragraph/1"
    assert decision.outcome == "admitted"
    assert decision.gate_results["locator"]["passed"] is True


def test_pypdf_parser_stamp_contains_exact_package_version_and_config() -> None:
    package_version = importlib.metadata.version("pypdf")

    assert PypdfAdapter().parser_version == (
        f"pypdf-{package_version}+text-layer-paragraph-v1"
    )


def test_default_pypdf_replays_second_of_repeated_text_on_two_pages(session) -> None:
    text = "Example Corp 2026-12-31 Revenue was 1,000.50 USD."
    raw = _make_repeated_text_pdf(text)
    digest = hashlib.sha256(raw).hexdigest()
    parsed = PypdfAdapter().extract_spans(raw, document_sha256=digest)

    assert len(parsed) == 2
    assert parsed[0].verbatim_text == parsed[1].verbatim_text
    assert parsed[0].locator.page == 1
    assert parsed[1].locator.page == 2
    seeded = _seed(
        session,
        raw_bytes=raw,
        text=parsed[1].verbatim_text,
        mime_type="application/pdf",
        parser_version=PARSER_VERSION_PYPDF,
        locator_v1=parsed[1].locator.to_storage_dict(),
        span_text_sha256=parsed[1].text_sha256,
        span_context_hash=parsed[1].context_hash,
        entity_names=("Example Corp",),
        metric_terms=("Revenue",),
        subject="example corp",
        predicate="revenue",
        numeric_value="1000.500",
        unit="USD",
        normalized_text="Example Corp 2026-12-31 Revenue was 1000.500 USD",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "admitted"
    assert decision.gate_results["locator"]["facts"]["round_trip"] is True
    assert decision.gate_results["locator"]["facts"]["replay_identity"]["parser"] == {
        "family": "pypdf",
        "stamp": PARSER_VERSION_PYPDF,
        "package_version": importlib.metadata.version("pypdf"),
        "config": "text-layer-paragraph-v1",
    }


def test_default_pypdf_quarantines_altered_persisted_paragraph(session) -> None:
    raw = _make_text_pdf("Example Corp 2026 Revenue was 100 USD.")
    digest = hashlib.sha256(raw).hexdigest()
    parsed = PypdfAdapter().extract_spans(raw, document_sha256=digest)[0]
    altered = parsed.verbatim_text.replace("100", "999")
    seeded = _seed(
        session,
        raw_bytes=raw,
        text=altered,
        mime_type="application/pdf",
        parser_version=PARSER_VERSION_PYPDF,
        locator_v1=parsed.locator.to_storage_dict(),
        span_text_sha256=compute_text_sha256(altered),
        span_context_hash=parsed.context_hash,
        entity_names=("Example Corp",),
        metric_terms=("Revenue",),
        subject="Example Corp",
        predicate="Revenue",
        numeric_value="999",
        unit="USD",
        normalized_text="Example Corp 2026 Revenue was 999 USD",
    )

    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    assert decision.outcome == "quarantined"
    assert decision.gate_results["locator"]["passed"] is False


@pytest.mark.parametrize("mode", ["no_match", "parser_error"])
def test_pdf_locator_parser_failure_quarantines_and_records_every_gate(
    session, mode
) -> None:
    seeded = _seed(
        session,
        raw_bytes=b"%PDF-1.7 invented fixture",
        mime_type="application/pdf",
        parser_version="fixture-pdf-v1",
    )
    parser = (
        _FixturePdfParser([])
        if mode == "no_match"
        else _FixturePdfParser(error=RuntimeError("parser exploded"))
    )

    decision = AutomaticAdmissionGate(
        session, clock=lambda: NOW, pdf_parser=parser
    ).evaluate(seeded.candidate, seeded.context)

    assert decision.outcome == "quarantined"
    assert decision.gate_results["locator"]["passed"] is False
    assert set(decision.gate_results) == {"source", "temporal", "locator", "semantic"}


def test_evaluation_rechecks_lease_immediately_before_persistence(session) -> None:
    seeded = _seed(session)
    seeded.job.lease_expires_at = NOW + timedelta(minutes=1)
    session.flush()
    clock = _SteppingClock(
        NOW,
        NOW,
        NOW,
        NOW + timedelta(minutes=2),
    )

    with pytest.raises(StaleLeaseError):
        AutomaticAdmissionGate(session, clock=clock).evaluate(
            seeded.candidate, seeded.context
        )

    assert _counts(session) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("quarantined", "fence_number"),
    [(False, 2), (True, 3)],
)
def test_sqlite_write_cas_blocks_token_rotation_before_decision_or_exception_flush(
    session, quarantined, fence_number
) -> None:
    seeded = _seed(session, **({"subject": None} if quarantined else {}))
    engine = session.get_bind()
    listener, calls = _rotate_token_on_fence(
        engine, job_id=seeded.job.id, fence_number=fence_number
    )
    try:
        with pytest.raises(StaleLeaseError):
            AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
                seeded.candidate, seeded.context
            )
    finally:
        event.remove(engine, "before_execute", listener)

    assert calls() >= fence_number
    assert _counts(session) == (0, 0, 0, 0, 0)


def test_idempotent_decision_read_uses_write_cas_boundary(session) -> None:
    seeded = _seed(session)
    gate = AutomaticAdmissionGate(session, clock=lambda: NOW)
    first = gate.evaluate(seeded.candidate, seeded.context)
    statements: list[str] = []

    def capture(connection, clauseelement, multiparams, params, execution_options):
        statements.append(str(clauseelement))

    engine = session.get_bind()
    event.listen(engine, "before_execute", capture)
    try:
        second = gate.evaluate(seeded.candidate, seeded.context)
    finally:
        event.remove(engine, "before_execute", capture)

    assert second.id == first.id
    assert any(
        sql.startswith("UPDATE acquisition_jobs SET updated_at=")
        and "status" in sql
        and "stage" in sql
        and "lease_token" in sql
        and "lease_expires_at" in sql
        for sql in statements
    )


def test_evaluation_rejects_request_mutation_between_gates_and_write(
    session, monkeypatch
) -> None:
    seeded = _seed(session)
    gate = AutomaticAdmissionGate(session, clock=lambda: NOW)
    real_fence = gate._write_fence
    calls = 0

    def mutate_then_fence(context, expected_digest):
        nonlocal calls
        calls += 1
        if calls == 1:
            changed = dict(seeded.job.request_snapshot)
            changed["metric_terms"] = ["净利润"]
            seeded.job.request_snapshot = changed
            session.flush()
        return real_fence(context, expected_digest)

    monkeypatch.setattr(gate, "_write_fence", mutate_then_fence)

    with pytest.raises(ConflictError, match="changed"):
        gate.evaluate(seeded.candidate, seeded.context)

    assert _counts(session) == (0, 0, 0, 0, 0)


def test_publication_rechecks_lease_immediately_before_writes(session) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    seeded.job.lease_expires_at = NOW + timedelta(minutes=1)
    session.flush()
    clock = _SteppingClock(NOW, NOW + timedelta(minutes=2))

    with pytest.raises(StaleLeaseError):
        AtomicClaimService(session, clock=clock).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize("fence_number", [2, 3])
def test_sqlite_write_cas_rolls_back_partial_automatic_publication(
    session, fence_number
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    engine = session.get_bind()
    listener, calls = _rotate_token_on_fence(
        engine, job_id=seeded.job.id, fence_number=fence_number
    )
    try:
        with pytest.raises(StaleLeaseError):
            AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
                seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
            )
    finally:
        event.remove(engine, "before_execute", listener)

    assert calls() >= fence_number
    assert _counts(session) == (1, 0, 0, 0, 0)


def test_idempotent_publication_read_uses_write_cas_boundary(session) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    service = AtomicClaimService(session, clock=lambda: NOW)
    first_statement, first_link = service.publish_automatically(
        seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
    )
    statements: list[str] = []

    def capture(connection, clauseelement, multiparams, params, execution_options):
        statements.append(str(clauseelement))

    engine = session.get_bind()
    event.listen(engine, "before_execute", capture)
    try:
        second_statement, second_link = service.publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )
    finally:
        event.remove(engine, "before_execute", capture)

    assert second_statement.id == first_statement.id
    assert second_link.id == first_link.id
    assert any(
        sql.startswith("UPDATE acquisition_jobs SET updated_at=") for sql in statements
    )


def test_publication_revalidates_contract_at_publication_clock(session) -> None:
    seeded = _seed(session, contract_effective_until=NOW + timedelta(minutes=1))
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )

    with pytest.raises(ValidationError, match="contract"):
        AtomicClaimService(
            session, clock=lambda: NOW + timedelta(minutes=2)
        ).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("metric_terms", ["净利润"]),
        ("entity_names", ["另一家公司"]),
        ("security_codes", ["000001"]),
        ("allowed_source_roles", ["licensed_provider"]),
        ("cutoff", "2026-08-12T15:59:59Z"),
        ("period_start", "2025-01-01"),
        ("round", 99),
        ("thesis_statement", "mutated thesis"),
        ("future_security_field", {"enabled": False}),
    ],
)
def test_publication_rejects_any_frozen_request_security_mutation(
    session, field, value
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    changed = dict(seeded.job.request_snapshot)
    changed[field] = value
    seeded.job.request_snapshot = changed
    session.flush()

    with pytest.raises(ValidationError, match="audit|digest|request"):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("enabled_adapter_keys", ["gildata", "szse"]),
        ("version", "other-policy"),
        ("exact_hosts", ["evil.example"]),
        ("max_response_bytes", 1),
        ("permission_declarations", [["sse", "mutated"]]),
    ],
)
def test_publication_rejects_frozen_policy_snapshot_mutation(
    session, field, value
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    changed = dict(seeded.job.policy_snapshot)
    changed[field] = value
    seeded.job.policy_snapshot = changed
    session.flush()

    with pytest.raises(ValidationError, match="audit|digest|request"):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)


@pytest.mark.parametrize(
    ("table", "assignment"),
    [
        ("source_contracts", "allow_display = 0"),
        ("source_contracts", "allow_ai_processing = 0"),
        ("source_contracts", "source_type = 'licensed_provider'"),
        ("acquisition_attempts", "operation = 'search'"),
        ("acquisition_attempts", "outcome = 'failed'"),
        ("acquisition_attempts", "started_at = '2026-08-12 15:59:59'"),
        ("acquisition_attempts", "finished_at = '2026-08-12 16:00:01'"),
        ("source_references", "source_role = 'licensed_provider'"),
        (
            "source_references",
            'metadata_json = \'{"provider_identity":"Wrong Provider"}\'',
        ),
        ("source_references", "published_at = '2026-08-12 16:00:01'"),
        (
            "document_versions",
            "source_url = 'https://www.sse.com.cn/disclosure/other.pdf'",
        ),
        ("document_versions", "published_at = '2026-08-11 16:00:00'"),
        ("document_versions", "available_at = '2026-08-12 16:00:01'"),
        ("document_versions", "acquired_at = '2026-08-12 16:00:01'"),
    ],
)
def test_publication_revalidates_immutable_source_attempt_and_temporal_facts(
    session, table, assignment
) -> None:
    seeded = _seed(session)
    decision = AutomaticAdmissionGate(session, clock=lambda: NOW).evaluate(
        seeded.candidate, seeded.context
    )
    if table == "source_contracts":
        row = session.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == seeded.document.id
            )
        )
        assert row is not None
        row_id = row.id
    elif table == "acquisition_attempts":
        row_id = seeded.artifact.attempt_id
    elif table == "document_versions":
        row_id = seeded.document.id
    else:
        row_id = seeded.reference.id
    session.connection().exec_driver_sql(
        f"UPDATE {table} SET {assignment} WHERE id = ?", (row_id.hex,)
    )
    session.expire_all()

    with pytest.raises(ValidationError):
        AtomicClaimService(session, clock=lambda: NOW).publish_automatically(
            seeded.candidate.id, decision.id, lease_token=LEASE_TOKEN
        )

    assert _counts(session) == (1, 0, 0, 0, 0)
