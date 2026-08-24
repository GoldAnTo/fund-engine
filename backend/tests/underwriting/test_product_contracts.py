from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

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
    ProductRevisionView,
    PublicationStatus,
    ResearchAgendaInput,
    ResearchScopeInput,
    ReviewStatus,
    RevisionBoundaryInput,
    ScenarioKey,
    SecurityRightsInput,
    ValueNature,
    Provenance,
    EpistemicStatus,
    agenda_items_hash,
)
from app.underwriting.domain.types import AnswerabilityState
from app.underwriting.domain import (
    PriceSnapshotInput as ExportedPriceSnapshotInput,
    ProductRevisionView as ExportedProductRevisionView,
    agenda_items_hash as exported_agenda_items_hash,
)


NOW = datetime(2026, 8, 24, 9, 30, tzinfo=UTC)
EARLIER = datetime(2026, 6, 30, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64


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
        "raw_hash": HASH_A,
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
        "raw_hash": HASH_B,
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
        "raw_hash": HASH_C,
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
        "raw_hash": HASH_D,
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
                source_manifest_hash=HASH_A,
                definition_bundle_hash=HASH_B,
                parser_bundle_hash=HASH_C,
            ),
            "cutoff_at",
        ),
    ],
)
def test_product_contracts_reject_naive_datetimes(factory, field_name) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        factory(**{field_name: datetime(2026, 8, 24, 9, 30)})


@pytest.mark.parametrize("currency", ["cn", "cny", "CNY1"])
def test_price_and_fx_require_three_letter_uppercase_currency_codes(
    currency: str,
) -> None:
    with pytest.raises(ValueError, match="three-letter uppercase"):
        _price(currency=currency)

    with pytest.raises(ValueError, match="three-letter uppercase"):
        _fx(base_currency=currency)


@pytest.mark.parametrize("currency", ["AAA", "ZZZ"])
def test_product_currency_registry_rejects_unsupported_uppercase_codes(
    currency: str,
) -> None:
    with pytest.raises(ValueError, match="supported product currency"):
        _price(currency=currency)


def test_product_currency_registry_accepts_the_increment_a_market_currencies() -> None:
    assert _price(currency="CNY").currency == "CNY"
    assert _price(currency="USD").currency == "USD"
    assert _fx(base_currency="USD", quote_currency="CNY").rate == Decimal("7.2")


def test_market_times_must_be_in_non_decreasing_order() -> None:
    with pytest.raises(ValueError, match="market_at must not be later"):
        _price(market_at=NOW, available_at=EARLIER)

    with pytest.raises(ValueError, match="market_at must not be later"):
        _fx(market_at=NOW, available_at=EARLIER)

    with pytest.raises(ValueError, match="market_at must not be later"):
        _capital_structure(market_at=NOW, available_at=EARLIER)


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
    with pytest.raises(
        ValueError, match="diluted_shares must be a positive finite Decimal"
    ):
        _capital_structure(diluted_shares=Decimal("0"))

    with pytest.raises(
        ValueError, match="diluted_shares must not be less than basic_shares"
    ):
        _capital_structure(diluted_shares=Decimal("999"))

    with pytest.raises(ValueError, match="report period is invalid"):
        _capital_structure(report_period_start=NOW, report_period_end=EARLIER)


def test_security_rights_requires_positive_economic_rights_and_valid_interval() -> None:
    with pytest.raises(
        ValueError, match="economic_units must be a positive finite Decimal"
    ):
        _rights(economic_units=Decimal("0"))

    with pytest.raises(ValueError, match="effective interval is invalid"):
        _rights(effective_to=datetime(2025, 12, 31, tzinfo=UTC))


def test_security_rights_allows_zero_vote_and_dividend_entitlements_but_rejects_negative_votes() -> (
    None
):
    goog = _rights(votes_per_unit=Decimal("0"), dividend_rights_per_unit=Decimal("0"))
    assert goog.votes_per_unit == Decimal("0")
    assert goog.dividend_rights_per_unit == Decimal("0")

    with pytest.raises(
        ValueError, match="votes_per_unit must be a non-negative finite Decimal"
    ):
        _rights(votes_per_unit=Decimal("-1"))


