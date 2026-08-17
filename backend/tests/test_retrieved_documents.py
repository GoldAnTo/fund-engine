from __future__ import annotations

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from app.acquisition.sources import RetrievedEnvelope, SourceReferenceValue
from app.datasources.docling import ParsedSpan
from app.documents.locators import SourceLocatorV1, TextPosition, TextQuote
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    Base,
    CaseDocumentVersion,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
    Thesis,
)
from app.models.source_governance import ProviderRecord, SourceContract
from app.repositories.documents import DocumentRepository
from app.repositories.acquisition import StaleLeaseError
from app.services.ingest import DocumentService
from app.services.retrieved_documents import (
    FetchCheckpointContext,
    FrozenRequestContext,
    RetrievedDocumentFreezer,
)


NOW = datetime(2026, 8, 13, 9, 30, tzinfo=UTC)
LEASE_TOKEN = "fixture-lease-token"


class MutableClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FixturePdfParser:
    parser_version = "fixture-pdf-v1"

    def __init__(
        self,
        *,
        failure: Exception | None = None,
        on_parse=None,
    ) -> None:
        self.calls = 0
        self.failure = failure
        self.on_parse = on_parse

    def extract_spans(self, raw: bytes, *, document_sha256: str):
        self.calls += 1
        if self.on_parse is not None:
            self.on_parse()
        if self.failure is not None:
            raise self.failure
        text = "PDF fixture paragraph"
        locator = SourceLocatorV1(
            document_sha256=document_sha256,
            page=2,
            parser_version=self.parser_version,
            text_position=TextPosition(start=4, end=4 + len(text)),
            text_quote=TextQuote(exact=text, prefix="pre", suffix="post"),
        )
        return [
            ParsedSpan(
                locator=locator,
                verbatim_text=text,
                text_sha256=hashlib.sha256(text.encode()).hexdigest(),
                context_hash="c" * 64,
            )
        ]


def _factory(session):
    return sessionmaker(bind=session.get_bind(), future=True, expire_on_commit=False)


def _seed_job(session, *, tenant_id="team-a", stage="freezing"):
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
        statement="test thesis",
        created_by="tester",
        created_at=NOW,
    )
    session.add(thesis)
    session.flush()
    job = AcquisitionJob(
        tenant_id=tenant_id,
        research_case_id=case.id,
        thesis_id=thesis.id,
        research_run_id=None,
        idempotency_key=uuid.uuid4().hex,
        request_snapshot={},
        policy_snapshot={"version": "test-v1"},
        status="running",
        stage=stage,
        attempt=1,
        reference_count=0,
        fetched_count=0,
        frozen_count=0,
        admitted_count=0,
        exception_count=0,
        lease_owner="worker-a",
        lease_token=LEASE_TOKEN,
        lease_expires_at=NOW + timedelta(days=365),
        created_at=NOW,
        updated_at=NOW,
    )
    session.add(job)
    session.commit()
    return case, job


def _add_reference(
    session,
    job,
    *,
    external_record_id=None,
    external_version="v1",
    canonical_url="https://issuer.example/report.pdf",
    title="Issuer annual report",
    source_role="company_disclosure",
    adapter_key="fixture.exchange",
    attempt_no=None,
    metadata=None,
    outcome="succeeded",
    finished_at=NOW,
    operation="fetch",
    source_reference_id=None,
    published_at=NOW,
):
    attempt_no = attempt_no or (
        session.scalar(
            select(func.count(AcquisitionAttempt.id)).where(
                AcquisitionAttempt.job_id == job.id
            )
        )
        + 1
    )
    reference = SourceReference(
        job_id=job.id,
        adapter_key=adapter_key,
        external_record_id=external_record_id or uuid.uuid4().hex,
        external_version=external_version,
        canonical_url=canonical_url,
        title=title,
        published_at=published_at,
        source_role=source_role,
        metadata_json=metadata
        or {
            "provider_identity": "issuer.example",
            "security_code": "600001",
            "source_publication": "2026-08-13",
        },
        created_at=NOW,
    )
    session.add(reference)
    session.flush()
    attempt = AcquisitionAttempt(
        job_id=job.id,
        adapter_key=adapter_key,
        operation=operation,
        attempt_no=attempt_no,
        started_at=NOW,
        finished_at=finished_at,
        outcome=outcome,
        retryable=False,
        safe_metadata={"source_reference_id": str(source_reference_id or reference.id)},
    )
    session.add(attempt)
    session.commit()
    return reference, attempt


def _context(
    job,
    attempt,
    case,
    *,
    source_metadata=None,
    retrieved_at=NOW,
    lease_token=LEASE_TOKEN,
):
    return FrozenRequestContext(
        job_id=job.id,
        attempt_id=attempt.id,
        research_case_id=case.id,
        tenant_id=job.tenant_id,
        declared_actor="system:acquisition-worker",
        lease_token=lease_token,
        source_metadata=source_metadata
        or {
            "permissions": {"ai_processing": True, "display": True},
            "region": "CN",
            "retention_policy": "case_retained",
            "deletion_policy": "contractual",
        },
        retrieved_at=retrieved_at,
    )


