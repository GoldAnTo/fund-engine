"""Deterministic, lease-fenced gates for automatically admitted evidence.

The gate records an immutable machine decision; it never impersonates a human
review.  Formal publication is a separate operation on ``AtomicClaimService``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Final
from urllib.parse import unquote, urlsplit

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY
from app.datasources.docling import (
    PARSER_VERSION_PYPDF,
    PYPDF_CONFIG_VERSION,
    PYPDF_PACKAGE_VERSION,
    ParsedSpan,
    PdfParserAdapter,
    PypdfAdapter,
)
from app.documents.locators import (
    LocatorInvalidError,
    SourceLocatorV1,
    compute_text_sha256,
    round_trip_check,
    validate_locator_v1,
)
from app.domain.acquisition import B_SCOPE_POLICY_VERSION
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
    AtomicClaimCandidate,
    AIRun,
    CaseDocumentVersion,
    DocumentVersion,
    SourceSpan,
    Thesis,
    ValidationError,
)
from app.models.source_governance import SourceContract
from app.repositories.acquisition import (
    StaleLeaseError,
    validate_persistable_json,
    validate_persistable_text,
)
from app.services.source_admission import source_contract_is_active


B_SCOPE_GATE_VERSION: Final = "b-scope-admission-gates-v3"
AUTOMATIC_ADMISSION_GATE_VERSION: Final = B_SCOPE_GATE_VERSION
_GATE_NAMES: Final = ("source", "temporal", "locator", "semantic")
_QUARANTINE_REASON: Final = "automatic_admission_quarantined"
_OBJECTIVE_ROLES: Final = {
    "support": frozenset({"supports"}),
    "contradict": frozenset({"contradicts"}),
    "alternative_explanation": frozenset({"contextualizes"}),
    "verify_rule": frozenset({"supports", "contradicts"}),
}
_AUTHORITY_BY_SOURCE_ROLE: Final = {
    "company_disclosure": "primary_disclosure",
    "licensed_provider": "licensed_research",
}
ADAPTER_SOURCE_IDENTITY: Final = {
    "gildata": ("licensed_provider", "Gildata", "licensed_research"),
    "sse": (
        "company_disclosure",
        "Shanghai Stock Exchange",
        "primary_disclosure",
    ),
    "szse": (
        "company_disclosure",
        "Shenzhen Stock Exchange",
        "primary_disclosure",
    ),
}
_NUMERIC_VALUE_RE: Final = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_NUMERIC_TOKEN_RE: Final = re.compile(
    r"(?<![0-9A-Za-z.])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?![0-9A-Za-z.])"
)
_UNIT_SEPARATOR_RE: Final = re.compile(r"^[\s,，.:：;；()（）]{0,4}")
_YEAR_RE: Final = re.compile(r"^(20\d{2})$")
_MONTH_RE: Final = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])$")
_METRIC_VALUE_CONNECTOR_RE: Final = re.compile(
    r"^(?:[\s\t|,，:：=＝()（）]|为|达|是|were?|was|is|at|equals?|reached)*$",
    re.IGNORECASE,
)
_SEGMENT_BOUNDARY_RE: Final = re.compile(
    r"(?:[\r\n]+|[。！？!?；;]+|(?<!\d)\.(?!\d))"
)
_NEGATION_MARKERS: Final = ("没有", "未", "不", "not", "no ")
_DIRECTION_MARKERS: Final = {
    "increase": (
        "增长",
        "上升",
        "增加",
        "提升",
        "高于",
        "超过",
        "increased",
        "increase",
        "rose",
        "growth",
        "higher than",
        "above",
        ">",
    ),
    "decrease": (
        "下降",
        "减少",
        "降低",
        "下滑",
        "低于",
        "少于",
        "decreased",
        "decrease",
        "fell",
        "lower than",
        "below",
        "<",
    ),
    "equal": ("等于", "持平", "unchanged", "equal to", "same as"),
}
_WORKER_IDENTITY_RE: Final = re.compile(
    r"^(system:[a-z0-9_.-]+)@([A-Za-z0-9_.-]+)(?:#[A-Za-z0-9_.-]+)?$"
)
_URL_HOSTS: Final = {
    "sse": {
        "canonical": frozenset({"www.sse.com.cn"}),
        "final": frozenset(
            {"www.sse.com.cn", "static.sse.com.cn", "big5.sse.com.cn"}
        ),
    },
    "szse": {
        "canonical": frozenset({"disc.static.szse.cn"}),
        "final": frozenset({"disc.static.szse.cn"}),
    },
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(child) for key, child in value.items()}
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


def _validate_fact_text(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            _validate_fact_text(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_fact_text(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        validate_persistable_text(value, path=path)


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


def _required_text_tuple(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ValueError(f"{field_name} must be a collection")
    normalized = tuple(_required_text(value, field_name) for value in values)
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def frozen_request_digest(job: AcquisitionJob) -> str:
    """Hash complete frozen snapshots plus immutable job-binding identity."""
    request = job.request_snapshot if isinstance(job.request_snapshot, dict) else {}
    policy = job.policy_snapshot if isinstance(job.policy_snapshot, dict) else {}
    validate_persistable_json(request, path="$.request_snapshot")
    validate_persistable_json(policy, path="$.policy_snapshot")
    _validate_fact_text(request, path="$.request_snapshot")
    _validate_fact_text(policy, path="$.policy_snapshot")
    payload = {
        "job_binding": {
            "tenant_id": job.tenant_id,
            "research_case_id": str(job.research_case_id),
            "thesis_id": str(job.thesis_id),
            "research_run_id": (
                str(job.research_run_id) if job.research_run_id is not None else None
            ),
            "idempotency_key": job.idempotency_key,
        },
        "request_snapshot": request,
        "policy_snapshot": policy,
    }
    validate_persistable_json(payload, path="$")
    _validate_fact_text(payload, path="$")
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {
            "byte_size": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_value(child) for child in value]
    return value


def _canonical_row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        column.name: _canonical_value(getattr(row, column.name))
        for column in row.__table__.columns
    }


def canonical_admission_digest(
    *,
    job: AcquisitionJob,
    candidate: AtomicClaimCandidate,
    reference: SourceReference,
    attempt: AcquisitionAttempt,
    artifact: RetrievalArtifact,
    document: DocumentVersion,
    span: SourceSpan,
    binding: RetrievalArtifactDocument,
    contract: SourceContract | None,
    ai_run: AIRun,
) -> str:
    """Bind one decision to every immutable value it authorizes."""
    payload = {
        "schema": "automatic-admission-lineage/v1",
        "job_digest": frozen_request_digest(job),
        "candidate": _canonical_row(candidate),
        "span": _canonical_row(span),
        "artifact": _canonical_row(artifact),
        "reference": _canonical_row(reference),
        "attempt": _canonical_row(attempt),
        "binding": _canonical_row(binding),
        "document": _canonical_row(document),
        "contract": _canonical_row(contract),
        "ai_run": _canonical_row(ai_run),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def trusted_extraction_run(
    session: Session,
    candidate: AtomicClaimCandidate,
    document: DocumentVersion,
    span: SourceSpan,
) -> AIRun:
    fields = (
        candidate.structured_fields
        if isinstance(candidate.structured_fields, Mapping)
        else {}
    )
    run_ref = fields.get("run_ref")
    if not isinstance(run_ref, str) or not run_ref.startswith("extract:"):
        raise ValidationError("automatic-admission extraction lineage is invalid")
    try:
        run_id = uuid.UUID(run_ref.removeprefix("extract:"))
    except ValueError as exc:
        raise ValidationError(
            "automatic-admission extraction lineage is invalid"
        ) from exc
    run = session.get(AIRun, run_id)
    input_ref = run.input_ref if run is not None and isinstance(run.input_ref, dict) else {}
    scope = fields.get("scope") if isinstance(fields.get("scope"), Mapping) else {}
    trusted_status = run is not None and (
        run.status == "success"
        or (
            run.status == "partial"
            and input_ref.get("rule_fallback") is True
            and scope.get("extraction_method") == "financial_table_v1"
        )
    )
    if (
        run is None
        or run.kind != "extract"
        or not trusted_status
        or run.finished_at is None
        or input_ref.get("document_version_id") != str(document.id)
        or str(span.id) not in tuple(input_ref.get("span_ids") or ())
    ):
        raise ValidationError("automatic-admission extraction lineage is invalid")
    return run


def worker_replay_identity(job: AcquisitionJob) -> dict[str, str]:
    owner = job.lease_owner
    match = _WORKER_IDENTITY_RE.fullmatch(owner or "")
    if match is None:
        raise ValidationError("automatic-admission worker identity is invalid")
    return {"actor": match.group(1), "version": match.group(2)}


def parser_replay_identity(parser_version: str) -> dict[str, str | None]:
    if parser_version.startswith("pypdf-"):
        if parser_version != PARSER_VERSION_PYPDF:
            raise ValidationError("automatic-admission pypdf stamp is invalid")
        return {
            "family": "pypdf",
            "stamp": parser_version,
            "package_version": PYPDF_PACKAGE_VERSION,
            "config": PYPDF_CONFIG_VERSION,
        }
    return {
        "family": parser_version.split("-", 1)[0],
        "stamp": parser_version,
        "package_version": None,
        "config": parser_version,
    }


def lease_write_fence(
    session: Session,
    *,
    job_id: uuid.UUID,
    lease_token: str,
    now: datetime,
    allowed_stages: frozenset[str] = frozenset({"admitting"}),
) -> None:
    """Serialize a worker write and compare-and-swap its lease eligibility."""
    checked_at = _require_aware(now, "lease fence clock")
    with session.no_autoflush:
        result = session.execute(
            update(AcquisitionJob)
            .where(
                AcquisitionJob.id == job_id,
                AcquisitionJob.status == "running",
                AcquisitionJob.stage.in_(allowed_stages),
                AcquisitionJob.lease_token == lease_token,
                AcquisitionJob.lease_expires_at.is_not(None),
                AcquisitionJob.lease_expires_at > checked_at,
            )
            .values(updated_at=AcquisitionJob.updated_at)
            .execution_options(synchronize_session=False)
        )
    if result.rowcount != 1:
        raise StaleLeaseError("acquisition lease is stale")


def automatic_temporal_failures(
    reference: SourceReference,
    attempt: AcquisitionAttempt,
    artifact: RetrievalArtifact,
    document: DocumentVersion,
    *,
    cutoff: datetime,
    evaluation_at: datetime,
) -> list[str]:
    """Return fail-closed chronology errors for one automatic source lineage."""
    failures: list[str] = []
    cutoff = _require_aware(cutoff, "cutoff")
    evaluation_at = _require_aware(evaluation_at, "evaluation_at")
    values = {
        "reference_published_at": reference.published_at,
        "reference_created_at": reference.created_at,
        "attempt_started_at": attempt.started_at,
        "attempt_finished_at": attempt.finished_at,
        "published_at": document.published_at,
        "available_at": document.available_at,
        "acquired_at": document.acquired_at,
        "retrieved_at": artifact.retrieved_at,
    }
    for name, value in values.items():
        if value is None:
            failures.append(f"temporal_{name}_missing")
    if any(value is None for value in values.values()):
        return failures
    normalized = {name: _as_utc(value) for name, value in values.items()}
    for name in ("reference_published_at", "published_at", "available_at"):
        if normalized[name] > cutoff:
            failures.append(f"temporal_{name}_after_cutoff")
    for name in (
        "reference_created_at",
        "attempt_started_at",
        "attempt_finished_at",
        "retrieved_at",
        "acquired_at",
    ):
        if normalized[name] > evaluation_at:
            failures.append(f"temporal_{name}_after_evaluation")
    chronology = (
        ("reference_created_at", "attempt_started_at"),
        ("attempt_started_at", "attempt_finished_at"),
        ("attempt_finished_at", "retrieved_at"),
        ("retrieved_at", "acquired_at"),
    )
    for earlier, later in chronology:
        if normalized[earlier] > normalized[later]:
            failures.append(f"temporal_{earlier}_after_{later}")
    if normalized["published_at"] != normalized["reference_published_at"]:
        failures.append("temporal_publication_mismatch")
    if normalized["published_at"] > normalized["available_at"]:
        failures.append("temporal_publication_after_availability")
    return failures


def adapter_url_is_authorized(adapter_key: str, url: object, *, final: bool) -> bool:
    """Apply exact adapter URL boundaries to persisted canonical/final URLs."""
    if not isinstance(url, str) or not url or url != url.strip():
        return False
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.path
        or "%" in parsed.path
        or unquote(parsed.path) != parsed.path
        or "//" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
    ):
        return False
    if not parsed.path.startswith("/") or parsed.path == "/":
        return False
    if adapter_key == "gildata":
        return parsed.scheme == "gildata" and parsed.hostname in {
            "research-report",
            "announcement",
        }
    boundary = _URL_HOSTS.get(adapter_key)
    if boundary is None:
        return False
    kind = "final" if final else "canonical"
    return parsed.scheme == "https" and parsed.hostname in boundary[kind]


def successful_fetch_attempt(lineage: _Lineage) -> bool:
    metadata = (
        lineage.attempt.safe_metadata
        if isinstance(lineage.attempt.safe_metadata, Mapping)
        else {}
    )
    return (
        lineage.attempt.job_id == lineage.job.id
        and lineage.attempt.adapter_key == lineage.reference.adapter_key
        and lineage.attempt.operation == "fetch"
        and lineage.attempt.outcome == "succeeded"
        and lineage.attempt.finished_at is not None
        and metadata.get("source_reference_id") == str(lineage.reference.id)
    )


@dataclass(frozen=True, slots=True)
class GateResult:
    """One immutable, JSON-safe and credential-safe gate result."""

    name: str
    passed: bool
    reason_code: str
    facts: Mapping[str, Any]

    def __post_init__(self) -> None:
        name = _required_text(self.name, "gate name")
        if name not in _GATE_NAMES:
            raise ValueError("gate name is not recognized")
        reason_code = _required_text(self.reason_code, "reason_code")
        if not isinstance(self.passed, bool):
            raise ValueError("passed must be a bool")
        if not isinstance(self.facts, Mapping):
            raise ValueError("facts must be a mapping")
        copied = _thaw_json(self.facts)
        validate_persistable_json(copied, path=f"$.gate_results.{name}.facts")
        _validate_fact_text(copied, path=f"$.gate_results.{name}.facts")
        try:
            json.dumps(copied, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("gate facts must be JSON-safe") from exc
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "facts", _freeze_json(copied))

    def as_json(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason_code": self.reason_code,
            "facts": _thaw_json(self.facts),
        }


@dataclass(frozen=True, slots=True)
class AdmissionContext:
    """Explicit worker facts that must agree with the frozen job request."""

    job_id: uuid.UUID
    retrieval_artifact_id: uuid.UUID
    thesis_id: uuid.UUID
    cutoff: datetime
    objective: str
    target_link_role: str
    gate_version: str
    policy_version: str
    allowed_source_roles: frozenset[str]
    metric_terms: tuple[str, ...]
    lease_token: str
    expected_subject: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("job_id", "retrieval_artifact_id", "thesis_id"):
            if not isinstance(getattr(self, field_name), uuid.UUID):
                raise ValueError(f"{field_name} must be a UUID")
        object.__setattr__(self, "cutoff", _require_aware(self.cutoff, "cutoff"))
        for field_name in (
            "objective",
            "target_link_role",
            "gate_version",
            "policy_version",
            "lease_token",
        ):
            object.__setattr__(
                self, field_name, _required_text(getattr(self, field_name), field_name)
            )
        roles = frozenset(
            _required_text_tuple(self.allowed_source_roles, "allowed_source_roles")
        )
        metrics = _required_text_tuple(self.metric_terms, "metric_terms")
        object.__setattr__(self, "allowed_source_roles", roles)
        object.__setattr__(self, "metric_terms", metrics)
        if self.expected_subject is not None:
            object.__setattr__(
                self,
                "expected_subject",
                _required_text(self.expected_subject, "expected_subject"),
            )
        if self.gate_version != B_SCOPE_GATE_VERSION:
            raise ValueError("gate_version does not match the active B-scope gates")
        if self.policy_version != B_SCOPE_POLICY_VERSION:
            raise ValueError("policy_version does not match the active B-scope policy")
        if not roles <= B_SCOPE_POLICY.allowed_source_roles:
            raise ValueError("allowed_source_roles exceed the active B-scope policy")


@dataclass(frozen=True, slots=True)
class _Lineage:
    job: AcquisitionJob
    candidate: AtomicClaimCandidate
    reference: SourceReference
    attempt: AcquisitionAttempt
    artifact: RetrievalArtifact
    document: DocumentVersion
    span: SourceSpan
    binding: RetrievalArtifactDocument
    thesis: Thesis
    contract: SourceContract | None
    ai_run: AIRun
    case_attached: bool


class AutomaticAdmissionGate:
    """Evaluate every governed gate and append one immutable decision."""

    def __init__(
        self,
        session: Session,
        *,
        clock=_utcnow,
        pdf_parser: PdfParserAdapter | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._pdf_parser = pdf_parser or PypdfAdapter()

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime):
            raise ValueError("automatic-admission clock must return datetime")
        return _require_aware(value, "automatic-admission clock")

    def _require_lease(
        self, context: AdmissionContext, *, lock: bool = True
    ) -> AcquisitionJob:
        now = self._now()
        query = select(AcquisitionJob).where(
            AcquisitionJob.id == context.job_id,
            AcquisitionJob.status == "running",
            AcquisitionJob.stage == "admitting",
            AcquisitionJob.lease_token == context.lease_token,
            AcquisitionJob.lease_expires_at.is_not(None),
            AcquisitionJob.lease_expires_at > now,
        )
        if lock:
            query = query.with_for_update()
        query = query.execution_options(populate_existing=True)
        job = self._session.scalar(query)
        if (
            job is None
            or job.status != "running"
            or job.stage != "admitting"
            or job.lease_token != context.lease_token
            or job.lease_expires_at is None
            or _as_utc(job.lease_expires_at) <= now
        ):
            raise StaleLeaseError("acquisition lease is stale")
        return job

    def _require_frozen_lease(
        self, context: AdmissionContext, expected_digest: str
    ) -> AcquisitionJob:
        job = self._require_lease(context)
        if frozen_request_digest(job) != expected_digest:
            raise ConflictError("automatic-admission request changed during evaluation")
        return job

    def _write_fence(
        self, context: AdmissionContext, expected_digest: str
    ) -> AcquisitionJob:
        lease_write_fence(
            self._session,
            job_id=context.job_id,
            lease_token=context.lease_token,
            now=self._now(),
        )
        job = self._session.scalar(
            select(AcquisitionJob)
            .where(AcquisitionJob.id == context.job_id)
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise StaleLeaseError("acquisition lease is stale")
        if frozen_request_digest(job) != expected_digest:
            raise ConflictError("automatic-admission request changed during evaluation")
        return job

    def _load_lineage(
        self,
        candidate_or_id: AtomicClaimCandidate | uuid.UUID,
        context: AdmissionContext,
        job: AcquisitionJob,
    ) -> _Lineage:
        candidate_id = (
            candidate_or_id.id
            if isinstance(candidate_or_id, AtomicClaimCandidate)
            else candidate_or_id
        )
        if not isinstance(candidate_id, uuid.UUID):
            raise ValidationError("automatic-admission candidate is invalid")
        candidate = self._session.get(AtomicClaimCandidate, candidate_id)
        artifact = self._session.get(RetrievalArtifact, context.retrieval_artifact_id)
        thesis = self._session.get(Thesis, context.thesis_id)
        if candidate is None or artifact is None or thesis is None:
            raise ValidationError("automatic-admission lineage is incomplete")
        reference = self._session.get(SourceReference, artifact.source_reference_id)
        attempt = self._session.get(AcquisitionAttempt, artifact.attempt_id)
        binding = self._session.scalar(
            select(RetrievalArtifactDocument).where(
                RetrievalArtifactDocument.retrieval_artifact_id == artifact.id
            )
        )
        span = self._session.get(SourceSpan, candidate.source_span_id)
        document = (
            self._session.get(DocumentVersion, binding.document_version_id)
            if binding is not None
            else None
        )
        snapshot = (
            job.request_snapshot if isinstance(job.request_snapshot, dict) else {}
        )
        if (
            reference is None
            or attempt is None
            or binding is None
            or span is None
            or document is None
            or reference.job_id != job.id
            or attempt.job_id != job.id
            or span.document_version_id != document.id
            or thesis.id != job.thesis_id
            or thesis.research_case_id != job.research_case_id
            or snapshot.get("case_id") != str(job.research_case_id)
            or snapshot.get("thesis_id") != str(job.thesis_id)
        ):
            raise ValidationError("automatic-admission lineage mismatch")
        case_attached = (
            self._session.scalar(
                select(CaseDocumentVersion.id).where(
                    CaseDocumentVersion.research_case_id == job.research_case_id,
                    CaseDocumentVersion.document_version_id == document.id,
                )
            )
            is not None
        )
        contract = self._session.scalar(
            select(SourceContract).where(
                SourceContract.document_version_id == document.id
            )
        )
        ai_run = trusted_extraction_run(self._session, candidate, document, span)
        worker_replay_identity(job)
        return _Lineage(
            job=job,
            candidate=candidate,
            reference=reference,
            attempt=attempt,
            artifact=artifact,
            document=document,
            span=span,
            binding=binding,
            thesis=thesis,
            contract=contract,
            ai_run=ai_run,
            case_attached=case_attached,
        )

    @staticmethod
    def _result(name: str, failures: list[str], facts: dict[str, Any]) -> GateResult:
        return GateResult(
            name=name,
            passed=not failures,
            reason_code=failures[0] if failures else "passed",
            facts={**facts, "failures": failures},
        )

    def _source_gate(
        self,
        lineage: _Lineage,
        context: AdmissionContext,
        *,
        evaluation_at: datetime,
    ) -> GateResult:
        snapshot = lineage.job.request_snapshot
        snapshot_roles = tuple(snapshot.get("allowed_source_roles") or ())
        request_policy_version = snapshot.get("source_policy_version")
        policy_snapshot_version = (
            lineage.job.policy_snapshot.get("version")
            if isinstance(lineage.job.policy_snapshot, dict)
            else None
        )
        policy_adapters = tuple(
            lineage.job.policy_snapshot.get("enabled_adapter_keys") or ()
            if isinstance(lineage.job.policy_snapshot, dict)
            else ()
        )
        failures: list[str] = []
        role = lineage.reference.source_role
        adapter_key = lineage.reference.adapter_key
        mapped_identity = ADAPTER_SOURCE_IDENTITY.get(adapter_key)
        if (
            role not in snapshot_roles
            or role not in context.allowed_source_roles
            or role not in B_SCOPE_POLICY.allowed_source_roles
        ):
            failures.append("source_role_not_allowed")
        if set(snapshot_roles) != set(context.allowed_source_roles):
            failures.append("source_allowlist_context_mismatch")
        if (
            request_policy_version != context.policy_version
            or policy_snapshot_version != context.policy_version
        ):
            failures.append("source_policy_context_mismatch")
        if (
            adapter_key not in B_SCOPE_POLICY.enabled_adapter_keys
            or adapter_key not in policy_adapters
        ):
            failures.append("source_adapter_not_enabled")
        provider_identity = (
            lineage.reference.metadata_json.get("provider_identity")
            if isinstance(lineage.reference.metadata_json, dict)
            else None
        )
        if not isinstance(provider_identity, str) or not provider_identity.strip():
            failures.append("source_provider_identity_missing")
        if mapped_identity is None:
            failures.append("source_adapter_identity_unknown")
        else:
            mapped_role, mapped_provider, mapped_authority = mapped_identity
            if role != mapped_role:
                failures.append("source_adapter_role_mismatch")
            if provider_identity != mapped_provider:
                failures.append("source_provider_identity_mismatch")
        if not adapter_url_is_authorized(
            adapter_key, lineage.reference.canonical_url, final=False
        ):
            failures.append("source_canonical_url_not_authorized")
        if not adapter_url_is_authorized(
            adapter_key, lineage.artifact.final_url, final=True
        ):
            failures.append("source_final_url_not_authorized")
        if not successful_fetch_attempt(lineage):
            failures.append("source_fetch_attempt_lineage_mismatch")
        contract = lineage.contract
        if contract is None:
            failures.append("source_contract_missing")
        else:
            if not source_contract_is_active(contract, at=evaluation_at):
                failures.append("source_contract_inactive")
            if not contract.allow_ai_processing or not contract.allow_display:
                failures.append("source_contract_permissions_denied")
            expected_role = mapped_identity[0] if mapped_identity is not None else None
            if (
                contract.source_type != expected_role
                or contract.research_source_type != expected_role
            ):
                failures.append("source_contract_type_mismatch")
        expected_authority = (
            mapped_identity[2]
            if mapped_identity is not None
            else _AUTHORITY_BY_SOURCE_ROLE.get(role)
        )
        if (
            expected_authority is None
            or lineage.document.source_authority != expected_authority
            or lineage.candidate.authority_level != expected_authority
        ):
            failures.append("source_authority_mismatch")
        if lineage.document.source_url != lineage.reference.canonical_url:
            failures.append("source_document_url_mismatch")
        if not lineage.case_attached:
            failures.append("source_case_attachment_missing")
        return self._result(
            "source",
            failures,
            {
                "adapter_key": adapter_key,
                "adapter_enabled": adapter_key in policy_adapters,
                "active_policy_adapter_enabled": (
                    adapter_key in B_SCOPE_POLICY.enabled_adapter_keys
                ),
                "provider_identity_present": isinstance(provider_identity, str)
                and bool(provider_identity.strip()),
                "provider_identity_matches_adapter": bool(
                    mapped_identity is not None
                    and provider_identity == mapped_identity[1]
                ),
                "canonical_url_authorized": adapter_url_is_authorized(
                    adapter_key, lineage.reference.canonical_url, final=False
                ),
                "final_url_authorized": adapter_url_is_authorized(
                    adapter_key, lineage.artifact.final_url, final=True
                ),
                "successful_fetch_attempt": successful_fetch_attempt(lineage),
                "source_role": role,
                "allowed_source_roles": sorted(context.allowed_source_roles),
                "request_policy_version": request_policy_version,
                "policy_snapshot_version": policy_snapshot_version,
                "contract_present": contract is not None,
                "contract_source_type": (
                    contract.source_type if contract is not None else None
                ),
                "contract_research_source_type": (
                    contract.research_source_type if contract is not None else None
                ),
                "contract_active": (
                    source_contract_is_active(contract, at=evaluation_at)
                    if contract is not None
                    else False
                ),
                "allow_ai_processing": bool(contract and contract.allow_ai_processing),
                "allow_display": bool(contract and contract.allow_display),
                "case_attached": lineage.case_attached,
                "document_authority": lineage.document.source_authority,
                "document_url_matches_reference": (
                    lineage.document.source_url == lineage.reference.canonical_url
                ),
            },
        )

    @staticmethod
    def _snapshot_cutoff(snapshot: dict[str, Any]) -> datetime | None:
        raw = snapshot.get("cutoff")
        if not isinstance(raw, str) or not raw.strip():
            return None
        try:
            parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(UTC)

    def _temporal_gate(
        self,
        lineage: _Lineage,
        context: AdmissionContext,
        *,
        evaluation_at: datetime,
    ) -> GateResult:
        snapshot_cutoff = self._snapshot_cutoff(lineage.job.request_snapshot)
        values = {
            "reference_published_at": lineage.reference.published_at,
            "reference_created_at": lineage.reference.created_at,
            "attempt_started_at": lineage.attempt.started_at,
            "attempt_finished_at": lineage.attempt.finished_at,
            "published_at": lineage.document.published_at,
            "available_at": lineage.document.available_at,
            "acquired_at": lineage.document.acquired_at,
            "retrieved_at": lineage.artifact.retrieved_at,
        }
        failures = automatic_temporal_failures(
            lineage.reference,
            lineage.attempt,
            lineage.artifact,
            lineage.document,
            cutoff=context.cutoff,
            evaluation_at=evaluation_at,
        )
        if lineage.binding.relation == "content_duplicate":
            # The immutable document predates this later retrieval of the
            # exact same bytes. Requiring the reused document's acquired_at
            # to follow every duplicate fetch reverses valid chronology.
            failures = [
                failure
                for failure in failures
                if failure != "temporal_retrieved_at_after_acquired_at"
            ]
            if (
                lineage.document.acquired_at is not None
                and lineage.artifact.retrieved_at is not None
                and _as_utc(lineage.document.acquired_at)
                > _as_utc(lineage.artifact.retrieved_at)
            ):
                failures.append("temporal_duplicate_acquired_after_retrieved_at")
        if snapshot_cutoff is None:
            failures.append("temporal_snapshot_cutoff_missing")
        elif snapshot_cutoff != context.cutoff:
            failures.append("temporal_cutoff_context_mismatch")
        return self._result(
            "temporal",
            failures,
            {
                "cutoff": context.cutoff.isoformat(),
                "snapshot_cutoff": (
                    snapshot_cutoff.isoformat() if snapshot_cutoff else None
                ),
                "binding_relation": lineage.binding.relation,
                **{
                    name: _as_utc(value).isoformat() if value is not None else None
                    for name, value in values.items()
                },
            },
        )

    @staticmethod
    def _locator_identity(locator: SourceLocatorV1) -> str:
        stored = locator.to_storage_dict()
        identity = {
            key: stored.get(key)
            for key in (
                "schema",
                "document_sha256",
                "page",
                "parser_version",
                "text_position",
                "bbox",
                "parser_item_ref",
                "table_row",
                "table_col",
            )
            if key in stored
        }
        if locator.parser_version.startswith("pypdf-"):
            identity["__upgraded"] = locator.extra.get("__upgraded")
        return json.dumps(identity, sort_keys=True, separators=(",", ":"))

    def _replay_pdf_span(
        self, lineage: _Lineage, locator: SourceLocatorV1
    ) -> ParsedSpan:
        wanted_identity = self._locator_identity(locator)
        replayed = self._pdf_parser.extract_spans(
            lineage.artifact.raw_bytes,
            document_sha256=lineage.artifact.content_sha256,
        )
        for parsed in replayed:
            if self._locator_identity(parsed.locator) != wanted_identity:
                continue
            actual_hash = compute_text_sha256(parsed.verbatim_text)
            if (
                parsed.verbatim_text == lineage.span.verbatim_text
                and parsed.text_sha256 == actual_hash == lineage.span.text_sha256
                and parsed.context_hash == lineage.span.context_hash
            ):
                return parsed
        raise ValueError("persisted PDF span was not reproduced by parser replay")

    def _re_extract(self, lineage: _Lineage, locator: SourceLocatorV1) -> str:
        if lineage.artifact.mime_type.startswith("text/"):
            text = lineage.artifact.raw_bytes.decode("utf-8")
            if locator.text_position is None:
                raise ValueError("text artifact locator lacks a text position")
            return text[locator.text_position.start : locator.text_position.end]
        if lineage.artifact.mime_type != "application/pdf":
            raise ValueError("unsupported artifact type for locator replay")
        return self._replay_pdf_span(lineage, locator).verbatim_text

    def _locator_gate(
        self, lineage: _Lineage, _context: AdmissionContext
    ) -> GateResult:
        failures: list[str] = []
        actual_artifact_sha = hashlib.sha256(lineage.artifact.raw_bytes).hexdigest()
        if actual_artifact_sha != lineage.artifact.content_sha256:
            failures.append("locator_artifact_hash_mismatch")
        if lineage.document.content_sha256 != lineage.artifact.content_sha256:
            failures.append("locator_document_hash_mismatch")
        actual_text_sha = compute_text_sha256(lineage.span.verbatim_text)
        if not lineage.span.text_sha256 or lineage.span.text_sha256 != actual_text_sha:
            failures.append("locator_span_hash_mismatch")
        candidate = lineage.candidate
        quote_bounds_valid = (
            candidate.quote_start >= 0
            and candidate.quote_end > candidate.quote_start
            and candidate.quote_end <= len(lineage.span.verbatim_text)
        )
        actual_slice = (
            lineage.span.verbatim_text[candidate.quote_start : candidate.quote_end]
            if quote_bounds_valid
            else ""
        )
        actual_quote_sha = hashlib.sha256(candidate.quote.encode("utf-8")).hexdigest()
        if not quote_bounds_valid or actual_slice != candidate.quote:
            failures.append("locator_quote_slice_mismatch")
        if candidate.quote_sha256 != actual_quote_sha:
            failures.append("locator_quote_hash_mismatch")
        locator: SourceLocatorV1 | None = None
        try:
            locator_payload = dict(lineage.span.locator_v1 or {})
            typed_keys = {
                "schema",
                "document_sha256",
                "page",
                "parser_version",
                "text_position",
                "text_quote",
                "bbox",
                "parser_item_ref",
                "table_row",
                "table_col",
                "extra",
            }
            flattened_extra = {
                key: value
                for key, value in locator_payload.items()
                if key not in typed_keys
            }
            if flattened_extra:
                nested_extra = dict(locator_payload.get("extra") or {})
                nested_extra.update(flattened_extra)
                locator_payload = {
                    key: value
                    for key, value in locator_payload.items()
                    if key in typed_keys
                }
                locator_payload["extra"] = nested_extra
            locator = validate_locator_v1(locator_payload)
        except LocatorInvalidError:
            failures.append("locator_v1_invalid")
        round_trip = False
        if locator is not None:
            if locator.document_sha256 != lineage.document.content_sha256:
                failures.append("locator_document_locator_hash_mismatch")
            if locator.parser_version != lineage.document.parser_version:
                failures.append("locator_parser_version_mismatch")
            if (
                locator.text_quote is not None
                and locator.text_quote.exact != lineage.span.verbatim_text
            ):
                failures.append("locator_exact_quote_mismatch")
            try:
                replayed_text = self._re_extract(lineage, locator)
                if locator.parser_version.startswith("pypdf-"):
                    round_trip = replayed_text == lineage.span.verbatim_text
                else:
                    round_trip = round_trip_check(
                        locator,
                        lineage.span.verbatim_text,
                        re_extract=lambda _value: replayed_text,
                    )
            except Exception:
                round_trip = False
            if not round_trip:
                failures.append("locator_round_trip_failed")
        return self._result(
            "locator",
            failures,
            {
                "artifact_sha256": lineage.artifact.content_sha256,
                "document_sha256": lineage.document.content_sha256,
                "span_text_sha256": lineage.span.text_sha256,
                "actual_span_text_sha256": actual_text_sha,
                "quote_sha256": candidate.quote_sha256,
                "actual_quote_sha256": actual_quote_sha,
                "quote_start": candidate.quote_start,
                "quote_end": candidate.quote_end,
                "round_trip": round_trip,
                "locator_schema": locator.schema if locator is not None else None,
                "parser_version": lineage.document.parser_version,
            },
        )

    @staticmethod
    def _normalize_metric(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return re.sub(
            r"[^0-9a-z\u3400-\u9fff]+",
            " ",
            unicodedata.normalize("NFKC", value).casefold(),
        ).strip()

    @classmethod
    def _metric_matches(cls, candidate_metric: object, terms: tuple[str, ...]) -> bool:
        normalized_candidate = cls._normalize_metric(candidate_metric)
        if not normalized_candidate:
            return False
        return any(
            normalized_candidate == cls._normalize_metric(term) for term in terms
        )

    @staticmethod
    def _normalize_grounding(value: object) -> str:
        if not isinstance(value, str):
            return ""
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())

    @staticmethod
    def _parse_decimal(value: object) -> Decimal | None:
        if isinstance(value, bool):
            return None
        raw = str(value).strip() if isinstance(value, (str, int)) else ""
        if not _NUMERIC_VALUE_RE.fullmatch(raw):
            return None
        try:
            parsed = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None

    @classmethod
    def _numeric_unit_grounded(
        cls, text: object, numeric: Decimal, unit: object
    ) -> bool:
        normalized_text = cls._normalize_grounding(text)
        normalized_unit = cls._normalize_grounding(unit)
        if not normalized_text or not normalized_unit:
            return False
        for match in _NUMERIC_TOKEN_RE.finditer(normalized_text):
            try:
                token_value = Decimal(match.group(1).replace(",", ""))
            except InvalidOperation:
                continue
            if token_value != numeric:
                continue
            after = normalized_text[match.end() :]
            before = normalized_text[: match.start()]
            after_separator = _UNIT_SEPARATOR_RE.match(after)
            if after_separator and after[after_separator.end() :].startswith(
                normalized_unit
            ):
                return True
            reversed_before = before[::-1]
            before_separator = _UNIT_SEPARATOR_RE.match(reversed_before)
            if before_separator and before[
                : len(before) - before_separator.end()
            ].endswith(normalized_unit):
                return True
        return False

    @classmethod
    def _metric_anchors(
        cls, text: str, metric_terms: tuple[str, ...]
    ) -> tuple[tuple[int, int, str], ...]:
        normalized_terms = sorted(
            {
                normalized
                for term in metric_terms
                if (normalized := cls._normalize_grounding(term))
            },
            key=lambda term: (-len(term), term),
        )
        matches: list[tuple[int, int, str]] = []
        for term in normalized_terms:
            boundary_start = r"(?<![0-9a-z])" if term[0].isascii() else ""
            boundary_end = r"(?![0-9a-z])" if term[-1].isascii() else ""
            pattern = re.compile(
                f"{boundary_start}{re.escape(term)}{boundary_end}", re.IGNORECASE
            )
            matches.extend(
                (match.start(), match.end(), term) for match in pattern.finditer(text)
            )
        selected: list[tuple[int, int, str]] = []
        for candidate in sorted(matches, key=lambda item: (item[0], -item[1])):
            if any(
                candidate[0] < existing[1] and existing[0] < candidate[1]
                for existing in selected
            ):
                continue
            selected.append(candidate)
        return tuple(sorted(selected))

    @classmethod
    def _metric_value_grounded(
        cls,
        text: object,
        *,
        predicate: object,
        metric_terms: tuple[str, ...],
        numeric: Decimal,
        unit: object,
    ) -> bool:
        normalized_text = cls._normalize_grounding(text)
        normalized_predicate = cls._normalize_grounding(predicate)
        normalized_unit = cls._normalize_grounding(unit)
        if not normalized_text or not normalized_predicate or not normalized_unit:
            return False
        anchors = cls._metric_anchors(
            normalized_text, (*metric_terms, normalized_predicate)
        )
        for index, (start, end, term) in enumerate(anchors):
            if term != normalized_predicate:
                continue
            boundary = anchors[index + 1][0] if index + 1 < len(anchors) else len(
                normalized_text
            )
            window = normalized_text[end:boundary]
            first_numeric = _NUMERIC_TOKEN_RE.search(window)
            if first_numeric is None:
                continue
            connector = window[: first_numeric.start()]
            reversed_connector = connector[::-1]
            before_separator = _UNIT_SEPARATOR_RE.match(reversed_connector)
            unit_prefixed = False
            if before_separator:
                before_unit = connector[: len(connector) - before_separator.end()]
                if before_unit.endswith(normalized_unit):
                    unit_prefixed = bool(
                        _METRIC_VALUE_CONNECTOR_RE.fullmatch(
                            before_unit[: -len(normalized_unit)]
                        )
                    )
            if not unit_prefixed and not _METRIC_VALUE_CONNECTOR_RE.fullmatch(
                connector
            ):
                continue
            try:
                token_value = Decimal(first_numeric.group(1).replace(",", ""))
            except InvalidOperation:
                continue
            if token_value != numeric:
                continue
            after = window[first_numeric.end() :]
            after_separator = _UNIT_SEPARATOR_RE.match(after)
            if after_separator and after[after_separator.end() :].startswith(
                normalized_unit
            ):
                return True
            if unit_prefixed:
                return True
        return False

    @classmethod
    def _period_grounded(
        cls,
        period: date,
        grain: str,
        text: object,
    ) -> bool:
        haystack = cls._normalize_grounding(text)
        year = period.year
        month = period.month
        day = period.day
        month_name = (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        )[month - 1]
        search_text = haystack
        if grain == "year":
            english_months = (
                r"january|february|march|april|may|june|july|august|"
                r"september|october|november|december"
            )
            search_text = re.sub(
                rf"\b(?:{english_months})\s+\d{{1,2}},?\s+{year}\b",
                "",
                haystack,
                flags=re.IGNORECASE,
            )
            patterns = (
                rf"(?<!\d){year}\s*年(?!\s*\d{{1,2}}\s*月)",
                rf"(?<![\d-]){year}(?!\s*[-/]\s*\d{{1,2}})(?!\s*年)(?!\d)",
            )
        elif grain == "month":
            patterns = (
                rf"(?<!\d){year}-{month:02d}(?!-\d{{1,2}})(?!\d)",
                rf"(?<!\d){year}/{month:02d}(?!/\d{{1,2}})(?!\d)",
                rf"(?<!\d){year}\s*年\s*0?{month}\s*月(?!\s*\d{{1,2}}\s*日)",
                rf"\b{month_name}\s+{year}\b",
            )
        else:
            patterns = (
                rf"(?<!\d){year}-{month:02d}-{day:02d}(?!\d)",
                rf"(?<!\d){year}/{month:02d}/{day:02d}(?!\d)",
                rf"(?<!\d){year}\s*年\s*0?{month}\s*月\s*0?{day}\s*日",
                rf"\b{month_name}\s+0?{day},?\s+{year}\b",
            )
        return any(
            re.search(pattern, search_text, re.IGNORECASE) for pattern in patterns
        )

    @classmethod
    def _segments(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, str):
            return ()
        return tuple(
            segment
            for raw in _SEGMENT_BOUNDARY_RE.split(value)
            if (segment := cls._normalize_grounding(raw))
        )

    @classmethod
    def _polarity(cls, value: object) -> tuple[bool, str] | None:
        normalized = cls._normalize_grounding(value)
        if not normalized:
            return None
        negated = any(marker in normalized for marker in _NEGATION_MARKERS)
        directions = {
            direction
            for direction, markers in _DIRECTION_MARKERS.items()
            if any(marker in normalized for marker in markers)
        }
        if len(directions) > 1:
            return None
        return negated, next(iter(directions), "neutral")

    @classmethod
    def _segment_supports_claim(
        cls,
        segment: str,
        *,
        subject: object,
        predicate: object,
        numeric: Decimal | None,
        unit: object,
        period: date,
        period_grain: str,
        normalized_claim: object,
        metric_terms: tuple[str, ...],
    ) -> bool:
        normalized_subject = cls._normalize_grounding(subject)
        normalized_predicate = cls._normalize_grounding(predicate)
        if (
            not normalized_subject
            or normalized_subject not in segment
            or not normalized_predicate
            or normalized_predicate not in segment
            or not cls._period_grounded(period, period_grain, segment)
        ):
            return False
        if numeric is not None and not cls._metric_value_grounded(
            segment,
            predicate=predicate,
            metric_terms=metric_terms,
            numeric=numeric,
            unit=unit,
        ):
            return False
        source_polarity = cls._polarity(segment)
        claim_polarity = cls._polarity(normalized_claim)
        return source_polarity is not None and source_polarity == claim_polarity

    def _semantic_gate(
        self, lineage: _Lineage, context: AdmissionContext
    ) -> GateResult:
        snapshot = lineage.job.request_snapshot
        fields = (
            lineage.candidate.structured_fields
            if isinstance(lineage.candidate.structured_fields, dict)
            else {}
        )
        subject = fields.get("subject")
        predicate = fields.get("metric", fields.get("predicate"))
        period = fields.get("observed_period")
        numeric_value = fields.get("numeric_value")
        unit = fields.get("unit")
        scope = fields.get("scope") if isinstance(fields.get("scope"), dict) else {}
        table_context = (
            scope.get("extraction_method") == "financial_table_v1"
            and lineage.candidate.authority_level == "primary_disclosure"
        )
        parsed_period: str | None = None
        parsed_date: date | None = None
        period_grain: str | None = None
        period_valid = False
        if isinstance(period, str) and period.strip():
            raw_period = period.strip()
            year_match = _YEAR_RE.fullmatch(raw_period)
            if year_match is not None:
                parsed_date = date(int(year_match.group(1)), 1, 1)
                parsed_period = raw_period
                period_grain = "year"
                period_valid = True
            elif (month_match := _MONTH_RE.fullmatch(raw_period)) is not None:
                parsed_date = date(
                    int(month_match.group(1)), int(month_match.group(2)), 1
                )
                parsed_period = raw_period
                period_grain = "month"
                period_valid = True
            else:
                try:
                    parsed_date = date.fromisoformat(raw_period)
                    parsed_period = parsed_date.isoformat()
                    period_grain = "day"
                    period_valid = True
                except ValueError:
                    pass
        snapshot_metrics = tuple(snapshot.get("metric_terms") or ())
        snapshot_entities = tuple(snapshot.get("entity_names") or ())
        quote_text = self._normalize_grounding(lineage.candidate.quote)
        normalized_claim_text = self._normalize_grounding(
            lineage.candidate.normalized_text
        )
        normalized_subject = self._normalize_grounding(subject)
        normalized_predicate = self._normalize_grounding(predicate)
        normalized_span = self._normalize_grounding(lineage.span.verbatim_text)
        failures: list[str] = []
        if not isinstance(subject, str) or not subject.strip():
            failures.append("semantic_subject_missing")
        elif self._normalize_metric(subject) not in {
            self._normalize_metric(entity) for entity in snapshot_entities
        }:
            failures.append("semantic_subject_mismatch")
        elif normalized_subject not in normalized_claim_text or (
            normalized_subject not in quote_text
            and not (table_context and normalized_subject in normalized_span)
        ):
            failures.append("semantic_subject_not_grounded")
        if not isinstance(period, str) or not period.strip():
            failures.append("semantic_period_missing")
        else:
            if parsed_date is None or period_grain is None:
                failures.append("semantic_period_invalid")
            else:
                try:
                    period_start = date.fromisoformat(str(snapshot.get("period_start")))
                    period_end = date.fromisoformat(str(snapshot.get("period_end")))
                except ValueError:
                    failures.append("semantic_request_period_invalid")
                else:
                    if period_grain == "year":
                        period_last = date(parsed_date.year, 12, 31)
                        in_window = (
                            period_start <= parsed_date and period_last <= period_end
                        )
                    elif period_grain == "month":
                        period_last = date(
                            parsed_date.year,
                            parsed_date.month,
                            monthrange(parsed_date.year, parsed_date.month)[1],
                        )
                        in_window = (
                            period_start <= parsed_date and period_last <= period_end
                        )
                    else:
                        in_window = period_start <= parsed_date <= period_end
                    if not in_window:
                        failures.append("semantic_period_outside_request")
                if not self._period_grounded(
                    parsed_date, period_grain, lineage.candidate.normalized_text
                ):
                    failures.append("semantic_period_not_grounded")
        if numeric_value is not None and (
            not isinstance(unit, str) or not unit.strip()
        ):
            failures.append("semantic_numeric_unit_missing")
        if not self._metric_matches(predicate, snapshot_metrics):
            failures.append("semantic_metric_mismatch")
        elif (
            normalized_predicate not in quote_text
            or normalized_predicate not in normalized_claim_text
        ):
            failures.append("semantic_metric_not_grounded")
        parsed_numeric = None
        if numeric_value is not None:
            parsed_numeric = self._parse_decimal(numeric_value)
            if parsed_numeric is None:
                failures.append("semantic_numeric_value_invalid")
            elif not self._metric_value_grounded(
                lineage.candidate.quote,
                predicate=predicate,
                metric_terms=snapshot_metrics,
                numeric=parsed_numeric,
                unit=unit,
            ):
                failures.append("semantic_numeric_quote_mismatch")
            elif not self._metric_value_grounded(
                lineage.candidate.normalized_text,
                predicate=predicate,
                metric_terms=snapshot_metrics,
                numeric=parsed_numeric,
                unit=unit,
            ):
                failures.append("semantic_normalized_text_numeric_mismatch")
        quote_segments = self._segments(lineage.candidate.quote)
        supporting_segments = ()
        if parsed_date is not None and period_grain is not None and table_context:
            table_period_grounded = self._period_grounded(
                parsed_date, period_grain, lineage.span.verbatim_text
            ) or (
                parsed_date.month == 6
                and parsed_date.day == 30
                and re.search(
                    rf"{parsed_date.year}\s*年\s*(?:半年度|上半年)",
                    lineage.span.verbatim_text,
                )
                is not None
            )
            source_polarity = self._polarity(quote_text)
            claim_polarity = self._polarity(normalized_claim_text)
            metric_value_grounded = parsed_numeric is None or self._metric_value_grounded(
                lineage.candidate.quote,
                predicate=predicate,
                metric_terms=snapshot_metrics,
                numeric=parsed_numeric,
                unit=unit,
            )
            if (
                normalized_subject in normalized_span
                and normalized_predicate in quote_text
                and normalized_predicate in normalized_claim_text
                and table_period_grounded
                and metric_value_grounded
                and source_polarity is not None
                and source_polarity == claim_polarity
            ):
                supporting_segments = (quote_text,)
        elif parsed_date is not None and period_grain is not None:
            supporting_segments = tuple(
                segment
                for segment in quote_segments
                if self._segment_supports_claim(
                    segment,
                    subject=subject,
                    predicate=predicate,
                    numeric=parsed_numeric,
                    unit=unit,
                    period=parsed_date,
                    period_grain=period_grain,
                    normalized_claim=lineage.candidate.normalized_text,
                    metric_terms=snapshot_metrics,
                )
            )
        if not supporting_segments:
            failures.append("semantic_single_segment_support_missing")
        if tuple(context.metric_terms) != snapshot_metrics:
            failures.append("semantic_metric_context_mismatch")
        if context.objective != snapshot.get("objective"):
            failures.append("semantic_objective_context_mismatch")
        if context.target_link_role != snapshot.get("target_link_role"):
            failures.append("semantic_link_role_context_mismatch")
        if snapshot.get("target_link_role") not in _OBJECTIVE_ROLES.get(
            snapshot.get("objective"), frozenset()
        ):
            failures.append("semantic_snapshot_role_invalid")
        if context.expected_subject is not None:
            if context.expected_subject not in snapshot_entities:
                failures.append("semantic_subject_context_mismatch")
        confidence = (
            lineage.candidate.validation_result.get("confidence")
            if isinstance(lineage.candidate.validation_result, dict)
            else None
        )
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
        ):
            confidence = None
        return self._result(
            "semantic",
            failures,
            {
                "subject_present": isinstance(subject, str) and bool(subject.strip()),
                "subject_sha256": (
                    hashlib.sha256(subject.strip().encode("utf-8")).hexdigest()
                    if isinstance(subject, str) and subject.strip()
                    else None
                ),
                "predicate_present": isinstance(predicate, str)
                and bool(predicate.strip()),
                "predicate_sha256": (
                    hashlib.sha256(predicate.strip().encode("utf-8")).hexdigest()
                    if isinstance(predicate, str) and predicate.strip()
                    else None
                ),
                "observed_period": parsed_period,
                "observed_period_grain": period_grain,
                "observed_period_valid": period_valid,
                "numeric_value_present": numeric_value is not None,
                "numeric_value_valid": (
                    numeric_value is None or parsed_numeric is not None
                ),
                "unit_present": isinstance(unit, str) and bool(unit.strip()),
                "metric_term_sha256": [
                    hashlib.sha256(term.encode("utf-8")).hexdigest()
                    for term in snapshot_metrics
                    if isinstance(term, str)
                ],
                "metric_term_count": len(snapshot_metrics),
                "objective": snapshot.get("objective"),
                "target_link_role": snapshot.get("target_link_role"),
                "confidence": confidence,
                "segment_count": len(quote_segments),
                "supporting_segment_count": len(supporting_segments),
                "supporting_segment_sha256": [
                    hashlib.sha256(segment.encode("utf-8")).hexdigest()
                    for segment in supporting_segments
                ],
            },
        )

    @staticmethod
    def _assert_same_decision(
        existing: AutomaticAdmissionDecision,
        *,
        context: AdmissionContext,
        outcome: str,
        gate_results: dict[str, Any],
    ) -> None:
        if (
            existing.retrieval_artifact_id != context.retrieval_artifact_id
            or existing.outcome != outcome
            or existing.gate_results != gate_results
        ):
            raise ConflictError(
                "automatic-admission context changed for an existing decision"
            )

    def _get_existing(
        self, candidate_id: uuid.UUID, context: AdmissionContext
    ) -> AutomaticAdmissionDecision | None:
        return self._session.scalar(
            select(AutomaticAdmissionDecision).where(
                AutomaticAdmissionDecision.job_id == context.job_id,
                AutomaticAdmissionDecision.candidate_id == candidate_id,
                AutomaticAdmissionDecision.gate_version == context.gate_version,
                AutomaticAdmissionDecision.policy_version == context.policy_version,
            )
        )

    def _ensure_exception(
        self,
        decision: AutomaticAdmissionDecision,
        lineage: _Lineage,
        gate_results: dict[str, Any],
        context: AdmissionContext,
        request_digest: str,
    ) -> AcquisitionException:
        existing = self._get_exception(decision, lineage)
        failed = [name for name in _GATE_NAMES if not gate_results[name]["passed"]]
        detail = {
            "decision_id": str(decision.id),
            "failed_gates": failed,
            "reason_codes": {
                name: gate_results[name]["reason_code"] for name in failed
            },
            "gate_version": decision.gate_version,
            "policy_version": decision.policy_version,
        }
        validate_persistable_json(detail, path="$.automatic_admission_exception")
        if existing is not None:
            if existing.detail_json != detail:
                existing_decision_id = (
                    existing.detail_json.get("decision_id")
                    if isinstance(existing.detail_json, dict)
                    else None
                )
                if existing_decision_id == str(decision.id):
                    raise ConflictError(
                        "automatic-admission exception differs from existing decision"
                    )
                # The exception row is a candidate-level quarantine marker and its
                # uniqueness key deliberately does not include the gate version.
                # A newer immutable decision therefore reuses the historical marker;
                # the version-specific gate facts remain on the new decision itself.
            return existing
        exception = AcquisitionException(
            job_id=decision.job_id,
            source_reference_id=lineage.reference.id,
            retrieval_artifact_id=decision.retrieval_artifact_id,
            candidate_id=decision.candidate_id,
            reason_code=_QUARANTINE_REASON,
            detail_json=detail,
            created_at=self._now(),
        )
        try:
            with self._session.begin_nested():
                self._session.add(exception)
                self._write_fence(context, request_digest)
                self._session.flush()
        except IntegrityError:
            winner = self._get_exception(decision, lineage)
            if (
                winner is None
                or winner.source_reference_id != lineage.reference.id
                or winner.detail_json != detail
            ):
                raise
            exception = winner
        return exception

    def _get_exception(
        self,
        decision: AutomaticAdmissionDecision,
        lineage: _Lineage,
    ) -> AcquisitionException | None:
        return self._session.scalar(
            select(AcquisitionException)
            .where(
                AcquisitionException.job_id == decision.job_id,
                AcquisitionException.source_reference_id == lineage.reference.id,
                AcquisitionException.retrieval_artifact_id
                == decision.retrieval_artifact_id,
                AcquisitionException.candidate_id == decision.candidate_id,
                AcquisitionException.reason_code == _QUARANTINE_REASON,
            )
            .order_by(AcquisitionException.created_at, AcquisitionException.id)
            .limit(1)
        )

    def evaluate(
        self,
        candidate: AtomicClaimCandidate | uuid.UUID,
        context: AdmissionContext,
    ) -> AutomaticAdmissionDecision:
        if not isinstance(context, AdmissionContext):
            raise TypeError("context must be an AdmissionContext")
        job = self._require_lease(context)
        lineage = self._load_lineage(candidate, context, job)
        evaluation_at = self._now()
        results = (
            self._source_gate(lineage, context, evaluation_at=evaluation_at),
            self._temporal_gate(lineage, context, evaluation_at=evaluation_at),
            self._locator_gate(lineage, context),
            self._semantic_gate(lineage, context),
        )
        digest = frozen_request_digest(job)
        admission_digest = canonical_admission_digest(
            job=job,
            candidate=lineage.candidate,
            reference=lineage.reference,
            attempt=lineage.attempt,
            artifact=lineage.artifact,
            document=lineage.document,
            span=lineage.span,
            binding=lineage.binding,
            contract=lineage.contract,
            ai_run=lineage.ai_run,
        )
        run_ref = lineage.candidate.structured_fields["run_ref"]
        normalizer_version = lineage.candidate.validation_result.get(
            "normalizer_version"
        )
        if not isinstance(normalizer_version, str) or not normalizer_version.strip():
            raise ValidationError("automatic-admission normalizer identity is invalid")
        replay_identity = {
            "extraction": {
                "airun_id": str(lineage.ai_run.id),
                "run_ref": run_ref,
                "model_version": lineage.ai_run.model_version,
                "prompt_version": lineage.ai_run.prompt_version,
            },
            "parser": parser_replay_identity(lineage.document.parser_version),
            "normalizer_version": normalizer_version,
            "worker": worker_replay_identity(job),
        }
        results = tuple(
            GateResult(
                result.name,
                result.passed,
                result.reason_code,
                {
                    **result.as_json()["facts"],
                    "frozen_request_digest": digest,
                    "admission_digest": admission_digest,
                    "replay_identity": replay_identity,
                },
            )
            for result in results
        )
        gate_results = {result.name: result.as_json() for result in results}
        outcome = (
            "admitted" if all(result.passed for result in results) else "quarantined"
        )
        self._write_fence(context, digest)
        existing = self._get_existing(lineage.candidate.id, context)
        if existing is not None:
            self._assert_same_decision(
                existing,
                context=context,
                outcome=outcome,
                gate_results=gate_results,
            )
            if outcome == "quarantined":
                self._ensure_exception(existing, lineage, gate_results, context, digest)
            return existing

        decision = AutomaticAdmissionDecision(
            job_id=job.id,
            candidate_id=lineage.candidate.id,
            retrieval_artifact_id=lineage.artifact.id,
            outcome=outcome,
            gate_version=context.gate_version,
            policy_version=context.policy_version,
            gate_results=gate_results,
            created_at=evaluation_at,
        )
        with self._session.begin_nested():
            try:
                with self._session.begin_nested():
                    self._session.add(decision)
                    self._write_fence(context, digest)
                    self._session.flush()
            except IntegrityError:
                existing = self._get_existing(lineage.candidate.id, context)
                if existing is None:
                    raise
                self._assert_same_decision(
                    existing,
                    context=context,
                    outcome=outcome,
                    gate_results=gate_results,
                )
                decision = existing
            if outcome == "quarantined":
                self._ensure_exception(decision, lineage, gate_results, context, digest)
        return decision
