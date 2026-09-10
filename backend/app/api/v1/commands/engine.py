"""Engine commands (prototype 监测与更新 · AI RERUN, plus extract/propose).

Re-running the assess step for one thesis freezes a new snapshot and appends
a new provisional AIAssessment plus its AIRun audit record.  Nothing is
overwritten — the evolution shows up in the snapshot-compare view.

Extraction runs over one document version (pending versions only make sense;
the extractor itself is append-only), and proposal fans evidence links out
for one thesis into the review queue.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.ai.assessment_gen import AssessmentGenerator
from app.ai.client import LLMClient
from app.ai.extraction import StatementExtractor
from app.ai.proposal import EvidenceProposer
from app.api.v1.commands.common import commit_or_rollback
from app.db import get_db
from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import DocumentVersion, Thesis
from app.services.compliance import ComplianceRefusedError
from app.services.jobs import JobService
from app.schemas.v1.commands import (
    ExtractResponse,
    ExtractStatementDTO,
    ProposedLinkDTO,
    ProposeResponse,
    RerunAssessmentDTO,
    RerunResponse,
)

router = APIRouter(prefix="/theses", tags=["engine-commands-v1"])
documents_router = APIRouter(prefix="/documents", tags=["engine-commands-v1"])


@router.post(
    "/{thesis_id}/rerun",
    response_model=RerunResponse,
    status_code=status.HTTP_201_CREATED,
)
def rerun_assessment(
    thesis_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    client = LLMClient.from_env()
    try:
        assessment = AssessmentGenerator(client).generate(
            thesis_id, datetime.now(timezone.utc), db
        )
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except ComplianceRefusedError as exc:
        # The generator already deleted the half-frozen snapshot; persist
        # ONLY the failed AIRun (audit trail for the refusal), then 422.
        commit_or_rollback(db)
        raise ValidationFailedError(str(exc)) from exc
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
    db: Session = Depends(get_db),
):
    """Run the propose step for one thesis.

    Recall is cutoff-safe (only statements visible before this run started).
    Every proposed link enters the review queue as a ``Proposal(kind=evidence_link)``
    — nothing is auto-confirmed; a human decision publishes the formal link.
    The work runs inside a Job row so progress / cancellation are observable.
    """
    thesis = db.get(Thesis, thesis_id)
    if thesis is None:
        raise NotFoundError(f"thesis {thesis_id} not found")
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
    try:
        proposal_ids = EvidenceProposer(client).propose(thesis_id, db)
    except ValueError as exc:
        jobs.finish(job, status="failed", error=str(exc))
        commit_or_rollback(db)
        raise NotFoundError(str(exc)) from exc
    jobs.progress(job, step="proposed", progress=100)
    jobs.finish(job, status="succeeded", step="proposed")
    commit_or_rollback(db)
    return ProposeResponse(
        thesis_id=str(thesis_id),
        mode="mock" if client._mock else client.model_version,
        job_id=str(job.id),
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
    "/{document_version_id}/extract",
    response_model=ExtractResponse,
    status_code=status.HTTP_201_CREATED,
)
def extract_statements(
    document_version_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Run the extract step over one document version.

    Append-only: statements are added, never replaced.  The engine script
    feeds only pending versions (spans present, no statements yet); calling
    this on an already-extracted version will append duplicates.
    """
    version = db.get(DocumentVersion, document_version_id)
    if version is None:
        raise NotFoundError(f"document version {document_version_id} not found")
    client = LLMClient.from_env()
    statements = StatementExtractor(client).extract(document_version_id, db)
    commit_or_rollback(db)
    # Honest reason when no statements were produced — distinguishes
    # "nothing to extract" from "LLM refused / blank input".
    reason = _extract_reason(db, document_version_id, statements)
    return ExtractResponse(
        document_version_id=str(document_version_id),
        mode="mock" if client._mock else client.model_version,
        statement_count=len(statements),
        reason=reason,
        statements=[
            ExtractStatementDTO(
                id=str(s.id),
                kind=s.kind,
                normalized_text=s.normalized_text,
                observed_period=(
                    s.observed_period.isoformat() if s.observed_period else None
                ),
            )
            for s in statements
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
