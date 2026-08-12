"""Durable operational state and immutable provenance for source acquisition."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    Uuid,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


class AcquisitionJob(Base):
    """Mutable lease-fenced state for one idempotent acquisition request."""

    __tablename__ = "acquisition_jobs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_acquisition_jobs_tenant_key"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', "
            "'partial', 'failed', 'cancelled')",
            name="ck_acquisition_jobs_status",
        ),
        CheckConstraint(
            "stage IN ('queued', 'searching', 'fetching', 'freezing', "
            "'extracting', 'admitting', 'succeeded', 'partial', 'failed', "
            "'cancelled')",
            name="ck_acquisition_jobs_stage",
        ),
        CheckConstraint(
            "attempt >= 0", name="ck_acquisition_jobs_attempt_non_negative"
        ),
        CheckConstraint(
            "reference_count >= 0 AND fetched_count >= 0 AND frozen_count >= 0 "
            "AND admitted_count >= 0 AND exception_count >= 0",
            name="ck_acquisition_jobs_counters_non_negative",
        ),
        CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_acquisition_jobs_lease_complete",
        ),
        Index("ix_acquisition_jobs_case", "research_case_id"),
        Index("ix_acquisition_jobs_thesis", "thesis_id"),
        Index("ix_acquisition_jobs_research_run", "research_run_id"),
        Index("ix_acquisition_jobs_claim", "status", "retry_at", "created_at"),
        Index("ix_acquisition_jobs_lease_expiry", "lease_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    research_case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "research_cases.id", name="fk_acquisition_jobs_research_case_id"
        ),
        nullable=False,
    )
    thesis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("theses.id", name="fk_acquisition_jobs_thesis_id"),
        nullable=False,
    )
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("research_runs.id", name="fk_acquisition_jobs_research_run_id"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    request_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    reference_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    fetched_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    frozen_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    admitted_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    exception_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    lease_owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionJobEvent(Base):
    __tablename__ = "acquisition_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "seq", name="uq_acquisition_job_events_job_seq"),
        CheckConstraint("seq >= 0", name="ck_acquisition_job_events_seq_non_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("acquisition_jobs.id", name="fk_acquisition_job_events_job_id"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionAttempt(Base):
    __tablename__ = "acquisition_attempts"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "adapter_key",
            "operation",
            "attempt_no",
            name="uq_acquisition_attempts_operation_attempt",
        ),
        CheckConstraint(
            "attempt_no >= 1", name="ck_acquisition_attempts_attempt_positive"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("acquisition_jobs.id", name="fk_acquisition_attempts_job_id"),
        nullable=False,
    )
    adapter_key: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safe_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class SourceReference(Base):
    __tablename__ = "source_references"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "adapter_key",
            "external_record_id",
            "external_version",
            name="uq_source_references_provider_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("acquisition_jobs.id", name="fk_source_references_job_id"),
        nullable=False,
    )
    adapter_key: Mapped[str] = mapped_column(String(128), nullable=False)
    external_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_version: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", server_default=""
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_role: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetrievalArtifact(Base):
    __tablename__ = "retrieval_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "source_reference_id",
            "attempt_id",
            name="uq_retrieval_artifacts_reference_attempt",
        ),
        CheckConstraint(
            "byte_size > 0", name="ck_retrieval_artifacts_byte_size_positive"
        ),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_retrieval_artifacts_sha256_length",
        ),
        CheckConstraint(
            "byte_size = length(raw_bytes)",
            name="ck_retrieval_artifacts_byte_size_matches_raw",
        ),
        Index("ix_retrieval_artifacts_attempt", "attempt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    source_reference_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "source_references.id",
            name="fk_retrieval_artifacts_source_reference_id",
        ),
        nullable=False,
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("acquisition_attempts.id", name="fk_retrieval_artifacts_attempt_id"),
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(256), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    etag: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetrievalArtifactDocument(Base):
    __tablename__ = "retrieval_artifact_documents"
    __table_args__ = (
        UniqueConstraint(
            "retrieval_artifact_id",
            name="uq_retrieval_artifact_documents_artifact",
        ),
        Index("ix_retrieval_artifact_documents_document", "document_version_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    retrieval_artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "retrieval_artifacts.id",
            name="fk_retrieval_artifact_documents_artifact_id",
        ),
        nullable=False,
    )
    document_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "document_versions.id",
            name="fk_retrieval_artifact_documents_document_version_id",
        ),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    publication_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AutomaticAdmissionDecision(Base):
    __tablename__ = "automatic_admission_decisions"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "candidate_id",
            "gate_version",
            "policy_version",
            name="uq_automatic_admission_decisions_gate_policy",
        ),
        CheckConstraint(
            "outcome IN ('admitted', 'quarantined')",
            name="ck_automatic_admission_decisions_outcome",
        ),
        Index("ix_automatic_admission_decisions_candidate", "candidate_id"),
        Index("ix_automatic_admission_decisions_artifact", "retrieval_artifact_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "acquisition_jobs.id", name="fk_automatic_admission_decisions_job_id"
        ),
        nullable=False,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "atomic_claim_candidates.id",
            name="fk_automatic_admission_decisions_candidate_id",
        ),
        nullable=False,
    )
    retrieval_artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey(
            "retrieval_artifacts.id",
            name="fk_automatic_admission_decisions_artifact_id",
        ),
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    gate_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    gate_results: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionException(Base):
    __tablename__ = "acquisition_exceptions"
    __table_args__ = (
        Index("ix_acquisition_exceptions_job", "job_id"),
        Index("ix_acquisition_exceptions_source_reference", "source_reference_id"),
        Index("ix_acquisition_exceptions_artifact", "retrieval_artifact_id"),
        Index("ix_acquisition_exceptions_candidate", "candidate_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    job_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("acquisition_jobs.id", name="fk_acquisition_exceptions_job_id"),
        nullable=False,
    )
    source_reference_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "source_references.id",
            name="fk_acquisition_exceptions_source_reference_id",
        ),
        nullable=True,
    )
    retrieval_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "retrieval_artifacts.id",
            name="fk_acquisition_exceptions_retrieval_artifact_id",
        ),
        nullable=True,
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "atomic_claim_candidates.id",
            name="fk_acquisition_exceptions_candidate_id",
        ),
        nullable=True,
    )
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    detail_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
