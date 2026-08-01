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
from app.models.ledger import DocumentVersion
from app.services.compliance import ComplianceRefusedError
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
        # Refused text never reaches the ledger; drop the half-frozen
        # snapshot + failed AIRun and surface the refusal as 422.
        db.rollback()
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

    Recall is cutoff-safe (only statements visible before this run started),
    and every proposed link enters the review queue as machine_generated —
    nothing is auto-confirmed.
    """
    client = LLMClient.from_env()
    try:
        links = EvidenceProposer(client).propose(thesis_id, db)
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    commit_or_rollback(db)
    return ProposeResponse(
        thesis_id=str(thesis_id),
        mode="mock" if client._mock else client.model_version,
        link_count=len(links),
        links=[
            ProposedLinkDTO(
                link_id=str(link.id),
                source_statement_id=str(link.source_statement_id),
                role=link.role,
                reason=link.reason,
                scope=dict(link.scope),
            )
            for link in links
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
    return ExtractResponse(
        document_version_id=str(document_version_id),
        mode="mock" if client._mock else client.model_version,
        statement_count=len(statements),
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
