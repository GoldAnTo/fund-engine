"""Append-only ORM rows for the Wave 2 economic research model.

Rows in this module are deliberately persistence-shaped rather than domain
objects.  They retain every compiled research artefact with its historical
basis, source lineage and successor pointer so a later reader can replay what
was knowable at a particular cutoff.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
import json
from typing import Mapping

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, Uuid, bindparam, event, func, inspect, select
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.orm.util import identity_key

from app.models.ledger import Base, ValidationError, _uuid
from app.underwriting.domain.evidence_candidates import (
    CandidateEvidenceDossier,
    CandidateEvidenceReview,
)


_CANDIDATE_DOSSIER_FAMILY_LOCK_DOMAIN = b"underwriting:candidate-dossier-family:v1\x00"
_CANDIDATE_SQLITE_WRITE_RESERVATION = "candidate_sqlite_write_reservation"
_SQLITE_ORM_WRITE_TRANSACTION = "sqlite_orm_write_transaction"
_CANDIDATE_SELECTED_REPLAY = "candidate_selected_replay"


def _candidate_canonical_hash(value: object) -> str:
    """Hash JSON exactly as the kernel does without importing it during model setup."""
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


class UnderwritingSourceManifestVersion(Base):
    __tablename__ = "uw_source_manifest_versions"
    __table_args__ = (
        UniqueConstraint("manifest_key", "version", name="uq_uw_source_manifest_version"),
        Index("ix_uw_source_manifest_versions_basis_key_version", "basis_id", "manifest_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    manifest_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMetricDefinitionVersion(Base):
    __tablename__ = "uw_metric_definition_versions"
    __table_args__ = (
        UniqueConstraint("metric_key", "version", name="uq_uw_metric_definition_version"),
        Index("ix_uw_metric_definition_versions_basis_key_version", "basis_id", "metric_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    unit: Mapped[str] = mapped_column(String(48), nullable=False)
    period_semantics: Mapped[str] = mapped_column(String(32), nullable=False)
    source_role: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregation: Mapped[str] = mapped_column(String(32), nullable=False)
    reconciliation_tolerance: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_metric_definition_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMetricObservation(Base):
    __tablename__ = "uw_metric_observations"
    __table_args__ = (
        UniqueConstraint(
            "basis_id", "metric_key", "definition_version", "observed_end", "dimension_hash", "source_id",
            name="uq_uw_metric_observation_identity",
        ),
        Index("ix_uw_metric_observations_basis_metric_available_at", "basis_id", "metric_key", "available_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    metric_key: Mapped[str] = mapped_column(String(160), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    definition_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_metric_definition_versions.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    source_id: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(28, 8), nullable=False)
    unit: Mapped[str] = mapped_column(String(48), nullable=False)
    observed_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_locator: Mapped[str] = mapped_column(Text, nullable=False)
    dimensions: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    dimension_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_metric_observations.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingMechanismPackVersion(Base):
    __tablename__ = "uw_mechanism_pack_versions"
    __table_args__ = (
        UniqueConstraint("mechanism_key", "version", name="uq_uw_mechanism_pack_version"),
        Index("ix_uw_mechanism_pack_versions_object_basis_status", "object_id", "basis_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    mechanism_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    definition_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_mechanism_pack_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingIndustryStateVersion(Base):
    __tablename__ = "uw_industry_state_versions"
    __table_args__ = (
        UniqueConstraint("object_id", "basis_id", "version", name="uq_uw_industry_state_version"),
        Index("ix_uw_industry_state_versions_object_basis_version", "object_id", "basis_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    mechanism_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_mechanism_pack_versions.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingIndustryScenarioVersion(Base):
    __tablename__ = "uw_industry_scenario_versions"
    __table_args__ = (
        UniqueConstraint("industry_state_id", "scenario_key", "version", name="uq_uw_industry_scenario_version"),
        Index("ix_uw_industry_scenario_versions_state_key_version", "industry_state_id", "scenario_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    scenario_key: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    industry_state_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_industry_scenario_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingCompanyExposureVersion(Base):
    __tablename__ = "uw_company_exposure_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "industry_state_id", "exposure_key", "version", name="uq_uw_company_exposure_version"),
        Index("ix_uw_company_exposure_versions_company_basis_key", "company_id", "basis_id", "exposure_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    industry_state_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=False)
    exposure_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_company_exposure_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingEarningsEngineVersion(Base):
    __tablename__ = "uw_earnings_engine_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "basis_id", "version", name="uq_uw_earnings_engine_version"),
        Index("ix_uw_earnings_engine_versions_company_basis_version", "company_id", "basis_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    industry_state_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_industry_state_versions.id"), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_earnings_engine_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingForecastInputVersion(Base):
    __tablename__ = "uw_forecast_input_versions"
    __table_args__ = (
        UniqueConstraint("company_id", "basis_id", "input_key", "version", name="uq_uw_forecast_input_version"),
        Index("ix_uw_forecast_input_versions_company_basis_key", "company_id", "basis_id", "input_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    input_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    earnings_engine_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_earnings_engine_versions.id"), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_forecast_input_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingFalsifierVersion(Base):
    __tablename__ = "uw_falsifier_versions"
    __table_args__ = (
        UniqueConstraint("mechanism_id", "falsifier_key", "version", name="uq_uw_falsifier_version"),
        Index("ix_uw_falsifier_versions_mechanism_key_version", "mechanism_id", "falsifier_key", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    mechanism_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_mechanism_pack_versions.id"), nullable=False)
    falsifier_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("uw_falsifier_versions.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UnderwritingEvidenceCandidateDossierVersion(Base):
    __tablename__ = "uw_evidence_candidate_dossier_versions"
    __table_args__ = (
        UniqueConstraint(
            "object_id", "basis_id", "dossier_key", "version",
            name="uq_uw_evidence_candidate_dossier_version",
        ),
        UniqueConstraint("supersedes_id", name="uq_uw_evidence_candidate_dossier_successor"),
        CheckConstraint(
            "status = 'candidate'",
            name="ck_uw_evidence_candidate_dossier_status",
        ),
        CheckConstraint(
            "purpose = 'evidence_candidate'",
            name="ck_uw_evidence_candidate_dossier_purpose",
        ),
        Index(
            "ix_uw_evidence_candidate_dossiers_object_basis_status",
            "object_id", "basis_id", "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    dossier_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_research_objects.id"), nullable=False)
    basis_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_historical_bases.id"), nullable=False)
    source_manifest_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("uw_source_manifest_versions.id"), nullable=False)
    scope_statement: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rejected_calculations: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("uw_evidence_candidate_dossier_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_contract(
        cls, *, id: uuid.UUID, contract: CandidateEvidenceDossier
    ) -> "UnderwritingEvidenceCandidateDossierVersion":
        """Build a row solely from the sealed dossier contract."""
        return cls(
            id=id,
            dossier_key=contract.dossier_key,
            version=contract.version,
            object_id=contract.object_id,
            basis_id=contract.basis_id,
            source_manifest_id=contract.source_manifest_id,
            scope_statement=contract.scope_statement,
            purpose=contract.purpose,
            status=contract.status.value,
            rejected_calculations=list(contract.rejected_calculations),
            payload=contract.canonical_payload,
            source_manifest_hash=contract.source_manifest_hash,
            content_hash=contract.content_hash,
            supersedes_id=contract.supersedes_id,
            created_at=contract.created_at,
        )


class UnderwritingEvidenceCandidateReviewVersion(Base):
    __tablename__ = "uw_evidence_candidate_review_versions"
    __table_args__ = (
        UniqueConstraint(
            "dossier_id", "reviewer_identity", "reviewer_role",
            name="uq_uw_evidence_candidate_review_identity",
        ),
        UniqueConstraint("dossier_id", "reviewer_role", name="uq_uw_evidence_candidate_review_role"),
        UniqueConstraint("dossier_id", "reviewer_identity", name="uq_uw_evidence_candidate_review_reviewer"),
        Index("ix_uw_evidence_candidate_reviews_dossier_role", "dossier_id", "reviewer_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    dossier_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("uw_evidence_candidate_dossier_versions.id"), nullable=False
    )
    dossier_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_identity: Mapped[str] = mapped_column(String(320), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @classmethod
    def from_contract(
        cls, *, id: uuid.UUID, contract: CandidateEvidenceReview
    ) -> "UnderwritingEvidenceCandidateReviewVersion":
        """Build a row solely from the sealed review contract."""
        return cls(
            id=id,
            dossier_id=contract.dossier_id,
            dossier_content_hash=contract.dossier_content_hash,
            reviewer_identity=contract.reviewer_identity,
            reviewer_role=contract.reviewer_role,
            decision=contract.decision,
            rationale=contract.rationale,
            payload=contract.canonical_payload,
            content_hash=contract.content_hash,
            reviewed_at=contract.reviewed_at,
            created_at=contract.reviewed_at,
        )


def _validate_dossier_row(row: UnderwritingEvidenceCandidateDossierVersion) -> None:
    contract = CandidateEvidenceDossier.from_canonical_payload(row.payload)
    contract.validate_persisted_payload(row.payload, row.content_hash)
    if (row.object_id, row.basis_id, row.source_manifest_id, row.dossier_key, row.version,
        row.scope_statement, row.purpose, row.status, tuple(row.rejected_calculations),
        row.source_manifest_hash, row.supersedes_id, _stored_utc(row.created_at)) != (
        contract.object_id, contract.basis_id, contract.source_manifest_id, contract.dossier_key, contract.version,
        contract.scope_statement, contract.purpose, contract.status.value, contract.rejected_calculations,
        contract.source_manifest_hash, contract.supersedes_id, contract.created_at):
        raise ValidationError("dossier row does not match canonical contract")


def _validate_review_row(row: UnderwritingEvidenceCandidateReviewVersion) -> None:
    contract = CandidateEvidenceReview.from_canonical_payload(row.payload)
    contract.validate_persisted_payload(row.payload, row.content_hash)
    if (row.dossier_id, row.dossier_content_hash, row.reviewer_identity, row.reviewer_role,
        row.decision, row.rationale, _stored_utc(row.reviewed_at)) != (
        contract.dossier_id, contract.dossier_content_hash, contract.reviewer_identity, contract.reviewer_role,
        contract.decision, contract.rationale, contract.reviewed_at):
        raise ValidationError("review row does not match canonical contract")


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _has_unrelated_pending_work(session: Session, candidate_rows: tuple[object, ...]) -> bool:
    allowed = {id(row) for row in candidate_rows}
    return any(
        id(row) not in allowed
        for rows in (session.new, session.dirty, session.deleted)
        for row in rows
    )


def begin_candidate_sqlite_write(
    session: Session, *, candidate_rows: tuple[object, ...] = (),
) -> None:
    """Take SQLite's write reservation before any candidate-family read."""
    if session.get_bind().dialect.name != "sqlite":
        return
    if _has_unrelated_pending_work(session, candidate_rows):
        raise ValidationError("candidate write requires a clean SQLite Session")
    connection = session.connection()
    raw_connection = connection.connection.driver_connection
    # A caller may already have flushed unrelated work in this exact SQLAlchemy
    # transaction.  That write owns SQLite's writer lock; issuing a raw
    # ``BEGIN IMMEDIATE`` again can silently discard its outer transaction.
    if (
        session.in_transaction()
        and session.info.get(_SQLITE_ORM_WRITE_TRANSACTION) is raw_connection
    ):
        if not raw_connection.in_transaction:
            # SQLAlchemy may hold only its logical outer transaction after a
            # flush.  Materialize it before a publication savepoint so a
            # rollback to that savepoint cannot consume caller-owned rows.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            session.info[_CANDIDATE_SQLITE_WRITE_RESERVATION] = raw_connection
        return
    if session.info.get(_CANDIDATE_SQLITE_WRITE_RESERVATION) is raw_connection:
        if raw_connection.in_transaction:
            return
        session.info.pop(_CANDIDATE_SQLITE_WRITE_RESERVATION, None)
    if raw_connection.in_transaction:
        if session.info.get(_SQLITE_ORM_WRITE_TRANSACTION) is raw_connection:
            return
        raise ValidationError("candidate write requires a fresh SQLite transaction")
    connection.exec_driver_sql("BEGIN IMMEDIATE")
    session.info[_CANDIDATE_SQLITE_WRITE_RESERVATION] = raw_connection