def _envelope(content=b"first line\nsecond line\n", *, final_url=None, mime=None):
    return RetrievedEnvelope(
        content=content,
        mime_type=mime or "text/plain; charset=utf-8",
        final_url=final_url or "https://cdn.issuer.example/final/report.txt",
        etag='"fixture-etag"',
        last_modified="Thu, 13 Aug 2026 09:30:00 GMT",
        provider_request_id="request-123",
        metadata={"adapter_key": "fixture.exchange"},
    )


def _fetch_context(job, case, *, retrieved_at=NOW):
    return FetchCheckpointContext(
        job_id=job.id,
        research_case_id=case.id,
        tenant_id=job.tenant_id,
        declared_actor="system:acquisition-worker@test",
        lease_token=LEASE_TOKEN,
        claim_attempt=job.attempt,
        retrieved_at=retrieved_at,
    )


def _checkpoint_reference(session, job):
    reference = SourceReference(
        job_id=job.id,
        adapter_key="fixture.exchange",
        external_record_id=uuid.uuid4().hex,
        external_version="v1",
        canonical_url="https://issuer.example/report.txt",
        title="Issuer checkpoint report",
        published_at=NOW,
        source_role="company_disclosure",
        metadata_json={"provider_identity": "issuer.example"},
        created_at=NOW,
    )
    session.add(reference)
    session.commit()
    return reference


def _inline_reference():
    return SourceReferenceValue(
        adapter_key="fixture.exchange",
        external_record_id="inline-report-1",
        external_version="v1",
        canonical_url="https://issuer.example/inline.txt",
        title="Issuer inline report",
        published_at=NOW,
        source_role="company_disclosure",
        fetch_locator={"canonical_url": "https://issuer.example/inline.txt"},
        metadata={"provider_identity": "issuer.example"},
    )


def test_fetch_checkpoint_rejects_provider_observation_before_attempt_start(session):
    case, job = _seed_job(session, stage="fetching")
    reference = _checkpoint_reference(session, job)
    freezer = RetrievedDocumentFreezer(
        _factory(session), clock=MutableClock(NOW + timedelta(minutes=10))
    )

    with pytest.raises(
        ValueError, match="retrieved_at must not precede started_at"
    ):
        freezer.checkpoint_fetch(
            reference,
            _envelope(),
            _fetch_context(job, case, retrieved_at=NOW + timedelta(seconds=1)),
            started_at=NOW + timedelta(seconds=2),
        )

    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 0
        assert verify.scalar(select(func.count(AcquisitionAttempt.id))) == 0
        persisted_job = verify.get(AcquisitionJob, job.id)
        assert persisted_job is not None and persisted_job.status == "running"


def test_fetch_checkpoint_preserves_exact_provider_observation_and_retry_is_idempotent(
    session,
):
    case, job = _seed_job(session, stage="fetching")
    reference = _checkpoint_reference(session, job)
    observed_at = NOW + timedelta(seconds=1)
    freezer = RetrievedDocumentFreezer(
        _factory(session), clock=MutableClock(NOW + timedelta(minutes=10))
    )
    context = _fetch_context(job, case, retrieved_at=observed_at)

    first_artifact_id, first_attempt_id = freezer.checkpoint_fetch(
        reference, _envelope(), context, started_at=NOW
    )
    replay_artifact_id, replay_attempt_id = freezer.checkpoint_fetch(
        reference,
        _envelope(),
        _fetch_context(job, case, retrieved_at=NOW + timedelta(minutes=5)),
        started_at=NOW,
    )

    assert (replay_artifact_id, replay_attempt_id) == (
        first_artifact_id,
        first_attempt_id,
    )
    with _factory(session)() as verify:
        artifact = verify.get(RetrievalArtifact, first_artifact_id)
        attempt = verify.get(AcquisitionAttempt, first_attempt_id)
        assert artifact is not None and attempt is not None
        assert attempt.finished_at == observed_at.replace(tzinfo=None)
        assert artifact.retrieved_at == observed_at.replace(tzinfo=None)
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 1
        assert verify.scalar(select(func.count(AcquisitionAttempt.id))) == 1


def test_inline_checkpoint_retains_provider_observation_and_monotonic_persisted_time(
    session,
):
    case, job = _seed_job(session, stage="searching")
    provider_observed_at = NOW + timedelta(seconds=1)
    checkpoint_at = NOW + timedelta(seconds=2)
    freezer = RetrievedDocumentFreezer(
        _factory(session), clock=MutableClock(checkpoint_at)
    )
    reference = _inline_reference()
    envelope = _envelope(
        b"inline response",
        final_url="https://issuer.example/inline.txt",
    )

    reference_id, artifact_id, attempt_id = freezer.checkpoint_search_result(
        reference,
        envelope,
        _fetch_context(job, case, retrieved_at=provider_observed_at),
        started_at=NOW,
    )
    replay_ids = freezer.checkpoint_search_result(
        reference,
        envelope,
        _fetch_context(job, case, retrieved_at=NOW + timedelta(minutes=5)),
        started_at=NOW,
    )

    assert replay_ids == (reference_id, artifact_id, attempt_id)
    with _factory(session)() as verify:
        persisted_reference = verify.get(SourceReference, reference_id)
        artifact = verify.get(RetrievalArtifact, artifact_id)
        attempt = verify.get(AcquisitionAttempt, attempt_id)
        assert (
            persisted_reference is not None
            and artifact is not None
            and attempt is not None
        )
        assert attempt.safe_metadata["provider_response_observed_at"] == (
            provider_observed_at.isoformat()
        )
        assert persisted_reference.created_at <= attempt.started_at
        assert attempt.started_at <= attempt.finished_at
        assert attempt.finished_at == artifact.retrieved_at
        assert attempt.finished_at == checkpoint_at.replace(tzinfo=None)
        assert verify.scalar(select(func.count(SourceReference.id))) == 1
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 1
        assert verify.scalar(select(func.count(AcquisitionAttempt.id))) == 1


