"""Immutable, non-promotable contracts for reviewed evidence candidates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
import hashlib
import json
import re
from typing import Literal, Mapping
import unicodedata
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


class CandidateEvidenceDossierStatus(StrEnum):
    CANDIDATE = "candidate"


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


_FORBIDDEN_CANDIDATE_ENGLISH_TERMS = (
    ("actual_utilization", ("actual_utilization", "actual_utilisation")),
    ("effective_capacity", ("effective_capacity",)),
    ("price", ("price",)),
    ("valuation", ("valuation",)),
    ("recommendation", ("recommendation",)),
    ("action", ("action",)),
)
_FORBIDDEN_CANDIDATE_CHINESE_TERMS = (
    ("actual_utilization", ("实际利用率", "实际产能利用率", "實際利用率", "實際產能利用率")),
    ("effective_capacity", ("有效产能", "有效產能")),
    ("price", ("价格", "價格", "售价", "售價")),
    ("valuation", ("估值",)),
    ("recommendation", ("建议", "建議", "推荐", "推薦")),
    ("action", ("买入", "買入", "卖出", "賣出", "持仓", "持倉", "建仓", "建倉")),
)


def _normalized_semantic_text(value: str) -> tuple[tuple[str, ...], str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    ascii_tokens = tuple(
        token
        for token in re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", normalized)).strip("_").split("_")
        if token
    )
    chinese_text = "".join(
        character for character in normalized
        if "\u3400" <= character <= "\u9fff"
    )
    return ascii_tokens, chinese_text


def _contains_semantic_term(tokens: tuple[str, ...], term: str) -> bool:
    term_tokens = tuple(term.split("_"))
    return any(
        tokens[index:index + len(term_tokens)] == term_tokens
        for index in range(len(tokens) - len(term_tokens) + 1)
    )


def _validate_candidate_semantics(*, fields: tuple[str, ...]) -> None:
    """Reject governed claim tokens in every UI-visible claim field.

    ``CandidateEvidenceItem`` supplies its complete UI-visible semantic
    surface, including exclusions and the splicing declaration.  Only the
    dossier's explicitly labelled ``rejected_calculations`` list remains
    outside this boundary so its frozen rejected formulae can be reproduced.
    The controlled vocabulary covers the exact English tokens and Chinese
    phrases defined above. NFKC/casefold tokenization prevents
    ``transaction`` from matching ``action``; Chinese matching compares only
    normalized Han text against that explicit phrase list rather than
    attempting language inference.
    """
    for field in fields:
        ascii_tokens, chinese_text = _normalized_semantic_text(field)
        for term, variants in _FORBIDDEN_CANDIDATE_ENGLISH_TERMS:
            if any(_contains_semantic_term(ascii_tokens, variant) for variant in variants):
                raise ValidationError(f"forbidden candidate semantic: {term}")
        for term, variants in _FORBIDDEN_CANDIDATE_CHINESE_TERMS:
            if any(variant in chinese_text for variant in variants):
                raise ValidationError(f"forbidden candidate semantic: {term}")


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
        _validate_candidate_semantics(
            fields=tuple(
                value for value in (
                    self.metric_key, self.scope_statement, self.methodology,
                    self.transcription_method, self.scenario_use, self.unknown_reason,
                    self.prohibited_splicing_declaration, *self.exclusions,
                )
                if value is not None
            )
        )

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
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
            "exclusions": list(self.exclusions),
            "methodology": self.methodology,
            "prohibited_splicing_declaration": self.prohibited_splicing_declaration,
            "transcription_method": self.transcription_method,
            "error_bound": None if self.error_bound is None else str(self.error_bound),
            "scenario_use": self.scenario_use,
            "not_observed_declared": self.not_observed_declared,
            "unknown_reason": self.unknown_reason,
        }

    @property
    def content_hash(self) -> str:
        return _canonical_hash(self.canonical_payload)

    @classmethod
    def from_canonical_payload(cls, payload: Mapping[str, object]) -> "CandidateEvidenceItem":
        expected = set(cls.__dataclass_fields__)
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValidationError("candidate item payload is not canonical")
        try:
            return cls(
                metric_key=payload["metric_key"], status=CandidateEvidenceStatus(payload["status"]),
                value=None if payload["value"] is None else Decimal(payload["value"]), unit=payload["unit"],
                observed_start=datetime.fromisoformat(payload["observed_start"]), observed_end=datetime.fromisoformat(payload["observed_end"]),
                available_at=datetime.fromisoformat(payload["available_at"]), source_id=payload["source_id"],
                source_locator=payload["source_locator"], scope_statement=payload["scope_statement"],
                exclusions=tuple(payload["exclusions"]), methodology=payload["methodology"],
                prohibited_splicing_declaration=payload["prohibited_splicing_declaration"],
                transcription_method=payload["transcription_method"],
                error_bound=None if payload["error_bound"] is None else Decimal(payload["error_bound"]),
                scenario_use=payload["scenario_use"], not_observed_declared=payload["not_observed_declared"],
                unknown_reason=payload["unknown_reason"],
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise ValidationError("candidate item payload is not canonical") from exc


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
    status: CandidateEvidenceDossierStatus = CandidateEvidenceDossierStatus.CANDIDATE
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
        if type(self.version) is not int:
            raise ValidationError("version must be an integer")
        if self.version < 1:
            raise ValidationError("version must be at least 1")
        if not isinstance(self.status, CandidateEvidenceDossierStatus):
            raise ValidationError("status must be a CandidateEvidenceDossierStatus")
        if self.purpose != "evidence_candidate":
            raise ValidationError("purpose must be evidence_candidate")
        _validate_candidate_semantics(fields=(self.dossier_key, self.scope_statement))
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
    def canonical_payload(self) -> dict[str, object]:
        return {
            "object_id": str(self.object_id), "basis_id": str(self.basis_id),
            "source_manifest_id": str(self.source_manifest_id), "dossier_key": self.dossier_key,
            "version": self.version, "scope_statement": self.scope_statement,
            "status": self.status.value,
            "purpose": self.purpose,
            "items": [item.canonical_payload for item in self.items],
            "rejected_calculations": list(self.rejected_calculations),
            "source_manifest_hash": self.source_manifest_hash,
            "created_at": self.created_at.isoformat(),
            "supersedes_id": None if self.supersedes_id is None else str(self.supersedes_id),
        }

    @property
    def content_hash(self) -> str:
        return _canonical_hash(self.canonical_payload)

    def validate_persisted_payload(
        self, payload: Mapping[str, object], content_hash: str
    ) -> None:
        if not isinstance(payload, Mapping) or dict(payload) != self.canonical_payload:
            raise ValidationError("dossier payload does not match canonical contract")
        if content_hash != self.content_hash:
            raise ValidationError("dossier content_hash does not match canonical contract")

    @classmethod
    def from_canonical_payload(cls, payload: Mapping[str, object]) -> "CandidateEvidenceDossier":
        expected = {
            "object_id", "basis_id", "source_manifest_id", "dossier_key", "version", "scope_statement",
            "status", "purpose", "items", "rejected_calculations", "source_manifest_hash", "created_at", "supersedes_id",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValidationError("dossier payload is not canonical")
        try:
            return cls(
                object_id=UUID(payload["object_id"]), basis_id=UUID(payload["basis_id"]),
                source_manifest_id=UUID(payload["source_manifest_id"]), dossier_key=payload["dossier_key"],
                version=payload["version"], scope_statement=payload["scope_statement"],
                items=tuple(CandidateEvidenceItem.from_canonical_payload(item) for item in payload["items"]),
                rejected_calculations=tuple(payload["rejected_calculations"]), source_manifest_hash=payload["source_manifest_hash"],
                created_at=datetime.fromisoformat(payload["created_at"]), status=CandidateEvidenceDossierStatus(payload["status"]),
                purpose=payload["purpose"], supersedes_id=None if payload["supersedes_id"] is None else UUID(payload["supersedes_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("dossier payload is not canonical") from exc


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
        _validate_candidate_semantics(fields=(self.rationale,))
        object.__setattr__(self, "reviewed_at", _utc(self.reviewed_at, "reviewed_at"))

    @property
    def canonical_payload(self) -> dict[str, object]:
        return {
            "dossier_id": str(self.dossier_id), "dossier_content_hash": self.dossier_content_hash,
            "reviewer_identity": self.reviewer_identity, "reviewer_role": self.reviewer_role,
            "decision": self.decision, "rationale": self.rationale,
            "reviewed_at": self.reviewed_at.isoformat(),
        }

    @property
    def content_hash(self) -> str:
        return _canonical_hash(self.canonical_payload)

    def validate_persisted_payload(
        self, payload: Mapping[str, object], content_hash: str
    ) -> None:
        if not isinstance(payload, Mapping) or dict(payload) != self.canonical_payload:
            raise ValidationError("review payload does not match canonical contract")
        if content_hash != self.content_hash:
            raise ValidationError("review content_hash does not match canonical contract")

    @classmethod
    def from_canonical_payload(cls, payload: Mapping[str, object]) -> "CandidateEvidenceReview":
        expected = {"dossier_id", "dossier_content_hash", "reviewer_identity", "reviewer_role", "decision", "rationale", "reviewed_at"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValidationError("review payload is not canonical")
        try:
            return cls(
                dossier_id=UUID(payload["dossier_id"]), dossier_content_hash=payload["dossier_content_hash"],
                reviewer_identity=payload["reviewer_identity"], reviewer_role=payload["reviewer_role"],
                decision=payload["decision"], rationale=payload["rationale"],
                reviewed_at=datetime.fromisoformat(payload["reviewed_at"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("review payload is not canonical") from exc
