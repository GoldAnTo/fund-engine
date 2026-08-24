from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    AssessmentConfidence,
    AssessmentDirection,
    AssessmentState,
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    ProductHistoricalBasisInput,
    PublicationStatus,
    ResearchAgendaInput,
    ResearchScopeInput,
    ReviewStatus,
    ScenarioKey,
    SecurityRightsInput,
    ValueNature,
    Provenance,
    EpistemicStatus,
)
from app.underwriting.domain.types import AnswerabilityState


NOW = datetime(2026, 8, 24, 9, 30, tzinfo=UTC)
EARLIER = datetime(2026, 6, 30, tzinfo=UTC)


def _scope() -> ResearchScopeInput:
    return ResearchScopeInput(
        primary_company_id=uuid4(),
        target_security_ids=(uuid4(),),
        industry_ids=(uuid4(),),
        covered_segments=("power_battery",),
        user_focus="long-term margins",
        exclusions=("unrelated subsidiaries",),
    )


def _price(**overrides: object) -> PriceSnapshotInput:
    values: dict[str, object] = {
        "security_identity_id": uuid4(),
        "price": Decimal("123.45"),
        "currency": "CNY",
        "price_type": "close",
        "adjustment_basis": "unadjusted",
        "market_at": EARLIER,
        "available_at": NOW,
        "source_id": "exchange-feed",
        "raw_hash": "raw-price-hash",
    }
    values.update(overrides)
    return PriceSnapshotInput(**values)  # type: ignore[arg-type]


def _fx(**overrides: object) -> FXSnapshotInput:
    values: dict[str, object] = {
        "base_currency": "USD",
        "quote_currency": "CNY",
        "rate": Decimal("7.2"),
        "quote_direction": FxQuoteDirection.QUOTE_PER_BASE,
        "market_at": EARLIER,
        "available_at": NOW,
        "source_id": "fx-feed",
        "raw_hash": "raw-fx-hash",
    }
    values.update(overrides)
    return FXSnapshotInput(**values)  # type: ignore[arg-type]


def _capital_structure(**overrides: object) -> CapitalStructureSnapshotInput:
    values: dict[str, object] = {
        "company_id": uuid4(),
        "currency": "CNY",
        "cash": Decimal("100"),
        "debt": Decimal("50"),
        "minority_interest": Decimal("10"),
        "investments": Decimal("5"),
        "pension_liabilities": Decimal("2"),
        "other_adjustments": Decimal("-1"),
        "basic_shares": Decimal("1000"),
        "diluted_shares": Decimal("1100"),
        "potential_dilution_descriptors": ("employee options",),
        "report_period_start": datetime(2026, 1, 1, tzinfo=UTC),
        "report_period_end": EARLIER,
        "market_at": EARLIER,
        "available_at": NOW,
        "source_id": "annual-report",
        "raw_hash": "raw-capital-hash",
    }
    values.update(overrides)
    return CapitalStructureSnapshotInput(**values)  # type: ignore[arg-type]


def _rights(**overrides: object) -> SecurityRightsInput:
    values: dict[str, object] = {
        "security_identity_id": uuid4(),
        "economic_units": Decimal("1"),
        "votes_per_unit": Decimal("1"),
        "conversion_ratio": Decimal("1"),
        "adr_ratio": Decimal("1"),
        "dividend_rights_per_unit": Decimal("1"),
        "effective_from": datetime(2026, 1, 1, tzinfo=UTC),
        "effective_to": None,
        "source_id": "listing-rules",
        "raw_hash": "raw-rights-hash",
    }
    values.update(overrides)
    return SecurityRightsInput(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("enum_type", "expected_values"),
    [
        (ValueNature, {"actual", "forecast", "derived", "assumption"}),
        (
            Provenance,
            {
                "company_guidance",
                "consensus",
                "house",
                "market_implied",
                "third_party",
                "source_reported",
            },
        ),
        (ScenarioKey, {"base", "bull", "bear"}),
        (EpistemicStatus, {"observed", "estimated", "uncertain", "unknown"}),
        (
            ReviewStatus,
            {"candidate", "self_reviewed", "adopted", "challenged", "superseded"},
        ),
        (
            AssessmentDirection,
            {"provisional_bullish", "provisional_neutral", "provisional_cautious"},
        ),
        (AssessmentConfidence, {"low", "medium", "high"}),
        (PublicationStatus, {"draft", "user_frozen", "superseded"}),
    ],
)
def test_controlled_enums_have_exact_wire_sets_and_reject_unknown_values(
    enum_type, expected_values
) -> None:
    assert {member.value for member in enum_type} == expected_values

    with pytest.raises(ValueError):
        enum_type("not-a-wire-value")


