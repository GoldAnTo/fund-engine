"""Source-backed, scope-bound company impact candidate resolution."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol, Sequence
from unicodedata import normalize

from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import NotFoundError, ValidationFailedError
from app.models.event_impact import (
    CompanyImpactObservation,
    CompanyImpactRelation,
    CompanyIdentityAlias,
    EventImpactRefreshClaim,
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
from app.models.operational import ResearchRun, ResearchTask
from app.services.source_admission import classify_source
from app.repositories.outbox import emit_event


_INITIAL_REFRESH_KEY_SUFFIX = "initial"
_REFRESH_REQUEST_EVENT_TYPE = "event_impact_refresh_requested"


def _before_refresh_claim_insert() -> None:
    """Test seam for proving the unique-claim race without sleeps."""


def _before_refresh_claim_lock() -> None:
    """Test seam for synchronizing competing worker execution attempts."""


def _before_company_insert() -> None:
    """Test seam for proving cross-case canonical-company races without sleeps."""


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


@dataclass(frozen=True)
class _ResolvedFactor:
    factor: EventResearchScopeFactor
    candidates: Sequence[ResolvedImpactCompany]


class EventImpactResearchService:
    def __init__(self, session: Session, resolver: ImpactResolver | None = None) -> None:
        self._session = session
        self._resolver = resolver or _EmptyImpactResolver()

    def refresh(
        self,
        case_id: uuid.UUID,
        *,
        scope_version_id: uuid.UUID | None = None,
        refresh_key: str | None = None,
        output_slot=None,
    ) -> ImpactRefreshResult:
        scope = self._scope_for(case_id, scope_version_id)
        refresh_key = refresh_key or self._initial_refresh_key(scope.id)
        self._claim_refresh(case_id, scope.id, refresh_key)
        # This row is the durable execution mutex.  The lock covers resolver
        # output through the caller's commit, so competing workers observe the
        # completed append-only trace instead of writing a second one.
        _before_refresh_claim_lock()
        self._session.scalar(
            select(EventImpactRefreshClaim)
            .where(EventImpactRefreshClaim.scope_version_id == scope.id)
            .where(EventImpactRefreshClaim.refresh_key == refresh_key)
            .with_for_update()
        )
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

        # Providers are deliberately run before any refresh output is added
        # to this Session.  If one factor fails, the task failure commit cannot
        # accidentally publish the hypotheses from factors that happened to
        # resolve first.
        resolved_factors: list[_ResolvedFactor] = []
        for factor in factors:
            candidates = list(
                self._resolver.resolve(
                    factor_statement=factor.statement,
                    statements=admissible_statements,
                )
            )
            resolved_factors.append(_ResolvedFactor(factor, candidates))

        if output_slot is not None and not output_slot():
            return ImpactRefreshResult(0, 0, 0, 0)

        return self._append_refresh_output(
            case_id=case_id,
            scope=scope,
            refresh_key=refresh_key,
            resolved_factors=resolved_factors,
            admissible_by_id=admissible_by_id,
            statement_dates=statement_dates,
        )

    def _append_refresh_output(
        self,
        *,
        case_id: uuid.UUID,
        scope: EventResearchScopeVersion,
        refresh_key: str,
        resolved_factors: Sequence[_ResolvedFactor],
        admissible_by_id: dict[uuid.UUID, SourceStatement],
        statement_dates: dict[uuid.UUID, date],
    ) -> ImpactRefreshResult:
        """Append a complete refresh trace in one savepoint or append none."""
        with self._session.begin_nested():
            resolved_candidates: list[_ResolvedCandidate] = []
            for resolved_factor in resolved_factors:
                factor = resolved_factor.factor
                candidates = resolved_factor.candidates
                hypothesis = EventImpactHypothesis(
                    research_case_id=case_id,
                    scope_version_id=scope.id,
                    statement=factor.statement,
                    classification="candidate",
                    rank=factor.position,
                    score_components={"refresh_key": refresh_key},
                    explanation=self._pending_evidence_explanation(
                        candidates, admissible_by_id
                    ),
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
            hypotheses_created=len(resolved_factors) + unresolved_candidate_count,
            relations_created=relations_created,
            source_rejected_count=source_rejected_count,
            unresolved_candidate_count=unresolved_candidate_count,
        )

    def schedule_refresh(
        self, case_id: uuid.UUID, scope_version_id: uuid.UUID, run_id: uuid.UUID
    ) -> EventImpactRefreshClaim:
        """Atomically claim and enqueue one executable scope refresh task."""
        scope = self._scope_for(case_id, scope_version_id)
        run = self._session.get(ResearchRun, run_id)
        if run is None or run.research_case_id != case_id:
            raise ValidationFailedError("research run does not belong to the event research case")
        refresh_key = self._initial_refresh_key(scope.id)
        claim, created = self._claim_refresh(
            case_id, scope.id, refresh_key, run_id=run_id
        )
        task_query = f"impact_refresh:{claim.id}:{scope.id}:{refresh_key}"
        active_task = self._session.scalar(
            select(ResearchTask.id)
            .where(ResearchTask.run_id == run_id)
            .where(ResearchTask.task_type == "impact_refresh")
            .where(ResearchTask.query == task_query)
            .where(ResearchTask.status.in_(("queued", "running", "done")))
            .limit(1)
        )
        if active_task is None:
            self._session.add(
                ResearchTask(
                    run_id=run_id,
                    research_case_id=case_id,
                    thesis_id=None,
                    task_type="impact_refresh",
                    query=task_query,
                    result=None,
                    created_at=_utcnow(),
                    updated_at=_utcnow(),
                )
            )
        if created:
            emit_event(
                self._session,
                type=_REFRESH_REQUEST_EVENT_TYPE,
                aggregate_type="event_impact_refresh_claim",
                aggregate_id=claim.id,
                ref_type="research_case",
                ref_id=case_id,
                origin="operational",
                payload={
                    "research_case_id": str(case_id),
                    "scope_version_id": str(scope.id),
                    "refresh_claim_id": str(claim.id),
                    "run_id": str(run_id),
                },
            )
        self._session.flush()
        return claim

    def _claim_refresh(
        self,
        case_id: uuid.UUID,
        scope_version_id: uuid.UUID,
        refresh_key: str,
        *,
        run_id: uuid.UUID | None = None,
    ) -> tuple[EventImpactRefreshClaim, bool]:
        """Use the unique ledger claim as the concurrency boundary."""
        existing = self._session.scalar(
            select(EventImpactRefreshClaim)
            .where(EventImpactRefreshClaim.scope_version_id == scope_version_id)
            .where(EventImpactRefreshClaim.refresh_key == refresh_key)
            .limit(1)
        )
        if existing is not None:
            return existing, False
        try:
            _before_refresh_claim_insert()
            with self._session.begin_nested():
                claim = EventImpactRefreshClaim(
                    research_case_id=case_id,
                    scope_version_id=scope_version_id,
                    run_id=run_id,
                    refresh_key=refresh_key,
                    created_at=_utcnow(),
                )
                self._session.add(claim)
                self._session.flush()
            return claim, True
        except IntegrityError:
            claim = self._session.scalar(
                select(EventImpactRefreshClaim)
                .where(EventImpactRefreshClaim.scope_version_id == scope_version_id)
                .where(EventImpactRefreshClaim.refresh_key == refresh_key)
                .limit(1)
            )
            if claim is None:  # pragma: no cover - protects unusual DB drivers
                raise
            return claim, False

    def _legacy_schedule_event(
        self, case_id: uuid.UUID, scope: EventResearchScopeVersion
    ) -> DomainEvent | None:
        """Retained only for audit compatibility; tasks/claims drive execution."""
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
        identities = {key[1] for key in keys}
        company_types = {candidate.type for candidate in candidates}
        # The steady-state lookup is deliberately restricted to the two
        # persisted identities.  Migration 0021 backfills aliases for legacy
        # immutable rows, so refresh never falls back to a type-wide (or even
        # repeated lower/trim) scan of companies with a NULL identity.
        existing = list(
            self._session.scalars(
                select(Company)
                .where(Company.type.in_(company_types))
                .where(Company.canonical_identity.in_(identities))
            )
        )
        existing.extend(
            self._session.scalars(
                select(Company)
                .join(
                    CompanyIdentityAlias,
                    CompanyIdentityAlias.company_id == Company.id,
                )
                .where(CompanyIdentityAlias.company_type.in_(company_types))
                .where(CompanyIdentityAlias.canonical_identity.in_(identities))
            )
        )
        companies: dict[tuple[str, str, str], Company] = {}
        for company in {company.id: company for company in existing}.values():
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
        try:
            with self._session.begin_nested():
                _before_company_insert()
                self._session.add(company)
                self._session.flush()
            companies[key] = company
            return company
        except IntegrityError:
            winner = self._session.scalar(
                select(Company)
                .where(Company.type == candidate.type)
                .where(Company.canonical_identity == key[1])
                .limit(1)
            )
            if winner is None:
                raise
            companies[key] = winner
            return winner

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
