from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from app.models.ledger import (
    CausalEdge,
    CausalStep,
    EvidenceLink,
    ResearchCase,
    SourceStatement,
    Thesis,
    ValidationError,
)
from app.repositories.research import ResearchRepository

_SOURCE_STATEMENT_KINDS = frozenset(
    {"disclosed_fact", "management_attribution", "forecast", "research_opinion"}
)
_EVIDENCE_ROLES = frozenset({"supports", "contradicts", "contextualizes"})
_CREATOR_TYPES = frozenset({"human", "ai"})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime) -> datetime:
    """Ensure a datetime is timezone-aware (assume UTC if naive).

    SQLite strips timezone info on round-trip, so values read back from the
    database may be naive even though they were stored as aware.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class ResearchService:
    """Admits source statements and evidence links with service-layer validation."""

    def __init__(self, repository: ResearchRepository) -> None:
        self._repo = repository

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
        if not title.strip():
            raise ValidationError("case title must not be empty")
        if (
            period_start is not None
            and period_end is not None
            and period_start > period_end
        ):
            raise ValidationError("period_start must not be after period_end")
        return self._repo.add_case(
            title=title,
            industry_topic=industry_topic,
            created_by=created_by,
            research_object=research_object,
            phenomenon=phenomenon,
            core_question=core_question,
            period_start=period_start,
            period_end=period_end,
            evidence_cutoff=evidence_cutoff,
        )

    def add_thesis(
        self,
        research_case_id: uuid.UUID,
        *,
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
        if not statement.strip():
            raise ValidationError("thesis statement must not be empty")
        if (
            observation_start is not None
            and observation_end is not None
            and observation_start > observation_end
        ):
            raise ValidationError(
                "observation_start must not be after observation_end"
            )
        if self._repo.get_case(research_case_id) is None:
            raise ValidationError(f"research case {research_case_id} not found")
        return self._repo.add_thesis(
            research_case_id=research_case_id,
            statement=statement,
            created_by=created_by,
            title=title,
            observation_start=observation_start,
            observation_end=observation_end,
            support_condition=support_condition,
            falsification_condition=falsification_condition,
            next_verification_event=next_verification_event,
            creator_type=creator_type,
            review_state=review_state,
        )

    def add_causal_step(
        self,
        thesis: Thesis,
        *,
        description: str,
        sequence: int,
    ) -> CausalStep:
        if not description.strip():
            raise ValidationError("causal step description must not be empty")
        if sequence < 1:
            raise ValidationError("causal step sequence must be >= 1")
        for existing in self._repo.causal_steps_for_thesis(thesis.id):
            if existing.sequence == sequence:
                raise ValidationError(
                    f"causal step sequence {sequence} already exists for this thesis"
                )
        return self._repo.add_causal_step(
            thesis_id=thesis.id,
            description=description.strip(),
            sequence=sequence,
        )

    def add_causal_edge(
        self,
        thesis: Thesis,
        *,
        source_step: CausalStep,
        target_step: CausalStep,
        rationale: str,
        creator_type: str = "human",
    ) -> CausalEdge:
        if not rationale.strip():
            raise ValidationError("causal edge rationale must not be empty")
        if creator_type not in _CREATOR_TYPES:
            raise ValidationError(f"invalid creator_type: {creator_type}")
        if source_step.id == target_step.id:
            raise ValidationError("causal edge must not be a self-loop")
        if source_step.thesis_id != thesis.id or target_step.thesis_id != thesis.id:
            raise ValidationError("causal edge steps must belong to the thesis")
        step_ids = [s.id for s in self._repo.causal_steps_for_thesis(thesis.id)]
        for edge in self._repo.causal_edges_for_steps(step_ids):
            if (
                edge.source_step_id == source_step.id
                and edge.target_step_id == target_step.id
            ):
                raise ValidationError("causal edge already exists for this step pair")
        # AI-drafted edges start as unconfirmed drafts; human-authored ones are
        # confirmed on entry (same boundary convention as theses).
        review_state = "confirmed" if creator_type == "human" else "draft"
        return self._repo.add_causal_edge(
            source_step_id=source_step.id,
            target_step_id=target_step.id,
            rationale=rationale.strip(),
            creator_type=creator_type,
            review_state=review_state,
        )

    def add_statement(
        self,
        source_span_id: uuid.UUID,
        normalized_text: str,
        *,
        kind: str,
        observed_period: date | None = None,
    ) -> SourceStatement:
        if kind not in _SOURCE_STATEMENT_KINDS:
            raise ValidationError(f"invalid source statement kind: {kind}")
        return self._repo.add_statement(
            source_span_id=source_span_id,
            kind=kind,
            normalized_text=normalized_text,
            observed_period=observed_period,
        )

    def link_evidence(
        self,
        thesis_id: uuid.UUID,
        source_statement_id: uuid.UUID,
        *,
        role: str,
        reason: str,
        scope: dict,
        available_at: datetime | None = None,
    ) -> EvidenceLink:
        if role not in _EVIDENCE_ROLES:
            raise ValidationError(f"invalid evidence role: {role}")
        if not reason:
            raise ValidationError("reason must not be empty")
        if not scope:
            raise ValidationError("scope must not be empty")
        if available_at is None:
            available_at = _utcnow()
        document_version = self._repo.get_document_version_for_statement(
            source_statement_id
        )
        if (
            document_version is not None
            and _ensure_aware(available_at) < _ensure_aware(document_version.available_at)
        ):
            raise ValidationError(
                "available_at must not precede the document version available_at"
            )
        return self._repo.link_evidence(
            thesis_id=thesis_id,
            source_statement_id=source_statement_id,
            role=role,
            reason=reason,
            scope=scope,
            available_at=available_at,
            creator_type="ai",
            review_state="machine_generated",
        )