def release_candidate_sqlite_write(session: Session) -> None:
    """Release a reservation taken for a rejected repository candidate write."""
    reservation = session.info.pop(_CANDIDATE_SQLITE_WRITE_RESERVATION, None)
    if reservation is not None:
        raw_connection = session.connection().connection.driver_connection
        if reservation is raw_connection and raw_connection.in_transaction:
            # The preflight above guarantees this transaction contained no caller work.
            # It is therefore safe to release only the reservation we opened.
            session.rollback()


def mark_sqlite_orm_write_transaction(session: Session) -> None:
    """Remember an active ORM write transaction without claiming a candidate lock."""
    if session.get_bind().dialect.name != "sqlite":
        return
    raw_connection = session.connection().connection.driver_connection
    if raw_connection.in_transaction:
        session.info[_SQLITE_ORM_WRITE_TRANSACTION] = raw_connection


def require_candidate_write_read_committed(session: Session) -> None:
    """Reject PostgreSQL snapshots that cannot observe a waited-for family lock."""
    if session.get_bind().dialect.name != "postgresql":
        return
    isolation = session.connection().get_isolation_level().upper().replace("_", " ")
    if isolation in {"REPEATABLE READ", "SERIALIZABLE"}:
        raise ValidationError("candidate writes require READ COMMITTED")


