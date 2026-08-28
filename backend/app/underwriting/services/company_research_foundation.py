"""Canonical Alphabet foundation shared by initialization and legacy recovery."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.models.ledger import ValidationError
from app.underwriting.adapters.company_research import AlphabetCompanyResearchAdapter
from app.underwriting.domain.company_research import CompanyResearchDefaultPolicy
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    ResearchAgendaInput,
    ResearchScopeInput,
    agenda_items_hash,
)
from app.underwriting.domain.types import InvestmentMandateInput
from app.underwriting.persistence.models import UnderwritingMandateVersion
from app.underwriting.persistence.product_models import (
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchScopeVersion,
)
from app.underwriting.services.product_project import (
    agenda_generator_provenance,
    product_mandate_record_content_hash,
    research_agenda_content_hash,
    research_agenda_payload,
    research_scope_content_hash,
    research_scope_payload,
)

_AGENDA_TEMPLATE_KEY = "company-research-default"


@dataclass(frozen=True, slots=True)
class CompanyResearchFoundationContract:
    mandate: InvestmentMandateInput
    scope: ResearchScopeInput
    agenda_items: tuple[str, ...]
    agenda_generator: AgendaGeneratorInput

    def agenda(self, scope_id: UUID) -> ResearchAgendaInput:
        return ResearchAgendaInput(
            scope_id=scope_id,
            items=self.agenda_items,
            generator=self.agenda_generator,
        )


def alphabet_company_research_foundation_contract(
    *,
    company_external_key: str,
    company_id: UUID,
    security_ids: tuple[UUID, ...],
    request_hash: str,
    strategy_version: str,
) -> CompanyResearchFoundationContract:
    """Build the source-independent first-version Alphabet foundation."""
    adapter = AlphabetCompanyResearchAdapter()
    if not adapter.supports(company_external_key):
        raise ValidationError("company research foundation identity is invalid")
    policy = CompanyResearchDefaultPolicy(strategy_version=strategy_version)
    business_modules = adapter.business_modules(company_external_key)
    agenda_items = tuple(
        module.key for module in (*policy.generic_modules, *business_modules)
    )
    return CompanyResearchFoundationContract(
        mandate=InvestmentMandateInput(
            mandate_key=_AGENDA_TEMPLATE_KEY,
            horizon_years=policy.horizon_years,
            base_currency=policy.base_currency,
            required_return=policy.required_return,
            permanent_loss_limit=policy.permanent_loss_limit,
            comparison_set=("absolute_intrinsic_value",),
        ),
        scope=ResearchScopeInput(
            primary_company_id=company_id,
            target_security_ids=tuple(sorted(security_ids, key=str)),
            industry_ids=(),
            covered_segments=(),
            user_focus=None,
            exclusions=(),
        ),
        agenda_items=agenda_items,
        agenda_generator=AgendaGeneratorInput(
            method=AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
            template_key=_AGENDA_TEMPLATE_KEY,
            template_version=strategy_version,
            model_name=None,
            prompt_template_version=None,
            input_summary_hash=request_hash,
            output_hash=agenda_items_hash(agenda_items),
        ),
    )


def authenticate_company_research_foundation(
    *,
    project_id: UUID,
    contract: CompanyResearchFoundationContract,
    mandate: UnderwritingMandateVersion,
    scope: UnderwritingResearchScopeVersion,
    agenda: UnderwritingResearchAgendaVersion,
) -> None:
    """Authenticate immutable hashes and the exact governed first-version values."""
    expected_scope = research_scope_payload(
        primary_company_id=contract.scope.primary_company_id,
        target_security_ids=contract.scope.target_security_ids,
        industry_ids=contract.scope.industry_ids,
        covered_segments=contract.scope.covered_segments,
        user_focus=contract.scope.user_focus,
        exclusions=contract.scope.exclusions,
    )
    expected_agenda = research_agenda_payload(contract.agenda_items)
    expected_generator = agenda_generator_provenance(contract.agenda_generator)
    expected_mandate_key = f"product.project:{project_id}"
    valid = (
        mandate.project_id == project_id
        and mandate.version == 1
        and mandate.supersedes_id is None
        and mandate.mandate_key == expected_mandate_key
        and mandate.horizon_years == contract.mandate.horizon_years
        and mandate.base_currency == contract.mandate.base_currency
        and mandate.required_return == contract.mandate.required_return
        and mandate.permanent_loss_limit == contract.mandate.permanent_loss_limit
        and tuple(mandate.comparison_set) == contract.mandate.comparison_set
        and mandate.benchmark_key is None
        and mandate.required_excess_return is None
        and mandate.effective_at is not None
        and mandate.expires_at is None
        and mandate.content_hash == product_mandate_record_content_hash(mandate)
        and scope.project_id == project_id
        and scope.version == 1
        and scope.supersedes_id is None
        and scope.payload == expected_scope
        and scope.content_hash
        == research_scope_content_hash(project_id=project_id, payload=scope.payload)
        and agenda.project_id == project_id
        and agenda.version == 1
        and agenda.supersedes_id is None
        and agenda.scope_id == scope.id
        and agenda.payload == expected_agenda
        and agenda.generator_provenance == expected_generator
        and agenda.content_hash
        == research_agenda_content_hash(
            project_id=project_id,
            scope_id=scope.id,
            payload=agenda.payload,
            generator_provenance=agenda.generator_provenance,
        )
    )
    if not valid:
        raise ValidationError("company research foundation contract is invalid")
