"""Engine commands (prototype 监测与更新 · AI RERUN, plus extract/propose).

Re-running the assess step for one thesis freezes a new snapshot and appends
a new provisional AIAssessment plus its AIRun audit record.  Nothing is
overwritten — the evolution shows up in the snapshot-compare view.

Extraction runs over one document version and only creates review-gated atomic
claim candidates; proposal fans evidence links out
for one thesis into the review queue.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient, LLMProviderError
from app.ai.extraction import StatementExtractor
from app.ai.prompts import EXTRACT_PROMPT_VERSION
from app.ai.runs import record_run
from app.ai.proposal import EvidenceProposer
from app.api.v1.commands.common import commit_or_rollback
from app.api.v1.tenant_context import require_research_tenant
from app.services.review_tenant_access import ReviewTenantAccess
from app.services.case_tenant_access import CaseTenantAccess
from app.db import get_db
from app.errors import NotFoundError, UpstreamUnavailableError, ValidationFailedError
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentVersion,
    ValidationError,
)
from app.models.operational import Job
from app.models.source_governance import SourceContract
from app.services.compliance import ComplianceRefusedError
from app.services.jobs import JobService
from app.services.ingest import DocumentService
from app.repositories.documents import DocumentRepository
from app.services.source_governance import SourceGovernanceService
from app.services.source_admission import source_contract_is_active
from app.schemas.v1.commands import (
    ExtractResponse,
    ExtractCandidateDTO,
    ProposedLinkDTO,
    ProposeResponse,
    RerunAssessmentDTO,
    RerunResponse,
    CreateDocumentSupplementRequest,
    CreateDocumentSupplementResponse,
)

router = APIRouter(prefix="/theses", tags=["engine-commands-v1"])
documents_router = APIRouter(prefix="/documents", tags=["engine-commands-v1"])


def _lock_propose_job(db: Session, job_id: uuid.UUID) -> Job | None:
    """Lock and refresh the short post-provider Job transition."""
    with db.no_autoflush:
        return db.scalar(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )


def _propose_job_accepts_output(db: Session, job_id: uuid.UUID) -> bool:
    """Claim the output slot unless a committed cancellation won first."""
    job = _lock_propose_job(db, job_id)
    return bool(
        job is not None
        and job.status == "running"
        and not job.cancel_requested
    )


def _propose_job_is_cancelled(job: Job | None) -> bool:
    return bool(
        job is not None
        and (job.cancel_requested or job.status == "cancelled")
    )


def _propose_response(
    *,
    thesis_id: uuid.UUID,
    client: LLMClient,
    job_id: uuid.UUID,
    proposal_ids: list[uuid.UUID],
) -> ProposeResponse:
    return ProposeResponse(
        thesis_id=str(thesis_id),
        mode="mock" if client._mock else client.model_version,
        job_id=str(job_id),
        link_count=len(proposal_ids),
        links=[
            ProposedLinkDTO(
                proposal_id=str(pid),
                source_statement_id="",
                role="",
                reason="",
                scope={},
            )
            for pid in proposal_ids
        ],
    )


@documents_router.post(
    "/{document_version_id}/supplements",
    response_model=CreateDocumentSupplementResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_document_supplement(
    document_version_id: uuid.UUID,
    payload: CreateDocumentSupplementRequest,
    tenant_id: str = Depends(require_research_tenant),
    db: Session = Depends(get_db),
):
    """Freeze user-supplied recovery text without changing the original file."""
    try:
        case_id = uuid.UUID(payload.case_id)
    except ValueError as exc:
        raise ValidationFailedError("case_id must be a UUID") from exc
    CaseTenantAccess(db).require_case(case_id, tenant_id)
    original = db.scalar(select(DocumentVersion).where(
        DocumentVersion.id == document_version_id,
        DocumentRepository.owned_attachment(tenant_id, case_id),
    ))
    if original is None:
        raise NotFoundError("document version not found")
    original_contract = db.scalar(
        select(SourceContract).where(SourceContract.document_version_id == document_version_id)
    )
    docs = DocumentService(DocumentRepository(db))
    supplement = docs.freeze(
        raw=payload.raw_text.encode("utf-8"),
        source_url=f"supplement://{document_version_id}/{uuid.uuid4()}",
        parser_version="user-supplement-v1",
        title=f"补充正文 · {original.title or str(document_version_id)}",
        parse_state="partial",
        source_authority=payload.source_metadata.get("authority_level", "user_supplied"),
        supplements_document_version_id=original.id,
        claimed_page_reference=payload.claimed_page_reference.strip(),
    )
    if supplement.supplements_document_version_id not in {None, original.id}:
        raise ValidationFailedError("identical supplement content is already frozen for another original document")
    docs.attach_to_case(research_case_id=case_id, document_version_id=supplement.id)
    existing_span = next(
        (
            span
            for span in DocumentRepository(db).spans_for_version(supplement.id)
            if span.verbatim_text == payload.raw_text
            and span.locator.get("kind") == "supplement_text"
            and span.locator.get("supplements_document_version_id") == str(original.id)
        ),
        None,
    )
    if existing_span is None:
        docs.add_span(
            document_version_id=supplement.id,
            locator={
                "kind": "supplement_text",
                "supplements_document_version_id": str(original.id),
                "claimed_page_reference": payload.claimed_page_reference.strip(),
                "source_metadata": payload.source_metadata,
            },
            verbatim_text=payload.raw_text,
        )
    try:
        contract = SourceGovernanceService(db).record_supplement_intake(
            document=supplement,
            original_contract=original_contract,
            source_metadata=payload.source_metadata,
            declared_by=payload.created_by,
        )
    except ValueError as exc:
        db.rollback()
        raise ValidationFailedError(str(exc)) from exc
    commit_or_rollback(db)
    return CreateDocumentSupplementResponse(
        document_version_id=str(supplement.id),
        original_document_version_id=str(original.id),
        claimed_page_reference=payload.claimed_page_reference.strip(),
        extraction_allowed=contract.allow_ai_processing and contract.allow_display,
    )


@router.post(
    "/{thesis_id}/rerun",
    response_model=RerunResponse,
    status_code=status.HTTP_201_CREATED,
)
def rerun_assessment(
    thesis_id: uuid.UUID,
    tenant_id: str = Depends(require_research_tenant),
    db: Session = Depends(get_db),
):
    ReviewTenantAccess(db).require_thesis(thesis_id, tenant_id)
    client = LLMClient.from_env()
    try:
        assessment = AssessmentGenerator(client).generate(
            thesis_id, datetime.now(timezone.utc), db
        )
    except ValueError as exc:
        commit_or_rollback(db)
        raise NotFoundError(str(exc)) from exc
    except (ComplianceRefusedError, ValidationError) as exc:
        # The generator rolled back every partial domain write and appended
        # one failed AIRun in a clean transaction. Persist that audit before
        # translating the domain refusal to a 422 response.
        commit_or_rollback(db)
        raise ValidationFailedError(str(exc)) from exc
    except Exception:
        # Unexpected provider/runtime failures use the same generator-owned
        # clean failure transaction. Preserve its audit before propagating
        # the 500-class error to the global handler.
        commit_or_rollback(db)
        raise
    commit_or_rollback(db)
    return RerunResponse(
        thesis_id=str(thesis_id),
        mode="mock" if client._mock else client.model_version,
        assessment=RerunAssessmentDTO(
            id=str(assessment.id),
            snapshot_id=str(assessment.snapshot_id),
            conclusion=assessment.conclusion,
            rationale=assessment.rationale,
            gaps=[str(g) for g in assessment.gaps],
            displayed_as_provisional=assessment.displayed_as_provisional,
            created_at=assessment.created_at.isoformat(),
        ),
    )


@router.post(
    "/{thesis_id}/propose",
    response_model=ProposeResponse,
    status_code=status.HTTP_201_CREATED,
)
def propose_evidence(
    thesis_id: uuid.UUID,
    tenant_id: str = Depends(require_research_tenant),
    db: Session = Depends(get_db),
):
    """Run the propose step for one thesis.

    Recall is cutoff-safe (only statements visible before this run started).
    Every proposed link enters the review queue as a ``Proposal(kind=evidence_link)``
    — nothing is auto-confirmed; a human decision publishes the formal link.
    The work runs inside a Job row so progress / cancellation are observable.
    """
    thesis = ReviewTenantAccess(db).require_thesis(thesis_id, tenant_id)
    client = LLMClient.from_env()
    jobs = JobService(db)
    job = jobs.create(
        kind="propose",
        target_type="thesis",
        target_id=thesis_id,
        research_case_id=thesis.research_case_id,
        actor=f"ai:{client.model_version}",
    )
    jobs.start(job, step="recalling statements")
    job_id = job.id
    # Make the operational attempt observable before provider work.  The
    # proposer may roll back its output transaction on any later failure.
    commit_or_rollback(db)
    try:
        proposal_ids = EvidenceProposer(client).propose(
            thesis_id,
            db,
            before_persist=lambda: _propose_job_accepts_output(db, job_id),
        )
    except Exception:
        # EvidenceProposer records the provider detail on its failed AIRun.
        # A cancellation committed while the provider was in flight wins over
        # both the provider failure and its audit row.
        failed_job = _lock_propose_job(db, job_id)
        if _propose_job_is_cancelled(failed_job):
            db.rollback()
            cancelled_job = _lock_propose_job(db, job_id)
            if cancelled_job is not None:
                jobs.finish(
                    cancelled_job,
                    status="cancelled",
                    step="cancelled",
                )
            commit_or_rollback(db)
            return _propose_response(
                thesis_id=thesis_id,
                client=client,
                job_id=job_id,
                proposal_ids=[],
            )
        if failed_job is not None:
            jobs.finish(
                failed_job,
                status="failed",
                error="provider execution failed",
            )
        commit_or_rollback(db)
        raise
    current_job = _lock_propose_job(db, job_id)
    if _propose_job_is_cancelled(current_job):
        if current_job is not None:
            jobs.finish(
                current_job,
                status="cancelled",
                step="cancelled",
            )
        proposal_ids = []
    elif current_job is not None:
        jobs.progress(current_job, step="proposed", progress=100)
        jobs.finish(current_job, status="succeeded", step="proposed")
    commit_or_rollback(db)
    return _propose_response(
        thesis_id=thesis_id,
        client=client,
        job_id=job_id,
        proposal_ids=proposal_ids,
    )


@documents_router.post(
    "/{document_version_id}/extract",
    response_model=ExtractResponse,
    status_code=status.HTTP_201_CREATED,
)
def extract_statements(
    document_version_id: uuid.UUID,
    case_id: uuid.UUID | None = None,
    tenant_id: str = Depends(require_research_tenant),
    db: Session = Depends(get_db),
):
    """Run the extract step without publishing formal statements.

    Returned candidates retain an exact original quote and await an explicit
    human decision in the Case review workbench.
    """
    if case_id is not None:
        CaseTenantAccess(db).require_case(case_id, tenant_id)
    version = db.scalar(select(DocumentVersion).where(
        DocumentVersion.id == document_version_id,
        DocumentRepository.owned_attachment(tenant_id, case_id),
    ))
    if version is None:
        raise NotFoundError("document version not found")
    contract = db.scalar(
        select(SourceContract).where(
            SourceContract.document_version_id == document_version_id
        )
    )
    if contract is not None and (
        not contract.allow_ai_processing or not source_contract_is_active(contract)
    ):
        message = "来源合同禁止 AI 处理或当前已失效；没有创建候选或正式陈述。"
        record_run(
            db,
            kind="extract",
            model_version="not_run",
            prompt_version="extract-v1",
            input_ref={"document_version_id": str(document_version_id), "span_ids": []},
            output_summary="refused: frozen source contract forbids AI processing",
            status="failed",
            error=message,
            started_at=datetime.now(timezone.utc),
        )
        commit_or_rollback(db)
        raise ValidationFailedError(message)
    try:
        client = LLMClient.from_env()
    except (ValueError, RuntimeError) as exc:
        message = "document extraction model configuration is unavailable"
        record_run(
            db, kind="extract", model_version="not_run",
            prompt_version=EXTRACT_PROMPT_VERSION,
            input_ref={"document_version_id": str(document_version_id), "span_ids": []},
            output_summary="not started: model configuration unavailable",
            status="failed", error=message, started_at=datetime.now(timezone.utc),
        )
        commit_or_rollback(db)
        raise UpstreamUnavailableError(message) from exc
    try:
        candidates = StatementExtractor(client).extract(document_version_id, db)
    except LLMProviderError as exc:
        # A timeout / connection error is a retryable dependency failure, not
        # an application defect. StatementExtractor has already persisted the
        # failed AIRun in its clean post-provider transaction.
        commit_or_rollback(db)
        raise UpstreamUnavailableError("LLM provider is temporarily unavailable") from exc
    except Exception:
        # StatementExtractor appends the failed AIRun in the post-provider
        # transaction; preserve it before the request unwinds to a generic 500.
        commit_or_rollback(db)
        raise
    commit_or_rollback(db)
    # Honest reason when no statements were produced — distinguishes
    # "nothing to extract" from "LLM refused / blank input".
    reason = _extract_reason(db, document_version_id, candidates)
    return ExtractResponse(
        document_version_id=str(document_version_id),
        mode="mock" if client._mock else client.model_version,
        candidate_count=len(candidates),
        reason=reason,
        candidates=[
            ExtractCandidateDTO(
                id=str(candidate.id),
                claim_type=candidate.claim_type,
                normalized_text=candidate.normalized_text,
                quote=candidate.quote,
                quote_start=candidate.quote_start,
                quote_end=candidate.quote_end,
            )
            for candidate in candidates
        ],
    )


def _extract_reason(db: Session, version_id: uuid.UUID, statements) -> str | None:
    """Surface a one-line explanation whenever statement_count is 0."""
    if statements:
        return None
    from app.models.ledger import AIRun, SourceSpan
    from sqlalchemy import func, select

    span_count = db.scalar(
        select(func.count()).select_from(SourceSpan).where(
            SourceSpan.document_version_id == version_id
        )
    )
    if not span_count:
        return "该版本没有附加来源片段，无法抽取陈述"
    last_run = db.scalar(
        select(AIRun)
        .where(AIRun.kind == "extract")
        .where(AIRun.input_ref["document_version_id"].as_string() == str(version_id))
        .order_by(AIRun.started_at.desc())
        .limit(1)
    )
    summary = last_run.output_summary if last_run else None
    if summary and "llm returned 0" in summary:
        return "LLM 抽取调用完成但未返回任何陈述（可能为纯结构化或合规受限）"
    return f"提取运行记录：{summary}" if summary else "提取未产生陈述"
