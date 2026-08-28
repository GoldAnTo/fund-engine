"""Durable state for the company-research workbench.

The preparation is a mutable operational projection.  Artifacts and events are
append-only records: their hashes allow reads to reject an administrator-bypass
tamper even when database triggers are unavailable (for example, in metadata
only tests).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.ledger import Base, _uuid


def _json_shape_constraints(
    column_name: str, expected_shape: str, constraint_name: str
) -> tuple[CheckConstraint, CheckConstraint]:
    return (
        CheckConstraint(
            f"json_type({column_name}) = '{expected_shape}'",
            name=constraint_name,
        ).ddl_if(dialect="sqlite"),
        CheckConstraint(
            f"json_typeof({column_name}) = '{expected_shape}'",
            name=constraint_name,
        ).ddl_if(dialect="postgresql"),
    )


PreparationStatus = Literal[
    "queued",
    "preparing_sources",
    "awaiting_evidence_review",
    "building_model",
    "awaiting_judgment_review",
    "ready_to_freeze",
    "recoverable_failure",
    "blocked",
    "completed",
]

COMPANY_RESEARCH_PREPARATION_STATUSES = frozenset(
    {
        "queued",
        "preparing_sources",
        "awaiting_evidence_review",
        "building_model",
        "awaiting_judgment_review",
        "ready_to_freeze",
        "recoverable_failure",
        "blocked",
        "completed",
    }
)

COMPANY_RESEARCH_ARTIFACT_KINDS = frozenset(
    {
        "evidence_index",
        "business_map",
        "driver_map",
        "financial_bridge",
        "scenario_set",
        "valuation_set",
        "research_gaps",
        "judgment_context",
        "memo",
    }
)
COMPANY_RESEARCH_PREPARATION_STEPS = COMPANY_RESEARCH_ARTIFACT_KINDS | {
    "model_bundle"
}


class CompanyResearchPreparation(Base):
    """The only mutable row in a company-research preparation lifecycle."""

    __tablename__ = "uw_company_research_preparations"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            + ", ".join(
                f"'{status}'"
                for status in sorted(COMPANY_RESEARCH_PREPARATION_STATUSES)
            )
            + ")",
            name="ck_uw_company_research_preparation_status",
        ),
        CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="ck_uw_company_research_preparation_progress",
        ),
        CheckConstraint(
            "attempt >= 1",
            name="ck_uw_company_research_preparation_attempt",
        ),
        CheckConstraint(
            "length(request_hash) = 64",
            name="ck_uw_company_research_preparation_request_hash",
        ),
        UniqueConstraint(
            "project_id", name="uq_uw_company_research_preparation_project"
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_uw_company_research_preparation_idempotency"
        ),
        UniqueConstraint("job_id", name="uq_uw_company_research_preparation_job"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_version: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(String(96), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("jobs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CompanyResearchArtifactVersion(Base):
    """A content-addressed, append-only version in one artifact family."""

    __tablename__ = "uw_company_research_artifact_versions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ("
            + ", ".join(f"'{kind}'" for kind in sorted(COMPANY_RESEARCH_ARTIFACT_KINDS))
            + ")",
            name="ck_uw_company_research_artifact_kind",
        ),
        CheckConstraint("version >= 1", name="ck_uw_company_research_artifact_version"),
        CheckConstraint(
            "length(input_hash) = 64",
            name="ck_uw_company_research_artifact_input_hash",
        ),
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_uw_company_research_artifact_content_hash",
        ),
        CheckConstraint(
            "(supersedes_id IS NULL AND parent_content_hash IS NULL) OR "
            "(supersedes_id IS NOT NULL AND parent_content_hash IS NOT NULL "
            "AND length(parent_content_hash) = 64)",
            name="ck_uw_company_research_artifact_parent_hash",
        ),
        *_json_shape_constraints(
            "payload",
            "object",
            "ck_uw_company_research_artifact_payload_shape",
        ),
        *_json_shape_constraints(
            "source_refs",
            "array",
            "ck_uw_company_research_artifact_source_refs_shape",
        ),
        UniqueConstraint(
            "project_id",
            "kind",
            "version",
            name="uq_uw_company_research_artifact_version",
        ),
        UniqueConstraint(
            "supersedes_id", name="uq_uw_company_research_artifact_successor"
        ),
        Index("ix_uw_company_research_artifact_project_kind", "project_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_research_projects.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("uw_company_research_artifact_versions.id"),
        nullable=True,
    )
    parent_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    source_refs: Mapped[list] = mapped_column(JSON(none_as_null=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CompanyResearchEvent(Base):
    """Append-only lifecycle event for one preparation."""

    __tablename__ = "uw_company_research_events"
    __table_args__ = (
        CheckConstraint(
            "length(content_hash) = 64",
            name="ck_uw_company_research_event_content_hash",
        ),
        CheckConstraint("sequence >= 1", name="ck_uw_company_research_event_sequence"),
        CheckConstraint(
            "(sequence = 1 AND previous_event_hash IS NULL) OR "
            "(sequence > 1 AND previous_event_hash IS NOT NULL "
            "AND length(previous_event_hash) = 64)",
            name="ck_uw_company_research_event_predecessor_hash",
        ),
        *_json_shape_constraints(
            "payload",
            "object",
            "ck_uw_company_research_event_payload_shape",
        ),
        UniqueConstraint(
            "preparation_id", "sequence", name="uq_uw_company_research_event_sequence"
        ),
        Index(
            "ix_uw_company_research_event_preparation", "preparation_id", "sequence"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    preparation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_company_research_preparations.id"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_event_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON(none_as_null=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
