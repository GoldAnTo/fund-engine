"""Persistence operations for the mutable research-preparation projection."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.research_preparation import ArtifactKind, PreparationStep
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
        lock_event_scope_case(self._session, case_id)
        return self._session.scalar(
            select(ResearchPreparation)
            .where(ResearchPreparation.research_case_id == case_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def append_event(
        self,
        preparation: ResearchPreparation,
        *,
        type: str,
        step: str | None,
        message: str | None,
        detail: dict[str, object],
    ) -> ResearchPreparationEvent:
        # The preparation row is the per-case serialization point.  Re-lock it
        # here so direct repository callers keep the append-only sequence safe.
        self._session.scalar(
            select(ResearchPreparation)
            .where(ResearchPreparation.id == preparation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
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
        kind: ArtifactKind,
        input_fingerprint: str,
        payload: dict[str, object],
    ) -> ResearchPreparationArtifact:
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
            payload=payload,
            state="current",
            invalidated_reason=None,
            created_at=_utcnow(),
        )
        self._session.add(artifact)
        self._session.flush()
        return artifact

    def queue_step_job(
        self, preparation: ResearchPreparation, step: PreparationStep
    ) -> Job:
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
