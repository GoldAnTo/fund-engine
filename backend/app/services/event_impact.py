"""Source-backed, scope-bound company impact candidate resolution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence
from unicodedata import normalize

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.event_impact import (
    CompanyImpactObservation,
    CompanyImpactRelation,
    EventImpactHypothesis,
)
from app.models.event_research import (
    EventResearchBrief,
    EventResearchScopeFactor,
    EventResearchScopeVersion,
)
from app.models.events import DomainEvent
from app.models.ledger import (
    CaseDocumentVersion,
    Company,
    DocumentVersion,
    EvidenceLink,
    SourceSpan,
    SourceStatement,
    Thesis,
)
from app.services.source_admission import classify_source
from app.repositories.outbox import emit_event


_INITIAL_REFRESH_KEY_SUFFIX = "initial"
_REFRESH_REQUEST_EVENT_TYPE = "event_impact_refresh_requested"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ResolvedImpactCompany:
    company_name: str
    type: str
    relation_kind: str
    direction: str
    mechanism: str
    source_statement_id: uuid.UUID | None


class ImpactResolver(Protocol):
    def resolve(
        self,
        *,
        factor_statement: str,
        statements: Sequence[SourceStatement],
    ) -> Sequence[ResolvedImpactCompany]: ...


class _EmptyImpactResolver:
    """Production-safe default until a prompted resolver is introduced."""

    def resolve(
        self,
        *,
        factor_statement: str,
        statements: Sequence[SourceStatement],
    ) -> Sequence[ResolvedImpactCompany]:
        return ()


@dataclass(frozen=True)
class ImpactRefreshResult:
    hypotheses_created: int
    relations_created: int
    source_rejected_count: int
    unresolved_candidate_count: int


@dataclass(frozen=True)
class _ResolvedCandidate:
    hypothesis: EventImpactHypothesis
    candidate: ResolvedImpactCompany


class EventImpactResearchService:
    def __init__(self, session: Session, resolver: ImpactResolver | None = None) -> None:
        self._session = session
        self._resolver = resolver or _EmptyImpactResolver()

    def refresh(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID | None = None,
    ) -> ImpactRefreshResult:
        scope = self._scope_for(case_id, scope_version_id)
        refresh_key = self._initial_refresh_key(scope.id)
        existing_hypotheses = self._session.scalars(
            select(EventImpactHypothesis).where(
                EventImpactHypothesis.research_case_id == case_id,
                EventImpactHypothesis.scope_version_id == scope.id,
            )
        )
        if any(
            hypothesis.score_components.get("refresh_key") == refresh_key
            for hypothesis in existing_hypotheses
        ):
            return ImpactRefreshResult(0, 0, 0, 0)
        factors = list(
            self._session.scalars(
                select(EventResearchScopeFactor)
                .where(EventResearchScopeFactor.scope_version_id == scope.id)
                .order_by(EventResearchScopeFactor.position)
            )
        )
        admissible_records = self._admissible_statement_records(case_id)
        admissible_statements = [statement for statement, _ in admissible_records]
        admissible_by_id = {statement.id: statement for statement in admissible_statements}
        statement_dates = {
            statement.id: statement.observed_period
            or (
                document.published_at.date()
                if document.published_at
                else document.available_at.date()
            )
            for statement, document in admissible_records
        }

        resolved_candidates: list[_ResolvedCandidate] = []
        for factor in factors:
            candidates = list(
                self._resolver.resolve(
                    factor_statement=factor.statement,
                    statements=admissible_statements,
                )
            )
            hypothesis = EventImpactHypothesis(
                research_case_id=case_id,
                scope_version_id=scope.id,
                statement=factor.statement,
                classification="candidate",
                rank=factor.position,
                score_components={"refresh_key": refresh_key},
                explanation=self._pending_evidence_explanation(candidates, admissible_by_id),
                created_at=_utcnow(),
            )
            self._session.add(hypothesis)
            resolved_candidates.extend(
                _ResolvedCandidate(hypothesis=hypothesis, candidate=candidate)
                for candidate in candidates
            )

        source_rejected_count = 0
        unresolved_candidate_count = 0
        source_backed_candidates: list[
            tuple[_ResolvedCandidate, SourceStatement]
        ] = []
        for entry in resolved_candidates:
            candidate = entry.candidate
            if candidate.source_statement_id is None:
                unresolved_candidate_count += 1
                self._session.add(
                    EventImpactHypothesis(
                        research_case_id=entry.hypothesis.research_case_id,
                        scope_version_id=entry.hypothesis.scope_version_id,
                        statement=(
                            f"{candidate.company_name} {candidate.relation_kind}: "
                            f"{candidate.mechanism}"
                        ),
                        classification="unresolved",
                        rank=entry.hypothesis.rank,
                        score_components={"refresh_key": refresh_key, "source": 0},
                        explanation=(
                            "source_statement_id is missing for "
                            f"{candidate.company_name}'s {candidate.relation_kind} "
                            "relationship; no relation or observation was appended."
                        ),
                        created_at=_utcnow(),
                    )
                )
                continue
            statement = admissible_by_id.get(candidate.source_statement_id)
            if statement is None:
                source_rejected_count += 1
                continue
            source_backed_candidates.append((entry, statement))

        # Source ownership/admission is checked before company creation.  A
        # resolver cannot manufacture entities by offering foreign/invalid ids.
        companies = self._companies_for(
            [entry.candidate for entry, _ in source_backed_candidates]
        )
        resolved_companies = [
            (
                entry,
                statement,
                self._resolve_or_create_company(entry.candidate, companies),
            )
            for entry, statement in source_backed_candidates
        ]
        # New Company ids are needed by the append-only relation rows.  This
        # is a single bulk flush, rather than a lookup/flush for each candidate.
        self._session.flush()

        relations_created = 0
        relation_sources: list[
            tuple[CompanyImpactRelation, ResolvedImpactCompany, SourceStatement]
        ] = []
        for entry, statement, company in resolved_companies:
            candidate = entry.candidate
            relation = CompanyImpactRelation(
                hypothesis=entry.hypothesis,
                scope_version_id=entry.hypothesis.scope_version_id,
                affected_company_id=company.id,
                relation_kind=candidate.relation_kind,
                direction=candidate.direction,
                mechanism=candidate.mechanism,
                status="candidate",
                source_statement_id=statement.id,
                created_at=_utcnow(),
            )
            self._session.add(relation)
            relation_sources.append((relation, candidate, statement))
            relations_created += 1
        self._session.flush()
        for relation, candidate, statement in relation_sources:
            self._session.add(
                CompanyImpactObservation(
                    relation_id=relation.id,
                    kind="relation",
                    status="verified",
                    source_statement_id=statement.id,
                    valuation_snapshot_id=None,
                    summary=(
                        f"{candidate.relation_kind}: {candidate.mechanism}. "
                        f"Evidence: {statement.normalized_text}"
                    ),
                    as_of_date=statement_dates[statement.id],
                    created_at=_utcnow(),
                )
            )
        self._session.flush()
        return ImpactRefreshResult(
            hypotheses_created=len(factors) + unresolved_candidate_count,
            relations_created=relations_created,
            source_rejected_count=source_rejected_count,
            unresolved_candidate_count=unresolved_candidate_count,
        )

    def schedule_refresh(
        self, case_id: uuid.UUID, scope_version_id: uuid.UUID
    ) -> DomainEvent:
        """Append one durable handoff for a specific immutable scope version."""
        scope = self._scope_for(case_id, scope_version_id)
        existing = self._session.scalar(
            select(DomainEvent)
            .where(DomainEvent.type == _REFRESH_REQUEST_EVENT_TYPE)
            .where(DomainEvent.aggregate_type == "event_research_scope")
            .where(DomainEvent.aggregate_id == str(scope.id))
            .limit(1)
        )
        if existing is not None:
            return existing
        return emit_event(
            self._session,
            type=_REFRESH_REQUEST_EVENT_TYPE,
            aggregate_type="event_research_scope",
            aggregate_id=scope.id,
            ref_type="research_case",
            ref_id=case_id,
            origin="operational",
            payload={
                "research_case_id": str(case_id),
                "scope_version_id": str(scope.id),
            },
        )

    def _scope_for(
        self, case_id: uuid.UUID, scope_version_id: uuid.UUID | None
    ) -> EventResearchScopeVersion:
        if self._session.scalar(
            select(EventResearchBrief.id).where(
                EventResearchBrief.research_case_id == case_id
            )
        ) is None:
            raise NotFoundError("event research case not found")
        scope_query = select(EventResearchScopeVersion).where(
            EventResearchScopeVersion.research_case_id == case_id
        )
        if scope_version_id is not None:
            scope = self._session.scalar(
                scope_query.where(EventResearchScopeVersion.id == scope_version_id)
            )
            if scope is None:
                raise ValidationFailedError(
                    "scope version does not belong to the event research case"
                )
            return scope
        scope = self._session.scalar(
            scope_query.order_by(EventResearchScopeVersion.version.desc()).limit(1)
        )
        if scope is None:
            raise ValidationFailedError("event research scope has not been created")
        return scope

    @staticmethod
    def _initial_refresh_key(scope_version_id: uuid.UUID) -> str:
        """Identity for the one initial refresh of a scope; later runs use a new key."""
        return f"scope:{scope_version_id}:{_INITIAL_REFRESH_KEY_SUFFIX}"

    def _admissible_statement_records(
        self, case_id: uuid.UUID
    ) -> list[tuple[SourceStatement, DocumentVersion]]:
        rows = self._session.execute(
            select(SourceStatement, DocumentVersion)
            .join(SourceSpan, SourceSpan.id == SourceStatement.source_span_id)
            .join(DocumentVersion, DocumentVersion.id == SourceSpan.document_version_id)
            .join(
                CaseDocumentVersion,
                CaseDocumentVersion.document_version_id == DocumentVersion.id,
            )
            .join(
                EvidenceLink,
                EvidenceLink.source_statement_id == SourceStatement.id,
            )
            .join(Thesis, Thesis.id == EvidenceLink.thesis_id)
            .where(CaseDocumentVersion.research_case_id == case_id)
            .where(Thesis.research_case_id == case_id)
            .order_by(SourceStatement.id)
            .distinct()
        )
        return [
            (statement, document)
            for statement, document in rows
            if classify_source(
                document.source_url,
                document.parser_version,
                document.parse_state in {"success", "parsed"},
            ).can_accept
        ]

    def _companies_for(
        self, candidates: Sequence[ResolvedImpactCompany]
    ) -> dict[tuple[str, str, str], Company]:
        keys = {
            self._company_key(candidate.company_name, candidate.type)
            for candidate in candidates
        }
        if not keys:
            return {}
        company_types = {candidate.type for candidate in candidates}
        existing = self._session.scalars(
            select(Company).where(Company.type.in_(company_types))
        )
        companies: dict[tuple[str, str, str], Company] = {}
        for company in existing:
            companies.setdefault(self._company_key(company.name, company.type), company)
            code_identity = self._canonical_identity(company.code.replace("-", " "))
            companies.setdefault(
                (
                    self._canonical_code(company.code),
                    code_identity,
                    company.type,
                ),
                company,
            )
        return companies

    def _resolve_or_create_company(
        self,
        candidate: ResolvedImpactCompany,
        companies: dict[tuple[str, str, str], Company],
    ) -> Company:
        key = self._company_key(candidate.company_name, candidate.type)
        company = companies.get(key)
        if company is not None:
            return company
        company = Company(
            code=key[0],
            name=self._normalized_title(candidate.company_name),
            type=candidate.type,
            created_at=_utcnow(),
        )
        self._session.add(company)
        companies[key] = company
        return company

    @staticmethod
    def _company_key(
        company_name: str,
        company_type: str,
    ) -> tuple[str, str, str]:
        identity = EventImpactResearchService._canonical_identity(company_name)
        return identity.replace(" ", "-"), identity, company_type

    @staticmethod
    def _normalized_title(value: str) -> str:
        return " ".join(normalize("NFKC", value).split())

    @staticmethod
    def _canonical_identity(value: str) -> str:
        return EventImpactResearchService._normalized_title(value).casefold()

    @staticmethod
    def _canonical_code(value: str) -> str:
        return EventImpactResearchService._canonical_identity(value).replace(" ", "-")

    @staticmethod
    def _pending_evidence_explanation(
        candidates: Sequence[ResolvedImpactCompany],
        admissible_by_id: dict[uuid.UUID, SourceStatement],
    ) -> str:
        if any(candidate.source_statement_id is None for candidate in candidates):
            return "Candidate company identified, but no admissible source statement supports it."
        if any(
            candidate.source_statement_id not in admissible_by_id
            for candidate in candidates
        ):
            return "Candidate company relation awaits an admissible same-case source statement."
        return "Candidate hypothesis pending evidence-based impact classification."