def _candidate_governance_schema_available(session: Session) -> bool:
    """Keep isolated row-shape tests independent of the full research schema."""
    return {
        "uw_historical_bases",
        "uw_source_manifest_versions",
        "uw_evidence_candidate_dossier_versions",
        "uw_evidence_candidate_review_versions",
    } <= set(inspect(session.get_bind()).get_table_names())


def candidate_dossier_family_lock_statement(
    *, object_id: uuid.UUID, basis_id: uuid.UUID, dossier_key: str
) -> tuple[object, int]:
    """Build the stable, namespaced PostgreSQL lock statement for one family."""
    identity = f"{object_id}:{basis_id}:{dossier_key}".encode("utf-8")
    lock_id = int.from_bytes(
        hashlib.sha256(_CANDIDATE_DOSSIER_FAMILY_LOCK_DOMAIN + identity).digest()[:8],
        byteorder="big", signed=True,
    )
    return (
        select(func.pg_advisory_xact_lock(
            bindparam("candidate_dossier_family_lock_id", value=lock_id)
        )),
        lock_id,
    )


def candidate_dossier_family_lock(
    session: Session, *, object_id: uuid.UUID, basis_id: uuid.UUID, dossier_key: str,
    candidate_rows: tuple[object, ...] = (),
) -> None:
    """Acquire one transaction-scoped candidate-family lock on every dialect."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        begin_candidate_sqlite_write(session, candidate_rows=candidate_rows)
        return
    if dialect != "postgresql":
        raise RuntimeError("database dialect cannot serialize candidate dossier family")
    statement, _ = candidate_dossier_family_lock_statement(
        object_id=object_id, basis_id=basis_id, dossier_key=dossier_key,
    )
    session.execute(statement)


def _candidate_basis_cutoff(session: Session, basis_id: uuid.UUID) -> datetime:
    from app.underwriting.persistence.models import UnderwritingHistoricalBasis

    basis = session.get(UnderwritingHistoricalBasis, basis_id)
    if basis is None:
        raise ValidationError("historical basis does not exist")
    return _stored_utc(basis.cutoff)


def _candidate_manifest(
    session: Session, *, manifest_id: uuid.UUID, basis_id: uuid.UUID, cutoff: datetime
) -> UnderwritingSourceManifestVersion:
    from app.underwriting.persistence.models import UnderwritingHistoricalBasis

    manifest = session.get(UnderwritingSourceManifestVersion, manifest_id)
    if manifest is None:
        raise ValidationError("source manifest does not exist")
    if manifest.basis_id != basis_id:
        raise ValidationError("source manifest must belong to the target basis")
    if _stored_utc(manifest.created_at) > cutoff:
        raise ValidationError("source manifest is unavailable at basis cutoff")
    try:
        # Application model registration imports this module while the
        # source-freeze service is still importing the kernel.
        from app.underwriting.services.source_freeze import freeze_manifest

        frozen = freeze_manifest(manifest.manifest, cutoff)
    except ValidationError as exc:
        raise ValidationError("source manifest is not a verified frozen manifest") from exc
    basis = session.get(UnderwritingHistoricalBasis, basis_id)
    if basis is None:
        raise ValidationError("historical basis does not exist")
    if frozen.manifest_hash != manifest.manifest_hash:
        raise ValidationError("source manifest frozen hash does not match content")
    if manifest.manifest_hash != basis.source_manifest_hash:
        raise ValidationError("source manifest hash does not match historical basis")
    if manifest.content_hash != _candidate_canonical_hash(manifest.manifest):
        raise ValidationError("source manifest content hash does not match content")
    return manifest


def _candidate_items_match_manifest(
    contract: CandidateEvidenceDossier,
    manifest: UnderwritingSourceManifestVersion,
    cutoff: datetime,
) -> None:
    sources = manifest.manifest.get("sources") if isinstance(manifest.manifest, Mapping) else None
    if not isinstance(sources, list):
        raise ValidationError("source manifest sources must be a list")
    membership: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            raise ValidationError("source manifest source must be an object")
        source_id, locator = source.get("source_id"), source.get("locator")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValidationError("source_id must not be empty")
        if not isinstance(locator, str) or not locator.strip():
            raise ValidationError("source locator is required")
        if source_id in membership:
            raise ValidationError("source_id must be unique")
        membership[source_id] = locator
    for item in contract.items:
        if item.available_at > cutoff:
            raise ValidationError("candidate item available_at must not exceed basis cutoff")
        if item.source_id not in membership:
            raise ValidationError("candidate item references unknown source")
        if item.source_locator != membership[item.source_id]:
            raise ValidationError("candidate item source_locator does not match source manifest")


def _validate_candidate_dossier_lineage(
    session: Session, row: UnderwritingEvidenceCandidateDossierVersion
) -> None:
    """Verify predecessor continuity without recursively materializing every parent."""
    current: Mapping[str, object] = {
        "id": row.id,
        "version": row.version,
        "supersedes_id": row.supersedes_id,
        "object_id": row.object_id,
        "basis_id": row.basis_id,
        "dossier_key": row.dossier_key,
        "source_manifest_id": row.source_manifest_id,
        "source_manifest_hash": row.source_manifest_hash,
    }
    table = UnderwritingEvidenceCandidateDossierVersion.__table__
    seen_ids: set[object] = set()
    while True:
        if current["id"] in seen_ids:
            raise ValidationError("dossier predecessor chain contains a cycle")
        seen_ids.add(current["id"])
        version = current["version"]
        supersedes_id = current["supersedes_id"]
        if version == 1:
            if supersedes_id is not None:
                raise ValidationError("initial dossier must not have a predecessor")
            return
        if not isinstance(version, int) or version < 1:
            raise ValidationError("dossier version must be positive")
        if supersedes_id is None:
            raise ValidationError("dossier successor requires a predecessor")
        loaded_parent = session.identity_map.get(
            identity_key(UnderwritingEvidenceCandidateDossierVersion, supersedes_id)
        )
        if loaded_parent is not None:
            parent: Mapping[str, object] | None = {
                "id": loaded_parent.id,
                "version": loaded_parent.version,
                "supersedes_id": loaded_parent.supersedes_id,
                "object_id": loaded_parent.object_id,
                "basis_id": loaded_parent.basis_id,
                "dossier_key": loaded_parent.dossier_key,
                "source_manifest_id": loaded_parent.source_manifest_id,
                "source_manifest_hash": loaded_parent.source_manifest_hash,
            }
        else:
            parent = session.execute(
                select(
                    table.c.id,
                    table.c.version,
                    table.c.supersedes_id,
                    table.c.object_id,
                    table.c.basis_id,
                    table.c.dossier_key,
                    table.c.source_manifest_id,
                    table.c.source_manifest_hash,
                ).where(table.c.id == supersedes_id)
            ).mappings().one_or_none()
        if parent is None:
            raise ValidationError("dossier predecessor does not exist")
        if (
            parent["object_id"] != current["object_id"]
            or parent["basis_id"] != current["basis_id"]
            or parent["dossier_key"] != current["dossier_key"]
            or parent["source_manifest_id"] != current["source_manifest_id"]
            or parent["source_manifest_hash"] != current["source_manifest_hash"]
        ):
            raise ValidationError("dossier successor must preserve immutable references")
        if parent["version"] + 1 != version:
            raise ValidationError("dossier successor version is not contiguous")
        current = parent


def validate_candidate_dossier_governance(
    session: Session,
    row: UnderwritingEvidenceCandidateDossierVersion,
    *,
    enforce_current: bool,
    enforce_lineage: bool = True,
) -> None:
    """Apply the candidate dossier's cross-row invariants for every writer."""
    _validate_dossier_row(row)
    contract = CandidateEvidenceDossier.from_canonical_payload(row.payload)
    cutoff = _candidate_basis_cutoff(session, row.basis_id)
    if _stored_utc(row.created_at) > cutoff:
        raise ValidationError("created_at must not exceed basis cutoff")
    manifest = _candidate_manifest(
        session, manifest_id=row.source_manifest_id, basis_id=row.basis_id, cutoff=cutoff,
    )
    if contract.source_manifest_hash != manifest.manifest_hash:
        raise ValidationError("dossier source_manifest_hash does not match source manifest")
    _candidate_items_match_manifest(contract, manifest, cutoff)
    if enforce_lineage:
        _validate_candidate_dossier_lineage(session, row)
    if not enforce_current:
        return
    current = session.scalar(
        select(UnderwritingEvidenceCandidateDossierVersion)
        .where(
            UnderwritingEvidenceCandidateDossierVersion.object_id == row.object_id,
            UnderwritingEvidenceCandidateDossierVersion.basis_id == row.basis_id,
            UnderwritingEvidenceCandidateDossierVersion.dossier_key == row.dossier_key,
        )
        .order_by(
            UnderwritingEvidenceCandidateDossierVersion.version.desc(),
            UnderwritingEvidenceCandidateDossierVersion.id.desc(),
        )
        .limit(1)
    )
    expected_version = (current.version + 1) if current else 1
    expected_parent = current.id if current else None
    if row.version != expected_version or row.supersedes_id != expected_parent:
        raise ValidationError("dossier successor version or predecessor is not canonical")