def test_datetime_ordering_uses_instants_across_a_dst_fold() -> None:
    new_york = ZoneInfo("America/New_York")
    earlier = datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=0)
    later = datetime(2026, 11, 1, 1, 30, tzinfo=new_york, fold=1)

    with pytest.raises(ValueError, match="market_at must not be later"):
        _price(market_at=later, available_at=earlier)

    with pytest.raises(ValueError, match="report period is invalid"):
        _capital_structure(report_period_start=later, report_period_end=earlier)

    with pytest.raises(ValueError, match="effective interval is invalid"):
        _rights(effective_from=later, effective_to=earlier)


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
            output_hash=agenda_items_hash(("business model",)),
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
            input_summary_hash=HASH_A,
            output_hash=HASH_B,
        )


def test_not_answerable_cannot_carry_direction_or_confidence() -> None:
    with pytest.raises(
        ValueError, match="direction and confidence must be null when not_answerable"
    ):
        AssessmentState(
            answerability=AnswerabilityState.NOT_ANSWERABLE,
            direction=AssessmentDirection.PROVISIONAL_BULLISH,
            confidence=AssessmentConfidence.LOW,
            publication_status=PublicationStatus.DRAFT,
        )


def test_formal_product_revision_cannot_be_draft_but_workspace_assessment_can() -> None:
    draft = AssessmentState(
        answerability=AnswerabilityState.NOT_ANSWERABLE,
        direction=None,
        confidence=None,
        publication_status=PublicationStatus.DRAFT,
    )
    assert draft.publication_status is PublicationStatus.DRAFT

    with pytest.raises(ValueError, match="formal product revision must not be draft"):
        ProductRevisionView(
            id=uuid4(),
            project_id=uuid4(),
            boundary_id=uuid4(),
            manifest_hash=HASH_A,
            price_snapshot_ids=(uuid4(),),
            fx_snapshot_ids=(),
            capital_structure_snapshot_id=uuid4(),
            security_rights_ids=(uuid4(),),
            answerability=AnswerabilityState.NOT_ANSWERABLE,
            direction=None,
            confidence=None,
            publication_status=PublicationStatus.DRAFT,
        )


def test_revision_boundary_and_product_revision_accept_complete_immutable_references() -> (
    None
):
    boundary = RevisionBoundaryInput(
        historical_basis_id=uuid4(),
        mandate_id=uuid4(),
        scope_id=uuid4(),
        agenda_id=uuid4(),
        price_snapshot_ids=(uuid4(),),
        fx_snapshot_ids=(uuid4(),),
        capital_structure_snapshot_id=uuid4(),
        security_rights_ids=(uuid4(),),
        parent_revision_id=uuid4(),
    )
    revision = ProductRevisionView(
        id=uuid4(),
        project_id=uuid4(),
        boundary_id=uuid4(),
        manifest_hash=HASH_A,
        price_snapshot_ids=(uuid4(),),
        fx_snapshot_ids=(uuid4(),),
        capital_structure_snapshot_id=uuid4(),
        security_rights_ids=(uuid4(),),
        answerability=AnswerabilityState.ANSWERABLE,
        direction=AssessmentDirection.PROVISIONAL_NEUTRAL,
        confidence=AssessmentConfidence.MEDIUM,
        publication_status=PublicationStatus.USER_FROZEN,
    )
    assert boundary.price_snapshot_ids
    assert revision.publication_status is PublicationStatus.USER_FROZEN


def test_product_revision_requires_complete_canonical_market_references() -> None:
    first, second = sorted((uuid4(), uuid4()), key=str)
    common = {
        "id": uuid4(),
        "project_id": uuid4(),
        "boundary_id": uuid4(),
        "manifest_hash": HASH_A,
        "fx_snapshot_ids": (),
        "capital_structure_snapshot_id": uuid4(),
        "answerability": AnswerabilityState.NOT_ANSWERABLE,
        "direction": None,
        "confidence": None,
        "publication_status": PublicationStatus.USER_FROZEN,
    }

    with pytest.raises(
        ValueError, match="price_snapshot_ids must be a non-empty tuple"
    ):
        ProductRevisionView(
            **common,
            price_snapshot_ids=(),
            security_rights_ids=(uuid4(),),
        )
    with pytest.raises(
        ValueError, match="security_rights_ids must not contain duplicates"
    ):
        ProductRevisionView(
            **common,
            price_snapshot_ids=(first,),
            security_rights_ids=(second, second),
        )
    with pytest.raises(ValueError, match="price_snapshot_ids must be canonical"):
        ProductRevisionView(
            **common,
            price_snapshot_ids=(second, first),
            security_rights_ids=(uuid4(),),
        )


