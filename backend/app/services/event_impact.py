"""Source-backed, scope-bound company impact candidate resolution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol, Sequence
from unicodedata import normalize

from sqlalchemy import or_, select
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ResolvedImpactCompany:
    company_name: str
    company_type: str
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

    def refresh(self, case_id: uuid.UUID) -> ImpactRefreshResult:
        scope = self._latest_scope(case_id)
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
                score_components={},
                explanation=self._pending_evidence_explanation(candidates, admissible_by_id),
                created_at=_utcnow(),
            )
            self._session.add(hypothesis)
            resolved_candidates.extend(
                _ResolvedCandidate(hypothesis=hypothesis, candidate=candidate)
                for candidate in candidates
            )

        companies = self._companies_for(
            [entry.candidate for entry in resolved_candidates]
        )
        resolved_companies = [
            (entry, self._resolve_or_create_company(entry.candidate, companies))
            for entry in resolved_candidates
        ]
        # New Company ids are needed by the append-only relation rows.  This
        # is a single bulk flush, rather than a lookup/flush for each candidate.
        self._session.flush()

        relations_created = 0
        source_rejected_count = 0
        unresolved_candidate_count = 0
        relation_sources: list[
            tuple[CompanyImpactRelation, ResolvedImpactCompany, SourceStatement]
        ] = []
        for entry, company in resolved_companies:
            candidate = entry.candidate
            if candidate.source_statement_id is None:
                unresolved_candidate_count += 1
                continue
            statement = admissible_by_id.get(candidate.source_statement_id)
            if statement is None:
                source_rejected_count += 1
                continue
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
            hypotheses_created=len(factors),
            relations_created=relations_created,
            source_rejected_count=source_rejected_count,
            unresolved_candidate_count=unresolved_candidate_count,
        )

    def _latest_scope(self, case_id: uuid.UUID) -> EventResearchScopeVersion:
        if self._session.scalar(
            select(EventResearchBrief.id).where(
                EventResearchBrief.research_case_id == case_id
            )
        ) is None:
            raise NotFoundError("event research case not found")
        scope = self._session.scalar(
            select(EventResearchScopeVersion)
            .where(EventResearchScopeVersion.research_case_id == case_id)
            .order_by(EventResearchScopeVersion.version.desc())
            .limit(1)
        )
        if scope is None:
            raise ValidationFailedError("event research scope has not been created")
        return scope

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
            self._company_key(candidate.company_name, candidate.company_type)
            for candidate in candidates
        }
        if not keys:
            return {}
        codes = {key[0] for key in keys}
        names = {candidate.company_name.strip() for candidate in candidates}
        company_types = {candidate.company_type for candidate in candidates}
        existing = self._session.scalars(
            select(Company).where(
                Company.type.in_(company_types),
                or_(Company.code.in_(codes), Company.name.in_(names)),
            )
        )
        companies: dict[tuple[str, str, str], Company] = {}
        for company in existing:
            company_name = self._company_key(company.name, company.type)[1]
            company_code = self._company_key(company.name, company.type, company.code)[0]
            for key in keys:
                if company.type == key[2] and (
                    company_name == key[1] or company_code == key[0]
                ):
                    companies.setdefault(key, company)
        return companies

    def _resolve_or_create_company(
        self,
        candidate: ResolvedImpactCompany,
        companies: dict[tuple[str, str, str], Company],
    ) -> Company:
        key = self._company_key(candidate.company_name, candidate.company_type)
        company = companies.get(key)
        if company is not None:
            return company
        company = Company(
            code=key[0],
            name=candidate.company_name.strip(),
            type=candidate.company_type,
            created_at=_utcnow(),
        )
        self._session.add(company)
        companies[key] = company
        return company

    @staticmethod
    def _company_key(
        company_name: str,
        company_type: str,
        code: str | None = None,
    ) -> tuple[str, str, str]:
        normalized_name = " ".join(normalize("NFKC", company_name).split()).casefold()
        normalized_code = (
            " ".join(normalize("NFKC", code).split()).casefold()
            if code is not None
            else normalized_name.replace(" ", "-")
        )
        return normalized_code, normalized_name, company_type

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
