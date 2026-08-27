"""Bounded source preparation for an initialized company-research project."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.models.operational import Job
from app.underwriting.adapters.company_research import AlphabetCompanyResearchAdapter
from app.underwriting.fixtures.alphabet_golden_case import (
    AlphabetGoldenCaseFixture,
    AlphabetGoldenCaseFixtureError,
    load_alphabet_golden_case_fixture,
)
from app.underwriting.domain.company_research import CompanyResearchValidationError
from app.underwriting.domain.company_research_provenance import canonical_source_refs
from app.underwriting.persistence.company_research_models import (
    CompanyResearchArtifactVersion,
    CompanyResearchEvent,
    CompanyResearchPreparation,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchRepository,
)
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.product_models import UnderwritingResearchProject


_SOURCE_UNAVAILABLE = "alphabet_source_unavailable"


@dataclass(frozen=True, slots=True)
class CompanyResearchSourcePreparation:
    status: str
    evidence_index: CompanyResearchArtifactVersion | None
    research_gaps: CompanyResearchArtifactVersion | None
    job: Job
    events: tuple[CompanyResearchEvent, ...]
    error: Mapping[str, object] | None


@dataclass(frozen=True, slots=True)
class CompanyResearchEvidenceCompilation:
    """Provider output held in memory until the worker owns a commit slot."""

    input_hash: str
    evidence_index_payload: Mapping[str, object]
    research_gaps_payload: Mapping[str, object]
    source_refs: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class CompanyResearchProviderInput:
    """Immutable, database-free input handed to a source provider."""

    preparation_id: UUID
    project_id: UUID
    company_external_key: str
    request_hash: str
    strategy_version: str


class CompanyResearchSourceCompiler:
    """Compile the bundled source fixture from an immutable provider input."""

    def __init__(self) -> None:
        self._adapter = AlphabetCompanyResearchAdapter()

    @staticmethod
    def _source_refs(fixture: AlphabetGoldenCaseFixture) -> tuple[dict[str, str], ...]:
        refs = (
            {
                "source_role": fact.source_role,
                "source_url": fact.source_url,
                "source_locator": fact.source_locator,
                "raw_hash": fact.raw_hash,
            }
            for fact in sorted(
                fixture.facts,
                key=lambda item: (
                    item.source_role,
                    item.source_url,
                    item.source_locator,
                    item.fact_key,
                ),
            )
        )
        # Several facts legitimately cite one exact disclosure location.  The
        # artifact owns a source *set*, while facts retain the many-to-one
        # evidence linkage in their payload.
        return canonical_source_refs(tuple(refs))

    @staticmethod
    def _evidence_payload(fixture: AlphabetGoldenCaseFixture) -> dict[str, object]:
        return {
            "fixture_content_hash": fixture.content_hash,
            "cutoff": fixture.cutoff.isoformat(),
            "company_external_key": fixture.company_external_key,
            "security_external_keys": list(fixture.security_external_keys),
            "facts": [fact.payload() for fact in fixture.facts],
        }

    @staticmethod
    def _gaps_payload(fixture: AlphabetGoldenCaseFixture) -> dict[str, object]:
        return {
            "fixture_content_hash": fixture.content_hash,
            "company_external_key": fixture.company_external_key,
            "gaps": [gap.payload() for gap in fixture.research_gaps],
        }

    @staticmethod
    def _load_fixture() -> AlphabetGoldenCaseFixture:
        return load_alphabet_golden_case_fixture()

    def compile_evidence_index(
        self,
        provider_input: CompanyResearchProviderInput,
        *,
        fixture: AlphabetGoldenCaseFixture | None = None,
    ) -> CompanyResearchEvidenceCompilation:
        source_fixture = fixture if fixture is not None else self._load_fixture()
        self._adapter.validate_source_modules(
            source_fixture.company_external_key, source_fixture.business_modules
        )
        if provider_input.company_external_key != source_fixture.company_external_key:
            raise ValidationError(
                "Alphabet source fixture does not match preparation company"
            )
        return CompanyResearchEvidenceCompilation(
            input_hash=source_fixture.content_hash,
            evidence_index_payload=self._evidence_payload(source_fixture),
            research_gaps_payload=self._gaps_payload(source_fixture),
            source_refs=self._source_refs(source_fixture),
        )


class CompanyResearchSourceService:
    """Prepare evidence only; assessment and publication belong to later steps."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._repository = CompanyResearchRepository(session)
        self._compiler = CompanyResearchSourceCompiler()

    @staticmethod
    def _utc(value: object) -> datetime:
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _source_refs(fixture: AlphabetGoldenCaseFixture) -> tuple[dict[str, str], ...]:
        return CompanyResearchSourceCompiler._source_refs(fixture)

    @staticmethod
    def _evidence_payload(fixture: AlphabetGoldenCaseFixture) -> dict[str, object]:
        return CompanyResearchSourceCompiler._evidence_payload(fixture)

    @staticmethod
    def _gaps_payload(fixture: AlphabetGoldenCaseFixture) -> dict[str, object]:
        return CompanyResearchSourceCompiler._gaps_payload(fixture)

    def _load_fixture(self) -> AlphabetGoldenCaseFixture:
        return load_alphabet_golden_case_fixture()

    def _provider_input(
        self, preparation: CompanyResearchPreparation
    ) -> CompanyResearchProviderInput:
        project = self._session.get(UnderwritingResearchProject, preparation.project_id)
        if project is None:
            raise ValidationError("company research preparation project is missing")
        company = self._session.scalar(
            select(UnderwritingResearchObject).where(
                UnderwritingResearchObject.id == project.primary_company_id
            )
        )
        if company is None:
            raise ValidationError("company research preparation company is missing")
        return CompanyResearchProviderInput(
            preparation_id=preparation.id,
            project_id=project.id,
            company_external_key=company.external_key,
            request_hash=preparation.request_hash,
            strategy_version=preparation.strategy_version,
        )

    def compile_evidence_index(
        self, *, preparation_id: UUID
    ) -> CompanyResearchEvidenceCompilation:
        """Read and validate source material without mutating durable state.

        The worker calls this outside its output transaction.  Keeping this
        boundary explicit prevents a slow file/provider operation from holding
        the preparation or Job row lock.
        """
        preparation = self._repository.preparation(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        return self._compiler.compile_evidence_index(
            self._provider_input(preparation), fixture=self._load_fixture()
        )

    def prepare_evidence_index(
        self, *, preparation_id: UUID
    ) -> CompanyResearchSourcePreparation:
        """Append source evidence and gaps, or a safe recoverable failure event."""
        now = self._utc(self._now())
        try:
            compiled = self.compile_evidence_index(preparation_id=preparation_id)
        except (
            AlphabetGoldenCaseFixtureError,
            CompanyResearchValidationError,
            ValidationError,
        ):
            failed, job, event = self._repository.fail_evidence_preparation(
                preparation_id, error_code=_SOURCE_UNAVAILABLE, created_at=now
            )
            return CompanyResearchSourcePreparation(
                status=failed.status,
                evidence_index=None,
                research_gaps=None,
                job=job,
                events=self._repository.events(preparation_id),
                error={"code": _SOURCE_UNAVAILABLE, "recoverable": True},
            )
        preparation, evidence_index, gaps, job, _event = (
            self._repository.complete_evidence_preparation(
                preparation_id,
                input_hash=compiled.input_hash,
                evidence_index_payload=compiled.evidence_index_payload,
                research_gaps_payload=compiled.research_gaps_payload,
                source_refs=compiled.source_refs,
                created_at=now,
            )
        )
        return CompanyResearchSourcePreparation(
            status=preparation.status,
            evidence_index=evidence_index,
            research_gaps=gaps,
            job=job,
            events=self._repository.events(preparation_id),
            error=None,
        )