def _seed_file_sqlite(tmp_path, *, reference_count=2):
    engine = create_engine(
        f"sqlite:///{tmp_path / f'retrieved-{uuid.uuid4().hex}.sqlite3'}",
        future=True,
        connect_args={"timeout": 5, "check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    with sessions() as seed:
        case, job = _seed_job(seed)
        references = [
            _add_reference(
                seed,
                job,
                canonical_url=f"https://mirror-{index}.issuer.example/report.pdf",
            )
            for index in range(reference_count)
        ]
    return engine, sessions, case, job, references


def test_created_artifact_document_governance_case_and_text_locators(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    freezer = RetrievedDocumentFreezer(_factory(session))

    result = freezer.freeze(reference, _envelope(), _context(job, attempt, case))

    assert result.relation == "created"
    assert result.document is not None
    assert result.artifact.content_sha256 == result.document.content_sha256
    assert result.artifact.byte_size == len(result.artifact.raw_bytes)
    assert result.binding is not None
    with _factory(session)() as verify:
        spans = list(
            verify.scalars(
                select(SourceSpan).where(
                    SourceSpan.document_version_id == result.document.id
                )
            )
        )
        spans.sort(key=lambda span: span.locator_v1["text_position"]["start"])
        assert [span.verbatim_text for span in spans] == ["first line", "second line"]
        assert spans[0].locator_v1["schema"] == "source-locator/v1"
        assert spans[0].locator_v1["text_position"] == {"start": 0, "end": 10}
        assert spans[0].locator_v1["text_quote"]["exact"] == "first line"
        assert spans[0].text_sha256 and spans[0].context_hash
        assert verify.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == result.document.id
            )
        )
        assert verify.scalar(
            select(CaseDocumentVersion).where(
                CaseDocumentVersion.research_case_id == case.id,
                CaseDocumentVersion.document_version_id == result.document.id,
            )
        )


def test_exact_identity_retry_reuses_outcome_without_parsing_again(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    parser = FixturePdfParser()
    freezer = RetrievedDocumentFreezer(_factory(session), pdf_parser=parser)
    envelope = _envelope(b"%PDF-fixture", mime="application/pdf")

    first = freezer.freeze(reference, envelope, _context(job, attempt, case))
    replay = freezer.freeze(reference, envelope, _context(job, attempt, case))

    assert replay.relation == first.relation == "created"
    assert replay.artifact.id == first.artifact.id
    assert replay.document.id == first.document.id
    assert parser.calls == 1
    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 1
        assert verify.scalar(select(func.count(RetrievalArtifactDocument.id))) == 1


def test_exact_identity_rejects_divergent_envelope_without_second_artifact(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    freezer = RetrievedDocumentFreezer(_factory(session))
    freezer.freeze(reference, _envelope(b"original"), _context(job, attempt, case))

    conflict = freezer.freeze(
        reference, _envelope(b"changed"), _context(job, attempt, case)
    )

    assert conflict.relation == "variant_conflict"
    assert conflict.document is None
    assert conflict.exception.reason_code == "variant_conflict"
    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 1


def test_same_bytes_under_another_reference_are_content_duplicate(session):
    case, job = _seed_job(session)
    first_ref, first_attempt = _add_reference(session, job)
    other_ref, other_attempt = _add_reference(session, job)
    freezer = RetrievedDocumentFreezer(_factory(session))
    envelope = _envelope(b"identical bytes")

    first = freezer.freeze(first_ref, envelope, _context(job, first_attempt, case))
    duplicate = freezer.freeze(other_ref, envelope, _context(job, other_attempt, case))

    assert duplicate.relation == "content_duplicate"
    assert duplicate.document.id == first.document.id
    assert duplicate.binding.relation == "content_duplicate"
    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(SourceSpan.id))) == 1


def test_same_publication_different_url_and_bytes_is_variant_conflict(session):
    case, job = _seed_job(session)
    first_ref, first_attempt = _add_reference(
        session, job, canonical_url="https://issuer.example/a.pdf"
    )
    variant_ref, variant_attempt = _add_reference(
        session, job, canonical_url="https://mirror.example/a.pdf"
    )
    freezer = RetrievedDocumentFreezer(_factory(session))
    freezer.freeze(
        first_ref, _envelope(b"edition A"), _context(job, first_attempt, case)
    )

    variant = freezer.freeze(
        variant_ref,
        _envelope(b"edition B"),
        _context(job, variant_attempt, case),
    )

    assert variant.relation == "variant_conflict"
    assert variant.document is None
    assert variant.binding is None
    assert variant.exception.reason_code == "variant_conflict"
    with _factory(session)() as verify:
        assert (
            verify.scalar(
                select(RetrievalArtifactDocument).where(
                    RetrievalArtifactDocument.retrieval_artifact_id
                    == variant.artifact.id
                )
            )
            is None
        )


def test_postgresql_publication_locks_are_namespaced_sorted_and_parameterized():
    canonical_url = "https://issuer.example/report.pdf"
    publication_key = "f" * 64
    locks = RetrievedDocumentFreezer.postgresql_publication_locks(
        canonical_url=canonical_url,
        publication_key=publication_key,
    )

    assert len(locks) == 2
    lock_ids = [lock_id for _statement, lock_id in locks]
    assert lock_ids == sorted(lock_ids)
    assert len(set(lock_ids)) == 2
    assert all(-(2**63) <= lock_id < 2**63 for lock_id in lock_ids)
    for index, (statement, lock_id) in enumerate(locks):
        compiled = statement.compile(dialect=postgresql.dialect())
        parameter = f"advisory_lock_id_{index}"
        assert "pg_advisory_xact_lock" in str(compiled)
        assert f"%({parameter})s" in str(compiled)
        assert compiled.params == {parameter: lock_id}

    # The domain separator must keep URL and publication identities distinct,
    # even when their literal identity strings happen to be identical.
    same_identity_locks = RetrievedDocumentFreezer.postgresql_publication_locks(
        canonical_url=publication_key,
        publication_key=publication_key,
    )
    assert len({lock_id for _statement, lock_id in same_identity_locks}) == 2


def test_sqlite_publication_lock_prevents_two_created_variants(tmp_path):
    engine, sessions, case, job, references = _seed_file_sqlite(tmp_path)
    barrier = Barrier(2)
    parser = FixturePdfParser(on_parse=lambda: barrier.wait(timeout=5))
    freezer = RetrievedDocumentFreezer(sessions, pdf_parser=parser, clock=lambda: NOW)
    envelopes = [
        _envelope(
            f"%PDF variant {index}".encode(),
            mime="application/pdf",
            final_url=f"https://cdn.issuer.example/report-{index}.pdf",
        )
        for index in range(2)
    ]

    def freeze(index):
        reference, attempt = references[index]
        return freezer.freeze(
            reference.id,
            envelopes[index],
            _context(job, attempt, case),
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(freeze, range(2)))
        assert sorted(result.relation for result in results) == [
            "created",
            "variant_conflict",
        ]
        with sessions() as verify:
            assert verify.scalar(select(func.count(DocumentVersion.id))) == 1
            assert verify.scalar(select(func.count(AcquisitionException.id))) == 1
    finally:
        engine.dispose()


def test_sqlite_concurrent_parser_exception_is_idempotent(tmp_path):
    engine, sessions, case, job, references = _seed_file_sqlite(
        tmp_path, reference_count=1
    )
    reference, attempt = references[0]
    barrier = Barrier(2)
    parser = FixturePdfParser(
        on_parse=lambda: barrier.wait(timeout=5),
        failure=RuntimeError("Bearer must-not-persist"),
    )
    freezer = RetrievedDocumentFreezer(sessions, pdf_parser=parser, clock=lambda: NOW)
    envelope = _envelope(b"%PDF invalid", mime="application/pdf")

    def freeze(_index):
        return freezer.freeze(reference.id, envelope, _context(job, attempt, case))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(freeze, range(2)))
        assert {result.relation for result in results} == {"parser_failure"}
        assert len({result.exception.id for result in results}) == 1
        with sessions() as verify:
            assert verify.scalar(select(func.count(AcquisitionException.id))) == 1
            assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 1
    finally:
        engine.dispose()


