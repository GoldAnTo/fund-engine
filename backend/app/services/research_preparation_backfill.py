"""Idempotent, no-provider backfill for the research-preparation stage."""
from __future__ import annotations

import uuid
from sqlalchemy import exists, func, select, tuple_
from sqlalchemy.orm import Session

from app.ai.research_preparation import preparation_ai_input_is_available
from app.domain.research_preparation import preparation_input_fingerprint
from app.models.event_research import EventResearchScopeVersion
from app.models.ledger import (
    AtomicClaimCandidate,
    AtomicClaimReview,
    CaseDocumentVersion,
    CaseTenantAdmission,
    DocumentVersion,
    ResearchCase,
    SourceSpan,
)
from app.models.operational import EventResearchLifecycle, Job, ResearchRun
from app.models.research_preparation import ResearchPreparation
from app.services.research_preparation import ResearchPreparationService


class ResearchPreparationBackfill:
    """Create preparation projections for old, admitted event cases only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue_eligible(self, *, limit: int = 100) -> list[ResearchPreparation]:
        if limit <= 0:
            return []
        page_size = max(100, limit)
        cursor: tuple[object, uuid.UUID] | None = None
        preparations: list[ResearchPreparation] = []
        while len(preparations) < limit:
            query = (
                select(ResearchCase.id, ResearchCase.created_at)
            .join(CaseTenantAdmission, CaseTenantAdmission.research_case_id == ResearchCase.id)
            .join(EventResearchLifecycle, EventResearchLifecycle.research_case_id == ResearchCase.id)
            .outerjoin(ResearchPreparation, ResearchPreparation.research_case_id == ResearchCase.id)
            .where(
                ResearchPreparation.id.is_(None),
                EventResearchLifecycle.status != "published",
                exists(
                    select(CaseDocumentVersion.id).where(
                        CaseDocumentVersion.research_case_id == ResearchCase.id,
                        CaseDocumentVersion.document_version_id
                        == CaseTenantAdmission.initial_document_version_id,
                    )
                ),
                ~exists(select(ResearchRun.id).where(ResearchRun.research_case_id == ResearchCase.id)),
                exists(select(EventResearchScopeVersion.id).where(EventResearchScopeVersion.research_case_id == ResearchCase.id)),
            )
            .order_by(ResearchCase.created_at, ResearchCase.id)
            .limit(page_size)
            )
            if cursor is not None:
                query = query.where(
                    tuple_(ResearchCase.created_at, ResearchCase.id) > cursor
                )
            page = list(self._session.execute(query))
            if not page:
                break
            for case_id, _created_at in page:
                preparation = self._enqueue_case(case_id)
                if preparation is not None:
                    preparations.append(preparation)
                    if len(preparations) == limit:
                        break
            cursor = (page[-1].created_at, page[-1].id)
        return preparations

    def _enqueue_case(self, case_id: uuid.UUID) -> ResearchPreparation | None:
        """Recheck every eligibility predicate while the stable Case row is locked."""
        service = ResearchPreparationService(self._session)
        # The stable Case lock serializes this check with preparation creation
        # and scope changes.  ``create_for_case`` takes the same lock again
        # before it writes, so this is safe on both PostgreSQL and SQLite.
        service._lock(case_id)
        admission = self._session.scalar(
            select(CaseTenantAdmission).where(CaseTenantAdmission.research_case_id == case_id)
        )
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc(), EventResearchScopeVersion.id.desc())
            .limit(1)
        )
        has_case_document = admission is not None and self._session.scalar(
            select(CaseDocumentVersion.id).where(
                CaseDocumentVersion.research_case_id == case_id,
                CaseDocumentVersion.document_version_id == admission.initial_document_version_id,
            ).limit(1)
        ) is not None
        if (
            admission is None
            or lifecycle is None
            or lifecycle.status == "published"
            or scope is None
            or not has_case_document
            or self._session.scalar(select(ResearchRun.id).where(ResearchRun.research_case_id == case_id).limit(1)) is not None
            or not self._ai_input_is_available(admission.initial_document_version_id)
        ):
            return None

        fingerprint = preparation_input_fingerprint(admission.initial_document_version_id, scope.id)
        preparation = service.create_for_case(case_id, input_fingerprint=fingerprint, actor="research_preparation_backfill")
        # Another transaction may have created it while this batch was read.
        # In that case the idempotent service return is already the desired row.
        if preparation.parse_claims_state != "queued":
            return preparation

        candidates = list(self._session.scalars(
            select(AtomicClaimCandidate)
            .join(SourceSpan, SourceSpan.id == AtomicClaimCandidate.source_span_id)
            .where(SourceSpan.document_version_id == admission.initial_document_version_id)
            .order_by(AtomicClaimCandidate.id)
        ))
        if not candidates:
            return preparation
        candidate_ids = [candidate.id for candidate in candidates]
        every_reviewed = self._every_candidate_reviewed(candidate_ids)
        preparation = service.reuse_existing_claim_candidates(
            case_id,
            candidate_ids=candidate_ids,
            every_candidate_reviewed=every_reviewed,
        )
        # ``create_for_case`` correctly queues parse by default.  Reused
        # immutable candidates need no provider invocation, so remove only
        # that freshly-created queued job before the transaction commits.
        jobs = list(self._session.scalars(
            select(Job).where(
                Job.kind == "prepare_research",
                Job.target_type == "research_preparation",
                Job.target_id == preparation.id,
                Job.status == "queued",
                Job.correlation_id == f"{preparation.id}:{preparation.version}:parse_claims",
            )
        ))
        for job in jobs:
            self._session.delete(job)
        return preparation

    def _every_candidate_reviewed(self, candidate_ids: list[uuid.UUID]) -> bool:
        if not candidate_ids:
            return False
        ranked_reviews = (
            select(
                AtomicClaimReview.atomic_claim_candidate_id.label("candidate_id"),
                AtomicClaimReview.id.label("review_id"),
                func.row_number().over(
                    partition_by=AtomicClaimReview.atomic_claim_candidate_id,
                    order_by=(
                        AtomicClaimReview.created_at.desc(),
                        AtomicClaimReview.id.desc(),
                    ),
                ).label("rank"),
            )
            .where(AtomicClaimReview.atomic_claim_candidate_id.in_(candidate_ids))
            .subquery()
        )
        reviewed_ids = set(self._session.scalars(
            select(ranked_reviews.c.candidate_id).where(ranked_reviews.c.rank == 1)
        ))
        return reviewed_ids == set(candidate_ids)

    def _ai_input_is_available(self, document_version_id: uuid.UUID) -> bool:
        document = self._session.get(DocumentVersion, document_version_id)
        return document is not None and preparation_ai_input_is_available(
            self._session, document
        )
