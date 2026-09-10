"""Bounded source preparation for an initialized company-research project."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.models.operational import Job
from app.underwriting.adapters.company_research import (
    AlphabetCompanyResearchAdapter,
    CatlCompanyResearchAdapter,
)
from app.underwriting.domain.company_research import CompanyResearchValidationError
from app.underwriting.domain.company_research_provenance import canonical_source_refs
from app.underwriting.fixtures.alphabet_golden_case import (
    LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256,
    AlphabetGoldenCaseFixture,
    AlphabetGoldenCaseFixtureError,
    load_alphabet_golden_case_fixture,
)
from app.underwriting.fixtures.catl_answerable_case import (
    CatlAnswerableCaseFixture,
    CatlAnswerableCaseFixtureError,
    load_catl_answerable_case_fixture,
)
from app.underwriting.hashing import canonical_hash
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
from app.underwriting.services.company_research_model_builder import (
    validate_company_research_evidence_payload_for_read,
)

_SOURCE_UNAVAILABLE = "company_research_source_unavailable"
_LEGACY_EVIDENCE_PAYLOAD_HASH = (
    "d63debc794d29058af694c0692250b7268012ceceb32e7e6e6518cf9a1930862"
)
_LEGACY_GAPS_PAYLOAD_HASH = (
    "37904a5c3e6be11999d0473d7421fa41a2836b1f0f40b3687f26f556575d6bd5"
)
_LEGACY_GAP_REASONS = {
    "market_price_missing": "No authenticated market price is bundled.",
    "usd_cny_fx_missing": "No authenticated USD/CNY FX rate is bundled.",
}

CompanyResearchSourceFixture = AlphabetGoldenCaseFixture | CatlAnswerableCaseFixture


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

    @staticmethod
    def _adapter(company_external_key: str):
        adapters = (
            AlphabetCompanyResearchAdapter(),
            CatlCompanyResearchAdapter(),
        )
        matches = tuple(
            item for item in adapters if item.supports(company_external_key)
        )
        if len(matches) != 1:
            raise ValidationError("company research source company is unsupported")
        return matches[0]

    @staticmethod
    def _published_source_refs(
        fixture: CompanyResearchSourceFixture,
    ) -> tuple[dict[str, str], ...]:
        return tuple(
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

    @classmethod
    def _source_refs(
        cls, fixture: CompanyResearchSourceFixture
    ) -> tuple[dict[str, str], ...]:
        # Several facts legitimately cite one exact disclosure location.  The
        # artifact owns a source *set*, while facts retain the many-to-one
        # evidence linkage in their payload.
        return canonical_source_refs(cls._published_source_refs(fixture))

    @staticmethod
    def _evidence_payload(
        fixture: CompanyResearchSourceFixture,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "fixture_content_hash": fixture.content_hash,
            "cutoff": fixture.cutoff.isoformat(),
            "company_external_key": fixture.company_external_key,
            "security_external_keys": list(fixture.security_external_keys),
            "facts": [fact.payload() for fact in fixture.facts],
        }
        counterevidence = getattr(fixture, "counterevidence_fact_keys", ())
        verification = getattr(fixture, "next_verification_events", ())
        if counterevidence or verification:
            payload["counterevidence_fact_keys"] = list(counterevidence)
            payload["next_verification_events"] = list(verification)
        return payload

    @staticmethod
    def _gaps_payload(fixture: CompanyResearchSourceFixture) -> dict[str, object]:
        return {
            "fixture_content_hash": fixture.content_hash,
            "company_external_key": fixture.company_external_key,
            "gaps": [gap.payload() for gap in fixture.research_gaps],
        }

    @staticmethod
    def _load_fixture(company_external_key: str) -> CompanyResearchSourceFixture:
        if company_external_key == "US:ALPHABET:COMPANY":
            return load_alphabet_golden_case_fixture()
        if company_external_key == "CN:300750:COMPANY":
            return load_catl_answerable_case_fixture()
        raise ValidationError("company research source company is unsupported")

    def compile_evidence_index(
        self,
        provider_input: CompanyResearchProviderInput,
        *,
        fixture: CompanyResearchSourceFixture | None = None,
    ) -> CompanyResearchEvidenceCompilation:
        source_fixture = (
            fixture
            if fixture is not None
            else self._load_fixture(provider_input.company_external_key)
        )
        adapter = self._adapter(provider_input.company_external_key)
        adapter.validate_source_modules(
            source_fixture.company_external_key, source_fixture.business_modules
        )
        if provider_input.company_external_key != source_fixture.company_external_key:
            raise ValidationError(
                "company research source fixture does not match preparation company"
            )
        return CompanyResearchEvidenceCompilation(
            input_hash=source_fixture.content_hash,
            evidence_index_payload=self._evidence_payload(source_fixture),
            research_gaps_payload=self._gaps_payload(source_fixture),
            source_refs=self._source_refs(source_fixture),
        )

    def compile_governed_evidence_index(
        self,
        provider_input: CompanyResearchProviderInput,
        *,
        source_manifest_hash: str,
        published_source_refs: object,
    ) -> CompanyResearchEvidenceCompilation:
        """Rebuild one exact published evidence contract, including the legacy root."""
        current = self.compile_evidence_index(provider_input)
        current_refs = list(current.source_refs)
        if source_manifest_hash == current.input_hash:
            if published_source_refs != current_refs:
                raise ValidationError("Alphabet evidence sources are not governed")
            return current
        if provider_input.company_external_key != "US:ALPHABET:COMPANY":
            raise ValidationError("company research evidence manifest is not governed")
        if source_manifest_hash != LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256:
            raise ValidationError("Alphabet evidence manifest is not governed")

        evidence_payload = dict(current.evidence_index_payload)
        evidence_payload["fixture_content_hash"] = source_manifest_hash
        gaps_payload = dict(current.research_gaps_payload)
        gaps_payload["fixture_content_hash"] = source_manifest_hash
        raw_gaps = gaps_payload.get("gaps")
        if not isinstance(raw_gaps, list):
            raise ValidationError("Alphabet legacy evidence contract is invalid")
        legacy_gaps = [dict(gap) for gap in raw_gaps]
        for gap in legacy_gaps:
            gap_key = gap.get("gap_key")
            if gap_key in _LEGACY_GAP_REASONS:
                gap["reason"] = _LEGACY_GAP_REASONS[gap_key]
        gaps_payload["gaps"] = legacy_gaps
        if (
            canonical_hash(evidence_payload) != _LEGACY_EVIDENCE_PAYLOAD_HASH
            or canonical_hash(gaps_payload) != _LEGACY_GAPS_PAYLOAD_HASH
        ):
            raise ValidationError("Alphabet legacy evidence contract is invalid")
        fixture = load_alphabet_golden_case_fixture()
        original_refs = list(self._published_source_refs(fixture))
        if published_source_refs == current_refs:
            governed_refs = current.source_refs
        elif published_source_refs == original_refs:
            governed_refs = tuple(original_refs)
        else:
            raise ValidationError("Alphabet legacy evidence sources are not governed")
        return CompanyResearchEvidenceCompilation(
            input_hash=source_manifest_hash,
            evidence_index_payload=evidence_payload,
            research_gaps_payload=gaps_payload,
            source_refs=governed_refs,
        )

    def authenticate_governed_evidence(
        self,
        provider_input: CompanyResearchProviderInput,
        *,
        evidence_input_hash: object,
        evidence_payload: object,
        evidence_source_refs: object,
        gaps_input_hash: object,
        gaps_payload: object,
        gaps_source_refs: object,
    ) -> CompanyResearchEvidenceCompilation:
        """Authenticate one persisted evidence/gaps root pair against a published contract."""
        if not isinstance(evidence_input_hash, str):
            raise ValidationError("Alphabet evidence manifest is not governed")
        expected = self.compile_governed_evidence_index(
            provider_input,
            source_manifest_hash=evidence_input_hash,
            published_source_refs=evidence_source_refs,
        )
        if (
            evidence_payload != expected.evidence_index_payload
            or evidence_source_refs != list(expected.source_refs)
            or gaps_input_hash != expected.input_hash
            or gaps_payload != expected.research_gaps_payload
            or gaps_source_refs != list(expected.source_refs)
        ):
            raise ValidationError("Alphabet evidence contract is invalid")
        return expected


def authenticate_governed_evidence_chain(
    *,
    provider_input: CompanyResearchProviderInput,
    evidence_chain: tuple[CompanyResearchArtifactVersion, ...],
    research_gaps: CompanyResearchArtifactVersion,
) -> CompanyResearchEvidenceCompilation:
    """Authenticate a source root and any explicit legacy review successors."""
    invalid = ValidationError("company research evidence lineage is invalid")
    if not evidence_chain:
        raise invalid
    for row in evidence_chain:
        try:
            validate_company_research_evidence_payload_for_read(row.payload)
        except ValidationError as exc:
            raise invalid from exc
    root = evidence_chain[0]
    try:
        expected = CompanyResearchSourceCompiler().authenticate_governed_evidence(
            provider_input,
            evidence_input_hash=root.input_hash,
            evidence_payload=root.payload,
            evidence_source_refs=root.source_refs,
            gaps_input_hash=research_gaps.input_hash,
            gaps_payload=research_gaps.payload,
            gaps_source_refs=research_gaps.source_refs,
        )
    except ValidationError as exc:
        raise invalid from exc
    root_facts = root.payload.get("facts")
    if not isinstance(root_facts, list) or any(
        isinstance(fact, Mapping) and "review_decision" in fact for fact in root_facts
    ):
        raise invalid
    for parent, successor in zip(evidence_chain, evidence_chain[1:], strict=False):
        if successor.source_refs != parent.source_refs:
            raise invalid
        before_facts = parent.payload.get("facts")
        after_facts = successor.payload.get("facts")
        if (
            not isinstance(before_facts, list)
            or not isinstance(after_facts, list)
            or len(before_facts) != len(after_facts)
        ):
            raise invalid
        changed: list[tuple[int, str, str, dict[str, object]]] = []
        for index, (before, after) in enumerate(
            zip(before_facts, after_facts, strict=True)
        ):
            if before == after:
                continue
            if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                raise invalid
            decision = after.get("review_decision")
            fact_key = after.get("fact_key")
            expected_fact = dict(before)
            if (
                "review_decision" in before
                or not isinstance(decision, str)
                or decision not in {"confirmed", "rejected"}
                or not isinstance(fact_key, str)
                or not fact_key
            ):
                raise invalid
            expected_fact["review_decision"] = decision
            if dict(after) != expected_fact:
                raise invalid
            changed.append((index, fact_key, decision, expected_fact))
        if len(changed) != 1:
            raise invalid
        index, fact_key, decision, expected_fact = changed[0]
        expected_payload = dict(parent.payload)
        expected_facts = [dict(fact) for fact in before_facts]
        expected_facts[index] = expected_fact
        expected_payload["facts"] = expected_facts
        if (
            successor.payload != expected_payload
            or successor.input_hash
            != canonical_hash(
                {
                    "parent": parent.content_hash,
                    "fact_key": fact_key,
                    "decision": decision,
                }
            )
        ):
            raise invalid
    return expected


def authenticate_governed_reviewed_evidence(
    *,
    provider_input: CompanyResearchProviderInput,
    evidence_chain: tuple[CompanyResearchArtifactVersion, ...],
    research_gaps: CompanyResearchArtifactVersion,
) -> CompanyResearchEvidenceCompilation:
    """Preserve the named legacy review boundary for existing readers."""
    return authenticate_governed_evidence_chain(
        provider_input=provider_input,
        evidence_chain=evidence_chain,
        research_gaps=research_gaps,
    )


class CompanyResearchSourceService:
    """Prepare evidence only; assessment and publication belong to later steps."""

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime],
        fixture_loaders: Mapping[
            str, Callable[[], CompanyResearchSourceFixture]
        ]
        | None = None,
        compiler: CompanyResearchSourceCompiler | None = None,
    ) -> None:
        self._session = session
        self._now = now
        self._repository = CompanyResearchRepository(session)
        self._compiler = compiler or CompanyResearchSourceCompiler()
        loaders: dict[str, Callable[[], CompanyResearchSourceFixture]] = {
            "US:ALPHABET:COMPANY": load_alphabet_golden_case_fixture,
            "CN:300750:COMPANY": load_catl_answerable_case_fixture,
        }
        if fixture_loaders is not None:
            loaders.update(fixture_loaders)
        self._fixture_loaders = loaders

    def _load_fixture(
        self, company_external_key: str
    ) -> CompanyResearchSourceFixture:
        loader = self._fixture_loaders.get(company_external_key)
        if loader is None:
            raise ValidationError("company research source company is unsupported")
        return loader()

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
    def _source_refs(
        fixture: CompanyResearchSourceFixture,
    ) -> tuple[dict[str, str], ...]:
        return CompanyResearchSourceCompiler._source_refs(fixture)

    @staticmethod
    def _evidence_payload(
        fixture: CompanyResearchSourceFixture,
    ) -> dict[str, object]:
        return CompanyResearchSourceCompiler._evidence_payload(fixture)

    @staticmethod
    def _gaps_payload(fixture: CompanyResearchSourceFixture) -> dict[str, object]:
        return CompanyResearchSourceCompiler._gaps_payload(fixture)

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
        provider_input = self._provider_input(preparation)
        fixture = self._load_fixture(provider_input.company_external_key)
        return self._compiler.compile_evidence_index(
            provider_input,
            fixture=fixture,
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
            CatlAnswerableCaseFixtureError,
            CompanyResearchValidationError,
            ValidationError,
        ):
            failed, job, _event = self._repository.fail_evidence_preparation(
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
