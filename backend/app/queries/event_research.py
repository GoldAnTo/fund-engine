"""Focused read models for the event research list and workbench."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import NotFoundError
from app.models.event_research import (
    CaseRelation,
    EventResearchBrief,
    EventResearchConclusion,
    EventResearchFactorDraft,
    EventResearchScopeEvidenceAssignment,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.ledger import (
    CaseDocumentVersion,
    DocumentVersion,
    EvidenceLink,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.models.operational import EventResearchLifecycle
from app.models.proposals import Proposal
from app.services.event_research_scope_evidence import current_mapped_evidence_ids
from app.services.source_admission import classify_source
from app.schemas.v1.event_research import (
    CaseRelationCaseDTO,
    CaseRelationDTO,
    EventConclusionDraftDTO,
    EventConclusionHistoryResponse,
    EventConclusionVersionDTO,
    EventKeyEvidenceDTO,
    EventNextActionDTO,
    EventResearchFactorDTO,
    EventResearchLifecycleDTO,
    EventResearchListItemDTO,
    EventResearchListResponse,
    EventResearchScopeDTO,
    EventResearchScopeHistoryItemDTO,
    EventResearchScopeHistoryResponse,
    EventWorkbenchProgressDTO,
    EventWorkbenchDTO,
    ResearchNetworkResponse,
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

    def network(self) -> ResearchNetworkResponse:
        cases = {
            brief.research_case_id: CaseRelationCaseDTO(
                case_id=str(brief.research_case_id),
                title=brief.event_title,
                lifecycle_status=lifecycle.status,
            )
            for brief, lifecycle in self._session.execute(
                select(EventResearchBrief, EventResearchLifecycle).join(
                    EventResearchLifecycle,
                    EventResearchLifecycle.research_case_id == EventResearchBrief.research_case_id,
                )
            )
        }
        visible = [
            CaseRelationDTO(
                id=str(relation.id),
                source_case=cases[relation.source_case_id],
                target_case=cases[relation.target_case_id],
                relation_type=relation.relation_type,
                reason=relation.reason,
                created_by=relation.created_by,
                review_state=relation.review_state,
                created_at=relation.created_at,
            )
            for relation in self._session.scalars(
                select(CaseRelation).order_by(CaseRelation.created_at.desc(), CaseRelation.id.desc())
            )
            if relation.source_case_id in cases and relation.target_case_id in cases
        ]
        return ResearchNetworkResponse(
            reviewed_relations=[item for item in visible if item.review_state == "reviewed"],
            candidate_relations=[item for item in visible if item.review_state == "machine_generated"],
        )

    def relations(self, case_id: uuid.UUID) -> ResearchNetworkResponse:
        if self._session.scalar(
            select(EventResearchBrief.id)
            .where(EventResearchBrief.research_case_id == case_id)
            .limit(1)
        ) is None:
            raise NotFoundError("event research case not found")
        network = self.network()
        involves_case = lambda relation: (
            relation.source_case.case_id == str(case_id)
            or relation.target_case.case_id == str(case_id)
        )
        return ResearchNetworkResponse(
            reviewed_relations=[item for item in network.reviewed_relations if involves_case(item)],
            candidate_relations=[item for item in network.candidate_relations if involves_case(item)],
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
        progress = self._progress(case_id, lifecycle)
        factors = self._factors(case_id, scope, self._pending_by_factor(case_id, scope))
        confidence = self._conclusion_confidence(factors)
        evidence = self._formal_evidence(case_id)
        conclusion = self._conclusion(case_id, lifecycle, confidence)
        return EventWorkbenchDTO(
            event=event,
            lifecycle=self._lifecycle(lifecycle),
            conclusion=conclusion,
            factors=factors,
            evidence=evidence,
            progress=progress,
            scope=self._scope(scope, case_id),
            next_action=self._next_action(lifecycle),
        )

    def conclusion_history(self, case_id: uuid.UUID) -> EventConclusionHistoryResponse:
        if self._session.scalar(
            select(EventResearchBrief.id)
            .where(EventResearchBrief.research_case_id == case_id)
            .limit(1)
        ) is None:
            raise NotFoundError("event research case not found")
        records = list(
            self._session.scalars(
                select(EventResearchConclusion)
                .where(EventResearchConclusion.research_case_id == case_id)
                .order_by(EventResearchConclusion.created_at, EventResearchConclusion.id)
            )
        )
        scope_versions = {
            scope.id: scope.version
            for scope in self._session.scalars(
                select(EventResearchScopeVersion).where(
                    EventResearchScopeVersion.research_case_id == case_id
                )
            )
        }
        return EventConclusionHistoryResponse(
            case_id=str(case_id),
            versions=[
                EventConclusionVersionDTO(
                    id=str(record.id),
                    sequence=index,
                    state=record.state,
                    text=record.text,
                    primary_factor=record.primary_factor,
                    scope_version=(
                        scope_versions.get(record.scope_version_id)
                        if record.scope_version_id is not None
                        else None
                    ),
                    based_on_conclusion_id=(
                        str(record.based_on_conclusion_id)
                        if record.based_on_conclusion_id is not None
                        else None
                    ),
                    reviewer=record.reviewer,
                    evidence_count=len(record.evidence_link_ids),
                    created_at=record.created_at,
                )
                for index, record in enumerate(records, start=1)
            ],
        )

    def scope_history(self, case_id: uuid.UUID) -> EventResearchScopeHistoryResponse:
        if self._session.scalar(select(EventResearchBrief.id).where(EventResearchBrief.research_case_id == case_id).limit(1)) is None:
            raise NotFoundError("event research case not found")
        scopes = list(self._session.scalars(select(EventResearchScopeVersion).where(EventResearchScopeVersion.research_case_id == case_id).order_by(EventResearchScopeVersion.version.desc())))
        return EventResearchScopeHistoryResponse(case_id=str(case_id), items=[EventResearchScopeHistoryItemDTO(version=scope.version, factors=[{"statement": factor.statement, "description": factor.description} for factor in self._session.scalars(select(EventResearchScopeFactor).where(EventResearchScopeFactor.scope_version_id == scope.id).order_by(EventResearchScopeFactor.position))], changed_by=scope.changed_by, change_reason=scope.change_summary, created_at=scope.created_at) for scope in scopes])

    def _conclusion(
        self,
        case_id: uuid.UUID,
        lifecycle: EventResearchLifecycle,
        confidence: str,
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
                    confidence=confidence,
                    citations=self._formal_evidence(case_id, record.evidence_link_ids),
                )
            return EventConclusionDraftDTO(
                state="published",
                text="结论已发布，正在载入可复核证据。",
                confidence=confidence,
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
                state=record.state, text=record.text, confidence=confidence, citations=reviewed
            )
        if record is not None:
            return EventConclusionDraftDTO(
                state="cannot_conclude",
                text="研究范围已更新，先前结论草案不再适用于当前因素。",
                confidence="low",
                citations=reviewed,
            )
        if lifecycle.status == "draft_ready":
            return EventConclusionDraftDTO(
                state="ai_draft",
                text="当前已进入结论复核：尚无足以支持主要因素判断的已审核证据；本研究不能给出因果结论。",
                confidence=confidence,
                citations=reviewed,
            )
        return EventConclusionDraftDTO(
            state="cannot_conclude",
            text="尚不能下结论：系统正在核验各项解释及其反证。",
            confidence="low",
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
            factor_rows = list(
                self._session.scalars(
                    select(EventResearchScopeFactor)
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
                factors=[
                    {"statement": factor.statement, "description": factor.description}
                    for factor in factor_rows
                ],
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
            factors=[{"statement": factor, "description": None} for factor in factors],
            unmapped_evidence_count=0,
        )

    def _factors(
        self,
        case_id: uuid.UUID,
        scope: EventResearchScopeVersion | None,
        pending_by_factor: dict[str, int],
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
        counts_by_factor: dict[str, dict[str, int]] = {}
        thesis_ids_by_statement = {
            statement: thesis_id
            for thesis_id, statement in self._session.execute(
                select(Thesis.id, Thesis.statement)
                .where(Thesis.research_case_id == case_id)
                .where(Thesis.statement.in_([factor.statement for factor in factors]))
            )
        }
        if scope is not None:
            rows = self._session.execute(
                select(
                    EventResearchScopeEvidenceAssignment.factor_statement,
                    EvidenceLink.role,
                    func.count(),
                )
                .join(
                    EvidenceLink,
                    EventResearchScopeEvidenceAssignment.evidence_link_id
                    == EvidenceLink.id,
                )
                .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
                .where(
                    EventResearchScopeEvidenceAssignment.scope_version_id == scope.id,
                    EventResearchScopeEvidenceAssignment.disposition == "mapped",
                    EventResearchScopeEvidenceAssignment.factor_statement
                    == Thesis.statement,
                    Thesis.research_case_id == case_id,
                    EvidenceLink.review_state == "reviewed",
                )
                .group_by(
                    EventResearchScopeEvidenceAssignment.factor_statement,
                    EvidenceLink.role,
                )
            )
            for statement, role, count in rows:
                if statement is not None:
                    counts_by_factor.setdefault(statement, {})[role] = int(count)
        result: list[EventResearchFactorDTO] = []
        for factor in factors[:5]:
            counts = counts_by_factor.get(factor.statement, {})
            result.append(
                EventResearchFactorDTO(
                    thesis_id=str(thesis_ids_by_statement[factor.statement]),
                    statement=factor.statement,
                    description=getattr(factor, "description", None),
                    position=factor.position,
                    reviewed_support_count=int(counts.get("supports", 0)),
                    reviewed_contradiction_count=int(counts.get("contradicts", 0)),
                    pending_proposal_count=pending_by_factor.get(factor.statement, 0),
                    current_gap=(
                        "有关键证据待审核" if pending_by_factor.get(factor.statement, 0)
                        else "尚缺少可采纳证据"
                        if int(counts.get("supports", 0)) + int(counts.get("contradicts", 0)) == 0
                        else None
                    ),
                )
            )
        return result

    def _pending_by_factor(
        self, case_id: uuid.UUID, scope: EventResearchScopeVersion | None
    ) -> dict[str, int]:
        if scope is None:
            return {}
        active_factors = set(self._session.scalars(
            select(EventResearchScopeFactor.statement).where(
                EventResearchScopeFactor.scope_version_id == scope.id
            )
        ))
        proposals = list(
            self._session.scalars(
                select(Proposal)
                .where(Proposal.research_case_id == case_id)
                .where(Proposal.kind == "evidence_link")
                .where(Proposal.status == "pending")
            )
        )
        statement_ids = {
            source_statement_id
            for proposal in proposals
            if (source_statement_id := _payload_uuid(proposal.payload, "source_statement_id"))
            is not None
        }
        thesis_ids = {
            thesis_id
            for proposal in proposals
            if (thesis_id := _payload_uuid(proposal.target_context, "thesis_id"))
            is not None
        }
        statements = self._records_by_id(SourceStatement, statement_ids)
        theses = self._records_by_id(Thesis, thesis_ids)
        spans = self._records_by_id(
            SourceSpan,
            {statement.source_span_id for statement in statements.values()},
        )
        documents = self._records_by_id(
            DocumentVersion,
            {span.document_version_id for span in spans.values()},
        )
        linked_document_ids = set(
            self._session.scalars(
                select(CaseDocumentVersion.document_version_id)
                .where(CaseDocumentVersion.research_case_id == case_id)
                .where(CaseDocumentVersion.document_version_id.in_(set(documents)))
            )
        ) if documents else set()
        pending: dict[str, int] = {}
        for proposal in proposals:
            source_statement = statements.get(
                _payload_uuid(proposal.payload, "source_statement_id")
            )
            thesis = theses.get(_payload_uuid(proposal.target_context, "thesis_id"))
            span = spans.get(source_statement.source_span_id) if source_statement else None
            document = documents.get(span.document_version_id) if span else None
            if thesis is None or thesis.research_case_id != case_id:
                continue
            if document is None or document.id not in linked_document_ids:
                continue
            admission = classify_source(
                document.source_url,
                document.parser_version,
                document.parse_state in {"success", "parsed"},
            )
            if admission.can_accept and thesis.statement in active_factors:
                pending[thesis.statement] = pending.get(thesis.statement, 0) + 1
        return pending

    def _records_by_id(self, model, ids: set[uuid.UUID]) -> dict:
        if not ids:
            return {}
        return {
            record.id: record
            for record in self._session.scalars(select(model).where(model.id.in_(ids)))
        }

    @staticmethod
    def _conclusion_confidence(factors: list[EventResearchFactorDTO]) -> str:
        if not factors or any(
            factor.reviewed_support_count + factor.reviewed_contradiction_count == 0
            for factor in factors
        ):
            return "low"
        return "medium" if any(factor.pending_proposal_count for factor in factors) else "high"

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
        review_summary = EventReviewQueueService(self._session).summary(case_id)
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
            if (
                lifecycle.active_run_id is None
                and lifecycle.next_human_action == "核验原文资料并完成研究协议"
            ):
                return EventNextActionDTO(
                    kind="review_intake",
                    label=lifecycle.next_human_action or "核验原文资料并完成研究协议",
                )
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


def _payload_uuid(payload: object, key: str) -> uuid.UUID | None:
    value = payload.get(key) if isinstance(payload, dict) else None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None
