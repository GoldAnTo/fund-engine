"""Fail-closed recovery for a legacy Company Research draft missing its basis."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.adapters.company_research import AlphabetCompanyResearchAdapter
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.persistence.company_research_repository import (
    CompanyResearchAuthenticatedHistoricalBasis,
    CompanyResearchBasisRecoveryState,
    CompanyResearchRepository,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_boundary import (
    CompanyResearchHistoricalBoundary,
    resolve_alphabet_company_research_boundary,
)
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)

_ALPHABET_COMPANY_KEY = "US:ALPHABET:COMPANY"
_TERMINAL_REVIEW_DECISIONS = frozenset({"confirmed", "rejected"})


class CompanyResearchHistoricalBasisRecovery:
    """Repair only the missing basis of one authenticated legacy workspace."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]) -> None:
        self._now = now
        self._company = CompanyResearchRepository(session)
        self._product_repository = ProductRepository(session)
        self._products = ResearchProjectService(session, now=now)
        self._drafts = WorkspaceDraftService(session, now=now)

    def _now_utc(self) -> datetime:
        value = self._now()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValidationError("clock must be a timezone-aware datetime")
        return value.astimezone(UTC)

    @staticmethod
    def _eligible(state: CompanyResearchBasisRecoveryState) -> None:
        if (
            state.preparation.status != "blocked"
            or state.preparation.current_step != "model_bundle"
            or state.preparation.progress != 30
            or state.preparation.last_error_code != "validation_failed"
            or state.job.status != "failed"
            or state.job.step != "model_bundle"
        ):
            raise ValidationError(
                "company research preparation is not eligible for basis recovery"
            )

    def _project_security_keys(
        self, state: CompanyResearchBasisRecoveryState
    ) -> tuple[str, ...]:
        project = self._products.project(state.preparation.project_id)
        if project is None:
            raise ValidationError(
                "company research historical basis recovery project is missing"
            )
        company = self._product_repository.object(project.primary_company_id)
        securities = tuple(
            self._product_repository.object(security_id)
            for security_id in project.target_security_ids
        )
        if (
            company is None
            or company.kind != "company"
            or company.external_key != _ALPHABET_COMPANY_KEY
            or not securities
            or any(security is None for security in securities)
            or any(
                security.kind != "security"
                for security in securities
                if security is not None
            )
        ):
            raise ValidationError(
                "company research historical basis recovery project identity is invalid"
            )
        security_keys = tuple(
            sorted(
                security.external_key
                for security in securities
                if security is not None
            )
        )
        fixture = load_alphabet_golden_case_fixture()
        template = AlphabetCompanyResearchAdapter().model_template()
        if (
            fixture.company_external_key != _ALPHABET_COMPANY_KEY
            or template.company_external_key != _ALPHABET_COMPANY_KEY
            or fixture.security_external_keys != template.security_external_keys
            or security_keys != fixture.security_external_keys
        ):
            raise ValidationError(
                "company research historical basis recovery project identity is invalid"
            )
        return security_keys

    @staticmethod
    def _validate_reviewed_evidence(
        state: CompanyResearchBasisRecoveryState,
        *,
        boundary: CompanyResearchHistoricalBoundary,
        security_keys: tuple[str, ...],
    ) -> None:
        evidence = state.evidence.payload
        gaps = state.research_gaps.payload
        facts = evidence.get("facts") if isinstance(evidence, Mapping) else None
        evidence_security_keys = (
            evidence.get("security_external_keys")
            if isinstance(evidence, Mapping)
            else None
        )
        valid = (
            isinstance(evidence, Mapping)
            and isinstance(gaps, Mapping)
            and evidence.get("fixture_content_hash")
            == boundary.basis_input.source_manifest_hash
            and gaps.get("fixture_content_hash")
            == boundary.basis_input.source_manifest_hash
            and CompanyResearchRepository.evidence_cutoff(state.evidence)
            == boundary.cutoff_at
            and evidence.get("company_external_key") == _ALPHABET_COMPANY_KEY
            and gaps.get("company_external_key") == _ALPHABET_COMPANY_KEY
            and isinstance(evidence_security_keys, list)
            and all(isinstance(value, str) for value in evidence_security_keys)
            and tuple(evidence_security_keys) == security_keys
            and isinstance(facts, list)
            and bool(facts)
            and all(
                isinstance(fact, Mapping)
                and fact.get("company_external_key") == _ALPHABET_COMPANY_KEY
                and fact.get("review_decision") in _TERMINAL_REVIEW_DECISIONS
                for fact in facts
            )
        )
        if not valid:
            raise ValidationError(
                "company research historical basis recovery evidence is invalid"
            )

    @staticmethod
    def _matches_boundary(
        basis: CompanyResearchAuthenticatedHistoricalBasis,
        boundary: CompanyResearchHistoricalBoundary,
    ) -> bool:
        expected = boundary.basis_input
        return (
            basis.cutoff_at == boundary.cutoff_at
            and basis.source_manifest_hash == expected.source_manifest_hash
            and basis.definition_bundle_hash == expected.definition_bundle_hash
            and basis.parser_bundle_hash == expected.parser_bundle_hash
            and basis.content_hash == boundary.basis_content_hash
        )

    def _authenticate_exact_basis(
        self,
        basis_id: UUID,
        boundary: CompanyResearchHistoricalBoundary,
    ) -> CompanyResearchAuthenticatedHistoricalBasis:
        basis = self._product_repository.product_basis(basis_id)
        if basis is None:
            raise ValidationError(
                "company research historical basis recovery found a conflicting basis"
            )
        try:
            authenticated = self._company.authenticate_historical_basis(basis)
        except ValidationError as exc:
            raise ValidationError(
                "company research historical basis recovery found a conflicting basis"
            ) from exc
        if not self._matches_boundary(authenticated, boundary):
            raise ValidationError(
                "company research historical basis recovery found a conflicting basis"
            )
        return authenticated

    def recover(self, preparation_id: UUID) -> UUID:
        state = self._company.lock_basis_recovery_state(preparation_id)
        self._eligible(state)
        security_keys = self._project_security_keys(state)
        try:
            cutoff = self._company.evidence_cutoff(state.evidence)
            boundary = resolve_alphabet_company_research_boundary(cutoff)
        except ValidationError as exc:
            raise ValidationError(
                "company research historical basis recovery evidence is invalid"
            ) from exc
        self._validate_reviewed_evidence(
            state, boundary=boundary, security_keys=security_keys
        )
        content = WorkspaceDraftService._content(state.draft.content)
        if (
            content.mandate_id is None
            or content.scope_id is None
            or content.agenda_id is None
            or not content.price_snapshot_ids
            or not content.fx_snapshot_ids
            or content.capital_structure_snapshot_id is None
            or not content.security_rights_ids
        ):
            raise ValidationError(
                "company research historical basis recovery draft is incomplete"
            )
        if content.historical_basis_id is not None:
            return self._authenticate_exact_basis(
                content.historical_basis_id, boundary
            ).id

        basis = self._product_repository.product_basis_by_content_hash(
            boundary.basis_content_hash
        )
        if basis is None:
            basis = self._products.create_historical_basis(boundary.basis_input)
        authenticated = self._authenticate_exact_basis(basis.id, boundary)
        created_at = self._now_utc()
        prior_lock_version = state.draft.lock_version
        recovered = self._drafts.save(
            state.preparation.project_id,
            expected_lock_version=prior_lock_version,
            patch=WorkspaceDraftPatch(historical_basis_id=authenticated.id),
        )
        self._company.append_event(
            preparation_id=state.preparation.id,
            event_type="historical_basis_recovered",
            payload={
                "basis_id": str(authenticated.id),
                "cutoff": boundary.cutoff_at.isoformat(),
                "source_manifest_hash": boundary.basis_input.source_manifest_hash,
                "prior_lock_version": prior_lock_version,
                "new_lock_version": recovered.lock_version,
                "created_at": created_at.isoformat(),
            },
            created_at=created_at,
        )
        return authenticated.id
