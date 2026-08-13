"""Persistence operations for the mutable research-preparation projection."""
from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.research_preparation import ArtifactKind, PreparationStep
from app.errors import ConflictError, NotFoundError
from app.models.ledger import (
    AtomicClaimCandidate,
    CaseTenantAdmission,
    DocumentVersion,
    SourceSpan,
)
from app.models.operational import Job
from app.models.research_preparation import (
    ResearchPreparation,
    ResearchPreparationArtifact,
    ResearchPreparationEvent,
)
from app.repositories.operational import JobRepository
from app.services.event_research_scope_evidence import lock_event_scope_case


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchPreparationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._jobs = JobRepository(session)

    def lock_for_case(self, case_id: uuid.UUID) -> ResearchPreparation | None:
        """Lock the stable Case row before reading the preparation projection."""
        if lock_event_scope_case(self._session, case_id) is None:
            raise NotFoundError(f"research case {case_id} not found")
        return self._session.scalar(
            select(ResearchPreparation)
            .where(ResearchPreparation.research_case_id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def lock_preparation_for_candidate_review(
        self, candidate_id: uuid.UUID
    ) -> list[ResearchPreparation]:
        """Lock every current preparation affected by a claim review.

        Candidate lookup is deliberately read-only until it has established a
        current parse artifact for the candidate.  When such an artifact
        exists, each Case → preparation pair is locked in canonical order.
        This matches protocol confirmation's lock order, so a review cannot
        interleave after that command has read its context.  A shared frozen
        document may legitimately map one candidate into multiple current
        preparations.
        """
        document_version_id = self._session.scalar(
            select(SourceSpan.document_version_id)
            .join(
                AtomicClaimCandidate,
                AtomicClaimCandidate.source_span_id == SourceSpan.id,
            )
            .where(AtomicClaimCandidate.id == candidate_id)
        )
        if document_version_id is None:
            return []
        if self._session.get(DocumentVersion, document_version_id) is None:
            return []
        candidate_case_ids = list(
            self._session.scalars(
                select(CaseTenantAdmission.research_case_id).where(
                    CaseTenantAdmission.initial_document_version_id
                    == document_version_id
                )
            )
        )
        if not candidate_case_ids:
            return []
        current_artifacts = list(
            self._session.scalars(
                select(ResearchPreparationArtifact)
                .join(
                    ResearchPreparation,
                    ResearchPreparation.id
                    == ResearchPreparationArtifact.research_preparation_id,
                )
                .where(
                    ResearchPreparation.research_case_id.in_(candidate_case_ids),
                    ResearchPreparationArtifact.kind == "atomic_claim_candidates",
                    ResearchPreparationArtifact.state == "current",
                )
            )
        )
        candidate_preparation_ids = {
            artifact.research_preparation_id
            for artifact in current_artifacts
            if self._artifact_includes_candidate(artifact, candidate_id)
        }
        if not candidate_preparation_ids:
            return []
        mappings = sorted(
            set(
                self._session.execute(
                    select(
                        ResearchPreparation.research_case_id,
                        ResearchPreparation.id,
                    ).where(ResearchPreparation.id.in_(candidate_preparation_ids))
                ).all()
            ),
            key=lambda mapping: (str(mapping[0]), str(mapping[1])),
        )
        if not mappings:
            return []

        locked_preparations: list[ResearchPreparation] = []
        for case_id, preparation_id in mappings:
            if lock_event_scope_case(self._session, case_id) is None:
                continue
            try:
                preparation = self._lock_preparation_after_case_lock(
                    case_id, preparation_id
                )
            except ConflictError:
                # The preliminary mapping became stale before its pair was
                # locked. Revalidation below remains authoritative.
                continue
            admission = self._session.scalar(
                select(CaseTenantAdmission).where(
                    CaseTenantAdmission.research_case_id == case_id,
                    CaseTenantAdmission.initial_document_version_id
                    == document_version_id,
                )
            )
            artifact = self._session.scalar(
                select(ResearchPreparationArtifact)
                .where(
                    ResearchPreparationArtifact.research_preparation_id
                    == preparation.id,
                    ResearchPreparationArtifact.kind == "atomic_claim_candidates",
                    ResearchPreparationArtifact.state == "current",
                )
                .with_for_update()
            )
            if (
                admission is not None
                and artifact is not None
                and self._artifact_includes_candidate(artifact, candidate_id)
            ):
                locked_preparations.append(preparation)
        if not locked_preparations:
            return []
        return locked_preparations

    @staticmethod
    def _artifact_includes_candidate(
        artifact: ResearchPreparationArtifact, candidate_id: uuid.UUID
    ) -> bool:
        candidates = artifact.payload.get("candidates")
        return isinstance(candidates, list) and any(
            isinstance(item, dict) and item.get("candidate_id") == str(candidate_id)
            for item in candidates
        )

    def _lock_case_then_preparation(
        self, research_case_id: uuid.UUID, preparation_id: uuid.UUID
    ) -> ResearchPreparation:
        """Take the stable Case lock before mutating its preparation row."""
        case = lock_event_scope_case(self._session, research_case_id)
        if case is None:
            raise ConflictError(f"research case {research_case_id} not found")
        return self._lock_preparation_after_case_lock(
            research_case_id, preparation_id
        )

    def _lock_preparation_after_case_lock(
        self, research_case_id: uuid.UUID, preparation_id: uuid.UUID
    ) -> ResearchPreparation:
        """Lock one preparation after the caller has locked its Case."""
        preparation = self._session.scalar(
            select(ResearchPreparation)
            .where(
                ResearchPreparation.id == preparation_id,
                ResearchPreparation.research_case_id == research_case_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if preparation is None:
            raise ConflictError("preparation does not belong to research case")
        return preparation

    def append_event(
        self,
        preparation: ResearchPreparation,
        *,
        research_case_id: uuid.UUID,
        type: str,
        step: str | None,
        message: str | None,
        detail: dict[str, object],
    ) -> ResearchPreparationEvent:
        preparation = self._lock_case_then_preparation(
            research_case_id, preparation.id
        )
        last_seq = self._session.scalar(
            select(func.max(ResearchPreparationEvent.seq)).where(
                ResearchPreparationEvent.research_preparation_id == preparation.id
            )
        )
        event = ResearchPreparationEvent(
            research_preparation_id=preparation.id,
            seq=(last_seq or 0) + 1,
            type=type,
            step=step,
            message=message,
            detail=detail,
            created_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def current_artifact(
        self, preparation_id: uuid.UUID, kind: ArtifactKind
    ) -> ResearchPreparationArtifact | None:
        return self._session.scalar(
            select(ResearchPreparationArtifact).where(
                ResearchPreparationArtifact.research_preparation_id == preparation_id,
                ResearchPreparationArtifact.kind == kind,
                ResearchPreparationArtifact.state == "current",
            )
        )

    def append_artifact(
        self,
        preparation: ResearchPreparation,
        *,
        research_case_id: uuid.UUID,
        kind: ArtifactKind,
        input_fingerprint: str,
        payload: dict[str, object],
        context_fingerprint: str | None = None,
    ) -> ResearchPreparationArtifact:
        if context_fingerprint is not None and re.fullmatch(
            r"[0-9a-f]{64}", context_fingerprint
        ) is None:
            raise ConflictError("preparation context fingerprint is invalid")
        if kind == "atomic_claim_candidates" and context_fingerprint is not None:
            raise ConflictError("claim candidate artifacts cannot carry a context fingerprint")
        preparation = self._lock_case_then_preparation(
            research_case_id, preparation.id
        )
        current = self._session.scalar(
            select(ResearchPreparationArtifact)
            .where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id,
                ResearchPreparationArtifact.kind == kind,
                ResearchPreparationArtifact.state == "current",
            )
            .with_for_update()
        )
        if current is not None:
            current.state = "superseded"
            self._session.flush()
        last_sequence = self._session.scalar(
            select(func.max(ResearchPreparationArtifact.sequence)).where(
                ResearchPreparationArtifact.research_preparation_id == preparation.id
            )
        )
        artifact = ResearchPreparationArtifact(
            research_preparation_id=preparation.id,
            kind=kind,
            sequence=(last_sequence or 0) + 1,
            preparation_version=preparation.version,
            input_fingerprint=input_fingerprint,
            context_fingerprint=context_fingerprint,
            payload=payload,
            state="current",
            invalidated_reason=None,
            created_at=_utcnow(),
        )
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def queue_step_job(
        self,
        preparation: ResearchPreparation,
        *,
        research_case_id: uuid.UUID,
        step: PreparationStep,
    ) -> Job:
        preparation = self._lock_case_then_preparation(
            research_case_id, preparation.id
        )
        correlation_id = f"{preparation.id}:{preparation.version}:{step}"
        active = self._session.scalar(
            select(Job)
            .where(
                Job.kind == "prepare_research",
                Job.target_type == "research_preparation",
                Job.target_id == preparation.id,
                Job.correlation_id == correlation_id,
                Job.status.in_(("queued", "running")),
            )
            .order_by(Job.created_at, Job.id)
        )
        if active is not None:
            return active
        return self._jobs.add_job(
            kind="prepare_research",
            target_type="research_preparation",
            target_id=preparation.id,
            research_case_id=preparation.research_case_id,
            correlation_id=correlation_id,
        )
