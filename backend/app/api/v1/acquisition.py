"""Protected commands and safe reads for governed acquisition jobs."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, time
from typing import Annotated, cast
from urllib.parse import parse_qsl, unquote, urlsplit

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY
from app.api.v1.commands.common import commit_or_rollback
from app.api.v1.tenant_context import (
    ResearchActor,
    require_research_actor,
    require_research_tenant,
)
from app.db import get_db
from app.domain.acquisition import (
    ACQUISITION_PLANNER_VERSION,
    AcquisitionPrincipal,
    AcquisitionRequest,
    EvidenceObjective,
)
from app.models.event_research import EventResearchScopeVersion
from app.errors import NotFoundError, ValidationFailedError
from app.models.acquisition import (
    AcquisitionException,
    AcquisitionJob,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    Company,
    DocumentVersion,
    EvidenceLink,
    ResearchCase,
    SourceSpan,
    SourceStatement,
    Stock,
    Thesis,
)
from app.models.research_protocol import (
    MechanismEdgeVersion,
    MetricDefinitionVersion,
    VerificationRuleVersion,
)
from app.models.operational import ResearchRun
from app.models.research_orchestration import ResearchOrchestration
from app.repositories.research_protocol import ResearchProtocolRepository
from app.schemas.v1.acquisition import (
    AcquisitionGateName,
    AcquisitionJobAcceptedDTO,
    AcquisitionCountersDTO,
    AcquisitionJobCreateRequest,
    AcquisitionJobDetailDTO,
    AcquisitionJobEventDTO,
    AcquisitionJobEventPayloadDTO,
    AcquisitionErrorSummaryDTO,
    AcquisitionEvidenceDTO,
    AcquisitionExceptionDTO,
    AcquisitionExceptionDetailDTO,
    AcquisitionGateResultDTO,
    AcquisitionStage,
    AcquisitionStatus,
)
from app.services.acquisition import AcquisitionModule
from app.services.case_tenant_access import CaseTenantAccess
from app.api.v1.dependencies import RequireCaseRoute


router = APIRouter(
    tags=["acquisition-v1"],
    dependencies=[Depends(require_research_tenant)],
)


_OBJECTIVE_ROLES = {
    EvidenceObjective.SUPPORT: "supports",
    EvidenceObjective.CONTRADICT: "contradicts",
    EvidenceObjective.ALTERNATIVE_EXPLANATION: "contextualizes",
}

_ACQUISITION_STATUSES = frozenset(
    {"queued", "running", "retry_wait", "succeeded", "partial", "failed", "cancelled"}
)
_ACQUISITION_STAGES = frozenset(
    {
        "queued",
        "searching",
        "fetching",
        "freezing",
        "extracting",
        "admitting",
        "succeeded",
        "partial",
        "failed",
        "cancelled",
    }
)
_GATE_NAMES: tuple[AcquisitionGateName, ...] = (
    "source",
    "temporal",
    "locator",
    "semantic",
)
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CONTROL_TEXT = re.compile(r"[\x00-\x1f\x7f]")
_CREDENTIAL_TEXT = re.compile(
    r"(?:traceback|authorization\s*:|bearer\s+|"
    r"(?:token|api[_-]?key|password|secret|"
    r"credential)\s*[=:])",
    re.IGNORECASE,
)
_CREDENTIAL_QUERY_MARKERS = (
    "token",
    "authorization",
    "credential",
    "secret",
    "password",
    "apikey",
    "signature",
)
_CREDENTIAL_QUERY_EXACT = frozenset({"auth", "key", "sig"})


def safe_public_text(
    value: object,
    *,
    max_length: int = 256,
    fallback: str = "redacted",
) -> str:
    """Return bounded display text or a fixed non-sensitive replacement."""
    if not isinstance(value, str):
        return fallback
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > max_length
        or _CONTROL_TEXT.search(normalized)
        or _CREDENTIAL_TEXT.search(normalized)
    ):
        return fallback
    return normalized


def _safe_public_token(value: object) -> str:
    normalized = safe_public_text(value, max_length=128)
    return normalized if _SAFE_TOKEN.fullmatch(normalized) else "redacted"


def _safe_public_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = safe_public_text(value, max_length=2048)
    if (
        normalized == "redacted"
        or any(character.isspace() for character in normalized)
        or safe_public_text(unquote(normalized), max_length=2048) == "redacted"
    ):
        return None
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    for key, child in query:
        query_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if query_key in _CREDENTIAL_QUERY_EXACT or any(
            marker in query_key for marker in _CREDENTIAL_QUERY_MARKERS
        ):
            return None
        if safe_public_text(key, max_length=128) == "redacted":
            return None
        if child and safe_public_text(child, max_length=512) == "redacted":
            return None
    return normalized


def _safe_uuid(value: object) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _safe_status(value: object) -> AcquisitionStatus:
    return cast(
        AcquisitionStatus,
        value if isinstance(value, str) and value in _ACQUISITION_STATUSES else "failed",
    )


def _safe_stage(value: object) -> AcquisitionStage:
    return cast(
        AcquisitionStage,
        value if isinstance(value, str) and value in _ACQUISITION_STAGES else "failed",
    )


def _security_code(value: str) -> str:
    """Freeze the six-digit issuer code used by exchange adapters."""
    match = re.fullmatch(r"(\d{6})(?:\.(?:SH|SZ))?", value.strip(), re.IGNORECASE)
    if match is None:
        raise ValidationFailedError("acquisition scope has no valid security code")
    return match.group(1)


def _scope_request(
    db: Session,
    *,
    case_id: uuid.UUID,
    thesis_id: uuid.UUID,
    payload: AcquisitionJobCreateRequest,
    tenant_id: str,
    idempotency_key: str,
) -> AcquisitionRequest:
    """Resolve all mutable-looking acquisition inputs from persisted records."""
    CaseTenantAccess(db).require_case(case_id, tenant_id)
    research_case = db.get(ResearchCase, case_id)
    thesis = db.get(Thesis, thesis_id)
    if (
        research_case is None
        or thesis is None
        or thesis.research_case_id != research_case.id
    ):
        raise NotFoundError("thesis not found")

    protocol = ResearchProtocolRepository(db)
    binding = protocol.effective_binding(thesis.id)
    if binding is None or binding.state != "approved":
        raise ValidationFailedError("acquisition scope has no approved outcome binding")
    verification_rule: VerificationRuleVersion | None = None
    if payload.objective is EvidenceObjective.VERIFY_RULE:
        selection = protocol.effective_case_template(case_id)
        edges = (
            tuple(
                db.scalars(
                    select(MechanismEdgeVersion)
                    .where(
                        MechanismEdgeVersion.template_version_id
                        == selection.template_version_id
                    )
                    .order_by(
                        MechanismEdgeVersion.edge_key,
                        MechanismEdgeVersion.id,
                    )
                )
            )
            if selection is not None
            else ()
        )
        effective_rules = tuple(
            rule
            for edge in edges
            if (rule := protocol.effective_rule(case_id, edge.id)) is not None
        )
        compatible_rules: list[VerificationRuleVersion] = []
        for rule in effective_rules:
            if rule.metric_definition_id != binding.metric_definition_id:
                continue
            rule_metric = db.get(MetricDefinitionVersion, rule.metric_definition_id)
            if (
                rule_metric is None
                or not binding.entity_scope.get(rule_metric.entity_scope)
            ):
                continue
            compatible_rules.append(rule)
        if len(compatible_rules) != 1:
            raise ValidationFailedError(
                "verify_rule requires exactly one compatible effective rule"
            )
        verification_rule = compatible_rules[0]

    metric_id = (
        verification_rule.metric_definition_id
        if verification_rule is not None
        else binding.metric_definition_id
    )
    metric = db.get(MetricDefinitionVersion, metric_id)
    if (
        metric is None
        or not metric.display_name.strip()
        or not metric.unit.strip()
        or not metric.period_semantics.strip()
    ):
        raise ValidationFailedError("acquisition scope has no metric definition")

    raw_company_id = binding.entity_scope.get("company_id")
    try:
        company_id = uuid.UUID(str(raw_company_id))
    except (TypeError, ValueError) as exc:
        raise ValidationFailedError(
            "acquisition scope has no persisted company identity"
        ) from exc
    company = db.get(Company, company_id)
    if company is None or not company.name.strip():
        raise ValidationFailedError("acquisition scope has no persisted company")
    stocks = tuple(
        db.scalars(
            select(Stock)
            .where(Stock.company_id == company.id)
            .order_by(Stock.code, Stock.id)
        )
    )
    if not stocks:
        raise ValidationFailedError("acquisition scope has no persisted security")
    security_codes = tuple(dict.fromkeys(_security_code(stock.code) for stock in stocks))

    if research_case.evidence_cutoff is None:
        raise ValidationFailedError("acquisition scope has no evidence cutoff")
    cutoff_date = research_case.evidence_cutoff
    if verification_rule is not None:
        cutoff_date = min(cutoff_date, verification_rule.available_at_deadline)
    cutoff = datetime.combine(cutoff_date, time.max, tzinfo=UTC)
    protocol_roles = (
        verification_rule.allowed_source_roles
        if verification_rule is not None
        else metric.allowed_source_roles
    )
    allowed_source_roles = (
        frozenset(protocol_roles)
        & frozenset(metric.allowed_source_roles)
        & frozenset(B_SCOPE_POLICY.allowed_source_roles)
    )
    if not allowed_source_roles:
        raise ValidationFailedError("acquisition scope has no allowed source roles")

    orchestration = db.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.tenant_id == tenant_id,
            ResearchOrchestration.research_case_id == research_case.id,
        )
    )
    if (
        orchestration is None
        or orchestration.current_scope_version_id is None
        or orchestration.current_research_run_id is None
    ):
        raise ValidationFailedError(
            "manual acquisition requires an active event research run and scope"
        )
    scope_version = db.get(
        EventResearchScopeVersion, orchestration.current_scope_version_id
    )
    research_run = db.get(ResearchRun, orchestration.current_research_run_id)
    if (
        scope_version is None
        or scope_version.research_case_id != research_case.id
        or research_run is None
        or research_run.research_case_id != research_case.id
    ):
        raise ValidationFailedError(
            "active event research run or scope does not belong to the case"
        )
    goal_id = f"thesis:{thesis.id}:{payload.objective.value}"
    canonical_idempotency_key = (
        f"run:{research_run.id}:scope:{scope_version.id}:"
        f"goal:{goal_id}:round:1"
    )
    if idempotency_key != canonical_idempotency_key:
        raise ValidationFailedError(
            "Idempotency-Key must match the active run, scope, goal, and round"
        )

    return AcquisitionRequest(
        tenant_id=tenant_id,
        case_id=research_case.id,
        thesis_id=thesis.id,
        research_run_id=research_run.id,
        scope_version_id=scope_version.id,
        goal_id=goal_id,
        round=1,
        objective=payload.objective,
        target_link_role=(
            "supports"
            if payload.objective is EvidenceObjective.VERIFY_RULE
            else _OBJECTIVE_ROLES[payload.objective]
        ),
        thesis_statement=thesis.statement,
        entity_names=(company.name,),
        security_codes=security_codes,
        metric_terms=(metric.display_name,),
        metric_periods=(
            f"{metric.period_semantics.strip()}:"
            f"{(verification_rule.observed_period_start if verification_rule is not None else binding.horizon_start).isoformat()}/"
            f"{(verification_rule.observed_period_end if verification_rule is not None else binding.horizon_end).isoformat()}",
        ),
        metric_units=(metric.unit.strip(),),
        period_start=(
            verification_rule.observed_period_start.isoformat()
            if verification_rule is not None
            else binding.horizon_start.isoformat()
        ),
        period_end=(
            verification_rule.observed_period_end.isoformat()
            if verification_rule is not None
            else binding.horizon_end.isoformat()
        ),
        cutoff=cutoff,
        allowed_source_roles=allowed_source_roles,
        source_policy_version=B_SCOPE_POLICY.version,
        planner_version=ACQUISITION_PLANNER_VERSION,
        previous_query_plan_id=None,
        expansion=None,
        idempotency_key=canonical_idempotency_key,
    )


def _authorized_acquisition_job(
    db: Session,
    job_id: uuid.UUID,
    actor: ResearchActor,
    case_policy: RequireCaseRoute,
) -> AcquisitionJob:
    job = db.get(AcquisitionJob, job_id)
    if job is None or job.tenant_id != actor.tenant_id:
        raise NotFoundError("acquisition job not found")
    case_policy.require(job.research_case_id)
    return job


@router.post(
    "/research-cases/{case_id}/theses/{thesis_id}/acquisition-jobs",
    response_model=AcquisitionJobAcceptedDTO,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_acquisition_job(
    case_id: uuid.UUID,
    thesis_id: uuid.UUID,
    payload: AcquisitionJobCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=512),
    ],
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> AcquisitionJobAcceptedDTO:
    principal = AcquisitionPrincipal(
        tenant_id=actor.tenant_id,
        actor=actor.server_actor,
    )
    module = AcquisitionModule(db)
    CaseTenantAccess(db).require_case(case_id, actor.tenant_id)
    thesis = db.get(Thesis, thesis_id)
    if thesis is None or thesis.research_case_id != case_id:
        raise NotFoundError("thesis not found")
    existing = module.replay_existing(
        tenant_id=actor.tenant_id,
        idempotency_key=idempotency_key,
        case_id=case_id,
        thesis_id=thesis_id,
        objective=payload.objective.value,
        principal=principal,
    )
    if existing is not None:
        return AcquisitionJobAcceptedDTO(
            id=existing.id,
            status=_safe_status(existing.status),
        )

    orchestration = db.scalar(
        select(ResearchOrchestration).where(
            ResearchOrchestration.tenant_id == actor.tenant_id,
            ResearchOrchestration.research_case_id == case_id,
        )
    )
    if (
        orchestration is not None
        and orchestration.current_scope_version_id is not None
        and orchestration.current_research_run_id is not None
    ):
        goal_id = f"thesis:{thesis_id}:{payload.objective.value}"
        canonical_key = (
            f"run:{orchestration.current_research_run_id}:"
            f"scope:{orchestration.current_scope_version_id}:"
            f"goal:{goal_id}:round:1"
        )
        if idempotency_key != canonical_key:
            raise ValidationFailedError(
                "Idempotency-Key must match the active run, scope, goal, and round"
            )
    request = _scope_request(
        db,
        case_id=case_id,
        thesis_id=thesis_id,
        payload=payload,
        tenant_id=actor.tenant_id,
        idempotency_key=idempotency_key,
    )
    job = module.request(
        request,
        principal=principal,
    )
    commit_or_rollback(db)
    return AcquisitionJobAcceptedDTO(id=job.id, status=_safe_status(job.status))


@router.get(
    "/acquisition-jobs/{job_id}",
    response_model=AcquisitionJobDetailDTO,
)
def acquisition_job_detail(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> AcquisitionJobDetailDTO:
    job = _authorized_acquisition_job(db, job_id, actor, case_policy)
    module = AcquisitionModule(db)
    view = module.get(
        job_id,
        principal=AcquisitionPrincipal(
            tenant_id=actor.tenant_id,
            actor=actor.server_actor,
        ),
    )
    return AcquisitionJobDetailDTO(
        id=view.id,
        status=_safe_status(view.status),
        stage=_safe_stage(view.stage),
        attempt=view.attempt,
        counters=AcquisitionCountersDTO(
            references=job.reference_count,
            fetched=job.fetched_count,
            frozen=job.frozen_count,
            admitted=job.admitted_count,
            exceptions=job.exception_count,
        ),
        retry_at=job.retry_at,
        error=(
            AcquisitionErrorSummaryDTO(
                code=_safe_public_token(job.error_code),
                summary="acquisition job failed",
            )
            if job.error_code
            else None
        ),
    )


def _safe_event_payload(payload: object) -> AcquisitionJobEventPayloadDTO:
    if not isinstance(payload, dict):
        return AcquisitionJobEventPayloadDTO()
    attempt = payload.get("attempt")
    retryable = payload.get("retryable")
    return AcquisitionJobEventPayloadDTO(
        attempt=(
            attempt
            if isinstance(attempt, int) and not isinstance(attempt, bool) and attempt >= 0
            else None
        ),
        adapter_key=(
            _safe_public_token(payload["adapter_key"])
            if "adapter_key" in payload
            else None
        ),
        operation=(
            _safe_public_token(payload["operation"])
            if "operation" in payload
            else None
        ),
        outcome=(
            _safe_public_token(payload["outcome"])
            if "outcome" in payload
            else None
        ),
        reason_code=(
            _safe_public_token(payload["reason_code"])
            if "reason_code" in payload
            else None
        ),
        retryable=retryable if isinstance(retryable, bool) else None,
    )


@router.get(
    "/acquisition-jobs/{job_id}/events",
    response_model=list[AcquisitionJobEventDTO],
    response_model_exclude_none=True,
)
def acquisition_job_events(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    after_seq: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> list[AcquisitionJobEventDTO]:
    _authorized_acquisition_job(db, job_id, actor, case_policy)
    events = AcquisitionModule(db).events(
        job_id,
        principal=AcquisitionPrincipal(
            tenant_id=actor.tenant_id,
            actor=actor.server_actor,
        ),
    )
    return [
        AcquisitionJobEventDTO(
            seq=event.seq,
            status=_safe_status(event.status),
            stage=_safe_stage(event.stage),
            message=safe_public_text(event.message, max_length=512),
            payload=_safe_event_payload(event.payload_json),
            created_at=event.created_at,
        )
        for event in events
        if event.seq > after_seq
    ]


def _safe_gate_results(
    payload: object,
) -> dict[AcquisitionGateName, AcquisitionGateResultDTO]:
    if not isinstance(payload, dict):
        return {}
    results: dict[AcquisitionGateName, AcquisitionGateResultDTO] = {}
    for gate_name in _GATE_NAMES:
        value = payload.get(gate_name)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("passed"), bool)
        ):
            continue
        results[gate_name] = AcquisitionGateResultDTO(
            passed=value["passed"],
            reason_code=_safe_public_token(value.get("reason_code")),
        )
    return results


@router.get(
    "/acquisition-jobs/{job_id}/evidence",
    response_model=list[AcquisitionEvidenceDTO],
)
def acquisition_job_evidence(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> list[AcquisitionEvidenceDTO]:
    _authorized_acquisition_job(db, job_id, actor, case_policy)
    principal = AcquisitionPrincipal(
        tenant_id=actor.tenant_id,
        actor=actor.server_actor,
    )
    refs = AcquisitionModule(db).admitted_evidence(job_id, principal=principal)
    if not refs:
        return []
    evidence_ids = [ref.evidence_link_id for ref in refs]
    rows = db.execute(
        select(
            EvidenceLink.id,
            SourceStatement.id,
            DocumentVersion.id,
            SourceReference.title,
            SourceReference.canonical_url,
            AutomaticAdmissionDecision.gate_results,
            EvidenceLink.review_state,
        )
        .select_from(EvidenceLink)
        .join(
            AutomaticAdmissionDecision,
            AutomaticAdmissionDecision.id
            == EvidenceLink.automatic_admission_decision_id,
        )
        .join(
            RetrievalArtifact,
            RetrievalArtifact.id
            == AutomaticAdmissionDecision.retrieval_artifact_id,
        )
        .join(
            SourceReference,
            SourceReference.id == RetrievalArtifact.source_reference_id,
        )
        .join(
            SourceStatement,
            SourceStatement.id == EvidenceLink.source_statement_id,
        )
        .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
        .join(
            RetrievalArtifactDocument,
            (RetrievalArtifactDocument.retrieval_artifact_id == RetrievalArtifact.id)
            & (
                RetrievalArtifactDocument.document_version_id
                == SourceSpan.document_version_id
            ),
        )
        .join(
            DocumentVersion,
            DocumentVersion.id == RetrievalArtifactDocument.document_version_id,
        )
        .where(
            EvidenceLink.id.in_(evidence_ids),
            AutomaticAdmissionDecision.job_id == job_id,
            AutomaticAdmissionDecision.outcome == "admitted",
            EvidenceLink.review_state == "automatically_admitted",
        )
        .order_by(EvidenceLink.created_at, EvidenceLink.id)
    )
    return [
        AcquisitionEvidenceDTO(
            evidence_link_id=evidence_link_id,
            source_statement_id=source_statement_id,
            document_version_id=document_version_id,
            source_title=safe_public_text(source_title, max_length=512),
            source_url=_safe_public_url(source_url),
            gate_results=_safe_gate_results(gate_results),
            review_state=review_state,
        )
        for (
            evidence_link_id,
            source_statement_id,
            document_version_id,
            source_title,
            source_url,
            gate_results,
            review_state,
        ) in rows
    ]


def _safe_exception_detail(
    record: AcquisitionException,
) -> AcquisitionExceptionDetailDTO:
    payload = record.detail_json if isinstance(record.detail_json, dict) else {}
    raw_failed = payload.get("failed_gates")
    failed_gates: list[AcquisitionGateName] | None = None
    if isinstance(raw_failed, list):
        failed_gates = []
        for gate_name in _GATE_NAMES:
            if gate_name in raw_failed:
                failed_gates.append(gate_name)

    raw_reasons = payload.get("reason_codes")
    reason_codes: dict[AcquisitionGateName, str] | None = None
    if isinstance(raw_reasons, dict):
        reason_codes = {
            gate_name: _safe_public_token(raw_reasons[gate_name])
            for gate_name in _GATE_NAMES
            if gate_name in raw_reasons
        }

    provider_status = payload.get("provider_status")
    if isinstance(provider_status, bool):
        provider_status = None
    elif isinstance(provider_status, int):
        pass
    elif isinstance(provider_status, str):
        provider_status = _safe_public_token(provider_status)
    else:
        provider_status = None

    return AcquisitionExceptionDetailDTO(
        adapter_key=(
            _safe_public_token(payload["adapter_key"])
            if "adapter_key" in payload
            else None
        ),
        attempt_id=_safe_uuid(payload.get("attempt_id")),
        candidate_id=(
            _safe_uuid(payload.get("candidate_id")) or record.candidate_id
        ),
        document_version_id=_safe_uuid(payload.get("document_version_id")),
        external_record_id=(
            safe_public_text(payload["external_record_id"], max_length=512)
            if "external_record_id" in payload
            else None
        ),
        provider_status=provider_status,
        retrieval_artifact_id=(
            _safe_uuid(payload.get("retrieval_artifact_id"))
            or record.retrieval_artifact_id
        ),
        source_reference_id=(
            _safe_uuid(payload.get("source_reference_id"))
            or record.source_reference_id
        ),
        status=(
            _safe_public_token(payload["status"])
            if "status" in payload
            else None
        ),
        outcome=(
            _safe_public_token(payload["outcome"])
            if "outcome" in payload
            else None
        ),
        failed_gates=failed_gates,
        reason_codes=reason_codes,
        gate_version=(
            _safe_public_token(payload["gate_version"])
            if "gate_version" in payload
            else None
        ),
        policy_version=(
            _safe_public_token(payload["policy_version"])
            if "policy_version" in payload
            else None
        ),
    )


@router.get(
    "/acquisition-jobs/{job_id}/exceptions",
    response_model=list[AcquisitionExceptionDTO],
    response_model_exclude_none=True,
)
def acquisition_job_exceptions(
    case_policy: RequireCaseRoute,
    job_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: ResearchActor = Depends(require_research_actor),
) -> list[AcquisitionExceptionDTO]:
    _authorized_acquisition_job(db, job_id, actor, case_policy)
    AcquisitionModule(db).get(
        job_id,
        principal=AcquisitionPrincipal(
            tenant_id=actor.tenant_id,
            actor=actor.server_actor,
        ),
    )
    records = db.scalars(
        select(AcquisitionException)
        .where(AcquisitionException.job_id == job_id)
        .order_by(AcquisitionException.created_at, AcquisitionException.id)
    )
    return [
        AcquisitionExceptionDTO(
            reason=_safe_public_token(record.reason_code),
            detail=_safe_exception_detail(record),
            created_at=record.created_at,
        )
        for record in records
    ]