def test_scope_and_boundary_reject_duplicate_or_wrongly_typed_collections() -> None:
    security_id = uuid4()
    with pytest.raises(
        ValueError, match="target_security_ids must not contain duplicates"
    ):
        ResearchScopeInput(
            primary_company_id=uuid4(),
            target_security_ids=(security_id, security_id),
            industry_ids=(),
            covered_segments=(),
            user_focus=None,
            exclusions=(),
        )

    snapshot_id = uuid4()
    with pytest.raises(
        ValueError, match="price_snapshot_ids must not contain duplicates"
    ):
        RevisionBoundaryInput(
            historical_basis_id=uuid4(),
            mandate_id=uuid4(),
            scope_id=uuid4(),
            agenda_id=uuid4(),
            price_snapshot_ids=(snapshot_id, snapshot_id),
            fx_snapshot_ids=(),
            capital_structure_snapshot_id=uuid4(),
            security_rights_ids=(uuid4(),),
            parent_revision_id=None,
        )

    with pytest.raises(ValueError, match="security_rights_ids must contain UUIDs"):
        RevisionBoundaryInput(
            historical_basis_id=uuid4(),
            mandate_id=uuid4(),
            scope_id=uuid4(),
            agenda_id=uuid4(),
            price_snapshot_ids=(uuid4(),),
            fx_snapshot_ids=(),
            capital_structure_snapshot_id=uuid4(),
            security_rights_ids=("not-a-uuid",),  # type: ignore[arg-type]
            parent_revision_id=None,
        )


def test_product_contracts_are_reexported_from_the_public_domain_package() -> None:
    assert ExportedPriceSnapshotInput is PriceSnapshotInput
    assert ExportedProductRevisionView is ProductRevisionView
    assert exported_agenda_items_hash is agenda_items_hash


def test_agenda_items_hash_is_deterministic_and_agenda_output_must_match() -> None:
    items = ("business model", "risks")
    assert (
        agenda_items_hash(items)
        == "ed75da581aa7149010b680bf85edaf24b53ffe95b1e041386dae3fdb04688309"
    )

    with pytest.raises(ValueError, match="output_hash must match agenda items hash"):
        ResearchAgendaInput(
            scope_id=uuid4(),
            items=items,
            generator=AgendaGeneratorInput(
                method=AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
                template_key="company-research-v1",
                template_version="1",
                model_name=None,
                prompt_template_version=None,
                input_summary_hash=None,
                output_hash=HASH_E,
            ),
        )


@pytest.mark.parametrize("invalid_hash", ["short", "A" * 64, "g" * 64])
def test_hash_fields_require_lowercase_sha256(invalid_hash: str) -> None:
    invalid_hash_builders = (
        lambda: AgendaGeneratorInput(
            method=AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
            template_key="company-research-v1",
            template_version="1",
            model_name=None,
            prompt_template_version=None,
            input_summary_hash=None,
            output_hash=invalid_hash,
        ),
        lambda: AgendaGeneratorInput(
            method=AgendaGenerationMethod.AI_GENERATED,
            template_key=None,
            template_version=None,
            model_name="gpt-5",
            prompt_template_version="agenda-v1",
            input_summary_hash=invalid_hash,
            output_hash=HASH_A,
        ),
        lambda: ProductHistoricalBasisInput(NOW, invalid_hash, HASH_B, HASH_C),
        lambda: ProductHistoricalBasisInput(NOW, HASH_A, invalid_hash, HASH_C),
        lambda: ProductHistoricalBasisInput(NOW, HASH_A, HASH_B, invalid_hash),
        lambda: _price(raw_hash=invalid_hash),
        lambda: _fx(raw_hash=invalid_hash),
        lambda: _capital_structure(raw_hash=invalid_hash),
        lambda: _rights(raw_hash=invalid_hash),
        lambda: ProductRevisionView(
            id=uuid4(),
            project_id=uuid4(),
            boundary_id=uuid4(),
            manifest_hash=invalid_hash,
            price_snapshot_ids=(uuid4(),),
            fx_snapshot_ids=(),
            capital_structure_snapshot_id=uuid4(),
            security_rights_ids=(uuid4(),),
            answerability=AnswerabilityState.ANSWERABLE,
            direction=None,
            confidence=None,
            publication_status=PublicationStatus.USER_FROZEN,
        ),
    )

    for build in invalid_hash_builders:
        with pytest.raises(ValueError, match="lowercase SHA-256"):
            build()


def test_product_contracts_are_frozen() -> None:
    value = _price()

    with pytest.raises(FrozenInstanceError):
        value.price = Decimal("1")
