"""Create and publish bounded, evidence-linked event conclusions."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ValidationFailedError
from app.models.event_research import (
    EventResearchConclusion,
    EventResearchScopeVersion,
)
from app.models.ledger import EvidenceLink, Thesis
from app.services.event_research_scope_evidence import (
    current_mapped_evidence_ids,
    lock_event_research_lifecycle,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EventConclusionService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def latest(self, case_id: uuid.UUID) -> EventResearchConclusion | None:
        return self._session.scalar(
            select(EventResearchConclusion)
            .where(EventResearchConclusion.research_case_id == case_id)
            .order_by(EventResearchConclusion.created_at.desc())
            .limit(1)
        )

    def create_draft(self, case_id: uuid.UUID) -> EventResearchConclusion:
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        mapped_evidence_ids = current_mapped_evidence_ids(self._session, case_id)
        evidence = list(
            self._session.execute(
                select(EvidenceLink, Thesis)
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .where(Thesis.research_case_id == case_id)
                .where(EvidenceLink.review_state == "reviewed")
                .where(EvidenceLink.id.in_(mapped_evidence_ids))
                .order_by(EvidenceLink.available_at.desc())
            )
        )
        scores: dict[str, int] = defaultdict(int)
        for link, thesis in evidence:
            if link.role == "supports":
                scores[thesis.statement] += 1
            elif link.role == "contradicts":
                scores[thesis.statement] -= 1
        primary_factor = max(scores, key=scores.get) if scores and max(scores.values()) > 0 else None
        if primary_factor is None:
            text = "当前尚无足以支持主要因素判断的已审核证据；本研究不能给出因果结论。"
        else:
            text = (
                f"在当前已审核证据范围内，{primary_factor} 是对本次市场反应最受支持的解释。"
                "这是一项可复核的暂定判断，不等同于唯一因果结论；反证和覆盖范围见下方证据。"
            )
        draft = EventResearchConclusion(
            research_case_id=case_id,
            scope_version_id=scope.id if scope is not None else None,
            state="ai_draft",
            text=text,
            primary_factor=primary_factor,
            evidence_link_ids=[str(link.id) for link, _ in evidence],
            based_on_conclusion_id=None,
            reviewer=None,
            created_at=_utcnow(),
        )
        self._session.add(draft)
        self._session.flush()
        return draft

    def publish(
        self, case_id: uuid.UUID, *, text: str, reviewer: str
    ) -> EventResearchConclusion:
        # Serialize with scope updates and worker lifecycle projections before
        # reading a draft or mutating the lifecycle row.
        lifecycle = lock_event_research_lifecycle(self._session, case_id)
        if lifecycle is None or lifecycle.status != "draft_ready":
            raise ValidationFailedError("event conclusion is not ready to publish")
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        draft = (
            self._session.scalar(
                select(EventResearchConclusion)
                .where(EventResearchConclusion.research_case_id == case_id)
                .where(EventResearchConclusion.state == "ai_draft")
                .where(EventResearchConclusion.scope_version_id == scope.id)
                .order_by(EventResearchConclusion.created_at.desc())
                .limit(1)
            )
            if scope is not None
            else None
        )
        if draft is None:
            raise ValidationFailedError(
                "event conclusion draft is not for the current scope"
            )
        published = EventResearchConclusion(
            research_case_id=case_id,
            scope_version_id=draft.scope_version_id,
            state="published",
            text=text,
            primary_factor=draft.primary_factor,
            evidence_link_ids=draft.evidence_link_ids,
            based_on_conclusion_id=draft.id,
            reviewer=reviewer,
            created_at=_utcnow(),
        )
        self._session.add(published)
        if lifecycle is not None:
            lifecycle.status = "published"
            lifecycle.status_summary = "研究结论已人工确认并发布"
            lifecycle.current_gap = None
            lifecycle.next_human_action = None
            lifecycle.updated_at = _utcnow()
        self._session.flush()
        return published
