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
    EventResearchFactorDraft,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import EvidenceLink, Thesis
from app.models.operational import EventResearchLifecycle, ResearchRun
from app.services.auto_research import AutoResearchService
from app.services.event_research_factors import normalize_event_research_factors


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
        try:
            normalized = normalize_event_research_factors(factors)
        except ValueError as exc:
            raise ValidationFailedError(str(exc)) from exc
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
        if previous is None:
            previous = self._backfill_legacy_scope(case_id)
        previous_factors = self._factors_for(previous.id) if previous else []
        retained = set(previous_factors).intersection(normalized)
        removed = set(previous_factors).difference(normalized)
        now = _utcnow()
        scope = EventResearchScopeVersion(
            research_case_id=case_id,
            version=(previous.version if previous else 0) + 1,
            changed_by=changed_by.strip(),
            change_summary="Updated event research factors",
            created_at=now,
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
        active_theses = self._sync_active_factor_theses(
            case_id, normalized, changed_by.strip(), now
        )
        reviewed_evidence = list(
            self._session.execute(
                select(EvidenceLink, Thesis)
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .where(Thesis.research_case_id == case_id)
                .where(EvidenceLink.review_state == "reviewed")
            )
        )
        active_factors = set(normalized)
        for link, thesis in reviewed_evidence:
            is_mapped = thesis.statement in active_factors
            self._session.add(
                EventResearchScopeEvidenceAssignment(
                    scope_version_id=scope.id,
                    evidence_link_id=link.id,
                    factor_statement=thesis.statement if is_mapped else None,
                    disposition="mapped" if is_mapped else "unmapped",
                    created_at=now,
                )
            )
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        if lifecycle is not None:
            self._continue_research_if_needed(lifecycle, active_theses, now)
        self._session.flush()
        return UpdatedEventResearchScope(
            version=scope.version,
            factors=normalized,
            reclassified_evidence_count=sum(
                thesis.statement in retained for _, thesis in reviewed_evidence
            ),
            unmapped_evidence_count=sum(
                thesis.statement in removed for _, thesis in reviewed_evidence
            ),
        )

    def _backfill_legacy_scope(
        self, case_id: uuid.UUID
    ) -> EventResearchScopeVersion | None:
        drafts = list(
            self._session.scalars(
                select(EventResearchFactorDraft)
                .where(EventResearchFactorDraft.research_case_id == case_id)
                .order_by(EventResearchFactorDraft.position)
            )
        )
        if not drafts:
            return None
        scope = EventResearchScopeVersion(
            research_case_id=case_id,
            version=1,
            changed_by=drafts[0].created_by,
            change_summary="Backfilled initial event research factors",
            created_at=drafts[0].created_at,
        )
        self._session.add(scope)
        self._session.flush()
        for draft in drafts:
            self._session.add(
                EventResearchScopeFactor(
                    scope_version_id=scope.id,
                    statement=draft.statement,
                    position=draft.position,
                )
            )
        self._session.flush()
        return scope

    def _sync_active_factor_theses(
        self,
        case_id: uuid.UUID,
        factors: list[str],
        changed_by: str,
        created_at: datetime,
    ) -> list[Thesis]:
        theses: list[Thesis] = []
        for statement in factors:
            thesis = self._session.scalar(
                select(Thesis)
                .where(Thesis.research_case_id == case_id)
                .where(Thesis.statement == statement)
                .order_by(Thesis.created_at, Thesis.id)
                .limit(1)
            )
            if thesis is None:
                thesis = Thesis(
                    research_case_id=case_id,
                    statement=statement,
                    created_by=changed_by,
                    created_at=created_at,
                    creator_type="human",
                    review_state="confirmed",
                )
                self._session.add(thesis)
                self._session.flush()
            theses.append(thesis)
        return theses

    def _continue_research_if_needed(
        self,
        lifecycle: EventResearchLifecycle,
        active_theses: list[Thesis],
        now: datetime,
    ) -> None:
        current_run = (
            self._session.get(ResearchRun, lifecycle.active_run_id)
            if lifecycle.active_run_id is not None
            else None
        )
        should_start_successor = (
            lifecycle.status in {"awaiting_scope", "exhausted"}
            or current_run is None
            or current_run.status not in {"queued", "running"}
        )
        if should_start_successor:
            successor = AutoResearchService(self._session).start(
                lifecycle.research_case_id,
                max_rounds=current_run.max_rounds if current_run is not None else 3,
                budget=current_run.budget if current_run is not None else 100,
                commit=False,
                thesis_ids=[thesis.id for thesis in active_theses],
            )
            lifecycle.status = "continuing"
            lifecycle.active_run_id = successor.id
            lifecycle.current_round = min(lifecycle.current_round + 1, 3)
            lifecycle.status_summary = "已更新因素，正在重新归类证据并继续检索"
            lifecycle.current_gap = "已更新因素，正在重新归类证据"
            lifecycle.next_human_action = None
            lifecycle.updated_at = now
            return
        lifecycle.status_summary = "已更新因素，正在重新归类证据"
        lifecycle.current_gap = "已更新因素，正在重新归类证据"
        lifecycle.next_human_action = None
        lifecycle.updated_at = now

    def _factors_for(self, scope_version_id: uuid.UUID) -> list[str]:
        return list(
            self._session.scalars(
                select(EventResearchScopeFactor.statement)
                .where(EventResearchScopeFactor.scope_version_id == scope_version_id)
                .order_by(EventResearchScopeFactor.position)
            )
        )
