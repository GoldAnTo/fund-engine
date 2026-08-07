"""Focused read models for the event research list and workbench."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.event_research import (
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchFactorDraft,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import DocumentVersion, EvidenceLink, SourceSpan, SourceStatement, Thesis
from app.models.operational import EventResearchLifecycle
from app.services.event_research_scope_evidence import current_mapped_evidence_ids
from app.schemas.v1.event_research import (
    EventConclusionDraftDTO,
    EventKeyEvidenceDTO,
    EventNextActionDTO,
    EventResearchFactorDTO,
    EventResearchLifecycleDTO,
    EventResearchListItemDTO,
    EventResearchListResponse,
    EventResearchScopeDTO,
    EventWorkbenchProgressDTO,
    EventWorkbenchDTO,
)
from app.services.event_review_queue import EventReviewQueueService


class EventResearchQueries:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list(self, *, status: str | None = None) -> EventResearchListResponse:
        stmt = (
            select(EventResearchBrief, EventResearchLifecycle)
            .join(
                EventResearchLifecycle,
                EventResearchLifecycle.research_case_id == EventResearchBrief.research_case_id,
            )
            .order_by(EventResearchLifecycle.updated_at.desc())
        )
        if status is not None:
            stmt = stmt.where(EventResearchLifecycle.status == status)
        return EventResearchListResponse(
            items=[self._list_item(brief, lifecycle) for brief, lifecycle in self._session.execute(stmt)]
        )

    def workbench(self, case_id: uuid.UUID) -> EventWorkbenchDTO:
        brief = self._session.scalar(
            select(EventResearchBrief)
            .where(EventResearchBrief.research_case_id == case_id)
            .order_by(EventResearchBrief.created_at.desc())
            .limit(1)
        )
        lifecycle = self._session.get(EventResearchLifecycle, case_id)
        if brief is None or lifecycle is None:
            raise NotFoundError("event research case not found")
        event = self._list_item(brief, lifecycle)
        scope = self._latest_scope(case_id)
        evidence = self._evidence(case_id)
        conclusion = self._conclusion(case_id, lifecycle, evidence)
        return EventWorkbenchDTO(
            event=event,
            lifecycle=self._lifecycle(lifecycle),
            conclusion=conclusion,
            factors=self._factors(case_id, scope, lifecycle.current_gap),
            evidence=evidence,
            progress=self._progress(case_id, lifecycle),
            scope=self._scope(scope, case_id),
            next_action=self._next_action(lifecycle),
        )

    def _conclusion(
        self,
        case_id: uuid.UUID,
        lifecycle: EventResearchLifecycle,
        evidence: list[EventKeyEvidenceDTO],
    ) -> EventConclusionDraftDTO:
        if lifecycle.status == "published":
            record = self._session.scalar(
                select(EventResearchConclusion)
                .where(EventResearchConclusion.research_case_id == case_id)
                .where(EventResearchConclusion.state == "published")
                .order_by(EventResearchConclusion.created_at.desc())
                .limit(1)
            )
            if record is not None:
                return EventConclusionDraftDTO(
                    state=record.state,
                    text=record.text,
                    citations=self._formal_evidence(case_id, record.evidence_link_ids),
                )
            return EventConclusionDraftDTO(
                state="published",
                text="结论已发布，正在载入可复核证据。",
                citations=self._formal_evidence(case_id),
            )
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        record = self._session.scalar(
            select(EventResearchConclusion)
            .where(EventResearchConclusion.research_case_id == case_id)
            .where(EventResearchConclusion.state == "ai_draft")
            .order_by(EventResearchConclusion.created_at.desc())
            .limit(1)
        )
        reviewed = self._formal_evidence(case_id)
        if record is not None and scope is not None and record.scope_version_id == scope.id:
            # The compact API DTO intentionally has no link id; the record's
            # immutable link-id snapshot is retained for audit, while the
            # visible citations remain limited to human-reviewed material.
            return EventConclusionDraftDTO(
                state=record.state, text=record.text, citations=reviewed
            )
        if record is not None:
            return EventConclusionDraftDTO(
                state="cannot_conclude",
                text="研究范围已更新，先前结论草案不再适用于当前因素。",
                citations=reviewed,
            )
        if lifecycle.status == "draft_ready":
            return EventConclusionDraftDTO(
                state="ai_draft",
                text="当前已进入结论复核：尚无足以支持主要因素判断的已审核证据；本研究不能给出因果结论。",
                citations=reviewed,
            )
        return EventConclusionDraftDTO(
            state="cannot_conclude",
            text="尚不能下结论：系统正在核验各项解释及其反证。",
            citations=reviewed,
        )

    @staticmethod
    def _list_item(
        brief: EventResearchBrief, lifecycle: EventResearchLifecycle
    ) -> EventResearchListItemDTO:
        return EventResearchListItemDTO(
            case_id=str(brief.research_case_id),
            event_title=brief.event_title,
            company_name=brief.company_name,
            ticker=brief.ticker,
            event_at=brief.event_at,
            lifecycle_status=lifecycle.status,
            status_summary=lifecycle.status_summary,
            next_human_action=lifecycle.next_human_action,
            updated_at=lifecycle.updated_at,
        )

    @staticmethod
    def _lifecycle(lifecycle: EventResearchLifecycle) -> EventResearchLifecycleDTO:
        return EventResearchLifecycleDTO(
            status=lifecycle.status,
            active_run_id=str(lifecycle.active_run_id) if lifecycle.active_run_id else None,
            current_round=lifecycle.current_round,
            status_summary=lifecycle.status_summary,
            current_gap=lifecycle.current_gap,
            next_human_action=lifecycle.next_human_action,
        )

    def _latest_scope(self, case_id: uuid.UUID) -> EventResearchScopeVersion | None:
        return self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )

    def _scope(
        self, scope: EventResearchScopeVersion | None, case_id: uuid.UUID
    ) -> EventResearchScopeDTO:
        if scope is not None:
            factors = list(
                self._session.scalars(
                    select(EventResearchScopeFactor.statement)
                    .where(EventResearchScopeFactor.scope_version_id == scope.id)
                    .order_by(EventResearchScopeFactor.position)
                )
            )
            unmapped_evidence_count = int(
                self._session.scalar(
                    select(func.count())
                    .select_from(EventResearchScopeEvidenceAssignment)
                    .where(EventResearchScopeEvidenceAssignment.scope_version_id == scope.id)
                    .where(EventResearchScopeEvidenceAssignment.disposition == "unmapped")
                )
                or 0
            )
            return EventResearchScopeDTO(
                version=scope.version,
                factors=factors,
                unmapped_evidence_count=unmapped_evidence_count,
            )
        # Only pre-scope migrations can reach this branch. New event cases
        # always create an immutable scope snapshot before work begins.
        factors = list(
            self._session.scalars(
                select(EventResearchFactorDraft.statement)
                .where(EventResearchFactorDraft.research_case_id == case_id)
                .order_by(EventResearchFactorDraft.position)
            )
        )
        return EventResearchScopeDTO(
            version=0,
            factors=factors,
            unmapped_evidence_count=0,
        )

    def _factors(
        self,
        case_id: uuid.UUID,
        scope: EventResearchScopeVersion | None,
        current_gap: str | None,
    ) -> list[EventResearchFactorDTO]:
        if scope is not None:
            factors = list(
                self._session.scalars(
                    select(EventResearchScopeFactor)
                    .where(EventResearchScopeFactor.scope_version_id == scope.id)
                    .order_by(EventResearchScopeFactor.position)
                )
            )
        else:
            factors = list(
                self._session.scalars(
                    select(EventResearchFactorDraft)
                    .where(EventResearchFactorDraft.research_case_id == case_id)
                    .order_by(EventResearchFactorDraft.position)
                )
            )
        result: list[EventResearchFactorDTO] = []
        for factor in factors[:5]:
            counts: dict[str, int] = {}
            if scope is not None:
                counts = dict(
                    self._session.execute(
                        select(EvidenceLink.role, func.count())
                        .join(
                            EventResearchScopeEvidenceAssignment,
                            EventResearchScopeEvidenceAssignment.evidence_link_id
                            == EvidenceLink.id,
                        )
                        .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                        .where(
                            EventResearchScopeEvidenceAssignment.scope_version_id
                            == scope.id,
                            EventResearchScopeEvidenceAssignment.disposition == "mapped",
                            EventResearchScopeEvidenceAssignment.factor_statement
                            == factor.statement,
                            Thesis.research_case_id == case_id,
                            Thesis.statement == factor.statement,
                            EvidenceLink.review_state == "reviewed",
                        )
                        .group_by(EvidenceLink.role)
                    ).all()
                )
            result.append(
                EventResearchFactorDTO(
                    statement=factor.statement,
                    position=factor.position,
                    reviewed_support_count=int(counts.get("supports", 0)),
                    reviewed_contradiction_count=int(counts.get("contradicts", 0)),
                    current_gap=current_gap,
                )
            )
        return result

    def _evidence(self, case_id: uuid.UUID) -> list[EventKeyEvidenceDTO]:
        rows = self._session.execute(
            select(EvidenceLink, Thesis, SourceStatement, SourceSpan, DocumentVersion)
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .join(SourceStatement, SourceStatement.id == EvidenceLink.source_statement_id)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .where(Thesis.research_case_id == case_id)
            .order_by(EvidenceLink.available_at.desc())
            .limit(50)
        )
        return [
            EventKeyEvidenceDTO(
                case_id=str(case_id),
                factor_statement=thesis.statement,
                role=link.role,
                review_state=link.review_state,
                source_title=document.title,
                source_url=document.source_url,
                excerpt=span.verbatim_text,
                locator=span.locator,
                available_at=link.available_at,
            )
            for link, thesis, statement, span, document in rows
        ]

    def _progress(
        self, case_id: uuid.UUID, lifecycle: EventResearchLifecycle
    ) -> EventWorkbenchProgressDTO:
        review_summary = EventReviewQueueService(self._session).review_queue(case_id).summary
        return EventWorkbenchProgressDTO(
            verified=len(current_mapped_evidence_ids(self._session, case_id)),
            pending=review_summary.pending,
            invalid_source=review_summary.invalid_source,
            current_gap=lifecycle.current_gap,
        )

    def _formal_evidence(
        self, case_id: uuid.UUID, evidence_link_ids: list[str] | None = None
    ) -> list[EventKeyEvidenceDTO]:
        mapped_evidence_ids = current_mapped_evidence_ids(self._session, case_id)
        if evidence_link_ids is not None:
            snapshot_ids = set(evidence_link_ids)
            mapped_evidence_ids = [
                evidence_id
                for evidence_id in mapped_evidence_ids
                if str(evidence_id) in snapshot_ids
            ]
        if not mapped_evidence_ids:
            return []
        rows = self._session.execute(
            select(EvidenceLink, Thesis, SourceStatement, SourceSpan, DocumentVersion)
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .join(SourceStatement, SourceStatement.id == EvidenceLink.source_statement_id)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .where(EvidenceLink.id.in_(mapped_evidence_ids))
            .where(EvidenceLink.review_state == "reviewed")
            .order_by(EvidenceLink.available_at.desc())
        )
        return [
            EventKeyEvidenceDTO(
                case_id=str(case_id),
                factor_statement=thesis.statement,
                role=link.role,
                review_state=link.review_state,
                source_title=document.title,
                source_url=document.source_url,
                excerpt=span.verbatim_text,
                locator=span.locator,
                available_at=link.available_at,
            )
            for link, thesis, statement, span, document in rows
        ]

    @staticmethod
    def _next_action(lifecycle: EventResearchLifecycle) -> EventNextActionDTO:
        if lifecycle.status == "awaiting_key_review":
            count = _leading_count(lifecycle.next_human_action)
            return EventNextActionDTO(
                kind="review_evidence",
                label=lifecycle.next_human_action or "审核关键证据",
                count=count,
            )
        if lifecycle.status == "draft_ready":
            return EventNextActionDTO(kind="review_conclusion", label="审核结论草案")
        if lifecycle.status in {"awaiting_scope", "exhausted", "cannot_conclude"}:
            return EventNextActionDTO(
                kind="edit_factors",
                label="编辑并继续自动研究",
            )
        if lifecycle.status == "published":
            return EventNextActionDTO(
                kind="view_conclusion_change", label="查看结论变更"
            )
        return EventNextActionDTO(kind="wait", label="系统继续处理")


def _leading_count(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(char for char in value if char.isdigit())
    return int(digits) if digits else None
