"""Persist governed acquisition jobs, artifacts, and automatic provenance.

Revision ID: 0053
Revises: 0052
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IMMUTABLE_TABLES = (
    "acquisition_job_events",
    "acquisition_attempts",
    "source_references",
    "retrieval_artifacts",
    "retrieval_artifact_documents",
    "automatic_admission_decisions",
    "acquisition_exceptions",
)


def _create_immutable_triggers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER no_update_{table} BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )
        op.execute(
            f"CREATE TRIGGER no_delete_{table} BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_mutable_ledger();"
        )


def _drop_immutable_triggers() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS no_delete_{table} ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS no_update_{table} ON {table};")


def upgrade() -> None:
    op.create_table(
        "acquisition_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=256), nullable=False),
        sa.Column("research_case_id", sa.Uuid(), nullable=False),
        sa.Column("thesis_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("request_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reference_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fetched_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("frozen_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("admitted_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("exception_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_owner", sa.String(length=256), nullable=True),
        sa.Column("lease_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', "
            "'partial', 'failed', 'cancelled')",
            name="ck_acquisition_jobs_status",
        ),
        sa.CheckConstraint(
            "stage IN ('queued', 'searching', 'fetching', 'freezing', "
            "'extracting', 'admitting', 'succeeded', 'partial', 'failed', "
            "'cancelled')",
            name="ck_acquisition_jobs_stage",
        ),
        sa.CheckConstraint(
            "attempt >= 0", name="ck_acquisition_jobs_attempt_non_negative"
        ),
        sa.CheckConstraint(
            "reference_count >= 0 AND fetched_count >= 0 AND frozen_count >= 0 "
            "AND admitted_count >= 0 AND exception_count >= 0",
            name="ck_acquisition_jobs_counters_non_negative",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) "
            "OR (lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL)",
            name="ck_acquisition_jobs_lease_complete",
        ),
        sa.ForeignKeyConstraint(
            ["research_case_id"],
            ["research_cases.id"],
            name="fk_acquisition_jobs_research_case_id",
        ),
        sa.ForeignKeyConstraint(
            ["thesis_id"], ["theses.id"], name="fk_acquisition_jobs_thesis_id"
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            name="fk_acquisition_jobs_research_run_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_acquisition_jobs_tenant_key"
        ),
    )
    op.create_index("ix_acquisition_jobs_case", "acquisition_jobs", ["research_case_id"])
    op.create_index("ix_acquisition_jobs_thesis", "acquisition_jobs", ["thesis_id"])
    op.create_index(
        "ix_acquisition_jobs_research_run", "acquisition_jobs", ["research_run_id"]
    )
    op.create_index(
        "ix_acquisition_jobs_claim",
        "acquisition_jobs",
        ["status", "retry_at", "created_at"],
    )
    op.create_index(
        "ix_acquisition_jobs_lease_expiry", "acquisition_jobs", ["lease_expires_at"]
    )

    op.create_table(
        "acquisition_job_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "seq >= 0", name="ck_acquisition_job_events_seq_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["acquisition_jobs.id"],
            name="fk_acquisition_job_events_job_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id", "seq", name="uq_acquisition_job_events_job_seq"
        ),
    )
    op.create_table(
        "acquisition_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_key", sa.String(length=128), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "attempt_no >= 1", name="ck_acquisition_attempts_attempt_positive"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["acquisition_jobs.id"],
            name="fk_acquisition_attempts_job_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "adapter_key",
            "operation",
            "attempt_no",
            name="uq_acquisition_attempts_operation_attempt",
        ),
    )
    op.create_table(
        "source_references",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("adapter_key", sa.String(length=128), nullable=False),
        sa.Column("external_record_id", sa.String(length=512), nullable=False),
        sa.Column(
            "external_version", sa.String(length=256), server_default="", nullable=False
        ),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_role", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["acquisition_jobs.id"],
            name="fk_source_references_job_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "adapter_key",
            "external_record_id",
            "external_version",
            name="uq_source_references_provider_identity",
        ),
    )
    op.create_table(
        "retrieval_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_reference_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("mime_type", sa.String(length=256), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.String(length=256), nullable=True),
        sa.Column("provider_request_id", sa.String(length=512), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "byte_size > 0", name="ck_retrieval_artifacts_byte_size_positive"
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_retrieval_artifacts_sha256_length",
        ),
        sa.CheckConstraint(
            "byte_size = length(raw_bytes)",
            name="ck_retrieval_artifacts_byte_size_matches_raw",
        ),
        sa.ForeignKeyConstraint(
            ["source_reference_id"],
            ["source_references.id"],
            name="fk_retrieval_artifacts_source_reference_id",
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["acquisition_attempts.id"],
            name="fk_retrieval_artifacts_attempt_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_reference_id",
            "attempt_id",
            name="uq_retrieval_artifacts_reference_attempt",
        ),
    )
    op.create_index(
        "ix_retrieval_artifacts_attempt", "retrieval_artifacts", ["attempt_id"]
    )
    op.create_table(
        "retrieval_artifact_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("publication_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["retrieval_artifact_id"],
            ["retrieval_artifacts.id"],
            name="fk_retrieval_artifact_documents_artifact_id",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_retrieval_artifact_documents_document_version_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "retrieval_artifact_id",
            name="uq_retrieval_artifact_documents_artifact",
        ),
    )
    op.create_index(
        "ix_retrieval_artifact_documents_document",
        "retrieval_artifact_documents",
        ["document_version_id"],
    )
    op.create_table(
        "automatic_admission_decisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("retrieval_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("gate_version", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("gate_results", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('admitted', 'quarantined')",
            name="ck_automatic_admission_decisions_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["acquisition_jobs.id"],
            name="fk_automatic_admission_decisions_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["atomic_claim_candidates.id"],
            name="fk_automatic_admission_decisions_candidate_id",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_artifact_id"],
            ["retrieval_artifacts.id"],
            name="fk_automatic_admission_decisions_artifact_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "candidate_id",
            "gate_version",
            "policy_version",
            name="uq_automatic_admission_decisions_gate_policy",
        ),
    )
    op.create_index(
        "ix_automatic_admission_decisions_candidate",
        "automatic_admission_decisions",
        ["candidate_id"],
    )
    op.create_index(
        "ix_automatic_admission_decisions_artifact",
        "automatic_admission_decisions",
        ["retrieval_artifact_id"],
    )
    op.create_table(
        "acquisition_exceptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("source_reference_id", sa.Uuid(), nullable=True),
        sa.Column("retrieval_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["acquisition_jobs.id"],
            name="fk_acquisition_exceptions_job_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_reference_id"],
            ["source_references.id"],
            name="fk_acquisition_exceptions_source_reference_id",
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_artifact_id"],
            ["retrieval_artifacts.id"],
            name="fk_acquisition_exceptions_retrieval_artifact_id",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["atomic_claim_candidates.id"],
            name="fk_acquisition_exceptions_candidate_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_acquisition_exceptions_job", "acquisition_exceptions", ["job_id"]
    )
    op.create_index(
        "ix_acquisition_exceptions_source_reference",
        "acquisition_exceptions",
        ["source_reference_id"],
    )
    op.create_index(
        "ix_acquisition_exceptions_artifact",
        "acquisition_exceptions",
        ["retrieval_artifact_id"],
    )
    op.create_index(
        "ix_acquisition_exceptions_candidate",
        "acquisition_exceptions",
        ["candidate_id"],
    )

    with op.batch_alter_table("source_statements") as batch:
        batch.add_column(
            sa.Column("automatic_admission_decision_id", sa.Uuid(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_source_statements_automatic_admission_decision_id",
            "automatic_admission_decisions",
            ["automatic_admission_decision_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_source_statements_automatic_admission_decision",
            ["automatic_admission_decision_id"],
        )
    with op.batch_alter_table("evidence_links") as batch:
        batch.add_column(
            sa.Column("automatic_admission_decision_id", sa.Uuid(), nullable=True)
        )
        batch.create_foreign_key(
            "fk_evidence_links_automatic_admission_decision_id",
            "automatic_admission_decisions",
            ["automatic_admission_decision_id"],
            ["id"],
        )
        batch.create_check_constraint(
            "ck_evidence_links_automatic_admission_provenance",
            "(review_state = 'automatically_admitted' AND "
            "automatic_admission_decision_id IS NOT NULL) OR "
            "(review_state <> 'automatically_admitted' AND "
            "automatic_admission_decision_id IS NULL)",
        )
        batch.create_index(
            "ix_evidence_links_automatic_admission_decision",
            ["automatic_admission_decision_id"],
        )

    _create_immutable_triggers()


def downgrade() -> None:
    with op.batch_alter_table("evidence_links") as batch:
        batch.drop_index("ix_evidence_links_automatic_admission_decision")
        batch.drop_constraint(
            "ck_evidence_links_automatic_admission_provenance", type_="check"
        )
        batch.drop_constraint(
            "fk_evidence_links_automatic_admission_decision_id", type_="foreignkey"
        )
        batch.drop_column("automatic_admission_decision_id")
    with op.batch_alter_table("source_statements") as batch:
        batch.drop_constraint(
            "uq_source_statements_automatic_admission_decision", type_="unique"
        )
        batch.drop_constraint(
            "fk_source_statements_automatic_admission_decision_id", type_="foreignkey"
        )
        batch.drop_column("automatic_admission_decision_id")

    _drop_immutable_triggers()

    op.drop_index("ix_acquisition_exceptions_candidate", table_name="acquisition_exceptions")
    op.drop_index("ix_acquisition_exceptions_artifact", table_name="acquisition_exceptions")
    op.drop_index(
        "ix_acquisition_exceptions_source_reference", table_name="acquisition_exceptions"
    )
    op.drop_index("ix_acquisition_exceptions_job", table_name="acquisition_exceptions")
    op.drop_table("acquisition_exceptions")
    op.drop_index(
        "ix_automatic_admission_decisions_artifact",
        table_name="automatic_admission_decisions",
    )
    op.drop_index(
        "ix_automatic_admission_decisions_candidate",
        table_name="automatic_admission_decisions",
    )
    op.drop_table("automatic_admission_decisions")
    op.drop_index(
        "ix_retrieval_artifact_documents_document",
        table_name="retrieval_artifact_documents",
    )
    op.drop_table("retrieval_artifact_documents")
    op.drop_index("ix_retrieval_artifacts_attempt", table_name="retrieval_artifacts")
    op.drop_table("retrieval_artifacts")
    op.drop_table("source_references")
    op.drop_table("acquisition_attempts")
    op.drop_table("acquisition_job_events")
    op.drop_index("ix_acquisition_jobs_lease_expiry", table_name="acquisition_jobs")
    op.drop_index("ix_acquisition_jobs_claim", table_name="acquisition_jobs")
    op.drop_index("ix_acquisition_jobs_research_run", table_name="acquisition_jobs")
    op.drop_index("ix_acquisition_jobs_thesis", table_name="acquisition_jobs")
    op.drop_index("ix_acquisition_jobs_case", table_name="acquisition_jobs")
    op.drop_table("acquisition_jobs")
