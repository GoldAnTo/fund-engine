"""Immutable, non-promotable contracts for reviewed evidence candidates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
from typing import Literal
from uuid import UUID

from app.models.ledger import ValidationError


class CandidateEvidenceStatus(StrEnum):
    SOURCE_REPORTED = "source_reported"
    OFFICIAL_AGGREGATE = "official_aggregate"
    CHART_APPROXIMATION = "chart_approximation"
    ASSUMPTION_BOUND = "assumption_bound"
    UNKNOWN = "unknown"


class CandidateEvidenceReviewRole(StrEnum):
    PROVENANCE = "provenance"
    METHODOLOGY = "methodology"


class CandidateEvidenceReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} must not be empty")
    return value.strip()


def _utc(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal")
    return value


def _hash(value: str, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValidationError(f"{field} must be a lowercase SHA-256")
    return value


def _canonical_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CandidateEvidenceItem:
    """One source-bound candidate, deliberately separate from ``MetricObservation``."""

    metric_key: str
    status: CandidateEvidenceStatus
    value: Decimal | None
    unit: str | None
    observed_start: datetime
    observed_end: datetime
    available_at: datetime
    source_id: str
    source_locator: str
    scope_statement: str
    exclusions: tuple[str, ...]
    methodology: str
    prohibited_splicing_declaration: str
    transcription_method: str | None = None
    error_bound: Decimal | None = None
    scenario_use: str | None = None
    not_observed_declared: bool = False
    unknown_reason: str | None = None

    def __post_init__(self) -> None:
        _text(self.metric_key, "metric_key")
        if not isinstance(self.status, CandidateEvidenceStatus):
            raise ValidationError("status must be a CandidateEvidenceStatus")
        for field in (
            "metric_key", "source_id", "source_locator", "scope_statement", "methodology",
            "prohibited_splicing_declaration",
        ):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        for field in ("observed_start", "observed_end", "available_at"):
            object.__setattr__(self, field, _utc(getattr(self, field), field))
        if self.observed_start > self.observed_end:
            raise ValidationError("observed_start must not exceed observed_end")
        if not isinstance(self.exclusions, tuple):
            raise ValidationError("exclusions must be a tuple")
        object.__setattr__(
            self,
            "exclusions",
            tuple(_text(value, "exclusion") for value in self.exclusions),
        )

        numeric_statuses = {
            CandidateEvidenceStatus.SOURCE_REPORTED,
            CandidateEvidenceStatus.OFFICIAL_AGGREGATE,
            CandidateEvidenceStatus.CHART_APPROXIMATION,
            CandidateEvidenceStatus.ASSUMPTION_BOUND,
        }
        if self.status in numeric_statuses:
            _decimal(self.value, "value")
            object.__setattr__(self, "unit", _text(self.unit, "unit"))
        if self.status is CandidateEvidenceStatus.CHART_APPROXIMATION:
            object.__setattr__(self, "transcription_method", _text(self.transcription_method, "chart_approximation requires transcription_method"))
            error_bound = _decimal(self.error_bound, "chart_approximation requires error_bound")
            if error_bound < 0:
                raise ValidationError("chart_approximation error_bound must not be negative")
        elif self.transcription_method is not None or self.error_bound is not None:
            raise ValidationError("transcription fields are only valid for chart_approximation")

        if self.status is CandidateEvidenceStatus.ASSUMPTION_BOUND:
            object.__setattr__(self, "scenario_use", _text(self.scenario_use, "assumption_bound requires scenario_use"))
            if self.not_observed_declared is not True:
                raise ValidationError("assumption_bound requires not_observed_declared")
        elif self.scenario_use is not None or self.not_observed_declared:
            raise ValidationError("assumption fields are only valid for assumption_bound")

        if self.status is CandidateEvidenceStatus.UNKNOWN:
            if self.value is not None:
                raise ValidationError("unknown must not carry a numeric value")
            if self.unit is not None:
                raise ValidationError("unknown must not carry a unit")
            object.__setattr__(self, "unknown_reason", _text(self.unknown_reason, "unknown requires unknown_reason"))
        elif self.unknown_reason is not None:
            raise ValidationError("unknown_reason is only valid for unknown")

    @property
    def content_hash(self) -> str:
        return _canonical_hash({
            "metric_key": self.metric_key,
            "status": self.status.value,
            "value": None if self.value is None else str(self.value),
            "unit": self.unit,
            "observed_start": self.observed_start.isoformat(),
            "observed_end": self.observed_end.isoformat(),
            "available_at": self.available_at.isoformat(),
            "source_id": self.source_id,
            "source_locator": self.source_locator,
            "scope_statement": self.scope_statement,
            "exclusions": self.exclusions,
            "methodology": self.methodology,
            "prohibited_splicing_declaration": self.prohibited_splicing_declaration,
            "transcription_method": self.transcription_method,
            "error_bound": None if self.error_bound is None else str(self.error_bound),
            "scenario_use": self.scenario_use,
            "not_observed_declared": self.not_observed_declared,
            "unknown_reason": self.unknown_reason,
        })


@dataclass(frozen=True, slots=True)
class CandidateEvidenceDossier:
    object_id: UUID
    basis_id: UUID
    source_manifest_id: UUID
    dossier_key: str
    version: int
    scope_statement: str
    items: tuple[CandidateEvidenceItem, ...]
    rejected_calculations: tuple[str, ...]
    source_manifest_hash: str
    created_at: datetime
    purpose: Literal["evidence_candidate"] = "evidence_candidate"
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        for field in ("object_id", "basis_id", "source_manifest_id"):
            if type(getattr(self, field)) is not UUID:
                raise ValidationError(f"{field} must be a UUID")
        if self.supersedes_id is not None and type(self.supersedes_id) is not UUID:
            raise ValidationError("supersedes_id must be a UUID")
        object.__setattr__(self, "dossier_key", _text(self.dossier_key, "dossier_key"))
        object.__setattr__(self, "scope_statement", _text(self.scope_statement, "scope_statement"))
        if self.version < 1:
            raise ValidationError("version must be at least 1")
        if self.purpose != "evidence_candidate":
            raise ValidationError("purpose must be evidence_candidate")
        if not isinstance(self.items, tuple) or not self.items:
            raise ValidationError("items must be a non-empty tuple")
        if any(type(item) is not CandidateEvidenceItem for item in self.items):
            raise ValidationError("items must contain CandidateEvidenceItem values")
        if len({item.content_hash for item in self.items}) != len(self.items):
            raise ValidationError("candidate items must be unique")
        if not isinstance(self.rejected_calculations, tuple) or not self.rejected_calculations:
            raise ValidationError("rejected_calculations must be a non-empty tuple")
        rejected_calculations = tuple(
            _text(calculation, "rejected_calculation")
            for calculation in self.rejected_calculations
        )
        if len(set(rejected_calculations)) != len(rejected_calculations):
            raise ValidationError("rejected_calculations must be unique")
        object.__setattr__(self, "rejected_calculations", rejected_calculations)
        _hash(self.source_manifest_hash, "source_manifest_hash")
        object.__setattr__(self, "created_at", _utc(self.created_at, "created_at"))

    @property
    def content_hash(self) -> str:
        return _canonical_hash({
            "object_id": str(self.object_id), "basis_id": str(self.basis_id),
            "source_manifest_id": str(self.source_manifest_id), "dossier_key": self.dossier_key,
            "version": self.version, "scope_statement": self.scope_statement,
            "purpose": self.purpose, "items": tuple(item.content_hash for item in self.items),
            "rejected_calculations": self.rejected_calculations,
            "source_manifest_hash": self.source_manifest_hash,
            "created_at": self.created_at.isoformat(),
            "supersedes_id": None if self.supersedes_id is None else str(self.supersedes_id),
        })


CandidateEvidenceDossierVersion = CandidateEvidenceDossier


@dataclass(frozen=True, slots=True)
class CandidateEvidenceReview:
    dossier_id: UUID
    dossier_content_hash: str
    reviewer_identity: str
    reviewer_role: Literal["provenance", "methodology"]
    decision: Literal["approve", "reject", "request_changes"]
    rationale: str
    reviewed_at: datetime

    def __post_init__(self) -> None:
        if type(self.dossier_id) is not UUID:
            raise ValidationError("dossier_id must be a UUID")
        _hash(self.dossier_content_hash, "dossier_content_hash")
        canonical_reviewer_identity = _text(self.reviewer_identity, "reviewer_identity")
        if self.reviewer_identity != canonical_reviewer_identity:
            raise ValidationError("reviewer_identity must be canonical")
        object.__setattr__(self, "reviewer_identity", canonical_reviewer_identity)
        if self.reviewer_role not in {role.value for role in CandidateEvidenceReviewRole}:
            raise ValidationError("reviewer_role must be provenance or methodology")
        if self.decision not in {decision.value for decision in CandidateEvidenceReviewDecision}:
            raise ValidationError("decision must be approve, reject, or request_changes")
        object.__setattr__(self, "rationale", _text(self.rationale, "rationale"))
        object.__setattr__(self, "reviewed_at", _utc(self.reviewed_at, "reviewed_at"))

    @property
    def content_hash(self) -> str:
        return _canonical_hash({
            "dossier_id": str(self.dossier_id), "dossier_content_hash": self.dossier_content_hash,
            "reviewer_identity": self.reviewer_identity, "reviewer_role": self.reviewer_role,
            "decision": self.decision, "rationale": self.rationale,
            "reviewed_at": self.reviewed_at.isoformat(),
        })
