from __future__ import annotations

import ast
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.underwriting.adapters.company_research.alphabet import (
    ALPHABET_BUSINESS_MODULES,
    AlphabetCompanyResearchAdapter,
)
from app.underwriting.domain.company_research import (
    DEFAULT_MODULES,
    CompanyResearchCompany,
    CompanyResearchDefaultPolicy,
    CompanyResearchIdentitySet,
    CompanyResearchSecurity,
    CompanyResearchValidationError,
    ResearchGap,
    ResearchGapSeverity,
    build_company_research_preview,
)
from app.underwriting.services.kernel import canonical_hash


@pytest.fixture
def alphabet_identity_set() -> CompanyResearchIdentitySet:
    company = CompanyResearchCompany(
        object_id=uuid4(),
        external_key="US:ALPHABET:COMPANY",
        canonical_name="Alphabet Inc.",
    )
    return CompanyResearchIdentitySet(
        company=company,
        securities=(
            CompanyResearchSecurity(
                object_id=uuid4(),
                company_id=company.object_id,
                external_key="NASDAQ:GOOGL",
                canonical_name="Alphabet Class A",
                symbol="GOOGL",
                exchange="NASDAQ",
                share_class="Class A",
                trading_currency="USD",
            ),
            CompanyResearchSecurity(
                object_id=uuid4(),
                company_id=company.object_id,
                external_key="NASDAQ:GOOG",
                canonical_name="Alphabet Class C",
                symbol="GOOG",
                exchange="NASDAQ",
                share_class="Class C",
                trading_currency="USD",
            ),
        ),
    )


def test_alphabet_default_policy_is_recomputable(alphabet_identity_set) -> None:
    adapter = AlphabetCompanyResearchAdapter()
    first = build_company_research_preview(
        adapter=adapter,
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    second = build_company_research_preview(
        adapter=adapter,
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )

    assert first == second
    assert first.strategy_version == "company-research-default.v1"
    assert first.horizon_years == 5
    assert first.base_currency == "CNY"
    assert first.required_return == Decimal("0.12")
    assert first.permanent_loss_limit == Decimal("0.25")
    assert first.security_external_keys == ("NASDAQ:GOOG", "NASDAQ:GOOGL")
    assert first.input_hash == canonical_hash(first.canonical_payload())


def test_default_module_and_alphabet_business_module_orders_are_fixed() -> None:
    assert DEFAULT_MODULES == (
        "overview",
        "business_map",
        "operating_drivers",
        "evidence_and_gaps",
        "industry_competition_regulation",
        "financials_cash_flow_capital_allocation",
        "scenarios_valuation_implied_expectations",
        "counterevidence_risks_next_checks",
        "versions_changes_memo",
    )
    assert ALPHABET_BUSINESS_MODULES == (
        "search_and_other_ads",
        "youtube_ads_and_subscriptions",
        "google_cloud",
        "other_google_services",
        "other_bets",
        "corporate_capital_allocation",
    )


@pytest.mark.parametrize(
    "cutoff_at",
    [datetime(2026, 8, 25, 1, 30), "2026-08-25T01:30:00Z"],
)
def test_preview_rejects_non_aware_cutoff(alphabet_identity_set, cutoff_at) -> None:
    with pytest.raises(CompanyResearchValidationError, match="cutoff_at"):
        build_company_research_preview(
            adapter=AlphabetCompanyResearchAdapter(),
            identities=alphabet_identity_set,
            cutoff_at=cutoff_at,
        )


def test_identity_set_rejects_missing_company_and_empty_or_unrelated_securities(
    alphabet_identity_set,
) -> None:
    with pytest.raises(CompanyResearchValidationError, match="company"):
        CompanyResearchIdentitySet(
            company=None, securities=alphabet_identity_set.securities
        )
    with pytest.raises(CompanyResearchValidationError, match="security"):
        CompanyResearchIdentitySet(company=alphabet_identity_set.company, securities=())
    unrelated = CompanyResearchSecurity(
        object_id=uuid4(),
        company_id=uuid4(),
        external_key="NYSE:OTHER",
        canonical_name="Other Common Stock",
        symbol="OTHER",
        exchange="NYSE",
        share_class="ordinary",
        trading_currency="USD",
    )
    with pytest.raises(CompanyResearchValidationError, match="company_id"):
        CompanyResearchIdentitySet(
            company=alphabet_identity_set.company, securities=(unrelated,)
        )


def test_identity_set_rejects_duplicate_security_id_or_external_key(
    alphabet_identity_set,
) -> None:
    security = alphabet_identity_set.securities[0]
    duplicate_id = CompanyResearchSecurity(
        object_id=security.object_id,
        company_id=security.company_id,
        external_key="NASDAQ:OTHER",
        canonical_name="Other Common Stock",
        symbol="OTHER",
        exchange="NASDAQ",
        share_class="ordinary",
        trading_currency="USD",
    )
    duplicate_key = CompanyResearchSecurity(
        object_id=uuid4(),
        company_id=security.company_id,
        external_key=security.external_key,
        canonical_name="Other Common Stock",
        symbol="OTHER",
        exchange="NASDAQ",
        share_class="ordinary",
        trading_currency="USD",
    )
    with pytest.raises(CompanyResearchValidationError, match="object_id"):
        CompanyResearchIdentitySet(
            company=alphabet_identity_set.company, securities=(security, duplicate_id)
        )
    with pytest.raises(CompanyResearchValidationError, match="external_key"):
        CompanyResearchIdentitySet(
            company=alphabet_identity_set.company, securities=(security, duplicate_key)
        )


def test_preview_canonicalizes_security_input_order(alphabet_identity_set) -> None:
    reversed_set = CompanyResearchIdentitySet(
        company=alphabet_identity_set.company,
        securities=tuple(reversed(alphabet_identity_set.securities)),
    )
    cutoff_at = datetime(2026, 8, 25, 1, 30, tzinfo=UTC)
    first = build_company_research_preview(
        adapter=AlphabetCompanyResearchAdapter(),
        identities=alphabet_identity_set,
        cutoff_at=cutoff_at,
    )
    second = build_company_research_preview(
        adapter=AlphabetCompanyResearchAdapter(),
        identities=reversed_set,
        cutoff_at=cutoff_at,
    )
    assert first == second
    assert first.security_external_keys == ("NASDAQ:GOOG", "NASDAQ:GOOGL")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_currency": "USD"},
        {"strategy_version": "company-research-default.v2"},
        {"required_return": Decimal("0.13")},
        {"permanent_loss_limit": Decimal("0.24")},
    ],
)
def test_default_policy_rejects_overrides(kwargs) -> None:
    with pytest.raises(CompanyResearchValidationError):
        CompanyResearchDefaultPolicy(**kwargs)


