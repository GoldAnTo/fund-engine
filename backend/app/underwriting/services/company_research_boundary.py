"""Authenticated historical boundaries for governed company-research cases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum

from app.models.ledger import ValidationError
from app.underwriting.adapters.company_research import (
    AlphabetCompanyResearchAdapter,
    CatlCompanyResearchAdapter,
)
from app.underwriting.domain.product_contracts import (
    ProductHistoricalBasisInput,
    product_historical_basis_content_hash,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256,
    load_alphabet_golden_case_fixture,
)
from app.underwriting.fixtures.catl_answerable_case import (
    load_catl_answerable_case_fixture,
)
from app.underwriting.hashing import canonical_hash

_DEFINITION_SCHEMA = "company-research.definition-bundle.v1"
_PARSER_SCHEMA = "company-research.parser-bundle.v1"
_PARSER_STRATEGY = "alphabet-golden-case-parser.v1"
_FIXTURE_SCHEMAS = (
    "alphabet.golden-case.manifest.v1",
    "alphabet.golden-case.business-map.v1",
    "alphabet.golden-case.source-facts.v1",
    "alphabet.golden-case.market-inputs.v1",
    "alphabet.golden-case.strategy-assumptions.v1",
)
_CATL_PARSER_STRATEGY = "catl-answerable-case-parser.v1"
_CATL_FIXTURE_SCHEMAS = (
    "catl.answerable-case.manifest.v1",
    "catl.answerable-case.business-map.v1",
    "catl.answerable-case.source-facts.v1",
    "catl.answerable-case.market-inputs.v1",
    "catl.answerable-case.strategy-assumptions.v1",
)


def _utc(value: object, field_name: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _json_contract(value: object) -> object:
    if isinstance(value, Enum):
        return _json_contract(value.value)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("JSON contract mapping keys must be strings")
            result[key] = _json_contract(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_json_contract(item) for item in value]
    if value is None or isinstance(value, (bool, int, str)):
        return value
    raise ValidationError(
        f"JSON contract does not support {type(value).__name__} values"
    )


@dataclass(frozen=True, slots=True)
class CompanyResearchHistoricalBoundary:
    cutoff_at: datetime
    basis_input: ProductHistoricalBasisInput
    basis_content_hash: str


def _resolve_boundary(
    *,
    requested_cutoff_at: datetime,
    fixture_cutoff: datetime,
    fixture_content_hash: str,
    source_manifest_hash: str | None,
    model_template: object,
    parser_strategy: str,
    fixture_schemas: tuple[str, ...],
    label: str,
) -> CompanyResearchHistoricalBoundary:
    requested_cutoff_at = _utc(requested_cutoff_at, "requested cutoff")
    if requested_cutoff_at < fixture_cutoff:
        raise ValidationError(
            f"requested cutoff precedes the authenticated {label} fixture"
        )
    governed_source_hash = (
        fixture_content_hash if source_manifest_hash is None else source_manifest_hash
    )
    if governed_source_hash != fixture_content_hash:
        raise ValidationError(f"{label} evidence manifest is not governed")
    definition_bundle_hash = canonical_hash(
        {
            "schema_version": _DEFINITION_SCHEMA,
            "model_template": _json_contract(asdict(model_template)),
        }
    )
    parser_bundle_hash = canonical_hash(
        {
            "schema_version": _PARSER_SCHEMA,
            "parser_strategy": parser_strategy,
            "fixture_schema_versions": fixture_schemas,
        }
    )
    basis_input = ProductHistoricalBasisInput(
        cutoff_at=fixture_cutoff,
        source_manifest_hash=governed_source_hash,
        definition_bundle_hash=definition_bundle_hash,
        parser_bundle_hash=parser_bundle_hash,
    )
    return CompanyResearchHistoricalBoundary(
        cutoff_at=fixture_cutoff,
        basis_input=basis_input,
        basis_content_hash=product_historical_basis_content_hash(basis_input),
    )


def resolve_alphabet_company_research_boundary(
    requested_cutoff_at: datetime,
    *,
    source_manifest_hash: str | None = None,
) -> CompanyResearchHistoricalBoundary:
    requested_cutoff_at = _utc(requested_cutoff_at, "requested cutoff")
    fixture = load_alphabet_golden_case_fixture()
    if requested_cutoff_at < fixture.cutoff:
        raise ValidationError(
            "requested cutoff precedes the authenticated Alphabet fixture"
        )
    governed_source_hash = (
        fixture.content_hash if source_manifest_hash is None else source_manifest_hash
    )
    if governed_source_hash not in {
        fixture.content_hash,
        LEGACY_EVIDENCE_MANIFEST_CONTENT_SHA256,
    }:
        raise ValidationError("Alphabet evidence manifest is not governed")

    template = AlphabetCompanyResearchAdapter().model_template()
    definition_bundle_hash = canonical_hash(
        {
            "schema_version": _DEFINITION_SCHEMA,
            "model_template": _json_contract(asdict(template)),
        }
    )
    parser_bundle_hash = canonical_hash(
        {
            "schema_version": _PARSER_SCHEMA,
            "parser_strategy": _PARSER_STRATEGY,
            "fixture_schema_versions": _FIXTURE_SCHEMAS,
        }
    )
    basis_input = ProductHistoricalBasisInput(
        cutoff_at=fixture.cutoff,
        source_manifest_hash=governed_source_hash,
        definition_bundle_hash=definition_bundle_hash,
        parser_bundle_hash=parser_bundle_hash,
    )
    basis_content_hash = product_historical_basis_content_hash(basis_input)
    return CompanyResearchHistoricalBoundary(
        cutoff_at=fixture.cutoff,
        basis_input=basis_input,
        basis_content_hash=basis_content_hash,
    )


def resolve_catl_company_research_boundary(
    requested_cutoff_at: datetime,
    *,
    source_manifest_hash: str | None = None,
) -> CompanyResearchHistoricalBoundary:
    fixture = load_catl_answerable_case_fixture()
    return _resolve_boundary(
        requested_cutoff_at=requested_cutoff_at,
        fixture_cutoff=fixture.cutoff,
        fixture_content_hash=fixture.content_hash,
        source_manifest_hash=source_manifest_hash,
        model_template=CatlCompanyResearchAdapter().model_template(),
        parser_strategy=_CATL_PARSER_STRATEGY,
        fixture_schemas=_CATL_FIXTURE_SCHEMAS,
        label="CATL",
    )


def resolve_company_research_boundary(
    company_external_key: str,
    requested_cutoff_at: datetime,
    *,
    source_manifest_hash: str | None = None,
) -> CompanyResearchHistoricalBoundary:
    """Resolve an exact governed company boundary; reject unknown identities."""
    if company_external_key == "US:ALPHABET:COMPANY":
        return resolve_alphabet_company_research_boundary(
            requested_cutoff_at,
            source_manifest_hash=source_manifest_hash,
        )
    if company_external_key == "CN:300750:COMPANY":
        return resolve_catl_company_research_boundary(
            requested_cutoff_at,
            source_manifest_hash=source_manifest_hash,
        )
    raise ValidationError("company research boundary is unavailable")
