"""Freeze fetched bytes before parsing and govern their document admission.

``RetrievedDocumentFreezer`` owns its transactions through an injected session
factory.  Callers must commit the job, attempt, and source reference before
calling it.  The service first commits only the immutable retrieval artifact,
closes that session, and parses outside any write transaction.  A second
service-owned transaction creates or reuses the document, records governance,
binds provenance, and attaches the document to its Case.  Consequently parser
failure cannot roll back fetched bytes, and unrelated caller changes can never
be committed by this service.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import bindparam, func
from sqlalchemy import inspect as sqlalchemy_inspect
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.acquisition.sources import RetrievedEnvelope, SourceReferenceValue
from app.datasources.docling import PdfParserAdapter, PypdfAdapter
from app.documents.locators import (
    SourceLocatorV1,
    TextPosition,
    TextQuote,
    compute_text_sha256,
)
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionException,
    AcquisitionJob,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import DocumentVersion
from app.repositories.acquisition import (
    AcquisitionRepository,
    StaleLeaseError,
    validate_persistable_json,
    validate_persistable_text,
)
from app.repositories.documents import DocumentRepository
from app.services.ingest import DocumentService
from app.services.source_governance import SourceGovernanceService


TEXT_PARSER_VERSION = "text-plain-v1"
_WHITESPACE_RE = re.compile(r"\s+")
_ARTIFACT_STAGES = frozenset({"fetching", "freezing"})
_OUTCOME_STAGES = frozenset({"freezing", "extracting"})
_EXCEPTION_STAGES = frozenset({"fetching", "freezing", "extracting"})
_CANONICAL_URL_LOCK_DOMAIN = b"retrieved-document:canonical-url:v1\x00"
_PUBLICATION_KEY_LOCK_DOMAIN = b"retrieved-document:publication-key:v1\x00"
_CREDENTIAL_QUERY_PARTS = (
    "token",
    "key",
    "secret",
    "signature",
    "credential",
    "auth",
    "expires",
)
_CREDENTIAL_KEY_PARTS = (
    "authorization",
    "credential",
    "password",
    "secret",
    "cookie",
    "apikey",
    "token",
)
_CREDENTIAL_TEXT_RE = re.compile(
    r"(?i)(?:authorization|proxy-authorization|cookie|set-cookie)\s*[:=]|"
    r"(?:token|api[\s_-]*key|password|secret|credential)\s*[:=]|"
    r"\b(?:basic|bearer)\s+\S+"
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required_identity(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    normalized = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise ValueError(f"{field_name} contains unsafe characters")
    return normalized


def _compact_nfkc(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKC", value).casefold())


def _validate_safe_metadata(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for index, (key, child) in enumerate(value.items()):
            if not isinstance(key, str) or not key.strip():
                raise ValueError(
                    f"source_metadata has an unsafe key at {path}[{index}]"
                )
            compact_key = _compact_nfkc(key)
            if any(part in compact_key for part in _CREDENTIAL_KEY_PARTS):
                raise ValueError(
                    f"source_metadata has a forbidden key at {path}[{index}]"
                )
            if "header" in compact_key and isinstance(child, (Mapping, list, tuple)):
                raise ValueError(
                    f"source_metadata has a forbidden container at {path}[{index}]"
                )
            _validate_safe_metadata(child, path=f"{path}[{index}]")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_safe_metadata(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFKC", value)
        try:
            validate_persistable_text(normalized, path=path)
        except ValueError as exc:
            raise ValueError(
                f"source_metadata has forbidden credential text at {path}"
            ) from exc
        if _CREDENTIAL_TEXT_RE.search(normalized):
            raise ValueError(f"source_metadata has forbidden credential text at {path}")


def _validate_auditable_url(value: str, *, field_name: str) -> None:
    normalized = unicodedata.normalize("NFKC", value)
    try:
        parsed = urlsplit(normalized)
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} URL is unsafe") from exc
    if not parsed.scheme or parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} URL contains credentials")
    for key, _value in parse_qsl(parsed.query, keep_blank_values=True):
        compact_key = _compact_nfkc(key)
        if any(part in compact_key for part in _CREDENTIAL_QUERY_PARTS):
            raise ValueError(f"{field_name} URL contains credential query fields")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class FetchCheckpointContext:
    """Lease-fenced facts used to atomically checkpoint a provider response."""

    job_id: uuid.UUID
    research_case_id: uuid.UUID
    tenant_id: str
    declared_actor: str
    lease_token: str
    claim_attempt: int
    retrieved_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("job_id", "research_case_id"):
            if not isinstance(getattr(self, field_name), uuid.UUID):
                raise ValueError(f"{field_name} must be a UUID")
        object.__setattr__(
            self, "tenant_id", _required_identity(self.tenant_id, "tenant_id")
        )
        object.__setattr__(
            self,
            "declared_actor",
            _required_identity(self.declared_actor, "declared_actor"),
        )
        object.__setattr__(
            self,
            "lease_token",
            _required_identity(self.lease_token, "lease_token"),
        )
        if (
            not isinstance(self.claim_attempt, int)
            or isinstance(self.claim_attempt, bool)
            or self.claim_attempt < 1
        ):
            raise ValueError("claim_attempt must be a positive integer")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        object.__setattr__(self, "retrieved_at", self.retrieved_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class FrozenRequestContext:
    """Secret-free worker facts required to freeze and govern one response.

    Authorization is not inferred from tenant identity.  ``source_metadata``
    must explicitly declare the permissions consumed by
    :class:`SourceGovernanceService`; automatic admission fails closed when AI
    processing or display is not allowed.
    """

    job_id: uuid.UUID
    attempt_id: uuid.UUID
    research_case_id: uuid.UUID
    tenant_id: str
    declared_actor: str
    lease_token: str
    source_metadata: Mapping[str, Any]
    retrieved_at: datetime

    def __post_init__(self) -> None:
        for field_name in ("job_id", "attempt_id", "research_case_id"):
            if not isinstance(getattr(self, field_name), uuid.UUID):
                raise ValueError(f"{field_name} must be a UUID")
        object.__setattr__(
            self, "tenant_id", _required_identity(self.tenant_id, "tenant_id")
        )
        object.__setattr__(
            self,
            "declared_actor",
            _required_identity(self.declared_actor, "declared_actor"),
        )
        object.__setattr__(
            self,
            "lease_token",
            _required_identity(self.lease_token, "lease_token"),
        )
        if not isinstance(self.source_metadata, Mapping):
            raise ValueError("source_metadata must be a mapping")
        copied = _thaw_json(self.source_metadata)
        _validate_safe_metadata(copied)
        validate_persistable_json(copied, path="$.source_metadata")
        object.__setattr__(self, "source_metadata", _freeze_json(copied))
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        object.__setattr__(self, "retrieved_at", self.retrieved_at.astimezone(UTC))


# More explicit alias for callers that prefer the module-qualified vocabulary.
RetrievedDocumentRequestContext = FrozenRequestContext


@dataclass(frozen=True, slots=True)
class RetrievedDocumentResult:
    relation: str
    artifact: RetrievalArtifact
    document: DocumentVersion | None = None
    binding: RetrievalArtifactDocument | None = None
    exception: AcquisitionException | None = None


@dataclass(frozen=True, slots=True)
class _ParsedDocument:
    parser_version: str
    spans: tuple[tuple[dict[str, Any], str, str, str], ...]


@dataclass(frozen=True, slots=True)
class _Classification:
    relation: str
    document_id: uuid.UUID | None = None


SessionFactory = Callable[[], Session]


class RetrievedDocumentFreezer:
    """Artifact-first document freezer with three ordered deduplication gates."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        pdf_parser: PdfParserAdapter | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self._session_factory = session_factory
        self._pdf_parser = pdf_parser or PypdfAdapter()
        self._clock = clock

    @staticmethod
    def _reference_id(reference: SourceReference | uuid.UUID) -> uuid.UUID:
        if isinstance(reference, uuid.UUID):
            return reference
        if not isinstance(reference, SourceReference):
            raise TypeError("reference must be a SourceReference or UUID")
        identity = sqlalchemy_inspect(reference).identity
        if (
            identity is None
            or len(identity) != 1
            or not isinstance(identity[0], uuid.UUID)
        ):
            raise ValueError("reference must have a persistent UUID identity")
        return identity[0]

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("retrieved-document clock must be timezone-aware")
        return value.astimezone(UTC)

    @contextmanager
    def _write_session(self, *, sqlite_immediate: bool):
        """Own one write transaction without inheriting caller state."""
        session = self._session_factory()
        try:
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite" and sqlite_immediate:
                session.execute(text("BEGIN IMMEDIATE"))
            else:
                session.begin()
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    @staticmethod
    def postgresql_publication_locks(
        *, canonical_url: str, publication_key: str
    ):
        lock_ids = sorted(
            int.from_bytes(
                hashlib.sha256(domain + identity.encode("utf-8")).digest()[:8],
                byteorder="big",
                signed=True,
            )
            for domain, identity in (
                (_CANONICAL_URL_LOCK_DOMAIN, canonical_url),
                (_PUBLICATION_KEY_LOCK_DOMAIN, publication_key),
            )
        )
        return tuple(
            (
                select(
                    func.pg_advisory_xact_lock(
                        bindparam(f"advisory_lock_id_{index}", value=lock_id)
                    )
                ),
                lock_id,
            )
            for index, lock_id in enumerate(lock_ids)
        )

    def _acquire_publication_locks(
        self,
        session: Session,
        *,
        canonical_url: str,
        publication_key: str,
    ) -> None:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            # BEGIN IMMEDIATE was issued before any classification read.
            return
        if dialect == "postgresql":
            for statement, _lock_id in self.postgresql_publication_locks(
                canonical_url=canonical_url,
                publication_key=publication_key,
            ):
                session.execute(statement)
            return
        raise RuntimeError("database dialect cannot serialize publication identity")

    def _require_lease(
        self,
        session: Session,
        *,
        context: FrozenRequestContext,
        allowed_stages: frozenset[str],
    ) -> AcquisitionJob:
        return self._require_lease_values(
            session,
            job_id=context.job_id,
            lease_token=context.lease_token,
            allowed_stages=allowed_stages,
        )

    def _require_lease_values(
        self,
        session: Session,
        *,
        job_id: uuid.UUID,
        lease_token: str,
        allowed_stages: frozenset[str],
    ) -> AcquisitionJob:
        job = session.scalar(
            select(AcquisitionJob)
            .where(AcquisitionJob.id == job_id)
            .with_for_update()
        )
        now = self._now()
        if (
            job is None
            or job.status != "running"
            or job.lease_token != lease_token
            or job.lease_expires_at is None
            or _aware_utc(job.lease_expires_at) <= now
            or job.stage not in allowed_stages
        ):
            raise StaleLeaseError("acquisition lease is stale")
        return job

    def checkpoint_fetch(
        self,
        reference: SourceReference | uuid.UUID,
        envelope: RetrievedEnvelope,
        context: FetchCheckpointContext,
        *,
        started_at: datetime,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Atomically persist a successful fetch attempt and its raw artifact."""
        reference_id = self._reference_id(reference)
        if not isinstance(envelope, RetrievedEnvelope):
            raise TypeError("envelope must be a RetrievedEnvelope")
        if not isinstance(context, FetchCheckpointContext):
            raise TypeError("context must be a FetchCheckpointContext")
        started = _aware_utc(started_at)
        finished = _aware_utc(context.retrieved_at)
        if finished < started:
            raise ValueError("retrieved_at must not precede started_at")
        digest = hashlib.sha256(envelope.content).hexdigest()

        with self._write_session(sqlite_immediate=True) as session:
            job = self._require_lease_values(
                session,
                job_id=context.job_id,
                lease_token=context.lease_token,
                allowed_stages=frozenset({"fetching"}),
            )
            reference_row = session.scalar(
                select(SourceReference)
                .where(SourceReference.id == reference_id)
                .with_for_update()
            )
            if (
                reference_row is None
                or reference_row.job_id != context.job_id
                or job.research_case_id != context.research_case_id
                or job.tenant_id != context.tenant_id
            ):
                raise ValueError("source reference/job lineage mismatch")
            self._validate_envelope_identity(reference_row, envelope)
            existing = session.scalar(
                select(RetrievalArtifact)
                .where(RetrievalArtifact.source_reference_id == reference_id)
                .order_by(RetrievalArtifact.retrieved_at, RetrievalArtifact.id)
                .limit(1)
            )
            if existing is not None:
                attempt = session.get(AcquisitionAttempt, existing.attempt_id)
                if (
                    existing.content_sha256 != digest
                    or attempt is None
                    or attempt.job_id != context.job_id
                    or attempt.operation != "fetch"
                    or attempt.outcome != "succeeded"
                ):
                    raise ValueError("persisted fetch checkpoint is inconsistent")
                return existing.id, attempt.id

            attempt_no = (
                session.scalar(
                    select(func.max(AcquisitionAttempt.attempt_no)).where(
                        AcquisitionAttempt.job_id == context.job_id,
                        AcquisitionAttempt.adapter_key == reference_row.adapter_key,
                        AcquisitionAttempt.operation == "fetch",
                    )
                )
                or 0
            ) + 1
            attempt = AcquisitionAttempt(
                id=uuid.uuid4(),
                job_id=context.job_id,
                adapter_key=reference_row.adapter_key,
                operation="fetch",
                attempt_no=attempt_no,
                started_at=started,
                finished_at=finished,
                outcome="succeeded",
                error_code=None,
                retryable=False,
                safe_metadata={
                    "source_reference_id": str(reference_row.id),
                    "claim_attempt": context.claim_attempt,
                },
            )
            artifact = RetrievalArtifact(
                id=uuid.uuid4(),
                source_reference_id=reference_row.id,
                attempt_id=attempt.id,
                content_sha256=digest,
                raw_bytes=envelope.content,
                mime_type=envelope.mime_type,
                byte_size=len(envelope.content),
                final_url=envelope.final_url,
                etag=envelope.etag,
                last_modified=envelope.last_modified,
                provider_request_id=envelope.provider_request_id,
                retrieved_at=finished,
            )
            session.add_all((attempt, artifact))
            session.flush()
            return artifact.id, attempt.id

    def checkpoint_search_result(
        self,
        reference: SourceReferenceValue,
        envelope: RetrievedEnvelope,
        context: FetchCheckpointContext,
        *,
        started_at: datetime,
    ) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        """Atomically persist an inline-search reference, attempt, and artifact."""
        if not isinstance(reference, SourceReferenceValue):
            raise TypeError("reference must be a SourceReferenceValue")
        if not isinstance(envelope, RetrievedEnvelope):
            raise TypeError("envelope must be a RetrievedEnvelope")
        if not isinstance(context, FetchCheckpointContext):
            raise TypeError("context must be a FetchCheckpointContext")
        started = _aware_utc(started_at)
        provider_observed_at = _aware_utc(context.retrieved_at)
        if provider_observed_at < started:
            raise ValueError("retrieved_at must not precede started_at")
        digest = hashlib.sha256(envelope.content).hexdigest()
        metadata = _thaw_json(reference.metadata)
        metadata["retrieval_locator"] = _thaw_json(reference.fetch_locator)

        with self._write_session(sqlite_immediate=True) as session:
            job = self._require_lease_values(
                session,
                job_id=context.job_id,
                lease_token=context.lease_token,
                allowed_stages=frozenset({"searching"}),
            )
            if (
                job.research_case_id != context.research_case_id
                or job.tenant_id != context.tenant_id
            ):
                raise ValueError("source reference/job lineage mismatch")
            reference_row = AcquisitionRepository(
                session, clock=self._clock
            ).create_or_get_reference(
                context.job_id,
                lease_token=context.lease_token,
                adapter_key=reference.adapter_key,
                external_record_id=reference.external_record_id,
                external_version=reference.external_version,
                canonical_url=reference.canonical_url,
                title=reference.title,
                published_at=reference.published_at,
                source_role=reference.source_role,
                metadata_json=metadata,
            )
            self._validate_envelope_identity(reference_row, envelope)
            existing = session.scalar(
                select(RetrievalArtifact)
                .where(RetrievalArtifact.source_reference_id == reference_row.id)
                .order_by(RetrievalArtifact.retrieved_at, RetrievalArtifact.id)
                .limit(1)
            )
            if existing is not None:
                attempt = session.get(AcquisitionAttempt, existing.attempt_id)
                if (
                    existing.content_sha256 != digest
                    or attempt is None
                    or attempt.job_id != context.job_id
                    or attempt.operation != "fetch"
                    or attempt.outcome != "succeeded"
                ):
                    raise ValueError("persisted inline fetch checkpoint is inconsistent")
                return reference_row.id, existing.id, attempt.id
            # Inline providers discover the reference and return its bytes in
            # one search call.  Preserve that provider-response observation in
            # metadata, then model a separate persistence sub-operation from
            # the newly frozen reference to this explicit checkpoint time.
            attempt_started = _aware_utc(reference_row.created_at)
            finished = self._now()
            if finished < attempt_started:
                raise ValueError("checkpoint time must not precede reference creation")
            if finished < provider_observed_at:
                raise ValueError("checkpoint time must not precede provider observation")
            attempt_no = (
                session.scalar(
                    select(func.max(AcquisitionAttempt.attempt_no)).where(
                        AcquisitionAttempt.job_id == context.job_id,
                        AcquisitionAttempt.adapter_key == reference.adapter_key,
                        AcquisitionAttempt.operation == "fetch",
                    )
                )
                or 0
            ) + 1
            attempt = AcquisitionAttempt(
                id=uuid.uuid4(),
                job_id=context.job_id,
                adapter_key=reference.adapter_key,
                operation="fetch",
                attempt_no=attempt_no,
                started_at=attempt_started,
                finished_at=finished,
                outcome="succeeded",
                error_code=None,
                retryable=False,
                safe_metadata={
                    "source_reference_id": str(reference_row.id),
                    "claim_attempt": context.claim_attempt,
                    "checkpoint": "inline_search_result",
                    "provider_response_observed_at": provider_observed_at.isoformat(),
                },
            )
            artifact = RetrievalArtifact(
                id=uuid.uuid4(),
                source_reference_id=reference_row.id,
                attempt_id=attempt.id,
                content_sha256=digest,
                raw_bytes=envelope.content,
                mime_type=envelope.mime_type,
                byte_size=len(envelope.content),
                final_url=envelope.final_url,
                etag=envelope.etag,
                last_modified=envelope.last_modified,
                provider_request_id=envelope.provider_request_id,
                retrieved_at=finished,
            )
            session.add_all((attempt, artifact))
            session.flush()
            return reference_row.id, artifact.id, attempt.id

    def freeze(
        self,
        reference: SourceReference | uuid.UUID,
        envelope: RetrievedEnvelope,
        request_context: FrozenRequestContext,
    ) -> RetrievedDocumentResult:
        """Commit an artifact, then deduplicate, parse, govern, and bind it."""
        reference_id = self._reference_id(reference)
        if not isinstance(envelope, RetrievedEnvelope):
            raise TypeError("envelope must be a RetrievedEnvelope")
        if not isinstance(request_context, FrozenRequestContext):
            raise TypeError("request_context must be a FrozenRequestContext")

        digest = hashlib.sha256(envelope.content).hexdigest()
        artifact_id, already_frozen = self._commit_artifact(
            reference_id=reference_id,
            envelope=envelope,
            context=request_context,
            digest=digest,
        )
        if already_frozen:
            artifact = self._load_artifact(artifact_id)
            if artifact.content_sha256 != digest:
                return self._exception_result(
                    artifact_id=artifact_id,
                    context=request_context,
                    reason_code="variant_conflict",
                    detail={"scope": "source_identity"},
                )
            existing = self._load_existing_outcome(artifact_id)
            if existing is not None:
                return existing

        reference_snapshot = self._load_reference(reference_id)
        publication_key, ambiguous = self.publication_key(reference_snapshot)
        source_metadata = self._source_metadata(reference_snapshot, request_context)
        if not self._declaration_allows_research(
            reference_snapshot, source_metadata, request_context
        ):
            return self._exception_result(
                artifact_id=artifact_id,
                context=request_context,
                reason_code="incompatible_source_contract",
                detail={"scope": "declared_permissions"},
            )

        parsed: _ParsedDocument | None = None
        if self._document_id_by_hash(digest) is None:
            try:
                parsed = self._parse(envelope, digest=digest)
            except Exception:
                return self._exception_result(
                    artifact_id=artifact_id,
                    context=request_context,
                    reason_code="parser_failure",
                    detail={"stage": "parse"},
                )

        try:
            return self._persist_usable(
                artifact_id=artifact_id,
                reference_id=reference_snapshot.id,
                context=request_context,
                digest=digest,
                publication_key=publication_key,
                publication_identity_ambiguous=ambiguous,
                parsed=parsed,
            )
        except IntegrityError:
            # A concurrent worker may win either the global content hash or
            # artifact-binding unique constraint.  Only accept that race when
            # the exact immutable artifact now has a complete outcome.
            existing = self._load_existing_outcome(artifact_id)
            if existing is not None:
                return existing
            return self._persist_usable(
                artifact_id=artifact_id,
                reference_id=reference_snapshot.id,
                context=request_context,
                digest=digest,
                publication_key=publication_key,
                publication_identity_ambiguous=ambiguous,
                parsed=parsed,
            )

    @staticmethod
    def publication_key(reference: SourceReference) -> tuple[str, bool]:
        """Return the SHA-256 publication key and whether identity is weak."""
        metadata = (
            reference.metadata_json if isinstance(reference.metadata_json, dict) else {}
        )
        provider = metadata.get("provider_identity") or metadata.get("publisher")
        if (
            reference.adapter_key == "gildata"
            and metadata.get("source_type") == "research_report"
        ):
            # Gildata transports reports from different publishers. Keep the
            # publisher in the historical publication-key slot; the trusted
            # transport identity remains separate in reference metadata.
            provider = metadata.get("publisher") or provider
        discriminator = metadata.get("security_code") or metadata.get("issuer_identity")
        publication_value = metadata.get("source_publication")
        if not publication_value and reference.published_at is not None:
            publication_value = reference.published_at.date().isoformat()
        title = _WHITESPACE_RE.sub(
            " ", unicodedata.normalize("NFKC", reference.title).strip().casefold()
        )
        values = (
            reference.adapter_key,
            str(provider or "").strip().casefold(),
            str(discriminator or "").strip().casefold(),
            title,
            str(publication_value or "").strip(),
            reference.source_role,
        )
        raw = "\x1f".join(values).encode("utf-8")
        ambiguous = not all((provider, discriminator, title, publication_value))
        return hashlib.sha256(raw).hexdigest(), ambiguous

    def _commit_artifact(
        self,
        *,
        reference_id: uuid.UUID,
        envelope: RetrievedEnvelope,
        context: FrozenRequestContext,
        digest: str,
    ) -> tuple[uuid.UUID, bool]:
        try:
            with self._write_session(sqlite_immediate=True) as session:
                job = self._require_lease(
                    session, context=context, allowed_stages=_ARTIFACT_STAGES
                )
                reference, attempt = self._require_lineage(
                    session,
                    reference_id=reference_id,
                    context=context,
                    job=job,
                    lock=True,
                )
                self._validate_envelope_identity(reference, envelope)
                existing = session.scalar(
                    select(RetrievalArtifact)
                    .where(RetrievalArtifact.source_reference_id == reference.id)
                    .order_by(RetrievalArtifact.retrieved_at, RetrievalArtifact.id)
                    .limit(1)
                )
                if existing is not None:
                    return existing.id, True
                artifact = RetrievalArtifact(
                    source_reference_id=reference.id,
                    attempt_id=attempt.id,
                    content_sha256=digest,
                    raw_bytes=envelope.content,
                    mime_type=envelope.mime_type,
                    byte_size=len(envelope.content),
                    final_url=envelope.final_url,
                    etag=envelope.etag,
                    last_modified=envelope.last_modified,
                    provider_request_id=envelope.provider_request_id,
                    retrieved_at=context.retrieved_at,
                )
                session.add(artifact)
                session.flush()
                artifact_id = artifact.id
            return artifact_id, False
        except IntegrityError:
            session = self._session_factory()
            try:
                existing = session.scalar(
                    select(RetrievalArtifact)
                    .where(RetrievalArtifact.source_reference_id == reference_id)
                    .order_by(RetrievalArtifact.retrieved_at, RetrievalArtifact.id)
                    .limit(1)
                )
                if existing is None:
                    raise
                return existing.id, True
            finally:
                session.close()

    @staticmethod
    def _validate_envelope_identity(
        reference: SourceReference, envelope: RetrievedEnvelope
    ) -> None:
        _validate_auditable_url(reference.canonical_url, field_name="canonical")
        _validate_auditable_url(envelope.final_url, field_name="final")
        metadata = envelope.metadata
        adapter_key = metadata.get("adapter_key")
        if adapter_key is not None and adapter_key != reference.adapter_key:
            raise ValueError("envelope adapter identity does not match reference")
        external_record_id = metadata.get("external_record_id")
        if (
            external_record_id is not None
            and external_record_id != reference.external_record_id
        ):
            raise ValueError("envelope provider identity does not match reference")
        envelope_provider = metadata.get("provider_identity")
        reference_metadata = (
            reference.metadata_json if isinstance(reference.metadata_json, dict) else {}
        )
        reference_provider = reference_metadata.get(
            "provider_identity"
        ) or reference_metadata.get("publisher")
        if (
            envelope_provider is not None
            and reference_provider is not None
            and envelope_provider != reference_provider
        ):
            raise ValueError("envelope provider identity does not match reference")

    @staticmethod
    def _require_lineage(
        session: Session,
        *,
        reference_id: uuid.UUID,
        context: FrozenRequestContext,
        job: AcquisitionJob,
        lock: bool,
    ) -> tuple[SourceReference, AcquisitionAttempt]:
        reference_stmt = select(SourceReference).where(
            SourceReference.id == reference_id
        )
        if lock:
            reference_stmt = reference_stmt.with_for_update()
        reference = session.scalar(reference_stmt)
        attempt = session.get(AcquisitionAttempt, context.attempt_id)
        if reference is None or attempt is None:
            raise ValueError("source reference/attempt/job lineage does not exist")
        safe_metadata = attempt.safe_metadata
        if not (
            reference.job_id == context.job_id
            and attempt.job_id == context.job_id
            and attempt.adapter_key == reference.adapter_key
            and attempt.operation == "fetch"
            and attempt.outcome == "succeeded"
            and attempt.finished_at is not None
            and isinstance(safe_metadata, Mapping)
            and safe_metadata.get("source_reference_id") == str(reference.id)
            and job.research_case_id == context.research_case_id
            and job.tenant_id == context.tenant_id
        ):
            raise ValueError("source reference/attempt/job lineage mismatch")
        return reference, attempt

    def _load_reference(self, reference_id: uuid.UUID) -> SourceReference:
        session = self._session_factory()
        try:
            reference = session.get(SourceReference, reference_id)
            if reference is None:
                raise ValueError("source reference lineage does not exist")
            # Materialize values before detaching for expire-on-commit factories.
            reference.metadata_json
            reference.canonical_url
            session.expunge(reference)
            return reference
        finally:
            session.close()

    def _load_artifact(self, artifact_id: uuid.UUID) -> RetrievalArtifact:
        session = self._session_factory()
        try:
            artifact = session.get(RetrievalArtifact, artifact_id)
            if artifact is None:
                raise RuntimeError("committed retrieval artifact is missing")
            artifact.content_sha256
            artifact.raw_bytes
            session.expunge(artifact)
            return artifact
        finally:
            session.close()

    def _source_metadata(
        self, reference: SourceReference, context: FrozenRequestContext
    ) -> dict[str, Any]:
        metadata = dict(reference.metadata_json or {})
        metadata.update(_thaw_json(context.source_metadata))
        metadata["tenant"] = context.tenant_id
        metadata["research_source_type"] = reference.source_role
        if reference.source_role == "licensed_provider":
            provider_identity = metadata.get("provider_identity") or metadata.get(
                "publisher"
            )
            metadata.setdefault("provider_name", provider_identity)
            metadata.setdefault("provider_record_id", reference.external_record_id)
            metadata.setdefault("request_scope", {})
            metadata.setdefault("retrieval_reference", reference.canonical_url)
        _validate_safe_metadata(metadata)
        validate_persistable_json(metadata, path="$.source_declaration")
        return metadata

    def _declaration_allows_research(
        self,
        reference: SourceReference,
        metadata: dict[str, Any],
        context: FrozenRequestContext,
    ) -> bool:
        session = self._session_factory()
        try:
            return SourceGovernanceService(
                session
            ).declared_event_intake_allows_research(
                source_type=reference.source_role,
                source_metadata=metadata,
                at=context.retrieved_at,
            )
        except ValueError:
            return False
        finally:
            session.close()

    def _document_id_by_hash(self, digest: str) -> uuid.UUID | None:
        session = self._session_factory()
        try:
            return session.scalar(
                select(DocumentVersion.id).where(
                    DocumentVersion.content_sha256 == digest
                )
            )
        finally:
            session.close()

    def _parse(self, envelope: RetrievedEnvelope, *, digest: str) -> _ParsedDocument:
        media_type = envelope.mime_type.split(";", 1)[0].strip().casefold()
        if media_type == "application/pdf":
            spans = self._pdf_parser.extract_spans(
                envelope.content, document_sha256=digest
            )
            if not spans:
                raise ValueError("parser produced no spans")
            values: list[tuple[dict[str, Any], str, str, str]] = []
            for span in spans:
                locator = span.locator.to_storage_dict()
                values.append(
                    (
                        locator,
                        span.verbatim_text,
                        span.text_sha256,
                        span.context_hash,
                    )
                )
            return _ParsedDocument(self._pdf_parser.parser_version, tuple(values))
        if media_type != "text/plain":
            raise ValueError("unsupported MIME type")
        parameters = [
            part.strip().casefold() for part in envelope.mime_type.split(";")[1:]
        ]
        charsets = [
            part.split("=", 1)[1].strip(' "')
            for part in parameters
            if part.startswith("charset=")
        ]
        if charsets and any(charset not in {"utf-8", "utf8"} for charset in charsets):
            raise ValueError("text/plain must use UTF-8")
        text = envelope.content.decode("utf-8", errors="strict")
        located: list[tuple[str, int, int]] = []
        offset = 0
        for physical_line in text.splitlines(keepends=True):
            line = physical_line.rstrip("\r\n")
            exact = line.strip()
            if exact:
                leading = len(line) - len(line.lstrip())
                start = offset + leading
                located.append((exact, start, start + len(exact)))
            offset += len(physical_line)
        if not located:
            raise ValueError("text parser produced no spans")
        values = []
        for index, (exact, start, end) in enumerate(located):
            previous = located[index - 1][0] if index else ""
            following = located[index + 1][0] if index + 1 < len(located) else ""
            context_hash = hashlib.sha256(
                f"{previous}\x00{exact}\x00{following}".encode("utf-8")
            ).hexdigest()
            locator = SourceLocatorV1(
                document_sha256=digest,
                page=1,
                parser_version=TEXT_PARSER_VERSION,
                text_position=TextPosition(start=start, end=end),
                text_quote=TextQuote(
                    exact=exact,
                    prefix=text[max(0, start - 32) : start],
                    suffix=text[end : end + 32],
                ),
            ).to_storage_dict()
            values.append((locator, exact, compute_text_sha256(exact), context_hash))
        return _ParsedDocument(TEXT_PARSER_VERSION, tuple(values))

    def _persist_usable(
        self,
        *,
        artifact_id: uuid.UUID,
        reference_id: uuid.UUID,
        context: FrozenRequestContext,
        digest: str,
        publication_key: str,
        publication_identity_ambiguous: bool,
        parsed: _ParsedDocument | None,
    ) -> RetrievedDocumentResult:
        try:
            with self._write_session(sqlite_immediate=True) as session:
                self._require_lease(
                    session, context=context, allowed_stages=_OUTCOME_STAGES
                )
                reference = session.get(SourceReference, reference_id)
                if reference is None:
                    raise RuntimeError("artifact provenance disappeared")
                self._acquire_publication_locks(
                    session,
                    canonical_url=reference.canonical_url,
                    publication_key=publication_key,
                )
                artifact = session.scalar(
                    select(RetrievalArtifact)
                    .where(RetrievalArtifact.id == artifact_id)
                    .with_for_update()
                )
                if artifact is None:
                    raise RuntimeError("artifact provenance disappeared")
                existing_binding = session.scalar(
                    select(RetrievalArtifactDocument).where(
                        RetrievalArtifactDocument.retrieval_artifact_id == artifact_id
                    )
                )
                if existing_binding is not None:
                    result_ids = (
                        existing_binding.relation,
                        existing_binding.document_version_id,
                        existing_binding.id,
                    )
                else:
                    classification = self._classify_in_session(
                        session,
                        digest=digest,
                        publication_key=publication_key,
                        canonical_url=reference.canonical_url,
                        publication_identity_ambiguous=publication_identity_ambiguous,
                    )
                    if classification.relation == "variant_conflict":
                        raise _VariantRace
                    documents = DocumentService(DocumentRepository(session))
                    if classification.relation == "content_duplicate":
                        document = session.get(
                            DocumentVersion, classification.document_id
                        )
                        if document is None:
                            raise RuntimeError("deduplicated document disappeared")
                        relation = "content_duplicate"
                    else:
                        if parsed is None:
                            raise RuntimeError("new documents require parsed spans")
                        document, created = documents.freeze_with_status(
                            raw=artifact.raw_bytes,
                            source_url=reference.canonical_url,
                            published_at=reference.published_at,
                            parser_version=parsed.parser_version,
                            title=reference.title,
                            # A content-derived key deliberately bypasses the
                            # legacy semantic natural-key collapse.  The exact
                            # publication relation is stored on our binding.
                            natural_key=f"retrieved:{digest}"[:32],
                            byte_size=artifact.byte_size,
                            source_authority=(
                                "primary_disclosure"
                                if reference.source_role == "company_disclosure"
                                else "licensed_research"
                            ),
                            supersedes_id=classification.document_id,
                            infer_supersedes=False,
                            available_at=reference.published_at,
                            acquired_at=context.retrieved_at,
                        )
                        if not created:
                            relation = "content_duplicate"
                        else:
                            relation = classification.relation
                            for locator, text, text_hash, context_hash in parsed.spans:
                                documents.add_span(
                                    document_version_id=document.id,
                                    locator=locator,
                                    verbatim_text=text,
                                    text_sha256=text_hash,
                                    context_hash=context_hash,
                                    locator_v1=locator,
                                )
                    metadata = self._source_metadata(reference, context)
                    governance = SourceGovernanceService(session)
                    try:
                        governance.record_event_intake(
                            document=document,
                            source_type=reference.source_role,
                            source_metadata=metadata,
                            declared_by=context.declared_actor,
                            incoming_source_url=reference.canonical_url,
                        )
                    except ValueError as exc:
                        raise _IncompatibleContract from exc
                    documents.attach_to_case(
                        research_case_id=context.research_case_id,
                        document_version_id=document.id,
                    )
                    binding = RetrievalArtifactDocument(
                        retrieval_artifact_id=artifact.id,
                        document_version_id=document.id,
                        relation=relation,
                        publication_key=publication_key,
                        created_at=context.retrieved_at,
                    )
                    session.add(binding)
                    session.flush()
                    result_ids = (relation, document.id, binding.id)
            return self._load_bound_result(
                artifact_id=artifact_id,
                relation=result_ids[0],
                document_id=result_ids[1],
                binding_id=result_ids[2],
            )
        except _VariantRace:
            return self._exception_result(
                artifact_id=artifact_id,
                context=context,
                reason_code="variant_conflict",
                detail={"publication_key": publication_key},
            )
        except _IncompatibleContract:
            return self._exception_result(
                artifact_id=artifact_id,
                context=context,
                reason_code="incompatible_source_contract",
                detail={"scope": "source_contract"},
            )

    @staticmethod
    def _classify_in_session(
        session: Session,
        *,
        digest: str,
        publication_key: str,
        canonical_url: str,
        publication_identity_ambiguous: bool,
    ) -> _Classification:
        by_hash = session.scalar(
            select(DocumentVersion).where(DocumentVersion.content_sha256 == digest)
        )
        if by_hash is not None:
            return _Classification("content_duplicate", by_hash.id)
        prior = session.execute(
            select(RetrievalArtifactDocument, DocumentVersion)
            .join(
                DocumentVersion,
                DocumentVersion.id == RetrievalArtifactDocument.document_version_id,
            )
            .where(RetrievalArtifactDocument.publication_key == publication_key)
            .order_by(DocumentVersion.acquired_at.desc(), DocumentVersion.id.desc())
            .limit(1)
        ).first()
        if prior is not None:
            _binding, prior_document = prior
            if (
                publication_identity_ambiguous
                or prior_document.source_url != canonical_url
            ):
                return _Classification("variant_conflict")
        if not publication_identity_ambiguous:
            predecessor = aliased(DocumentVersion)
            successor = aliased(DocumentVersion)
            source_predecessor = session.scalar(
                select(predecessor)
                .outerjoin(
                    successor,
                    (successor.supersedes_id == predecessor.id)
                    & (successor.source_url == canonical_url),
                )
                .where(
                    predecessor.source_url == canonical_url,
                    successor.id.is_(None),
                )
                .order_by(predecessor.acquired_at.desc(), predecessor.id.desc())
                .limit(1)
            )
            if source_predecessor is not None:
                return _Classification("supersedes", source_predecessor.id)
        return _Classification("created")

    def _exception_result(
        self,
        *,
        artifact_id: uuid.UUID,
        context: FrozenRequestContext,
        reason_code: str,
        detail: dict[str, Any],
    ) -> RetrievedDocumentResult:
        try:
            with self._write_session(sqlite_immediate=True) as session:
                self._require_lease(
                    session, context=context, allowed_stages=_EXCEPTION_STAGES
                )
                artifact = session.scalar(
                    select(RetrievalArtifact)
                    .where(RetrievalArtifact.id == artifact_id)
                    .with_for_update()
                )
                if artifact is None:
                    raise RuntimeError("committed retrieval artifact is missing")
                existing = session.scalar(
                    select(AcquisitionException)
                    .where(
                        AcquisitionException.job_id == context.job_id,
                        AcquisitionException.source_reference_id
                        == artifact.source_reference_id,
                        AcquisitionException.retrieval_artifact_id == artifact_id,
                        AcquisitionException.reason_code == reason_code,
                    )
                    .order_by(AcquisitionException.created_at, AcquisitionException.id)
                    .limit(1)
                )
                if existing is None:
                    validate_persistable_json(detail, path="$.exception")
                    existing = AcquisitionException(
                        job_id=context.job_id,
                        source_reference_id=artifact.source_reference_id,
                        retrieval_artifact_id=artifact.id,
                        candidate_id=None,
                        reason_code=reason_code,
                        detail_json=detail,
                        created_at=context.retrieved_at,
                    )
                    session.add(existing)
                    session.flush()
                exception_id = existing.id
            return self._load_exception_result(
                artifact_id=artifact_id,
                exception_id=exception_id,
                relation=reason_code,
            )
        except IntegrityError:
            session = self._session_factory()
            try:
                existing_id = session.scalar(
                    select(AcquisitionException.id)
                    .where(
                        AcquisitionException.job_id == context.job_id,
                        AcquisitionException.retrieval_artifact_id == artifact_id,
                        AcquisitionException.reason_code == reason_code,
                    )
                    .order_by(AcquisitionException.created_at, AcquisitionException.id)
                    .limit(1)
                )
                if existing_id is None:
                    raise
            finally:
                session.close()
            return self._load_exception_result(
                artifact_id=artifact_id,
                exception_id=existing_id,
                relation=reason_code,
            )

    def _load_existing_outcome(
        self, artifact_id: uuid.UUID
    ) -> RetrievedDocumentResult | None:
        session = self._session_factory()
        try:
            binding = session.scalar(
                select(RetrievalArtifactDocument).where(
                    RetrievalArtifactDocument.retrieval_artifact_id == artifact_id
                )
            )
            if binding is not None:
                return self._load_bound_result(
                    artifact_id=artifact_id,
                    relation=binding.relation,
                    document_id=binding.document_version_id,
                    binding_id=binding.id,
                )
            exception = session.scalar(
                select(AcquisitionException)
                .where(AcquisitionException.retrieval_artifact_id == artifact_id)
                .order_by(AcquisitionException.created_at, AcquisitionException.id)
                .limit(1)
            )
            if exception is not None:
                return self._load_exception_result(
                    artifact_id=artifact_id,
                    exception_id=exception.id,
                    relation=exception.reason_code,
                )
            return None
        finally:
            session.close()

    def _load_bound_result(
        self,
        *,
        artifact_id: uuid.UUID,
        relation: str,
        document_id: uuid.UUID,
        binding_id: uuid.UUID,
    ) -> RetrievedDocumentResult:
        session = self._session_factory()
        try:
            artifact = session.get(RetrievalArtifact, artifact_id)
            document = session.get(DocumentVersion, document_id)
            binding = session.get(RetrievalArtifactDocument, binding_id)
            if artifact is None or document is None or binding is None:
                raise RuntimeError("committed retrieved-document outcome is incomplete")
            # Load every field used by callers before detaching.
            artifact.content_sha256
            document.content_sha256
            document.supersedes_id
            binding.relation
            for value in (artifact, document, binding):
                session.expunge(value)
            return RetrievedDocumentResult(
                relation=relation,
                artifact=artifact,
                document=document,
                binding=binding,
            )
        finally:
            session.close()

    def _load_exception_result(
        self,
        *,
        artifact_id: uuid.UUID,
        exception_id: uuid.UUID,
        relation: str,
    ) -> RetrievedDocumentResult:
        session = self._session_factory()
        try:
            artifact = session.get(RetrievalArtifact, artifact_id)
            exception = session.get(AcquisitionException, exception_id)
            if artifact is None or exception is None:
                raise RuntimeError("committed retrieval exception is incomplete")
            artifact.content_sha256
            exception.detail_json
            session.expunge(artifact)
            session.expunge(exception)
            return RetrievedDocumentResult(
                relation=relation,
                artifact=artifact,
                exception=exception,
            )
        finally:
            session.close()


class _VariantRace(Exception):
    pass


class _IncompatibleContract(Exception):
    pass


# Concise aliases for orchestration code that names the component by role.
RetrievedDocumentService = RetrievedDocumentFreezer
FreezeResult = RetrievedDocumentResult
