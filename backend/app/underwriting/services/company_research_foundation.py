"""Canonical Alphabet foundation shared by initialization and legacy recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.adapters.company_research import AlphabetCompanyResearchAdapter
from app.underwriting.domain.company_research import (
    CompanyResearchCompany,
    CompanyResearchDefaultPolicy,
    CompanyResearchIdentitySet,
    CompanyResearchPreview,
    CompanyResearchSecurity,
    build_company_research_preview,
)
from app.underwriting.domain.types import ResearchObjectKind
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    ResearchAgendaInput,
    ResearchScopeInput,
    agenda_items_hash,
)
from app.underwriting.domain.types import InvestmentMandateInput
from app.underwriting.persistence.models import UnderwritingMandateVersion
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.persistence.product_models import (
    UnderwritingResearchAgendaVersion,
    UnderwritingResearchProject,
    UnderwritingResearchScopeVersion,
)
from app.underwriting.services.product_project import (
    agenda_generator_provenance,
    product_mandate_content_hash,
    research_agenda_content_hash,
    research_agenda_payload,
    research_project_content_hash,
    research_scope_content_hash,
    research_scope_payload,
)

_AGENDA_TEMPLATE_KEY = "company-research-default"


def build_alphabet_company_research_preview_at_cutoff(
    session: Session, *, company_id: UUID, cutoff_at: datetime
) -> CompanyResearchPreview:
    """Rebuild the exact canonical preview at a caller-supplied historical cutoff."""
    repository = ProductRepository(session)
    company = session.scalar(
        select(UnderwritingResearchObject)
        .where(UnderwritingResearchObject.id == company_id)
        .execution_options(populate_existing=True)
    )
    if company is None or company.kind != ResearchObjectKind.COMPANY.value:
        raise ValidationError("company research foundation identity is invalid")
    adapter = AlphabetCompanyResearchAdapter()
    if not adapter.supports(company.external_key):
        raise ValidationError("company research foundation identity is invalid")
    company_identity = repository.effective_identity(company_id, cutoff_at)
    if company_identity is None:
        raise ValidationError("company research foundation identity is invalid")
    rows = tuple(
        session.execute(
            repository._effective_company_children_statement(
                {company_id}, cutoff_at
            )
            .order_by("parent_id", "id")
            .execution_options(populate_existing=True)
        ).tuples()
    )
    if not rows:
        raise ValidationError("company research foundation identity is invalid")
    identities = CompanyResearchIdentitySet(
        company=CompanyResearchCompany(
            object_id=company_id,
            external_key=company.external_key,
            canonical_name=company_identity.canonical_name,
        ),
        securities=tuple(
            CompanyResearchSecurity(
                object_id=security.id,
                company_id=company_id,
                external_key=security.external_key,
                canonical_name=identity.canonical_name,
                symbol=identity.symbol,
                exchange=identity.exchange,
                share_class=identity.share_class,
                trading_currency=identity.trading_currency,
            )
            for _parent_id, security, identity in rows
        ),
    )
    return build_company_research_preview(
        adapter=adapter,
        identities=identities,
        cutoff_at=cutoff_at,
    )


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
    project: UnderwritingResearchProject,
    preparation_created_at: datetime,
    contract: CompanyResearchFoundationContract,
    mandate: UnderwritingMandateVersion,
    scope: UnderwritingResearchScopeVersion,
    agenda: UnderwritingResearchAgendaVersion,
    authenticated_legacy_request_cutoff_at: datetime | None = None,
) -> None:
    """Authenticate immutable hashes and the exact governed first-version values."""
    def invalid() -> ValidationError:
        return ValidationError("company research foundation contract is invalid")

    def uuid(value: object) -> UUID:
        if type(value) is not UUID:
            raise invalid()
        return value

    def integer(value: object) -> int:
        if type(value) is not int:
            raise invalid()
        return value

    def text(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise invalid()
        return value

    def optional_text(value: object) -> str | None:
        if value is None:
            return None
        return text(value)

    def decimal(value: object) -> Decimal:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise invalid()
        return value

    def stored_utc(value: object) -> datetime:
        if not isinstance(value, datetime):
            raise invalid()
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        if value.utcoffset() is None:
            raise invalid()
        return value.astimezone(UTC)

    def string_list(value: object) -> tuple[str, ...]:
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise invalid()
        return tuple(value)

    def uuid_string_list(value: object) -> tuple[str, ...]:
        values = string_list(value)
        try:
            parsed = tuple(UUID(item) for item in values)
        except (TypeError, ValueError) as exc:
            raise invalid() from exc
        if tuple(str(item) for item in parsed) != values:
            raise invalid()
        return values

    def mapping(value: object, keys: frozenset[str]) -> dict[str, object]:
        if not isinstance(value, Mapping) or set(value) != keys:
            raise invalid()
        return dict(value)

    project_id = uuid(project.id)
    company_id = uuid(project.primary_company_id)
    project_created_at = stored_utc(project.created_at)
    preparation_time = stored_utc(preparation_created_at)
    legacy_request_cutoff = (
        stored_utc(authenticated_legacy_request_cutoff_at)
        if authenticated_legacy_request_cutoff_at is not None
        else None
    )
    project_hash = text(project.content_hash)

    mandate_project_id = uuid(mandate.project_id)
    mandate_key = text(mandate.mandate_key)
    mandate_version = integer(mandate.version)
    mandate_horizon = integer(mandate.horizon_years)
    mandate_currency = text(mandate.base_currency)
    mandate_required_return = decimal(mandate.required_return)
    mandate_loss_limit = decimal(mandate.permanent_loss_limit)
    mandate_comparison_set = string_list(mandate.comparison_set)
    mandate_benchmark = optional_text(mandate.benchmark_key)
    mandate_excess = (
        decimal(mandate.required_excess_return)
        if mandate.required_excess_return is not None
        else None
    )
    mandate_effective_at = stored_utc(mandate.effective_at)
    mandate_expires_at = (
        stored_utc(mandate.expires_at) if mandate.expires_at is not None else None
    )
    mandate_created_at = stored_utc(mandate.created_at)
    mandate_hash = text(mandate.content_hash)

    scope_project_id = uuid(scope.project_id)
    scope_version = integer(scope.version)
    scope_created_at = stored_utc(scope.created_at)
    scope_payload = mapping(
        scope.payload,
        frozenset(
            {
                "primary_company_id",
                "target_security_ids",
                "industry_ids",
                "covered_segments",
                "user_focus",
                "exclusions",
            }
        ),
    )
    text(scope_payload["primary_company_id"])
    uuid_string_list(scope_payload["target_security_ids"])
    uuid_string_list(scope_payload["industry_ids"])
    string_list(scope_payload["covered_segments"])
    optional_text(scope_payload["user_focus"])
    string_list(scope_payload["exclusions"])
    scope_hash = text(scope.content_hash)

    agenda_project_id = uuid(agenda.project_id)
    agenda_scope_id = uuid(agenda.scope_id)
    agenda_version = integer(agenda.version)
    agenda_created_at = stored_utc(agenda.created_at)
    agenda_payload = mapping(agenda.payload, frozenset({"items"}))
    string_list(agenda_payload["items"])
    agenda_generator = mapping(
        agenda.generator_provenance,
        frozenset(
            {
                "method",
                "template_key",
                "template_version",
                "model_name",
                "prompt_template_version",
                "input_summary_hash",
                "output_hash",
            }
        ),
    )
    text(agenda_generator["method"])
    text(agenda_generator["template_key"])
    text(agenda_generator["template_version"])
    optional_text(agenda_generator["model_name"])
    optional_text(agenda_generator["prompt_template_version"])
    text(agenda_generator["input_summary_hash"])
    text(agenda_generator["output_hash"])
    agenda_hash = text(agenda.content_hash)

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
        company_id == contract.scope.primary_company_id
        and project_hash
        == research_project_content_hash(
            primary_company_id=contract.scope.primary_company_id,
            target_security_ids=contract.scope.target_security_ids,
        )
        and (
            project_created_at <= mandate_effective_at
            or mandate_effective_at == legacy_request_cutoff
        )
        and mandate_effective_at <= mandate_created_at
        <= scope_created_at
        <= agenda_created_at
        <= preparation_time
        and mandate_project_id == project_id
        and mandate_version == 1
        and mandate.supersedes_id is None
        and mandate_key == expected_mandate_key
        and mandate_horizon == contract.mandate.horizon_years
        and mandate_currency == contract.mandate.base_currency
        and mandate_required_return == contract.mandate.required_return
        and mandate_loss_limit == contract.mandate.permanent_loss_limit
        and mandate_comparison_set == contract.mandate.comparison_set
        and mandate_benchmark is None
        and mandate_excess is None
        and mandate_expires_at is None
        and mandate_hash
        == product_mandate_content_hash(
            project_id=mandate_project_id,
            mandate_key=mandate_key,
            horizon_years=mandate_horizon,
            base_currency=mandate_currency,
            required_return=mandate_required_return,
            permanent_loss_limit=mandate_loss_limit,
            comparison_set=mandate_comparison_set,
            benchmark_key=mandate_benchmark,
            required_excess_return=mandate_excess,
            effective_at=mandate_effective_at,
            expires_at=mandate_expires_at,
        )
        and scope_project_id == project_id
        and scope_version == 1
        and scope.supersedes_id is None
        and scope_payload == expected_scope
        and scope_hash
        == research_scope_content_hash(project_id=project_id, payload=scope_payload)
        and agenda_project_id == project_id
        and agenda_version == 1
        and agenda.supersedes_id is None
        and agenda_scope_id == scope.id
        and agenda_payload == expected_agenda
        and agenda_generator == expected_generator
        and agenda_hash
        == research_agenda_content_hash(
            project_id=project_id,
            scope_id=scope.id,
            payload=agenda_payload,
            generator_provenance=agenda_generator,
        )
    )
    if not valid:
        raise invalid()
