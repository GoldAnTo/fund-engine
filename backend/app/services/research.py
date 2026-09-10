from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from app.models.ledger import (
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
    ) -> ResearchCase:
        return self._repo.add_case(
            title=title,
            industry_topic=industry_topic,
            created_by=created_by,
        )

    def add_thesis(
        self,
        research_case_id: uuid.UUID,
        *,
        statement: str,
        created_by: str,
    ) -> Thesis:
        return self._repo.add_thesis(
            research_case_id=research_case_id,
            statement=statement,
            created_by=created_by,
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