def test_same_canonical_url_changed_bytes_creates_superseding_version(session):
    case, job = _seed_job(session)
    first_ref, first_attempt = _add_reference(session, job, external_version="v1")
    next_ref, next_attempt = _add_reference(session, job, external_version="v2")
    freezer = RetrievedDocumentFreezer(_factory(session))
    first = freezer.freeze(
        first_ref, _envelope(b"old publication"), _context(job, first_attempt, case)
    )

    updated = freezer.freeze(
        next_ref, _envelope(b"corrected publication"), _context(job, next_attempt, case)
    )

    assert updated.relation == "supersedes"
    assert updated.document.supersedes_id == first.document.id
    assert updated.document.id != first.document.id
    assert updated.binding.relation == "supersedes"
    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(SourceSpan.id))) == 2
        assert verify.scalar(select(func.count(SourceContract.id))) == 2
        assert verify.scalar(select(func.count(CaseDocumentVersion.id))) == 2


def test_same_url_changed_title_and_date_uses_exact_supersession_predecessor(session):
    case, job = _seed_job(session)
    url = "https://issuer.example/corrected-report.pdf"
    first_ref, first_attempt = _add_reference(
        session,
        job,
        external_version="v1",
        canonical_url=url,
        title="Original report title",
        published_at=NOW,
        metadata={
            "provider_identity": "issuer.example",
            "security_code": "600001",
            "source_publication": "2026-08-13",
        },
    )
    second_ref, second_attempt = _add_reference(
        session,
        job,
        external_version="v2",
        canonical_url=url,
        title="Corrected report title",
        published_at=NOW + timedelta(days=1),
        metadata={
            "provider_identity": "issuer.example",
            "security_code": "600001",
            "source_publication": "2026-08-14",
        },
    )
    freezer = RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW)
    first = freezer.freeze(
        first_ref, _envelope(b"original body"), _context(job, first_attempt, case)
    )

    corrected = freezer.freeze(
        second_ref,
        _envelope(b"corrected body"),
        _context(job, second_attempt, case),
    )

    assert corrected.relation == "supersedes"
    assert corrected.binding.relation == "supersedes"
    assert corrected.document.supersedes_id == first.document.id
    assert not (
        corrected.relation == "created" and corrected.document.supersedes_id is not None
    )


