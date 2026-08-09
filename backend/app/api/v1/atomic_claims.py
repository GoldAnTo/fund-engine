"""Inspectable human-review queue for source-grounded atomic claims."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.commands.common import commit_or_rollback, translate_validation
from app.db import get_db
from app.models.ledger import (
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseDocumentVersion,
    DocumentVersion,
    SourceSpan,
    SourceStatement,
)
from app.schemas.v1.commands import (
    AtomicClaimCandidateDTO,
    AtomicClaimQueueResponse,
    AtomicClaimReviewDTO,
    AtomicClaimReviewRequest,
    PublishedSourceStatementDTO,
)
from app.services.atomic_claims import AtomicClaimService
from app.repositories.operational import TaskRepository
from app.api.v1.tenant_context import require_research_tenant
from app.services.case_tenant_access import CaseTenantAccess
from app.models.ledger import CaseTenantAdmission


router = APIRouter(
    tags=["atomic-claim-review-v1"], dependencies=[Depends(require_research_tenant)]
)


def _require_case(db: Session, case_id: uuid.UUID, tenant_id: str) -> None:
    CaseTenantAccess(db).require_case(case_id, tenant_id)


def _require_candidate_tenant(db: Session, candidate_id: uuid.UUID, tenant_id: str) -> None:
    """A global candidate can be reviewed only when every admitted Case using
    its frozen document belongs to the caller's tenant.

    This prevents one tenant's irreversible review from changing another
    tenant's view of a deduplicated source document.
    """
    tenants = set(
        db.scalars(
            select(CaseTenantAdmission.tenant_id)
            .join(
                CaseDocumentVersion,
                CaseDocumentVersion.research_case_id
                == CaseTenantAdmission.research_case_id,
            )
            .join(DocumentVersion, DocumentVersion.id == CaseDocumentVersion.document_version_id)
            .join(SourceSpan, SourceSpan.document_version_id == DocumentVersion.id)
            .join(AtomicClaimCandidate, AtomicClaimCandidate.source_span_id == SourceSpan.id)
            .where(AtomicClaimCandidate.id == candidate_id)
        )
    )
    if tenants != {tenant_id}:
        # Hide both foreign candidates and shared cross-tenant candidates. The
        # latter require a future Case-scoped review record, not a global one.
        from app.errors import NotFoundError
        raise NotFoundError("atomic claim candidate not found")


def _statement_dto(value: SourceStatement | None) -> PublishedSourceStatementDTO | None:
    if value is None:
        return None
    return PublishedSourceStatementDTO(
        id=str(value.id),
        normalized_text=value.normalized_text,
        kind=value.kind,
        observed_period=value.observed_period,
        created_at=value.created_at,
    )


def _review_dto(db: Session, value: AtomicClaimReview) -> AtomicClaimReviewDTO:
    return AtomicClaimReviewDTO(
        id=str(value.id),
        outcome=value.outcome,
        reviewer=value.reviewer,
        reason=value.reason,
        published_source_statement=_statement_dto(
            db.get(SourceStatement, value.published_source_statement_id)
            if value.published_source_statement_id
            else None
        ),
        created_at=value.created_at,
    )


@router.get(
    "/research-cases/{case_id}/atomic-claims",
    response_model=AtomicClaimQueueResponse,
)
def list_atomic_claims(
    case_id: uuid.UUID,
    review_state: str | None = Query(default=None, pattern="^(awaiting_review|confirmed|modified|rejected)$"),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    _require_case(db, case_id, tenant_id)
    rows = db.execute(
        select(AtomicClaimCandidate, SourceSpan, DocumentVersion)
        .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
        .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
        .join(CaseDocumentVersion, CaseDocumentVersion.document_version_id == DocumentVersion.id)
        .where(CaseDocumentVersion.research_case_id == case_id)
        .order_by(AtomicClaimCandidate.created_at.desc())
        .limit(limit)
    ).all()
    items: list[AtomicClaimCandidateDTO] = []
    for candidate, span, document in rows:
        reviews = list(db.scalars(
            select(AtomicClaimReview)
            .where(AtomicClaimReview.atomic_claim_candidate_id == candidate.id)
            .order_by(AtomicClaimReview.created_at.asc())
        ))
        latest = reviews[-1] if reviews else None
        state = latest.outcome if latest else "awaiting_review"
        if review_state and state != review_state:
            continue
        published = (
            db.get(SourceStatement, latest.published_source_statement_id)
            if latest and latest.published_source_statement_id
            else None
        )
        items.append(AtomicClaimCandidateDTO(
            id=str(candidate.id),
            source_span_id=str(candidate.source_span_id),
            document_version_id=str(document.id),
            document_source_url=document.source_url,
            locator=dict(span.locator),
            quote=candidate.quote,
            quote_start=candidate.quote_start,
            quote_end=candidate.quote_end,
            quote_sha256=candidate.quote_sha256,
            normalized_text=candidate.normalized_text,
            claim_type=candidate.claim_type,
            assertion_actor=candidate.assertion_actor,
            authority_level=candidate.authority_level,
            structured_fields=dict(candidate.structured_fields),
            validation_result=dict(candidate.validation_result),
            created_at=candidate.created_at,
            review_state=state,
            review_history=[_review_dto(db, review) for review in reviews],
            published_source_statement=_statement_dto(published),
        ))
    return AtomicClaimQueueResponse(items=items)


@router.post(
    "/atomic-claims/{candidate_id}/reviews",
    response_model=AtomicClaimReviewDTO,
    status_code=status.HTTP_201_CREATED,
)
def review_atomic_claim(
    candidate_id: uuid.UUID,
    payload: AtomicClaimReviewRequest,
    db: Session = Depends(get_db),
    tenant_id: str = Depends(require_research_tenant),
):
    _require_candidate_tenant(db, candidate_id, tenant_id)
    review = translate_validation(
        AtomicClaimService(db).review,
        candidate_id,
        outcome=payload.outcome,
        reviewer=payload.reviewer,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        normalized_text=payload.normalized_text,
        observed_period=payload.observed_period,
    )
    TaskRepository(db).close_review_task(
        "review_atomic_claim",
        "atomic_claim_candidate",
        candidate_id,
    )
    commit_or_rollback(db)
    return _review_dto(db, review)