@contextmanager
def selected_candidate_replay(session: Session):
    """Keep a selected revision replay local to its sealed candidate parents."""
    previous = session.info.get(_CANDIDATE_SELECTED_REPLAY)
    session.info[_CANDIDATE_SELECTED_REPLAY] = True
    try:
        yield
    finally:
        if previous is None:
            session.info.pop(_CANDIDATE_SELECTED_REPLAY, None)
        else:
            session.info[_CANDIDATE_SELECTED_REPLAY] = previous


def validate_candidate_review_governance(
    session: Session,
    row: UnderwritingEvidenceCandidateReviewVersion,
    *,
    enforce_current: bool,
) -> None:
    """Apply review-to-current-dossier governance for every writer."""
    _validate_review_row(row)
    dossier = session.get(UnderwritingEvidenceCandidateDossierVersion, row.dossier_id)
    if dossier is None:
        raise ValidationError("candidate dossier does not exist")
    if dossier.content_hash != row.dossier_content_hash:
        raise ValidationError("review dossier_content_hash does not match dossier")
    cutoff = _candidate_basis_cutoff(session, dossier.basis_id)
    if _stored_utc(row.reviewed_at) > cutoff:
        raise ValidationError("reviewed_at must not exceed basis cutoff")
    if not enforce_current:
        return
    current = session.scalar(
        select(UnderwritingEvidenceCandidateDossierVersion)
        .where(
            UnderwritingEvidenceCandidateDossierVersion.object_id == dossier.object_id,
            UnderwritingEvidenceCandidateDossierVersion.basis_id == dossier.basis_id,
            UnderwritingEvidenceCandidateDossierVersion.dossier_key == dossier.dossier_key,
        )
        .order_by(
            UnderwritingEvidenceCandidateDossierVersion.version.desc(),
            UnderwritingEvidenceCandidateDossierVersion.id.desc(),
        )
        .limit(1)
    )
    if current is None or current.id != dossier.id:
        raise ValidationError("candidate dossier is no longer current")