def test_alternating_publication_keys_form_linear_canonical_url_chain(session):
    case, job = _seed_job(session)
    url = "https://issuer.example/rolling-report.pdf"
    k1_metadata = {
        "provider_identity": "issuer.example",
        "security_code": "600001",
        "source_publication": "2026-08-13",
    }
    k2_metadata = {
        "provider_identity": "issuer.example",
        "security_code": "600001",
        "source_publication": "2026-08-14",
    }
    first_ref, first_attempt = _add_reference(
        session,
        job,
        external_version="v1",
        canonical_url=url,
        title="K1 title",
        metadata=k1_metadata,
        published_at=NOW,
    )
    second_ref, second_attempt = _add_reference(
        session,
        job,
        external_version="v2",
        canonical_url=url,
        title="K2 title",
        metadata=k2_metadata,
        published_at=NOW + timedelta(days=1),
    )
    third_ref, third_attempt = _add_reference(
        session,
        job,
        external_version="v3",
        canonical_url=url,
        title="K1 title",
        metadata=k1_metadata,
        published_at=NOW,
    )
    freezer = RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW)

    first = freezer.freeze(
        first_ref, _envelope(b"K1 first body"), _context(job, first_attempt, case)
    )
    second = freezer.freeze(
        second_ref, _envelope(b"K2 body"), _context(job, second_attempt, case)
    )
    third = freezer.freeze(
        third_ref, _envelope(b"K1 revised body"), _context(job, third_attempt, case)
    )

    assert first.relation == "created"
    assert second.relation == "supersedes"
    assert second.binding.relation == "supersedes"
    assert second.document.supersedes_id == first.document.id
    assert third.relation == "supersedes"
    assert third.binding.relation == "supersedes"
    assert third.document.supersedes_id == second.document.id
    assert third.document.supersedes_id != first.document.id


def test_parser_failure_keeps_committed_artifact_visible_and_sanitizes_exception(
    session,
):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    parser = FixturePdfParser(failure=RuntimeError("Bearer super-secret-token"))
    freezer = RetrievedDocumentFreezer(_factory(session), pdf_parser=parser)

    result = freezer.freeze(
        reference,
        _envelope(b"not really a pdf", mime="application/pdf"),
        _context(job, attempt, case),
    )

    assert result.relation == "parser_failure"
    assert result.document is None
    assert result.exception.reason_code == "parser_failure"
    assert "secret" not in repr(result.exception.detail_json).lower()
    replay = freezer.freeze(
        reference,
        _envelope(b"not really a pdf", mime="application/pdf"),
        _context(job, attempt, case),
    )
    assert replay.exception.id == result.exception.id
    assert parser.calls == 1
    with _factory(session)() as independent:
        assert (
            independent.get(RetrievalArtifact, result.artifact.id).raw_bytes
            == b"not really a pdf"
        )
        assert (
            independent.scalar(
                select(RetrievalArtifactDocument).where(
                    RetrievalArtifactDocument.retrieval_artifact_id
                    == result.artifact.id
                )
            )
            is None
        )
        assert independent.scalar(select(func.count(AcquisitionException.id))) == 1


@pytest.mark.parametrize(
    ("content", "mime"),
    [(b"image", "image/png"), (b"\xff\xfe", "text/plain; charset=utf-8")],
)
def test_unsupported_or_invalid_text_is_parser_failure_after_artifact_commit(
    session, content, mime
):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    result = RetrievedDocumentFreezer(_factory(session)).freeze(
        reference, _envelope(content, mime=mime), _context(job, attempt, case)
    )
    assert result.relation == "parser_failure"
    with _factory(session)() as verify:
        assert verify.get(RetrievalArtifact, result.artifact.id) is not None


