"""Case list and dossier read assembly for the v1 API.

Builds wire DTOs directly from the append-only ledger. Never invents author,
reliability, confidence, status label or prose not present in the ledger.

Historical replay rule (design 10): the cutoff basis filters EVERY entity
(theses, causal steps, reviews, assessments, evidence links), not just
EvidenceLink. The AI/human boundary (design 9.2/9.3): machine-generated
evidence is hidden from the normal dossier and only revealed under an
explicit research mode; rejected proposals are never returned.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.ledger import ResearchCase
from app.queries.basis import HistoricalBasis
from app.repositories.research import ResearchRepository
from app.schemas.v1.cases import (
    AssessmentDTO,
    CaseListResponse,
    CaseSummaryDTO,
    CausalStepDTO,
    DossierResponse,
    EvidenceRecordDTO,
    ThesisSummaryDTO,
)
from app.schemas.v1.common import CursorPage

# review_state values considered "reviewed evidence" for the default dossier.
_REVIEWED_STATES = frozenset({"reviewed"})
# additional states visible only under explicit research mode.
_RESEARCH_STATES = frozenset({"reviewed", "machine_generated"})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class CaseReadQueries:
    """Read-only case list and dossier assembly."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = ResearchRepository(session)

    # --------------------------------------------------------------- list

    def list_cases(self, *, cursor: str | None, limit: int) -> CaseListResponse:
        after_created_at, after_id = (None, None)
        if cursor is not None:
            after_created_at, after_id = self._decode_cursor(cursor)

        cases = self._repo.cases_page(
            limit=limit,
            after_created_at=after_created_at,
            after_id=after_id,
        )
        has_more = len(cases) > limit
        page_items = cases[:limit]
        next_cursor = None
        if has_more and page_items:
            last = page_items[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)
        return CaseListResponse(
            items=[self._case_summary(c) for c in page_items],
            page=CursorPage(next_cursor=next_cursor, has_more=has_more),
        )

    # ------------------------------------------------------------ dossier

    def dossier(
        self,
        *,
        case_id: uuid.UUID,
        thesis_id: uuid.UUID | None,
        basis: HistoricalBasis,
        research_mode: bool = False,
    ) -> DossierResponse:
        case = self._repo.get_case(case_id, cutoff=basis.cutoff)
        if case is None:
            raise NotFoundError("research case not found")

        if thesis_id is not None:
            thesis = self._repo.thesis_by_id_for_case(
                case_id, thesis_id, cutoff=basis.cutoff
            )
            if thesis is None:
                raise NotFoundError("thesis not found in research case")
        else:
            thesis = self._repo.latest_thesis_for_case(
                case_id, cutoff=basis.cutoff
            )

        theses = self._repo.theses_for_case(case_id, cutoff=basis.cutoff)
        focus_thesis_id = str(thesis.id) if thesis is not None else ""

        assessment_dto = None
        causal_chain: list[CausalStepDTO] = []
        evidence: dict[str, list[EvidenceRecordDTO]] = {
            "supports": [],
            "contradicts": [],
            "contextualizes": [],
        }
        gaps: list[str] = []

        if thesis is not None:
            allowed_states = _RESEARCH_STATES if research_mode else _REVIEWED_STATES
            for link in self._repo.visible_links(
                thesis_id=thesis.id, cutoff=basis.cutoff
            ):
                if link.review_state not in allowed_states:
                    continue
                statement = self._repo.get_statement(link.source_statement_id)
                # A link whose statement did not exist at the cutoff would leak
                # future text; skip it (historical replay, design 10).
                if statement is None or _to_aware(statement.created_at) > basis.cutoff:
                    continue
                evidence.setdefault(link.role, []).append(
                    self._evidence_record(link, statement)
                )

            assessment = self._repo.latest_assessment_for_thesis(
                thesis.id, cutoff=basis.cutoff
            )
            if assessment is not None:
                assessment_dto = self._assessment(assessment, thesis.id, basis.cutoff)
                gaps = list(assessment.gaps)

            causal_chain = [
                CausalStepDTO(
                    id=str(step.id),
                    sequence=step.sequence,
                    description=step.description,
                )
                for step in self._repo.causal_steps_for_thesis(
                    thesis.id, cutoff=basis.cutoff
                )
            ]

        return DossierResponse(
            basis=basis.to_dto(),
            case=self._case_summary(case),
            theses=[self._thesis_summary(t) for t in theses],
            focus_thesis_id=focus_thesis_id,
            assessment=assessment_dto,
            causal_chain=causal_chain,
            evidence=evidence,
            competitive_explanations=[],
            gaps=gaps,
        )

    # ----------------------------------------------------------- mappers

    def _case_summary(self, case: ResearchCase) -> CaseSummaryDTO:
        # ResearchCase is append-only and has no updated_at column; the honest
        # value for "last update" is the creation timestamp.
        return CaseSummaryDTO(
            id=str(case.id),
            title=case.title,
            topic=case.industry_topic,
            created_by=case.created_by,
            created_at=_iso(case.created_at),
            updated_at=_iso(case.created_at),
        )

    def _thesis_summary(self, thesis) -> ThesisSummaryDTO:
        return ThesisSummaryDTO(
            id=str(thesis.id),
            statement=thesis.statement,
            created_by=thesis.created_by,
            created_at=_iso(thesis.created_at),
        )

    def _evidence_record(self, link, statement=None) -> EvidenceRecordDTO:
        if statement is None:
            statement = self._repo.get_statement(link.source_statement_id)
        span = self._repo.span_for_statement(link.source_statement_id)
        # A missing statement/span is a ledger integrity break; expose it as
        # explicit nulls rather than fabricating empty text.
        return EvidenceRecordDTO(
            link_id=str(link.id),
            statement_id=str(link.source_statement_id),
            statement_text=statement.normalized_text if statement else None,
            statement_kind=statement.kind if statement else None,
            span_id=str(span.id) if span else None,
            verbatim_text=span.verbatim_text if span else None,
            locator=span.locator if span else None,
            role=link.role,
            reason=link.reason,
            scope=link.scope,
            observed_period=_iso(statement.observed_period) if statement else None,
            available_at=_iso(link.available_at),
            review_state=link.review_state,
        )

    def _assessment(
        self, assessment, thesis_id: uuid.UUID, cutoff: datetime
    ) -> AssessmentDTO:
        review = self._repo.latest_review_for_assessment(
            assessment.id, cutoff=cutoff
        )
        review_dto = None
        if review is not None:
            review_dto = {
                "outcome": review.outcome,
                "conclusion": review.conclusion,
                "reason": review.reason,
                "reviewer": review.reviewer,
                "reviewed_at": _iso(review.created_at),
            }
        return AssessmentDTO(
            id=str(assessment.id),
            thesis_id=str(thesis_id),
            conclusion=assessment.conclusion,
            rationale=assessment.rationale,
            gaps=list(assessment.gaps),
            provisional=bool(assessment.displayed_as_provisional),
            review=review_dto,
        )

    # ----------------------------------------------------------- cursors

    @staticmethod
    def _encode_cursor(created_at: datetime, case_id: uuid.UUID) -> str:
        raw = json.dumps(
            {"created_at": created_at.isoformat(), "id": str(case_id)}
        ).encode()
        return base64.urlsafe_b64encode(raw).decode()

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
        try:
            raw = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(raw)
            created_at = datetime.fromisoformat(data["created_at"])
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return created_at, uuid.UUID(data["id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValidationFailedError("malformed cursor") from exc