def test_assessment_rejects_uncontrolled_enum_values() -> None:
    with pytest.raises(ValueError, match="assessment direction is invalid"):
        AssessmentState(
            answerability=AnswerabilityState.ANSWERABLE,
            direction="provisional_bullish",  # type: ignore[arg-type]
            confidence=AssessmentConfidence.LOW,
            publication_status=PublicationStatus.DRAFT,
        )


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (_price, "market_at"),
        (_fx, "available_at"),
        (_capital_structure, "report_period_end"),
        (_rights, "effective_from"),
        (
            lambda **kwargs: ProductHistoricalBasisInput(
                cutoff_at=kwargs.get("cutoff_at", NOW),
                source_manifest_hash="source-manifest",
                definition_bundle_hash="definition-bundle",
                parser_bundle_hash="parser-bundle",
            ),
            "cutoff_at",
        ),
    ],
)
def test_product_contracts_reject_naive_datetimes(factory, field_name) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        factory(**{field_name: datetime(2026, 8, 24, 9, 30)})


@pytest.mark.parametrize("currency", ["cn", "cny", "CNY1"])
def test_price_and_fx_require_three_letter_uppercase_currency_codes(currency: str) -> None:
    with pytest.raises(ValueError, match="three-letter uppercase"):
        _price(currency=currency)

    with pytest.raises(ValueError, match="three-letter uppercase"):
        _fx(base_currency=currency)


def test_price_is_bound_to_a_security_identity_and_has_positive_finite_value() -> None:
    with pytest.raises(ValueError, match="security_identity_id must be a UUID"):
        _price(security_identity_id="security")

    with pytest.raises(ValueError, match="price must be a positive finite Decimal"):
        _price(price=Decimal("0"))

    with pytest.raises(ValueError, match="price must be a positive finite Decimal"):
        _price(price=Decimal("NaN"))


def test_fx_requires_a_nonzero_rate_and_distinct_currencies() -> None:
    with pytest.raises(ValueError, match="fx rate must be a positive finite Decimal"):
        _fx(rate=Decimal("0"))

    with pytest.raises(ValueError, match="currencies must differ"):
        _fx(quote_currency="USD")


def test_capital_structure_requires_positive_shares_and_valid_report_period() -> None:
    with pytest.raises(ValueError, match="diluted_shares must be a positive finite Decimal"):
        _capital_structure(diluted_shares=Decimal("0"))

    with pytest.raises(ValueError, match="diluted_shares must not be less than basic_shares"):
        _capital_structure(diluted_shares=Decimal("999"))

    with pytest.raises(ValueError, match="report period is invalid"):
        _capital_structure(report_period_start=NOW, report_period_end=EARLIER)


def test_security_rights_requires_positive_economic_rights_and_valid_interval() -> None:
    with pytest.raises(ValueError, match="economic_units must be a positive finite Decimal"):
        _rights(economic_units=Decimal("0"))

    with pytest.raises(ValueError, match="effective interval is invalid"):
        _rights(effective_to=datetime(2025, 12, 31, tzinfo=UTC))


def test_agenda_requires_explicit_deterministic_or_complete_ai_provenance() -> None:
    scope = _scope()
    deterministic = ResearchAgendaInput(
        scope_id=uuid4(),
        items=("business model",),
        generator=AgendaGeneratorInput(
            method=AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
            template_key="company-research-v1",
            template_version="1",
            model_name=None,
            prompt_template_version=None,
            input_summary_hash=None,
            output_hash="agenda-hash",
        ),
    )
    assert deterministic.items == ("business model",)
    assert scope.target_security_ids

    with pytest.raises(ValueError, match="AI agenda provenance"):
        AgendaGeneratorInput(
            method=AgendaGenerationMethod.AI_GENERATED,
            template_key=None,
            template_version=None,
            model_name="gpt-5",
            prompt_template_version=None,
            input_summary_hash="input-hash",
            output_hash="output-hash",
        )


def test_not_answerable_cannot_carry_direction_or_confidence() -> None:
    with pytest.raises(ValueError, match="direction and confidence must be null when not_answerable"):
        AssessmentState(
            answerability=AnswerabilityState.NOT_ANSWERABLE,
            direction=AssessmentDirection.PROVISIONAL_BULLISH,
            confidence=AssessmentConfidence.LOW,
            publication_status=PublicationStatus.DRAFT,
        )


def test_product_contracts_are_frozen() -> None:
    value = _price()

    with pytest.raises(FrozenInstanceError):
        value.price = Decimal("1")
