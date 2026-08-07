"""Append-only event-research scope snapshots and evidence mapping counts."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import EvidenceLink, Thesis


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class UpdatedEventResearchScope:
    version: int
    factors: list[str]
    reclassified_evidence_count: int
    unmapped_evidence_count: int


class EventResearchScopeService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def update(
        self, case_id: uuid.UUID, factors: list[str], changed_by: str
    ) -> UpdatedEventResearchScope:
        normalized = self._normalize_factors(factors)
        if not changed_by.strip():
            raise ValidationFailedError("changed_by must not be empty")
        if self._session.scalar(
            select(EventResearchBrief.id).where(
                EventResearchBrief.research_case_id == case_id
            )
        ) is None:
            raise NotFoundError("event research case not found")

        previous = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        previous_factors = self._factors_for(previous.id) if previous else []
        retained = set(previous_factors).intersection(normalized)
        removed = set(previous_factors).difference(normalized)
        scope = EventResearchScopeVersion(
            research_case_id=case_id,
            version=(previous.version if previous else 0) + 1,
            changed_by=changed_by.strip(),
            change_summary="Updated event research factors",
            created_at=_utcnow(),
        )
        self._session.add(scope)
        self._session.flush()
        for position, statement in enumerate(normalized, start=1):
            self._session.add(
                EventResearchScopeFactor(
                    scope_version_id=scope.id,
                    statement=statement,
                    position=position,
                )
            )
        self._session.flush()
        return UpdatedEventResearchScope(
            version=scope.version,
            factors=normalized,
            reclassified_evidence_count=self._reviewed_evidence_count(case_id, retained),
            unmapped_evidence_count=self._reviewed_evidence_count(case_id, removed),
        )

    def _factors_for(self, scope_version_id: uuid.UUID) -> list[str]:
        return list(
            self._session.scalars(
                select(EventResearchScopeFactor.statement)
                .where(EventResearchScopeFactor.scope_version_id == scope_version_id)
                .order_by(EventResearchScopeFactor.position)
            )
        )

    def _reviewed_evidence_count(
        self, case_id: uuid.UUID, statements: set[str]
    ) -> int:
        if not statements:
            return 0
        return len(
            self._session.scalars(
                select(EvidenceLink.id)
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .where(Thesis.research_case_id == case_id)
                .where(Thesis.statement.in_(statements))
                .where(EvidenceLink.review_state == "reviewed")
            ).all()
        )

    @staticmethod
    def _normalize_factors(factors: list[str]) -> list[str]:
        if not 3 <= len(factors) <= 5:
            raise ValidationFailedError("event research scope requires 3 to 5 factors")
        normalized = [factor.strip() for factor in factors]
        if any(not factor for factor in normalized):
            raise ValidationFailedError("event research scope factors must not be empty")
        if len(set(normalized)) != len(normalized):
            raise ValidationFailedError("event research scope factors must be unique")
        return normalized
