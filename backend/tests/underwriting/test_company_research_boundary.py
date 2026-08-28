from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta
from enum import Enum
import json
import re
from zoneinfo import ZoneInfo

import pytest

from app.models.ledger import ValidationError
from app.underwriting.adapters.company_research import AlphabetCompanyResearchAdapter
from app.underwriting.domain.product_contracts import ProductHistoricalBasisInput
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.hashing import canonical_hash
from app.underwriting.services.company_research_boundary import (
    CompanyResearchHistoricalBoundary,
    _json_contract,
    resolve_alphabet_company_research_boundary,
)


SHA256 = re.compile(r"[0-9a-f]{64}")
_BASIS_SCHEMA = "product.historical-basis.v1"
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
_SOURCE_MANIFEST_HASH = "7a447c102c8416548bb5ebe14dbc4e74bdf2ed4616b4db35dc1ff8aece401788"
_DEFINITION_BUNDLE_HASH = "1548f2ff0a88c469f13b53bd448d2a4c94dc7a4d08487533f7ad2782911e7955"
_PARSER_BUNDLE_HASH = "d5ea79bd3cd6ed81c86586d7b9b9b4acf50be258d7de87dcdf8e313385b18b60"
_BASIS_CONTENT_HASH = "7d74d3ac964bfba9a15340656e47543bedd05abadce52fc935638cec8ffeb710"


def _later_cutoff() -> datetime:
    return load_alphabet_golden_case_fixture().cutoff + timedelta(days=3)


def _independent_json_contract(value: object) -> object:
    if isinstance(value, Enum):
        return _independent_json_contract(value.value)
    if isinstance(value, dict):
        return {
            key: _independent_json_contract(item) for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_independent_json_contract(item) for item in value]
    return value


def _stable_bytes(boundary: CompanyResearchHistoricalBoundary) -> bytes:
    return json.dumps(
        asdict(boundary),
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_requested_cutoff_after_fixture_resolves_to_authenticated_boundary() -> None:
    fixture = load_alphabet_golden_case_fixture()

    boundary = resolve_alphabet_company_research_boundary(_later_cutoff())
    fixture_boundary = resolve_alphabet_company_research_boundary(fixture.cutoff)

    assert boundary == fixture_boundary
    assert boundary.cutoff_at == fixture.cutoff
    assert boundary.basis_input.cutoff_at == fixture.cutoff
    assert boundary.basis_input.source_manifest_hash == fixture.content_hash


def test_boundary_hashes_are_lowercase_sha256() -> None:
    boundary = resolve_alphabet_company_research_boundary(_later_cutoff())

    assert SHA256.fullmatch(boundary.basis_input.source_manifest_hash)
    assert SHA256.fullmatch(boundary.basis_input.definition_bundle_hash)
    assert SHA256.fullmatch(boundary.basis_input.parser_bundle_hash)
    assert SHA256.fullmatch(boundary.basis_content_hash)


def test_repeated_boundary_resolution_is_byte_for_byte_stable() -> None:
    requested_cutoff = _later_cutoff()

    first = _stable_bytes(resolve_alphabet_company_research_boundary(requested_cutoff))
    second = _stable_bytes(resolve_alphabet_company_research_boundary(requested_cutoff))

    assert first == second


def test_cutoff_before_authenticated_fixture_is_rejected() -> None:
    fixture = load_alphabet_golden_case_fixture()

    with pytest.raises(
        ValidationError,
        match="requested cutoff precedes the authenticated Alphabet fixture",
    ):
        resolve_alphabet_company_research_boundary(
            fixture.cutoff - timedelta(seconds=1)
        )


def test_naive_requested_cutoff_is_rejected() -> None:
    with pytest.raises(ValidationError, match="requested cutoff.*timezone-aware"):
        resolve_alphabet_company_research_boundary(_later_cutoff().replace(tzinfo=None))


def test_boundary_uses_product_historical_basis_contract() -> None:
    boundary = resolve_alphabet_company_research_boundary(_later_cutoff())

    assert type(boundary.basis_input) is ProductHistoricalBasisInput


def test_non_utc_requested_cutoff_normalizes_to_the_same_boundary() -> None:
    requested_cutoff = _later_cutoff()

    utc_boundary = resolve_alphabet_company_research_boundary(requested_cutoff)
    non_utc_boundary = resolve_alphabet_company_research_boundary(
        requested_cutoff.astimezone(ZoneInfo("Asia/Shanghai"))
    )

    assert non_utc_boundary == utc_boundary


def test_hashes_authenticate_independent_definition_parser_and_basis_payloads() -> None:
    fixture = load_alphabet_golden_case_fixture()
    boundary = resolve_alphabet_company_research_boundary(_later_cutoff())
    template = AlphabetCompanyResearchAdapter().model_template()

    definition_hash = canonical_hash(
        {
            "schema_version": _DEFINITION_SCHEMA,
            "model_template": _independent_json_contract(asdict(template)),
        }
    )
    parser_hash = canonical_hash(
        {
            "schema_version": _PARSER_SCHEMA,
            "parser_strategy": _PARSER_STRATEGY,
            "fixture_schema_versions": _FIXTURE_SCHEMAS,
        }
    )
    basis_hash = canonical_hash(
        {
            "schema_version": _BASIS_SCHEMA,
            "cutoff_at": fixture.cutoff.isoformat(),
            "source_manifest_hash": fixture.content_hash,
            "definition_bundle_hash": definition_hash,
            "parser_bundle_hash": parser_hash,
        }
    )

    assert boundary.basis_input.source_manifest_hash == fixture.content_hash
    assert boundary.basis_input.definition_bundle_hash == definition_hash
    assert boundary.basis_input.parser_bundle_hash == parser_hash
    assert boundary.basis_content_hash == basis_hash


def test_hashes_match_current_authenticated_golden_contract() -> None:
    boundary = resolve_alphabet_company_research_boundary(_later_cutoff())

    assert boundary.basis_input.source_manifest_hash == _SOURCE_MANIFEST_HASH
    assert boundary.basis_input.definition_bundle_hash == _DEFINITION_BUNDLE_HASH
    assert boundary.basis_input.parser_bundle_hash == _PARSER_BUNDLE_HASH
    assert boundary.basis_content_hash == _BASIS_CONTENT_HASH


def test_template_drift_changes_definition_and_basis_but_not_source_or_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = AlphabetCompanyResearchAdapter().model_template()
    drifted_template = replace(template, template_version="alphabet-company-model.v2")
    monkeypatch.setattr(
        AlphabetCompanyResearchAdapter,
        "model_template",
        lambda self: drifted_template,
    )

    boundary = resolve_alphabet_company_research_boundary(_later_cutoff())

    assert boundary.basis_input.source_manifest_hash == _SOURCE_MANIFEST_HASH
    assert boundary.basis_input.parser_bundle_hash == _PARSER_BUNDLE_HASH
    assert boundary.basis_input.definition_bundle_hash != _DEFINITION_BUNDLE_HASH
    assert boundary.basis_content_hash != _BASIS_CONTENT_HASH


@pytest.mark.parametrize(
    "value",
    [object(), 1.5, {1: "non-string key"}],
)
def test_json_contract_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(ValidationError, match="JSON contract"):
        _json_contract(value)


def test_json_contract_converts_enums_and_nested_collections() -> None:
    class Marker(Enum):
        VALUE = "value"

    assert _json_contract({"marker": (Marker.VALUE, [None, True, 1, "text"])}) == {
        "marker": ["value", [None, True, 1, "text"]]
    }
