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


class CompanyResearchSourceService:
    """Prepare evidence only; assessment and publication belong to later steps."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._repository = CompanyResearchRepository(session)
        self._adapter = AlphabetCompanyResearchAdapter()

    @staticmethod
    def _utc(value: object) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _source_refs(fixture: AlphabetGoldenCaseFixture) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "source_role": fact.source_role,
                "source_url": fact.source_url,
                "source_locator": fact.source_locator,
                "raw_hash": fact.raw_hash,
            }
            for fact in sorted(
                fixture.facts,
                key=lambda item: (item.source_role, item.source_url, item.source_locator, item.fact_key),
            )
        )

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

    def _load_fixture(self) -> AlphabetGoldenCaseFixture:
        return load_alphabet_golden_case_fixture()

    def _validate_company(self, preparation: CompanyResearchPreparation, fixture: AlphabetGoldenCaseFixture) -> None:
        project = self._session.get(UnderwritingResearchProject, preparation.project_id)
        if project is None:
            raise ValidationError("company research preparation project is missing")
        company = self._session.scalar(
            select(UnderwritingResearchObject)
            .where(UnderwritingResearchObject.id == project.primary_company_id)
        )
        if company is None or company.external_key != fixture.company_external_key:
            raise ValidationError("Alphabet source fixture does not match preparation company")

    def prepare_evidence_index(self, *, preparation_id: UUID) -> CompanyResearchSourcePreparation:
        """Append source evidence and gaps, or a safe recoverable failure event."""
        now = self._utc(self._now())
        preparation = self._repository.preparation(preparation_id)
        if preparation is None:
            raise ValidationError("company research preparation not found")
        try:
            fixture = self._load_fixture()
            # The relation is intentionally resolved before any durable artifact write.
            self._validate_company(preparation, fixture)
            self._adapter.validate_source_modules(
                fixture.company_external_key, fixture.business_modules
            )
        except (
            AlphabetGoldenCaseFixtureError,
            CompanyResearchValidationError,
            ValidationError,
        ):
            failed, job, event = self._repository.fail_evidence_preparation(
                preparation_id, error_code=_SOURCE_UNAVAILABLE, created_at=now
            )
            return CompanyResearchSourcePreparation(
                status=failed.status, evidence_index=None, research_gaps=None, job=job,
                events=self._repository.events(preparation_id),
                error={"code": _SOURCE_UNAVAILABLE, "recoverable": True},
            )
        preparation, evidence_index, gaps, job, _event = self._repository.complete_evidence_preparation(
            preparation_id,
            input_hash=fixture.content_hash,
            evidence_index_payload=self._evidence_payload(fixture),
            research_gaps_payload=self._gaps_payload(fixture),
            source_refs=self._source_refs(fixture),
            created_at=now,
        )
        return CompanyResearchSourcePreparation(
            status=preparation.status, evidence_index=evidence_index, research_gaps=gaps,
            job=job, events=self._repository.events(preparation_id), error=None,
        )