def test_canonical_and_redirect_final_urls_are_both_auditable(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(
        session, job, canonical_url="https://issuer.example/canonical.pdf"
    )
    final_url = "https://cdn.issuer.example/redirected.pdf"
    result = RetrievedDocumentFreezer(_factory(session)).freeze(
        reference,
        _envelope(b"redirected body", final_url=final_url),
        _context(job, attempt, case),
    )
    with _factory(session)() as verify:
        stored_reference = verify.get(SourceReference, reference.id)
        stored_artifact = verify.get(RetrievalArtifact, result.artifact.id)
        assert stored_reference.canonical_url == "https://issuer.example/canonical.pdf"
        assert stored_artifact.final_url == final_url


def test_attempt_reference_job_adapter_and_operation_lineage_is_enforced(session):
    case, job = _seed_job(session)
    other_case, other_job = _seed_job(session)
    reference, _attempt = _add_reference(session, job)
    _other_ref, other_attempt = _add_reference(session, other_job)
    freezer = RetrievedDocumentFreezer(_factory(session))

    with pytest.raises(ValueError, match="lineage"):
        freezer.freeze(
            reference,
            _envelope(),
            _context(job, other_attempt, case),
        )
    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 0


@pytest.mark.parametrize("lease_state", ["missing", "wrong", "expired"])
def test_artifact_write_requires_current_unexpired_lease(session, lease_state):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    context_token = LEASE_TOKEN
    with _factory(session)() as mutate:
        stored = mutate.get(AcquisitionJob, job.id)
        if lease_state == "missing":
            stored.lease_owner = None
            stored.lease_token = None
            stored.lease_expires_at = None
        elif lease_state == "wrong":
            context_token = "stale-worker-token"
        else:
            stored.lease_expires_at = NOW
        mutate.commit()

    with pytest.raises(StaleLeaseError, match="lease"):
        RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW).freeze(
            reference,
            _envelope(),
            _context(job, attempt, case, lease_token=context_token),
        )

    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 0


def test_lease_expiring_during_parser_keeps_artifact_but_publishes_nothing(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    with _factory(session)() as mutate:
        mutate.get(AcquisitionJob, job.id).lease_expires_at = NOW + timedelta(hours=1)
        mutate.commit()
    clock = MutableClock()
    parser = FixturePdfParser(
        on_parse=lambda: setattr(clock, "now", NOW + timedelta(hours=2))
    )
    freezer = RetrievedDocumentFreezer(
        _factory(session), pdf_parser=parser, clock=clock
    )

    with pytest.raises(StaleLeaseError, match="lease"):
        freezer.freeze(
            reference,
            _envelope(b"%PDF fixture", mime="application/pdf"),
            _context(job, attempt, case),
        )

    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 1
        assert verify.scalar(select(func.count(DocumentVersion.id))) == 0
        assert verify.scalar(select(func.count(RetrievalArtifactDocument.id))) == 0
        assert verify.scalar(select(func.count(AcquisitionException.id))) == 0


def test_rotated_lease_between_artifact_and_document_phase_is_rejected(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)

    def rotate_lease():
        with _factory(session)() as mutate:
            stored = mutate.get(AcquisitionJob, job.id)
            stored.lease_token = "replacement-token"
            mutate.commit()

    parser = FixturePdfParser(on_parse=rotate_lease)
    freezer = RetrievedDocumentFreezer(
        _factory(session), pdf_parser=parser, clock=lambda: NOW
    )

    with pytest.raises(StaleLeaseError, match="lease"):
        freezer.freeze(
            reference,
            _envelope(b"%PDF fixture", mime="application/pdf"),
            _context(job, attempt, case),
        )

    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 1
        assert verify.scalar(select(func.count(DocumentVersion.id))) == 0


@pytest.mark.parametrize(
    ("attempt_overrides", "expected"),
    [
        ({"source_reference_id": uuid.uuid4()}, "lineage"),
        ({"outcome": "failed"}, "lineage"),
        ({"finished_at": None}, "lineage"),
        ({"operation": "search"}, "lineage"),
    ],
)
def test_attempt_must_be_exact_successful_finished_fetch(
    session, attempt_overrides, expected
):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job, **attempt_overrides)

    with pytest.raises(ValueError, match=expected):
        RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW).freeze(
            reference, _envelope(), _context(job, attempt, case)
        )

    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 0


def test_same_job_adapter_attempt_for_another_reference_is_rejected(session):
    case, job = _seed_job(session)
    first_reference, _first_attempt = _add_reference(session, job)
    _other_reference, other_attempt = _add_reference(session, job)

    with pytest.raises(ValueError, match="lineage"):
        RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW).freeze(
            first_reference,
            _envelope(),
            _context(job, other_attempt, case),
        )

    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 0


def test_freeze_accepts_uuid_and_expired_detached_reference(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    default_sessions = sessionmaker(bind=session.get_bind(), future=True)
    detached_session = default_sessions()
    detached = detached_session.get(SourceReference, reference.id)
    detached_session.commit()
    detached_session.close()
    freezer = RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW)

    first = freezer.freeze(
        detached, _envelope(b"detached body"), _context(job, attempt, case)
    )
    replay = freezer.freeze(
        reference.id, _envelope(b"detached body"), _context(job, attempt, case)
    )

    assert replay.artifact.id == first.artifact.id
    assert replay.document.id == first.document.id


def test_injected_pdf_parser_spans_are_persisted(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job)
    parser = FixturePdfParser()
    result = RetrievedDocumentFreezer(_factory(session), pdf_parser=parser).freeze(
        reference,
        _envelope(b"%PDF fixture", mime="application/pdf"),
        _context(job, attempt, case),
    )

    with _factory(session)() as verify:
        span = verify.scalar(
            select(SourceSpan).where(
                SourceSpan.document_version_id == result.document.id
            )
        )
        assert span.verbatim_text == "PDF fixture paragraph"
        assert span.locator_v1["page"] == 2
        assert result.document.parser_version == parser.parser_version