@event.listens_for(Session, "before_flush")
def _validate_direct_candidate_writes(session, _flush_context, _instances) -> None:
    """Close the direct-ORM bypass before SQLAlchemy emits candidate inserts."""
    rows = tuple(session.new)
    candidates = tuple(
        row for row in rows
        if isinstance(row, (UnderwritingEvidenceCandidateDossierVersion, UnderwritingEvidenceCandidateReviewVersion))
    )
    if not candidates:
        return
    if session.info.get("candidate_repository_write"):
        return
    if not _candidate_governance_schema_available(session):
        return
    require_candidate_write_read_committed(session)
    begin_candidate_sqlite_write(session, candidate_rows=candidates)
    with session.no_autoflush:
        for row in candidates:
            if isinstance(row, UnderwritingEvidenceCandidateDossierVersion):
                candidate_dossier_family_lock(
                    session, object_id=row.object_id, basis_id=row.basis_id,
                    dossier_key=row.dossier_key, candidate_rows=candidates,
                )
                validate_candidate_dossier_governance(session, row, enforce_current=True)
            else:
                dossier = session.get(UnderwritingEvidenceCandidateDossierVersion, row.dossier_id)
                if dossier is not None:
                    candidate_dossier_family_lock(
                        session, object_id=dossier.object_id, basis_id=dossier.basis_id,
                        dossier_key=dossier.dossier_key, candidate_rows=candidates,
                    )
                validate_candidate_review_governance(session, row, enforce_current=True)


