"""Fail-closed recovery for a legacy Company Research draft missing its basis."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
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
from app.underwriting.services.company_research_market_inputs import (
    CompanyResearchMarketInputs,
)
from app.underwriting.services.company_research_foundation import (
    alphabet_company_research_foundation_contract,
    authenticate_company_research_foundation,
)
from app.underwriting.services.market_snapshots import (
    MarketSnapshotService,
    WorkspaceMarketReferences,
)
from app.underwriting.services.product_project import ResearchProjectService
from app.underwriting.services.company_research_sources import (
    CompanyResearchProviderInput,
    CompanyResearchSourceCompiler,
)
from app.underwriting.hashing import canonical_hash
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
        self._market_inputs = CompanyResearchMarketInputs(session, now=now)
        self._market_snapshots = MarketSnapshotService(session, now=now)

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
        project = state.project
        if project is None:
            raise ValidationError(
                "company research historical basis recovery project is missing"
            )
        company = state.company
        securities = state.securities
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

    def _validate_reviewed_evidence(
        self,
        state: CompanyResearchBasisRecoveryState,
        *,
        boundary: CompanyResearchHistoricalBoundary,
        security_keys: tuple[str, ...],
    ) -> None:
        expected = CompanyResearchSourceCompiler().compile_evidence_index(
            CompanyResearchProviderInput(
                preparation_id=state.preparation.id,
                project_id=state.preparation.project_id,
                company_external_key=_ALPHABET_COMPANY_KEY,
                request_hash=state.preparation.request_hash,
                strategy_version=state.preparation.strategy_version,
            )
        )
        evidence_chain = state.evidence_chain
        gaps_chain = state.research_gaps_chain
        root = evidence_chain[0]
        if (
            root.input_hash != expected.input_hash
            or root.payload != expected.evidence_index_payload
            or tuple(root.source_refs) != expected.source_refs
            or len(gaps_chain) != 1
            or gaps_chain[0].input_hash != expected.input_hash
            or gaps_chain[0].payload != expected.research_gaps_payload
            or tuple(gaps_chain[0].source_refs) != expected.source_refs
            or expected.input_hash != boundary.basis_input.source_manifest_hash
            or tuple(
                expected.evidence_index_payload.get("security_external_keys", ())
            )
            != security_keys
        ):
            raise ValidationError(
                "company research historical basis recovery evidence is invalid"
            )
        for parent, successor in zip(
            evidence_chain, evidence_chain[1:], strict=False
        ):
            if tuple(successor.source_refs) != expected.source_refs:
                raise ValidationError(
                    "company research historical basis recovery evidence is invalid"
                )
            parent_facts = parent.payload.get("facts")
            successor_facts = successor.payload.get("facts")
            if (
                not isinstance(parent_facts, list)
                or not isinstance(successor_facts, list)
                or len(parent_facts) != len(successor_facts)
            ):
                raise ValidationError(
                    "company research historical basis recovery evidence is invalid"
                )
            changes: list[tuple[int, str, str, dict[str, object]]] = []
            for index, (before, after) in enumerate(
                zip(parent_facts, successor_facts, strict=True)
            ):
                if before == after:
                    continue
                if not isinstance(before, Mapping) or not isinstance(after, Mapping):
                    raise ValidationError(
                        "company research historical basis recovery evidence is invalid"
                    )
                decision = after.get("review_decision")
                expected_after = dict(before)
                if (
                    "review_decision" in before
                    or decision not in _TERMINAL_REVIEW_DECISIONS
                ):
                    raise ValidationError(
                        "company research historical basis recovery evidence is invalid"
                    )
                expected_after["review_decision"] = decision
                if after != expected_after:
                    raise ValidationError(
                        "company research historical basis recovery evidence is invalid"
                    )
                changes.append(
                    (index, str(after.get("fact_key")), str(decision), expected_after)
                )
            if len(changes) != 1:
                raise ValidationError(
                    "company research historical basis recovery evidence is invalid"
                )
            fact_index, fact_key, decision, expected_fact = changes[0]
            expected_payload = dict(parent.payload)
            expected_facts = deepcopy(parent_facts)
            expected_facts[fact_index] = expected_fact
            expected_payload["facts"] = expected_facts
            if canonical_hash(successor.payload) != canonical_hash(expected_payload):
                raise ValidationError(
                    "company research historical basis recovery evidence is invalid"
                )
            if successor.input_hash != canonical_hash(
                {
                    "parent": parent.content_hash,
                    "fact_key": fact_key,
                    "decision": decision,
                }
            ):
                raise ValidationError(
                    "company research historical basis recovery evidence is invalid"
                )
        head_facts = state.evidence.payload.get("facts")
        if (
            not isinstance(head_facts, list)
            or len(head_facts)
            != len(expected.evidence_index_payload.get("facts", ()))
            or any(
                not isinstance(fact, Mapping)
                or fact.get("review_decision") not in _TERMINAL_REVIEW_DECISIONS
                for fact in head_facts
            )
        ):
            raise ValidationError(
                "company research historical basis recovery evidence is invalid"
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
            return self._company.authenticate_governed_historical_basis(
                basis,
                expected_input=boundary.basis_input,
                expected_content_hash=boundary.basis_content_hash,
            )
        except ValidationError as exc:
            raise ValidationError(
                "company research historical basis recovery found a conflicting basis"
            ) from exc

    def _authenticate_workspace_references(
        self,
        state: CompanyResearchBasisRecoveryState,
        *,
        cutoff: datetime,
    ) -> None:
        content = state.draft_content
        if (
            content.mandate_id is None
            or content.scope_id is None
            or content.agenda_id is None
            or content.mandate_id != state.mandate_head_id
            or content.scope_id != state.scope_head_id
            or content.agenda_id != state.agenda_head_id
            or not content.price_snapshot_ids
            or not content.fx_snapshot_ids
            or content.capital_structure_snapshot_id is None
            or not content.security_rights_ids
        ):
            raise ValidationError(
                "company research historical basis recovery draft is incomplete"
            )
        if (
            state.company is None
            or state.mandate is None
            or state.scope is None
            or state.agenda is None
        ):
            raise ValidationError(
                "company research historical basis recovery draft is incomplete"
            )
        references = WorkspaceMarketReferences(
            mandate_id=content.mandate_id,
            scope_id=content.scope_id,
            agenda_id=content.agenda_id,
            price_snapshot_ids=content.price_snapshot_ids,
            fx_snapshot_ids=content.fx_snapshot_ids,
            capital_structure_snapshot_id=content.capital_structure_snapshot_id,
            security_rights_ids=content.security_rights_ids,
        )
        try:
            foundation = alphabet_company_research_foundation_contract(
                company_external_key=state.company.external_key,
                company_id=state.company.id,
                security_ids=tuple(row.id for row in state.securities),
                request_hash=state.preparation.request_hash,
                strategy_version=state.preparation.strategy_version,
            )
            authenticate_company_research_foundation(
                project_id=state.preparation.project_id,
                contract=foundation,
                mandate=state.mandate,
                scope=state.scope,
                agenda=state.agenda,
            )
            self._market_snapshots.workspace_reference_context(
                state.preparation.project_id,
                references,
                as_of=cutoff,
                model_currency="CNY",
            )
            expected = self._market_inputs.resolve(
                project_id=state.preparation.project_id,
                cutoff_at=cutoff,
                fresh=True,
            )
        except ValidationError as exc:
            raise ValidationError(
                "company research historical basis recovery draft is incomplete"
            ) from exc
        if (
            content.price_snapshot_ids != expected.price_snapshot_ids
            or content.fx_snapshot_ids != expected.fx_snapshot_ids
            or content.capital_structure_snapshot_id
            != expected.capital_structure_snapshot_id
            or content.security_rights_ids != expected.security_rights_ids
        ):
            raise ValidationError(
                "company research historical basis recovery draft is incomplete"
            )

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
        try:
            self._validate_reviewed_evidence(
                state, boundary=boundary, security_keys=security_keys
            )
        except ValidationError as exc:
            raise ValidationError(
                "company research historical basis recovery evidence is invalid"
            ) from exc
        self._authenticate_workspace_references(state, cutoff=boundary.cutoff_at)
        content = state.draft_content
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