def test_new_frozen_document_preserves_source_availability_and_acquisition_time(session):
    case, job = _seed_job(session)
    published_at = NOW - timedelta(days=2)
    reference, attempt = _add_reference(
        session, job, published_at=published_at
    )

    result = RetrievedDocumentFreezer(
        _factory(session), clock=lambda: NOW
    ).freeze(
        reference,
        _envelope(b"new cutoff-visible announcement"),
        _context(job, attempt, case, retrieved_at=NOW),
    )

    assert result.document is not None
    assert result.document.published_at.replace(tzinfo=UTC) == published_at
    assert result.document.available_at.replace(tzinfo=UTC) == published_at
    assert result.document.acquired_at.replace(tzinfo=UTC) == NOW


def test_licensed_provider_creates_provider_record(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(
        session,
        job,
        source_role="licensed_provider",
        adapter_key="fixture.provider",
        canonical_url="fixture://provider/report-1",
        metadata={
            "provider_identity": "Fixture Data",
            "provider_record_id": "report-1",
            "security_code": "600001",
            "source_publication": "2026-08-13",
        },
    )
    context = _context(
        job,
        attempt,
        case,
        source_metadata={
            "permissions": {"ai_processing": True, "display": True},
            "provider_name": "Fixture Data",
            "provider_record_id": "report-1",
            "request_scope": {"security_code": "600001"},
            "retrieval_reference": "fixture://provider/report-1",
            "contract_version": "contract-v1",
        },
    )
    envelope = RetrievedEnvelope(
        content=b"provider report",
        mime_type="text/plain",
        final_url="fixture://provider/report-1",
        etag=None,
        last_modified=None,
        provider_request_id=None,
        metadata={"adapter_key": "fixture.provider"},
    )

    result = RetrievedDocumentFreezer(_factory(session)).freeze(
        reference, envelope, context
    )

    with _factory(session)() as verify:
        contract = verify.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == result.document.id
            )
        )
        provider = verify.scalar(
            select(ProviderRecord).where(
                ProviderRecord.document_version_id == result.document.id
            )
        )
        assert (
            contract.source_type == contract.research_source_type == "licensed_provider"
        )
        assert provider.provider_name == "Fixture Data"
        assert provider.provider_record_id == "report-1"
        assert provider.retrieval_reference == "fixture://provider/report-1"


def test_incompatible_duplicate_contract_is_quarantined_without_case_attach(session):
    first_case, first_job = _seed_job(session, tenant_id="team-a")
    first_ref, first_attempt = _add_reference(session, first_job)
    freezer = RetrievedDocumentFreezer(_factory(session))
    first = freezer.freeze(
        first_ref,
        _envelope(b"shared bytes"),
        _context(first_job, first_attempt, first_case),
    )
    second_case, second_job = _seed_job(session, tenant_id="team-b")
    second_ref, second_attempt = _add_reference(session, second_job)

    quarantined = freezer.freeze(
        second_ref,
        _envelope(b"shared bytes"),
        _context(second_job, second_attempt, second_case),
    )

    assert quarantined.relation == "incompatible_source_contract"
    assert quarantined.document is None
    assert quarantined.binding is None
    assert quarantined.exception.reason_code == "incompatible_source_contract"
    with _factory(session)() as verify:
        assert (
            verify.scalar(
                select(CaseDocumentVersion).where(
                    CaseDocumentVersion.research_case_id == second_case.id,
                    CaseDocumentVersion.document_version_id == first.document.id,
                )
            )
            is None
        )
        assert verify.scalar(select(func.count(SourceContract.id))) == 1


def test_sqlite_content_duplicate_accepts_identical_bounded_contract(session):
    metadata = {
        "permissions": {"ai_processing": True, "display": True},
        "region": "CN",
        "effective_from": "2026-08-01T00:00:00Z",
        "effective_until": "2026-08-31T00:00:00Z",
        "retention_policy": "case_retained",
        "deletion_policy": "contractual",
    }
    first_case, first_job = _seed_job(session, tenant_id="team-a")
    first_ref, first_attempt = _add_reference(session, first_job)
    freezer = RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW)
    first = freezer.freeze(
        first_ref,
        _envelope(b"bounded shared bytes"),
        _context(first_job, first_attempt, first_case, source_metadata=metadata),
    )
    second_case, second_job = _seed_job(session, tenant_id="team-a")
    second_ref, second_attempt = _add_reference(session, second_job)

    duplicate = freezer.freeze(
        second_ref,
        _envelope(b"bounded shared bytes"),
        _context(second_job, second_attempt, second_case, source_metadata=metadata),
    )

    assert duplicate.relation == "content_duplicate"
    assert duplicate.document.id == first.document.id
    with _factory(session)() as verify:
        assert verify.scalar(
            select(CaseDocumentVersion).where(
                CaseDocumentVersion.research_case_id == second_case.id,
                CaseDocumentVersion.document_version_id == first.document.id,
            )
        )


