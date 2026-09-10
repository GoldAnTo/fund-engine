"""Resolve only native, governed artifact IDs for the frozen Gateway run."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.acquisition.policy import B_SCOPE_POLICY, INTAKE_MATERIAL_POLICY
from app.models.acquisition import (
    AcquisitionAttempt,
    AcquisitionJob,
    AutomaticAdmissionDecision,
    RetrievalArtifact,
    RetrievalArtifactDocument,
    SourceReference,
)
from app.models.ledger import (
    AtomicClaimCandidate,
    CaseDocumentVersion,
    ResearchCase,
    SourceSpan,
    Thesis,
    ValidationError,
)
from app.models.operational import ResearchRun, ResearchTask
from app.models.research_gateway import ArtifactReference, ResearchRunSpec
from app.queries.automatic_research import AutomaticResearchQueries
from app.services.acquisition import _json_safe
from app.services.automatic_admission import (
    B_SCOPE_GATE_VERSION,
    AdmissionContext,
    AutomaticAdmissionGate,
    _Lineage,
    canonical_admission_digest,
    frozen_request_digest,
    trusted_extraction_run,
)
from app.services.automatic_research_scope import (
    ValidatedAutomaticResearchScope,
    load_automatic_research_scope,
)
from app.services.automatic_source_bindings import (
    ValidatedAutomaticSourceBindings,
    validate_automatic_source_bindings,
)
from app.services.case_tenant_access import CaseTenantAccess
from app.services.source_admission import source_contract_is_active


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _cutoff(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Gateway frozen cutoff is invalid")  # noqa: TRY004 - malformed persisted authority
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Gateway frozen cutoff is invalid")
    return parsed.astimezone(UTC)


def _require_frozen_authority(
    session: Session,
    spec: ResearchRunSpec,
    run: ResearchRun,
    scope: ValidatedAutomaticResearchScope,
) -> datetime:
    case = session.get(ResearchCase, spec.native_case_id)
    policy = {
        "allowed_source_types": list(scope.allowed_source_types),
        "factors": [
            {
                "thesis_id": str(factor.thesis_id),
                "allowed_source_roles": list(factor.allowed_source_roles),
            }
            for factor in scope.factors
        ],
    }
    frozen_cutoff = spec.frozen_cutoff
    if (
        case is None
        or run.id != spec.native_run_id
        or run.research_case_id != spec.native_case_id
        or scope.snapshot() != spec.frozen_scope
        or spec.frozen_source_policy != policy
        or not isinstance(frozen_cutoff, dict)
        or set(frozen_cutoff) != {"evidence_cutoff", "case_evidence_cutoff"}
    ):
        raise ValueError("Gateway frozen source authority differs from native scope")
    cutoff = _cutoff(frozen_cutoff["evidence_cutoff"])
    case_cutoff = frozen_cutoff["case_evidence_cutoff"]
    if (
        cutoff != _utc(run.created_at)
        or (case.evidence_cutoff is None and case_cutoff is not None)
        or (
            case.evidence_cutoff is not None
            and _cutoff(case_cutoff) != _utc(case.evidence_cutoff)
        )
    ):
        raise ValueError("Gateway frozen cutoff differs from native scope")
    return cutoff


def validated_gateway_source_bindings(
    session: Session,
    spec: ResearchRunSpec,
    run: ResearchRun,
    scope: ValidatedAutomaticResearchScope,
) -> ValidatedAutomaticSourceBindings:
    """Authorize native progress and artifacts against the Gateway's frozen policy.

    This is read-only and never requires a live worker lease. Refresh mutable
    source rows after the caller's conversation mutex, before native validation.
    """
    CaseTenantAccess(session).require_case(spec.native_case_id, spec.tenant_id)
    cutoff = _require_frozen_authority(session, spec, run, scope)
    for model in (ResearchTask, AcquisitionJob):
        run_column = model.run_id if model is ResearchTask else model.research_run_id
        list(
            session.scalars(
                select(model)
                .where(run_column == run.id)
                .execution_options(populate_existing=True)
            )
        )
    try:
        bindings = validate_automatic_source_bindings(
            session, run, scope, allow_unbound_current_round=run.status == "queued",
        )
    except ValueError:
        # Native material intake dispatches the complete material set before
        # dispatching external objectives. Those later tasks are intentionally
        # unbound while the material jobs execute; all other matrix and frozen
        # authority checks still apply. Never accept partially bound externals.
        if not (scope.input_kind == "material" and run.round == 1
                and run.status == "waiting_for_sources" and run.stage == "retrieve"):
            raise
        bindings = validate_automatic_source_bindings(
            session, run, scope, allow_unbound_current_round=True,
        )
        material_ids = {task.id for task in bindings.tasks_by_id.values()
                        if task.task_type == "intake_material"}
        unbound = [task for task in bindings.tasks_by_id.values()
                   if task.id not in bindings.jobs_by_task_id]
        if (not material_ids or set(bindings.jobs_by_task_id) != material_ids
                or bindings.external_job_ids or not unbound
                or any(task.task_type == "intake_material" or task.round != 1
                       or task.status != "queued" or task.stage != "planned"
                       or task.result is not None for task in unbound)):
            raise ValueError("Gateway material-first source binding is invalid")
    for task_id, job in bindings.jobs_by_task_id.items():
        task = bindings.tasks_by_id[task_id]
        factor = scope.factor(task.thesis_id)
        material = task.task_type == "intake_material"
        policy = INTAKE_MATERIAL_POLICY if material else B_SCOPE_POLICY
        roles = (
            policy.allowed_source_roles
            if material
            else (frozenset(factor.allowed_source_roles) & policy.allowed_source_roles)
        )
        request = job.request_snapshot
        if (
            job.tenant_id != spec.tenant_id
            or request.get("tenant_id") != spec.tenant_id
            or _cutoff(request.get("cutoff")) != cutoff
            or request.get("allowed_source_roles") != sorted(roles)
            or request.get("source_policy_version") != policy.version
            or job.policy_snapshot != _json_safe(policy)
            or request.get("thesis_statement") != factor.statement
            or request.get("metric_terms") != [factor.statement]
        ):
            raise ValueError("Gateway acquisition authority differs from frozen policy")
    return bindings


def _authorized_link(
    session: Session,
    spec: ResearchRunSpec,
    scope: ValidatedAutomaticResearchScope,
    bindings: ValidatedAutomaticSourceBindings,
    row,
    *,
    cutoff: datetime,
) -> bool:
    link, statement, document, contract = row
    decision = session.get(
        AutomaticAdmissionDecision, link.automatic_admission_decision_id
    )
    if (
        decision is None
        or decision.job_id not in bindings.job_ids
        or decision.outcome != "admitted"
        or decision.gate_version != B_SCOPE_GATE_VERSION
        or decision.policy_version != B_SCOPE_POLICY.version
        or statement.automatic_admission_decision_id != decision.id
        or statement.atomic_claim_candidate_id != decision.candidate_id
    ):
        return False
    job = next(
        job for job in bindings.jobs_by_task_id.values() if job.id == decision.job_id
    )
    candidate = session.get(AtomicClaimCandidate, decision.candidate_id)
    artifact = session.get(RetrievalArtifact, decision.retrieval_artifact_id)
    span = session.get(SourceSpan, statement.source_span_id)
    thesis = session.get(Thesis, link.thesis_id)
    if (
        candidate is None
        or artifact is None
        or span is None
        or thesis is None
        or candidate.source_span_id != span.id
        or span.document_version_id != document.id
        or thesis.id != job.thesis_id
        or thesis.research_case_id != spec.native_case_id
        or link.role != job.request_snapshot.get("target_link_role")
    ):
        return False
    reference = session.get(SourceReference, artifact.source_reference_id)
    attempt = session.get(AcquisitionAttempt, artifact.attempt_id)
    binding = session.scalar(
        select(RetrievalArtifactDocument).where(
            RetrievalArtifactDocument.retrieval_artifact_id == artifact.id
        )
    )
    attached = (
        session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == spec.native_case_id,
                CaseDocumentVersion.document_version_id == document.id,
            )
        )
        is not None
    )
    if (
        reference is None
        or attempt is None
        or binding is None
        or reference.job_id != job.id
        or attempt.job_id != job.id
        or binding.document_version_id != document.id
        or contract is None
        or not source_contract_is_active(contract)
        or not contract.allow_display
        or not contract.allow_ai_processing
    ):
        return False
    lineage = _Lineage(
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
        ai_run=trusted_extraction_run(session, candidate, document, span),
        case_attached=attached,
    )
    digest = canonical_admission_digest(
        job=job,
        candidate=candidate,
        reference=reference,
        attempt=attempt,
        artifact=artifact,
        document=document,
        span=span,
        binding=binding,
        contract=contract,
        ai_run=lineage.ai_run,
    )
    request_digest = frozen_request_digest(job)
    results = decision.gate_results
    # The original deterministic locator/semantic gates are trusted only while
    # every value in their immutable lineage still matches the admission digest.
    if not isinstance(results, dict) or set(results) != {
        "source",
        "temporal",
        "locator",
        "semantic",
    }:
        return False
    for result in results.values():
        facts = result.get("facts") if isinstance(result, dict) else None
        if (
            not isinstance(facts, dict)
            or result.get("passed") is not True
            or facts.get("frozen_request_digest") != request_digest
            or facts.get("admission_digest") != digest
        ):
            return False
    context = AdmissionContext(
        job_id=job.id,
        retrieval_artifact_id=artifact.id,
        thesis_id=thesis.id,
        cutoff=cutoff,
        objective=job.request_snapshot["objective"],
        target_link_role=link.role,
        gate_version=B_SCOPE_GATE_VERSION,
        policy_version=B_SCOPE_POLICY.version,
        allowed_source_roles=frozenset(job.request_snapshot["allowed_source_roles"]),
        metric_terms=(scope.factor(thesis.id).statement,),
        lease_token="gateway-read-only",
    )
    gate = AutomaticAdmissionGate(session)
    now = datetime.now(UTC)
    return (
        gate._source_gate(lineage, context, evaluation_at=now).passed
        and gate._temporal_gate(lineage, context, evaluation_at=now).passed
        and _utc(link.available_at) <= cutoff
    )


def native_artifacts(
    session: Session, spec: ResearchRunSpec
) -> list[ArtifactReference]:
    CaseTenantAccess(session).require_case(spec.native_case_id, spec.tenant_id)
    run = session.get(ResearchRun, spec.native_run_id)
    if run is None or run.research_case_id != spec.native_case_id:
        return []
    scope = load_automatic_research_scope(session, run)
    try:
        cutoff = _require_frozen_authority(session, spec, run, scope)
    except (ValueError, TypeError):
        return []
    refs = [
        ArtifactReference(kind=kind, id=identifier, case_id=spec.native_case_id)
        for kind, identifier in (
            ("research_case", spec.native_case_id),
            ("research_run", run.id),
        )
    ]
    if run.round < 1:
        return refs
    try:
        bindings = validated_gateway_source_bindings(session, spec, run, scope)
    except ValueError:
        return refs
    queries = AutomaticResearchQueries(session)
    links = queries._validated_links(
        spec.native_case_id, scope.current_scope_id, scope, bindings.job_ids
    )
    seen_documents = set()
    all_authorized = True
    for row in links:
        link, statement, document, _ = row
        try:
            authorized = _authorized_link(
                session, spec, scope, bindings, row, cutoff=cutoff
            )
        except (ValueError, TypeError, KeyError, ValidationError):
            authorized = False
        if not authorized:
            all_authorized = False
            continue
        span = session.get(SourceSpan, statement.source_span_id)
        refs.append(
            ArtifactReference(
                kind="evidence_link",
                id=link.id,
                case_id=spec.native_case_id,
                locator_available=bool(span and (span.locator_v1 or span.locator)),
            )
        )
        if document.id not in seen_documents:
            refs.append(
                ArtifactReference(
                    kind="document_version", id=document.id, case_id=spec.native_case_id
                )
            )
            seen_documents.add(document.id)
    if run.status == "succeeded" and all_authorized:
        view = queries.get(spec.native_case_id, spec.tenant_id)
        if (
            view.run_id == str(run.id)
            and view.status == "completed"
            and view.result is not None
        ):
            # Native query validates this deterministic draft's complete lineage.
            draft_id = uuid.uuid5(
                uuid.NAMESPACE_URL, f"fund-engine:event-research:automatic:{run.id}"
            )
            refs.append(
                ArtifactReference(
                    kind="draft", id=draft_id, case_id=spec.native_case_id
                )
            )
    return refs