@pytest.mark.parametrize("horizon_years", [5.0, Decimal("5"), True, "5"])
def test_default_policy_and_preview_reject_non_integer_horizon_years(
    alphabet_identity_set, horizon_years
) -> None:
    with pytest.raises(
        CompanyResearchValidationError, match="horizon_years must be an int"
    ):
        CompanyResearchDefaultPolicy(horizon_years=horizon_years)

    preview = build_company_research_preview(
        adapter=AlphabetCompanyResearchAdapter(),
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )
    with pytest.raises(
        CompanyResearchValidationError, match="horizon_years must be an int"
    ):
        replace(preview, horizon_years=horizon_years, input_hash="")


def test_unknown_adapter_company_is_rejected(alphabet_identity_set) -> None:
    other_company = CompanyResearchCompany(
        object_id=uuid4(), external_key="US:OTHER:COMPANY", canonical_name="Other"
    )
    identities = CompanyResearchIdentitySet(
        company=other_company,
        securities=(
            CompanyResearchSecurity(
                object_id=uuid4(),
                company_id=other_company.object_id,
                external_key="NASDAQ:OTHER",
                canonical_name="Other Common Stock",
                symbol="OTHER",
                exchange="NASDAQ",
                share_class="ordinary",
                trading_currency="USD",
            ),
        ),
    )
    with pytest.raises(CompanyResearchValidationError, match="does not support"):
        build_company_research_preview(
            adapter=AlphabetCompanyResearchAdapter(),
            identities=identities,
            cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "modules",
    [
        ("search_and_other_ads", "search_and_other_ads"),
        ("unknown",),
    ],
)
def test_alphabet_adapter_rejects_tampered_or_duplicate_business_modules(
    alphabet_identity_set, modules, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.underwriting.adapters.company_research.alphabet.ALPHABET_BUSINESS_MODULES",
        modules,
    )
    with pytest.raises(CompanyResearchValidationError, match="business modules"):
        build_company_research_preview(
            adapter=AlphabetCompanyResearchAdapter(),
            identities=alphabet_identity_set,
            cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {
            "code": " ",
            "module_key": "overview",
            "severity": ResearchGapSeverity.HIGH,
            "message": "Missing",
        },
        {
            "code": "missing",
            "module_key": "overview",
            "severity": "high",
            "message": "Missing",
        },
        {
            "code": "missing",
            "module_key": "overview",
            "severity": ResearchGapSeverity.HIGH,
            "message": " ",
        },
    ],
)
def test_research_gap_requires_closed_valid_fields(kwargs) -> None:
    with pytest.raises(CompanyResearchValidationError):
        ResearchGap(**kwargs)


