from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import (
    AIAssessment,
    CausalEdge,
    CausalStep,
    DocumentVersion,
    EvidenceLink,
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
    ) -> ResearchCase:
        case = ResearchCase(
            title=title,
            industry_topic=industry_topic,
            created_by=created_by,
            created_at=_utcnow(),
        )
        self._session.add(case)
        self._session.flush()
        return case

    def add_thesis(
        self,
        *,
        research_case_id: uuid.UUID,
        statement: str,
        created_by: str,
    ) -> Thesis:
        thesis = Thesis(
            research_case_id=research_case_id,
            statement=statement,
            created_by=created_by,
            created_at=_utcnow(),
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
        return list(
            self._session.scalars(
                select(EvidenceLink)
                .where(EvidenceLink.thesis_id == thesis_id)
                .where(EvidenceLink.available_at <= cutoff)
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

    def get_ai_assessment(self, assessment_id: uuid.UUID) -> AIAssessment | None:
        return self._session.scalar(
            select(AIAssessment).where(AIAssessment.id == assessment_id)
        )
