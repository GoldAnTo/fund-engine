from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import json
import re

import pytest

from app.models.ledger import ValidationError
from app.underwriting.domain.product_contracts import ProductHistoricalBasisInput
from app.underwriting.fixtures.alphabet_golden_case import (
    load_alphabet_golden_case_fixture,
)
from app.underwriting.services.company_research_boundary import (
    CompanyResearchHistoricalBoundary,
    resolve_alphabet_company_research_boundary,
)


SHA256 = re.compile(r"[0-9a-f]{64}")


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

    boundary = resolve_alphabet_company_research_boundary(
        datetime(2026, 8, 28, tzinfo=UTC)
    )
    fixture_boundary = resolve_alphabet_company_research_boundary(fixture.cutoff)

    assert boundary == fixture_boundary
    assert boundary.cutoff_at == fixture.cutoff
    assert boundary.basis_input.cutoff_at == fixture.cutoff
    assert boundary.basis_input.source_manifest_hash == fixture.content_hash


def test_boundary_hashes_are_lowercase_sha256() -> None:
    boundary = resolve_alphabet_company_research_boundary(
        datetime(2026, 8, 28, tzinfo=UTC)
    )

    assert SHA256.fullmatch(boundary.basis_input.source_manifest_hash)
    assert SHA256.fullmatch(boundary.basis_input.definition_bundle_hash)
    assert SHA256.fullmatch(boundary.basis_input.parser_bundle_hash)
    assert SHA256.fullmatch(boundary.basis_content_hash)


def test_repeated_boundary_resolution_is_byte_for_byte_stable() -> None:
    requested_cutoff = datetime(2026, 8, 28, tzinfo=UTC)

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
        resolve_alphabet_company_research_boundary(datetime(2026, 8, 28))


def test_boundary_uses_product_historical_basis_contract() -> None:
    boundary = resolve_alphabet_company_research_boundary(
        datetime(2026, 8, 28, tzinfo=UTC)
    )

    assert type(boundary.basis_input) is ProductHistoricalBasisInput
