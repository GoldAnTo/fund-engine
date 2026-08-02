from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models.ledger import (
    AIAssessment,
    CaseThemeTagEvent,
    CausalEdge,
    CausalStep,
    DocumentVersion,
    EvidenceLink,
    EvidenceReview,
    EvidenceSnapshot,
    ResearchCase,
    ReviewDecision,
    SourceSpan,
    SourceStatement,
    Thesis,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchRepository:
    """Append-only persistence for research cases, theses, evidence, and assessments.

    No update or delete methods are exposed by design.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_case(
        self,
        *,
        title: str,
        industry_topic: str,
        created_by: str,
        research_object: str | None = None,
        phenomenon: str | None = None,
        core_question: str | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        evidence_cutoff: date | None = None,
    ) -> ResearchCase:
        case = ResearchCase(
            title=title,
            industry_topic=industry_topic,
            created_by=created_by,
            created_at=_utcnow(),
            research_object=research_object,
            phenomenon=phenomenon,
            core_question=core_question,
            period_start=period_start,
            period_end=period_end,
            evidence_cutoff=evidence_cutoff,
        )
        self._session.add(case)
        self._session.flush()
        return case

    def add_theme_tag_event(
        self,
        *,
        research_case_id: uuid.UUID,
        tag: str,
        op: str,
    ) -> CaseThemeTagEvent:
        event = CaseThemeTagEvent(
            research_case_id=research_case_id,
            tag=tag,
            op=op,
            created_at=_utcnow(),
        )
        self._session.add(event)
        self._session.flush()
        return event

    def theme_tag_events(
        self, research_case_id: uuid.UUID | None = None
    ) -> list[CaseThemeTagEvent]:
        """All tag events in creation order (optionally for one case)."""
        query = select(CaseThemeTagEvent).order_by(CaseThemeTagEvent.created_at)
        if research_case_id is not None:
            query = query.where(
                CaseThemeTagEvent.research_case_id == research_case_id
            )
        return list(self._session.scalars(query))

    def add_thesis(
        self,
        *,
        research_case_id: uuid.UUID,
        statement: str,
        created_by: str,
        title: str | None = None,
        observation_start: date | None = None,
        observation_end: date | None = None,
        support_condition: str | None = None,
        falsification_condition: str | None = None,
        next_verification_event: str | None = None,
        creator_type: str = "human",
        review_state: str = "confirmed",
    ) -> Thesis:
        thesis = Thesis(
            research_case_id=research_case_id,
            statement=statement,
            created_by=created_by,
            created_at=_utcnow(),
            title=title,
            observation_start=observation_start,
            observation_end=observation_end,
            support_condition=support_condition,
            falsification_condition=falsification_condition,
            next_verification_event=next_verification_event,
            creator_type=creator_type,
            review_state=review_state,
        )
        self._session.add(thesis)
        self._session.flush()
        return thesis

    def add_causal_step(
        self,
        *,
        thesis_id: uuid.UUID,
        description: str,
        sequence: int,
    ) -> CausalStep:
        step = CausalStep(
            thesis_id=thesis_id,
            description=description,
            sequence=sequence,
            created_at=_utcnow(),
        )
        self._session.add(step)
        self._session.flush()
        return step

    def add_causal_edge(
        self,
        *,
        source_step_id: uuid.UUID,
        target_step_id: uuid.UUID,
        rationale: str,
        creator_type: str,
        review_state: str,
    ) -> CausalEdge:
        edge = CausalEdge(
            source_step_id=source_step_id,
            target_step_id=target_step_id,
            rationale=rationale,
            creator_type=creator_type,
            review_state=review_state,
            created_at=_utcnow(),
        )
        self._session.add(edge)
        self._session.flush()
        return edge

    def add_statement(
        self,
        *,
        source_span_id: uuid.UUID,
        kind: str,
        normalized_text: str,
        observed_period: date | None = None,
    ) -> SourceStatement:
        statement = SourceStatement(
            source_span_id=source_span_id,
            kind=kind,
            normalized_text=normalized_text,
            observed_period=observed_period,
            created_at=_utcnow(),
        )
        self._session.add(statement)
        self._session.flush()
        return statement

    def link_evidence(
        self,
        *,
        thesis_id: uuid.UUID,
        source_statement_id: uuid.UUID,
        role: str,
        reason: str,
        scope: dict,
        available_at: datetime,
        creator_type: str = "ai",
        review_state: str = "machine_generated",
        model_version: str | None = None,
    ) -> EvidenceLink:
        link = EvidenceLink(
            thesis_id=thesis_id,
            source_statement_id=source_statement_id,
            role=role,
            reason=reason,
            scope=scope,
            available_at=available_at,
            creator_type=creator_type,
            review_state=review_state,
            model_version=model_version,
            created_at=_utcnow(),
        )
        self._session.add(link)
        self._session.flush()
        return link

    def get_statement(self, statement_id: uuid.UUID) -> SourceStatement | None:
        return self._session.scalar(
            select(SourceStatement).where(SourceStatement.id == statement_id)
        )

    def get_evidence_link(self, link_id: uuid.UUID) -> EvidenceLink | None:
        return self._session.scalar(
            select(EvidenceLink).where(EvidenceLink.id == link_id)
        )

    def get_document_version_for_statement(
        self, statement_id: uuid.UUID
    ) -> DocumentVersion | None:
        statement = self.get_statement(statement_id)
        if statement is None:
            return None
        span = self._session.scalar(
            select(SourceSpan).where(SourceSpan.id == statement.source_span_id)
        )
        if span is None:
            return None
        return self._session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.id == span.document_version_id
            )
        )

    def visible_links(
        self,
        *,
        thesis_id: uuid.UUID,
        cutoff: datetime,
    ) -> list[EvidenceLink]:
        # A link is visible at cutoff only if the evidence was available AND
        # the link had been written to the ledger by then. Filtering only
        # available_at would allow hindsight leakage via backfilled dates.
        return list(
            self._session.scalars(
                select(EvidenceLink)
                .where(EvidenceLink.thesis_id == thesis_id)
                .where(EvidenceLink.available_at <= cutoff)
                .where(EvidenceLink.created_at <= cutoff)
                .order_by(EvidenceLink.available_at)
            )
        )

    def insert_snapshot(
        self,
        *,
        thesis_id: uuid.UUID,
        cutoff: datetime,
        evidence_link_ids: list[str],
    ) -> EvidenceSnapshot:
        snapshot = EvidenceSnapshot(
            thesis_id=thesis_id,
            cutoff=cutoff,
            evidence_link_ids=evidence_link_ids,
            created_at=_utcnow(),
        )
        self._session.add(snapshot)
        self._session.flush()
        return snapshot

    def insert_ai_assessment(
        self,
        *,
        snapshot_id: uuid.UUID,
        conclusion: str,
        rationale: str,
        gaps: list[str],
        displayed_as_provisional: bool = True,
        creator_type: str = "ai",
        model_version: str | None = None,
    ) -> AIAssessment:
        assessment = AIAssessment(
            snapshot_id=snapshot_id,
            conclusion=conclusion,
            rationale=rationale,
            gaps=gaps,
            displayed_as_provisional=displayed_as_provisional,
            creator_type=creator_type,
            model_version=model_version,
            created_at=_utcnow(),
        )
        self._session.add(assessment)
        self._session.flush()
        return assessment

    def insert_review(
        self,
        *,
        ai_assessment_id: uuid.UUID,
        outcome: str,
        conclusion: str | None,
        reason: str,
        reviewer: str,
    ) -> ReviewDecision:
        review = ReviewDecision(
            ai_assessment_id=ai_assessment_id,
            outcome=outcome,
            conclusion=conclusion,
            reason=reason,
            reviewer=reviewer,
            created_at=_utcnow(),
        )
        self._session.add(review)
        self._session.flush()
        return review

    def insert_evidence_review(
        self,
        *,
        evidence_link_id: uuid.UUID,
        outcome: str,
        relation: str | None,
        factor_role: str,
        scope_boundary: str,
        reason: str,
        reviewer: str,
    ) -> EvidenceReview:
        review = EvidenceReview(
            evidence_link_id=evidence_link_id,
            outcome=outcome,
            relation=relation,
            factor_role=factor_role,
            scope_boundary=scope_boundary,
            reason=reason,
            reviewer=reviewer,
            created_at=_utcnow(),
        )
        self._session.add(review)
        self._session.flush()
        return review

    def evidence_reviews_for_links(
        self, link_ids: list[uuid.UUID]
    ) -> list[EvidenceReview]:
        if not link_ids:
            return []
        return list(
            self._session.scalars(
                select(EvidenceReview)
                .where(EvidenceReview.evidence_link_id.in_(link_ids))
                .order_by(EvidenceReview.created_at)
            )
        )

    def get_ai_assessment(self, assessment_id: uuid.UUID) -> AIAssessment | None:
        return self._session.scalar(
            select(AIAssessment).where(AIAssessment.id == assessment_id)
        )

    # ------------------------------------------------------------------ readers (workbench / projection)

    def get_case(
        self,
        case_id: uuid.UUID,
        *,
        cutoff: datetime | None = None,
    ) -> ResearchCase | None:
        if cutoff is None:
            return self._session.get(ResearchCase, case_id)
        # Historical view: a case created after the cutoff did not exist then.
        return self._session.scalar(
            select(ResearchCase)
            .where(ResearchCase.id == case_id)
            .where(ResearchCase.created_at <= cutoff)
        )

    def latest_thesis_for_case(
        self,
        research_case_id: uuid.UUID,
        *,
        cutoff: datetime | None = None,
    ) -> Thesis | None:
        query = (
            select(Thesis)
            .where(Thesis.research_case_id == research_case_id)
            .order_by(Thesis.created_at.desc())
        )
        if cutoff is not None:
            query = query.where(Thesis.created_at <= cutoff)
        return self._session.scalar(query.limit(1))

    def theses_for_case(
        self,
        research_case_id: uuid.UUID,
        *,
        cutoff: datetime | None = None,
    ) -> list[Thesis]:
        query = (
            select(Thesis)
            .where(Thesis.research_case_id == research_case_id)
            .order_by(Thesis.created_at)
        )
        if cutoff is not None:
            query = query.where(Thesis.created_at <= cutoff)
        return list(self._session.scalars(query))

    def cases_page(
        self,
        *,
        limit: int,
        after_created_at: datetime | None = None,
        after_id: uuid.UUID | None = None,
    ) -> list[ResearchCase]:
        """Return up to ``limit + 1`` cases newest-first for cursor pagination."""
        query = select(ResearchCase).order_by(
            ResearchCase.created_at.desc(), ResearchCase.id.desc()
        )
        if after_created_at is not None and after_id is not None:
            query = query.where(
                tuple_(ResearchCase.created_at, ResearchCase.id)
                < tuple_(after_created_at, after_id)
            )
        return list(self._session.scalars(query.limit(limit + 1)))

    def thesis_by_id_for_case(
        self,
        case_id: uuid.UUID,
        thesis_id: uuid.UUID,
        *,
        cutoff: datetime | None = None,
    ) -> Thesis | None:
        query = (
            select(Thesis)
            .where(Thesis.id == thesis_id)
            .where(Thesis.research_case_id == case_id)
        )
        if cutoff is not None:
            query = query.where(Thesis.created_at <= cutoff)
        return self._session.scalar(query)

    def latest_assessment_for_thesis(
        self,
        thesis_id: uuid.UUID,
        *,
        cutoff: datetime,
    ) -> AIAssessment | None:
        """Latest AI assessment for a thesis available on or before *cutoff*.

        Availability is governed by the assessment ``created_at``, not the
        snapshot's evidence cutoff, so a snapshot frozen with a future evidence
        cutoff is still visible once it has been created.
        """
        return self._session.scalar(
            select(AIAssessment)
            .join(
                EvidenceSnapshot,
                AIAssessment.snapshot_id == EvidenceSnapshot.id,
            )
            .where(EvidenceSnapshot.thesis_id == thesis_id)
            .where(AIAssessment.created_at <= cutoff)
            .order_by(AIAssessment.created_at.desc())
            .limit(1)
        )

    def latest_snapshot_for_thesis(
        self,
        thesis_id: uuid.UUID,
        *,
        cutoff: datetime,
    ) -> EvidenceSnapshot | None:
        """Latest frozen snapshot for a thesis written on or before *cutoff*."""
        return self._session.scalar(
            select(EvidenceSnapshot)
            .where(EvidenceSnapshot.thesis_id == thesis_id)
            .where(EvidenceSnapshot.created_at <= cutoff)
            .order_by(EvidenceSnapshot.created_at.desc())
            .limit(1)
        )

    def assessments_for_thesis(
        self,
        thesis_id: uuid.UUID,
        *,
        cutoff: datetime,
    ) -> list[AIAssessment]:
        """All AI assessments for a thesis created on or before *cutoff*."""
        return list(
            self._session.scalars(
                select(AIAssessment)
                .join(
                    EvidenceSnapshot,
                    AIAssessment.snapshot_id == EvidenceSnapshot.id,
                )
                .where(EvidenceSnapshot.thesis_id == thesis_id)
                .where(AIAssessment.created_at <= cutoff)
                .order_by(AIAssessment.created_at.desc())
            )
        )

    def latest_review_for_assessment(
        self,
        assessment_id: uuid.UUID,
        *,
        cutoff: datetime | None = None,
    ) -> ReviewDecision | None:
        query = (
            select(ReviewDecision)
            .where(ReviewDecision.ai_assessment_id == assessment_id)
            .order_by(ReviewDecision.created_at.desc())
        )
        if cutoff is not None:
            query = query.where(ReviewDecision.created_at <= cutoff)
        return self._session.scalar(query.limit(1))

    def causal_steps_for_thesis(
        self,
        thesis_id: uuid.UUID,
        *,
        cutoff: datetime | None = None,
    ) -> list[CausalStep]:
        query = (
            select(CausalStep)
            .where(CausalStep.thesis_id == thesis_id)
            .order_by(CausalStep.sequence)
        )
        if cutoff is not None:
            query = query.where(CausalStep.created_at <= cutoff)
        return list(self._session.scalars(query))

    def causal_edges_for_steps(
        self, step_ids: list[uuid.UUID]
    ) -> list[CausalEdge]:
        if not step_ids:
            return []
        return list(
            self._session.scalars(
                select(CausalEdge)
                .where(CausalEdge.source_step_id.in_(step_ids))
                .where(CausalEdge.target_step_id.in_(step_ids))
                .order_by(CausalEdge.created_at)
            )
        )

    def span_for_statement(
        self, statement_id: uuid.UUID
    ) -> SourceSpan | None:
        statement = self.get_statement(statement_id)
        if statement is None:
            return None
        return self._session.scalar(
            select(SourceSpan).where(SourceSpan.id == statement.source_span_id)
        )

    def statements_for_span_ids(
        self, span_ids: list[uuid.UUID]
    ) -> list[SourceStatement]:
        if not span_ids:
            return []
        return list(
            self._session.scalars(
                select(SourceStatement).where(
                    SourceStatement.source_span_id.in_(span_ids)
                )
            )
        )

    def links_for_statement_ids(
        self, statement_ids: list[uuid.UUID]
    ) -> list[EvidenceLink]:
        if not statement_ids:
            return []
        return list(
            self._session.scalars(
                select(EvidenceLink).where(
                    EvidenceLink.source_statement_id.in_(statement_ids)
                )
            )
        )

    def all_evidence_links(self) -> list[EvidenceLink]:
        return list(self._session.scalars(select(EvidenceLink)))

    def all_theses(self) -> list[Thesis]:
        return list(self._session.scalars(select(Thesis)))

    def all_causal_steps(self) -> list[CausalStep]:
        return list(self._session.scalars(select(CausalStep)))

    def all_causal_edges(self) -> list[CausalEdge]:
        return list(self._session.scalars(select(CausalEdge)))

    def all_statements(self) -> list[SourceStatement]:
        return list(self._session.scalars(select(SourceStatement)))

    def all_spans(self) -> list[SourceSpan]:
        return list(self._session.scalars(select(SourceSpan)))

    def all_document_versions(self) -> list[DocumentVersion]:
        return list(self._session.scalars(select(DocumentVersion)))

    def all_cases(self) -> list[ResearchCase]:
        return list(self._session.scalars(select(ResearchCase)))

    def all_snapshots(self) -> list[EvidenceSnapshot]:
        return list(self._session.scalars(select(EvidenceSnapshot)))

    def all_ai_assessments(self) -> list[AIAssessment]:
        return list(self._session.scalars(select(AIAssessment)))

    def all_reviews(self) -> list[ReviewDecision]:
        return list(self._session.scalars(select(ReviewDecision)))