def test_sqlite_content_duplicate_rejects_genuinely_different_contract_time(session):
    base_metadata = {
        "permissions": {"ai_processing": True, "display": True},
        "region": "CN",
        "effective_from": "2026-08-01T00:00:00Z",
        "effective_until": "2026-08-31T00:00:00Z",
        "retention_policy": "case_retained",
        "deletion_policy": "contractual",
    }
    first_case, first_job = _seed_job(session, tenant_id="team-a")
    first_ref, first_attempt = _add_reference(session, first_job)
    freezer = RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW)
    first = freezer.freeze(
        first_ref,
        _envelope(b"bounded mismatch bytes"),
        _context(first_job, first_attempt, first_case, source_metadata=base_metadata),
    )
    second_case, second_job = _seed_job(session, tenant_id="team-a")
    second_ref, second_attempt = _add_reference(session, second_job)
    changed = {**base_metadata, "effective_until": "2026-09-01T00:00:00Z"}

    quarantined = freezer.freeze(
        second_ref,
        _envelope(b"bounded mismatch bytes"),
        _context(second_job, second_attempt, second_case, source_metadata=changed),
    )

    assert quarantined.relation == "incompatible_source_contract"
    with _factory(session)() as verify:
        assert (
            verify.scalar(
                select(CaseDocumentVersion).where(
                    CaseDocumentVersion.research_case_id == second_case.id,
                    CaseDocumentVersion.document_version_id == first.document.id,
                )
            )
            is None
        )


def test_request_context_rejects_naive_time_blank_identity_and_secrets():
    base = {
        "job_id": uuid.uuid4(),
        "attempt_id": uuid.uuid4(),
        "research_case_id": uuid.uuid4(),
        "tenant_id": "team-a",
        "declared_actor": "system:worker",
        "lease_token": LEASE_TOKEN,
        "source_metadata": {"permissions": {"ai_processing": True, "display": True}},
        "retrieved_at": NOW,
    }
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenRequestContext(**{**base, "retrieved_at": NOW.replace(tzinfo=None)})
    with pytest.raises(ValueError, match="tenant_id"):
        FrozenRequestContext(**{**base, "tenant_id": " "})
    with pytest.raises(ValueError, match="sensitive|forbidden"):
        FrozenRequestContext(**{**base, "source_metadata": {"api_token": "secret"}})


@pytest.mark.parametrize(
    "unsafe_metadata",
    [
        {"note": "Authorization: Basic abc123"},
        {"note": "Ｃｏｏｋｉｅ： session=abc123"},
        {"nested": [{"note": "Ｂａｓｉｃ abc123"}]},
        {"request_headers": {"safe_name": "value"}},
    ],
)
def test_request_context_rejects_recursive_or_full_width_credentials(
    unsafe_metadata,
):
    with pytest.raises(ValueError) as caught:
        FrozenRequestContext(
            job_id=uuid.uuid4(),
            attempt_id=uuid.uuid4(),
            research_case_id=uuid.uuid4(),
            tenant_id="team-a",
            declared_actor="system:worker",
            lease_token=LEASE_TOKEN,
            source_metadata=unsafe_metadata,
            retrieved_at=NOW,
        )
    assert "abc123" not in str(caught.value)


@pytest.mark.parametrize(
    ("canonical_url", "final_url"),
    [
        (
            "https://issuer.example/report.pdf?access_token=credential",
            "https://cdn.issuer.example/report.pdf",
        ),
        (
            "https://issuer.example/report.pdf",
            "https://cdn.issuer.example/report.pdf?X-Amz-Signature=credential",
        ),
        (
            "https://issuer.example/report.pdf",
            "https://user:password@cdn.issuer.example/report.pdf",
        ),
    ],
)
def test_credential_urls_never_reach_artifacts_or_contracts(
    session, canonical_url, final_url
):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(session, job, canonical_url=canonical_url)
    try:
        envelope = _envelope(b"unsafe", final_url=final_url)
    except ValueError:
        envelope = None

    if envelope is not None:
        with pytest.raises(ValueError, match="URL|credential|unsafe"):
            RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW).freeze(
                reference, envelope, _context(job, attempt, case)
            )

    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(RetrievalArtifact.id))) == 0
        assert verify.scalar(select(func.count(SourceContract.id))) == 0


def test_reference_credential_metadata_never_reaches_source_contract(session):
    case, job = _seed_job(session)
    reference, attempt = _add_reference(
        session,
        job,
        metadata={
            "provider_identity": "issuer.example",
            "security_code": "600001",
            "source_publication": "2026-08-13",
            "note": "Authorization: Basic leaked-value",
        },
    )

    with pytest.raises(ValueError) as caught:
        RetrievedDocumentFreezer(_factory(session), clock=lambda: NOW).freeze(
            reference, _envelope(), _context(job, attempt, case)
        )

    assert "leaked-value" not in str(caught.value)
    with _factory(session)() as verify:
        assert verify.scalar(select(func.count(SourceContract.id))) == 0


def test_document_service_freeze_with_status_preserves_freeze_compatibility(session):
    service = DocumentService(DocumentRepository(session))
    raw = b"backward compatible"
    source_url = f"https://example.test/{uuid.uuid4().hex}"

    first, created = service.freeze_with_status(raw=raw, source_url=source_url)
    replay, replay_created = service.freeze_with_status(raw=raw, source_url=source_url)
    legacy = service.freeze(raw=raw, source_url=source_url)

    assert created is True
    assert replay_created is False
    assert first.id == replay.id == legacy.id