def test_preview_hash_binds_identity_and_cutoff(alphabet_identity_set) -> None:
    adapter = AlphabetCompanyResearchAdapter()
    first = build_company_research_preview(
        adapter=adapter,
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )
    later = build_company_research_preview(
        adapter=adapter,
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 31, tzinfo=UTC),
    )
    altered_security = CompanyResearchSecurity(
        object_id=alphabet_identity_set.securities[0].object_id,
        company_id=alphabet_identity_set.company.object_id,
        external_key="NASDAQ:GOOGL-ALT",
        canonical_name="Alphabet Class A",
        symbol="GOOGL",
        exchange="NASDAQ",
        share_class="Class A",
        trading_currency="USD",
    )
    altered = CompanyResearchIdentitySet(
        company=alphabet_identity_set.company,
        securities=(altered_security, alphabet_identity_set.securities[1]),
    )
    different = build_company_research_preview(
        adapter=adapter,
        identities=altered,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )
    assert first.input_hash != later.input_hash
    assert first.input_hash != different.input_hash


@pytest.mark.parametrize("canonical_name", ["", " Leading", "Cafe\u0301", "A" * 513])
def test_security_canonical_name_requires_normalized_bounded_identity_text(
    alphabet_identity_set, canonical_name
) -> None:
    with pytest.raises(CompanyResearchValidationError, match="canonical_name"):
        replace(alphabet_identity_set.securities[0], canonical_name=canonical_name)


def test_security_canonical_name_is_strict_and_binds_preview_hash(
    alphabet_identity_set,
) -> None:
    first = build_company_research_preview(
        adapter=AlphabetCompanyResearchAdapter(),
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )
    altered_security = replace(
        alphabet_identity_set.securities[0], canonical_name="Alphabet Class C Revised"
    )
    altered = CompanyResearchIdentitySet(
        company=alphabet_identity_set.company,
        securities=(altered_security, alphabet_identity_set.securities[1]),
    )
    second = build_company_research_preview(
        adapter=AlphabetCompanyResearchAdapter(),
        identities=altered,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )

    assert first.input_hash != second.input_hash
    assert "canonical_name" in first.canonical_payload()["securities"][0]


@pytest.mark.parametrize(
    "required_return, permanent_loss_limit",
    [
        (Decimal("0.120"), Decimal("0.250")),
        (Decimal("0.1200"), Decimal("0.2500")),
        (Decimal("1.2E-1"), Decimal("2.5E-1")),
    ],
)
def test_equivalent_default_decimals_have_one_canonical_preview_hash(
    alphabet_identity_set, required_return, permanent_loss_limit
) -> None:
    preview = build_company_research_preview(
        adapter=AlphabetCompanyResearchAdapter(),
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )
    equivalent = replace(
        preview,
        required_return=required_return,
        permanent_loss_limit=permanent_loss_limit,
    )

    assert equivalent == preview
    assert equivalent.input_hash == preview.input_hash
    assert equivalent.canonical_payload()["strategy"]["required_return"] == "0.12"
    assert equivalent.canonical_payload()["strategy"]["permanent_loss_limit"] == "0.25"


def test_preview_validates_gaps_against_its_generic_and_business_modules(
    alphabet_identity_set,
) -> None:
    preview = build_company_research_preview(
        adapter=AlphabetCompanyResearchAdapter(),
        identities=alphabet_identity_set,
        cutoff_at=datetime(2026, 8, 25, 1, 30, tzinfo=UTC),
    )
    generic_gap = ResearchGap(
        code="missing_generic",
        module_key="overview",
        severity=ResearchGapSeverity.HIGH,
        message="Missing overview evidence",
    )
    alphabet_gap = ResearchGap(
        code="missing_cloud",
        module_key="google_cloud",
        severity=ResearchGapSeverity.HIGH,
        message="Missing cloud evidence",
    )
    unknown_gap = ResearchGap(
        code="missing_unknown",
        module_key="not_a_real_module",
        severity=ResearchGapSeverity.HIGH,
        message="Missing unknown evidence",
    )
    other_adapter_gap = ResearchGap(
        code="missing_other_adapter",
        module_key="other_adapter_module",
        severity=ResearchGapSeverity.HIGH,
        message="Missing other adapter evidence",
    )

    assert preview.validate_research_gaps((generic_gap, alphabet_gap)) == (
        generic_gap,
        alphabet_gap,
    )
    with pytest.raises(CompanyResearchValidationError, match="not allowed"):
        preview.validate_research_gaps((unknown_gap,))
    with pytest.raises(CompanyResearchValidationError, match="not allowed"):
        preview.validate_research_gaps((other_adapter_gap,))


def test_domain_and_adapter_dependencies_remain_one_way() -> None:
    root = Path(__file__).resolve().parents[2]

    def imported_modules(path: Path) -> tuple[str, ...]:
        tree = ast.parse(path.read_text())
        return tuple(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )

    domain_imports = imported_modules(
        root / "app/underwriting/domain/company_research.py"
    )
    adapter_imports = imported_modules(
        root / "app/underwriting/adapters/company_research/alphabet.py"
    )
    assert not any("alphabet" in module for module in domain_imports)
    assert not any("persistence" in module for module in adapter_imports)
    assert not any("services" in module for module in adapter_imports)