@event.listens_for(Session, "after_flush_postexec")
def _mark_sqlite_orm_write_after_any_flush(session, _flush_context) -> None:
    mark_sqlite_orm_write_transaction(session)


@event.listens_for(Session, "after_transaction_end")
def _clear_sqlite_write_transaction_marker(session, transaction) -> None:
    """Do not reuse a committed transaction's SQLite writer-lock marker."""
    if transaction.parent is None:
        session.info.pop(_SQLITE_ORM_WRITE_TRANSACTION, None)
        session.info.pop(_CANDIDATE_SQLITE_WRITE_RESERVATION, None)


@event.listens_for(UnderwritingEvidenceCandidateDossierVersion, "before_insert")
def _validate_candidate_dossier_insert(_mapper, _connection, target) -> None:
    _validate_dossier_row(target)


@event.listens_for(UnderwritingEvidenceCandidateDossierVersion, "load")
def _validate_candidate_dossier_load(target, _context) -> None:
    _validate_dossier_row(target)


@event.listens_for(UnderwritingEvidenceCandidateReviewVersion, "before_insert")
def _validate_candidate_review_insert(_mapper, connection, target) -> None:
    _validate_review_row(target)
    expected_hash = connection.execute(
        select(UnderwritingEvidenceCandidateDossierVersion.content_hash).where(
            UnderwritingEvidenceCandidateDossierVersion.id == target.dossier_id
        )
    ).scalar_one_or_none()
    if expected_hash != target.dossier_content_hash:
        raise ValidationError("review dossier_content_hash does not match dossier")


@event.listens_for(UnderwritingEvidenceCandidateReviewVersion, "load")
def _validate_candidate_review_load(target, _context) -> None:
    _validate_review_row(target)


@event.listens_for(Session, "loaded_as_persistent")
def _validate_loaded_candidate_governance(session, instance) -> None:
    """Fail closed when Core/raw candidate rows are materialized by a fresh ORM session."""
    if not isinstance(
        instance,
        (UnderwritingEvidenceCandidateDossierVersion, UnderwritingEvidenceCandidateReviewVersion),
    ):
        return
    if not _candidate_governance_schema_available(session):
        return
    with session.no_autoflush:
        if isinstance(instance, UnderwritingEvidenceCandidateDossierVersion):
            # Older dossier versions remain valid historical evidence; only a
            # new successor must be the current family head before persistence.
            validate_candidate_dossier_governance(
                session,
                instance,
                enforce_current=False,
                enforce_lineage=not session.info.get(_CANDIDATE_SELECTED_REPLAY, False),
            )
        else:
            # A review is immutable historical evidence.  Currentness governs
            # insertion only; replay still verifies its sealed dossier hash,
            # basis cutoff, and source closure.
            validate_candidate_review_governance(session, instance, enforce_current=False)
