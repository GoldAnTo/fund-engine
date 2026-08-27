from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy import func, select

from app.models.ledger import ValidationError
from app.underwriting.domain.product_contracts import (
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    SecurityRightsInput,
)
from app.underwriting.fixtures.product_foundation import (
    load_product_foundation_fixture,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    AlphabetMarketInputBundle,
    CapturedCapitalStructure,
    CapturedMarketFX,
    CapturedMarketPrice,
    CapturedProvenance,
    CapturedSecurityRights,
)
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingFXSnapshot,
    UnderwritingPriceSnapshot,
    UnderwritingSecurityRightsVersion,
)
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.services.company_research_initializer import (
    CompanyResearchInitializer,
)
from app.underwriting.services.company_research_market_inputs import (
    CompanyResearchMarketInputs,
)
from app.underwriting.services.market_snapshots import MarketSnapshotService
from app.underwriting.services.market_snapshots import (
    capital_structure_snapshot_hash,
    price_snapshot_hash,
)
from app.underwriting.services.product_foundation_fixture import (
    ProductFoundationFixtureService,
)


CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)
RAW_HASH = "0" * 64


def _initialized(session):
    loaded = ProductFoundationFixtureService(session, now=lambda: CUTOFF).load(
        load_product_foundation_fixture()
    )
    company = loaded.objects["US:ALPHABET:COMPANY"]
    initializer = CompanyResearchInitializer(session, now=lambda: CUTOFF)
    preview = initializer.preview(company_id=company.id, cutoff_at=CUTOFF)
    return initializer.initialize(
        preview_hash=preview.input_hash,
        company_id=company.id,
        cutoff_at=CUTOFF,
        idempotency_key="alphabet-market-inputs",
    )


def _freeze_complete_market(session, initialized) -> None:
    service = MarketSnapshotService(session, now=lambda: CUTOFF)
    securities = {
        security.external_key: security
        for security in session.scalars(
            select(UnderwritingResearchObject).where(
                UnderwritingResearchObject.id.in_(initialized.project.target_security_ids)
            )
        )
    }
    for external_key, price in (
        ("NASDAQ:GOOG", Decimal("207.50")),
        ("NASDAQ:GOOGL", Decimal("206.25")),
    ):
        security = securities[external_key]
        service.freeze_price(
            PriceSnapshotInput(
                security.id,
                price,
                "USD",
                "official_close",
                "unadjusted",
                datetime(2026, 8, 25, 20, tzinfo=UTC),
                datetime(2026, 8, 25, 23, tzinfo=UTC),
                f"test:{external_key}",
                RAW_HASH,
            )
        )
        head = service.security_rights_head(security.id)
        assert head is not None
        service.freeze_security_rights(
            SecurityRightsInput(
                security.id,
                Decimal("5800"),
                Decimal("0") if external_key.endswith("GOOG") else Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                Decimal("1"),
                datetime(2026, 1, 1, tzinfo=UTC),
                None,
                f"test:rights:{external_key}",
                RAW_HASH,
            ),
            expected_parent_id=head.id,
        )
    service.freeze_fx(
        FXSnapshotInput(
            "USD",
            "CNY",
            Decimal("7.180000000000"),
            FxQuoteDirection.QUOTE_PER_BASE,
            datetime(2026, 8, 24, 16, tzinfo=UTC),
            datetime(2026, 8, 25, 22, tzinfo=UTC),
            "test:fed-h10",
            RAW_HASH,
        )
    )
    service.freeze_capital_structure(
        CapitalStructureSnapshotInput(
            initialized.project.primary_company_id,
            "USD",
            Decimal("30000"),
            Decimal("46000"),
            Decimal("0"),
            Decimal("96000"),
            Decimal("0"),
            Decimal("0"),
            Decimal("12000"),
            Decimal("12100"),
            ("unvested equity awards",),
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 6, 30, tzinfo=UTC),
            datetime(2026, 6, 30, tzinfo=UTC),
            datetime(2026, 7, 30, tzinfo=UTC),
            "test:alphabet-10q",
            RAW_HASH,
        )
    )


def _securities(session, initialized):
    return {
        row.external_key: row
        for row in session.scalars(
            select(UnderwritingResearchObject).where(
                UnderwritingResearchObject.id.in_(initialized.project.target_security_ids)
            )
        )
    }


def _captured_bundle() -> AlphabetMarketInputBundle:
    provenance = CapturedProvenance(
        "https://example.test/source",
        "synthetic test locator",
        RAW_HASH,
        "synthetic-provider.v1",
    )
    return AlphabetMarketInputBundle(
        content_hash="a" * 64,
        company_external_key="US:ALPHABET:COMPANY",
        security_external_keys=("NASDAQ:GOOG", "NASDAQ:GOOGL"),
        prices=(
            CapturedMarketPrice(
                "NASDAQ:GOOG", Decimal("207.5"), "USD", "official_close",
                "unadjusted", datetime(2026, 8, 25, 20, tzinfo=UTC),
                datetime(2026, 8, 25, 23, tzinfo=UTC), provenance,
            ),
            CapturedMarketPrice(
                "NASDAQ:GOOGL", Decimal("206.25"), "USD", "official_close",
                "unadjusted", datetime(2026, 8, 25, 20, tzinfo=UTC),
                datetime(2026, 8, 25, 23, tzinfo=UTC), provenance,
            ),
        ),
        fx=CapturedMarketFX(
            "USD", "CNY", Decimal("7.18"), "quote_per_base",
            datetime(2026, 8, 24, 16, tzinfo=UTC),
            datetime(2026, 8, 25, 22, tzinfo=UTC), provenance,
        ),
        capital_structure=CapturedCapitalStructure(
            "US:ALPHABET:COMPANY",
            "USD",
            tuple(
                (key, Decimal(value))
                for key, value in (
                    ("cash", "30000"), ("debt", "46000"),
                    ("minority_interest", "0"), ("investments", "96000"),
                    ("pension_liabilities", "0"), ("other_adjustments", "0"),
                    ("basic_shares", "12000"), ("diluted_shares", "12100"),
                )
            ),
            ("unvested equity awards",),
            datetime(2026, 4, 1, tzinfo=UTC),
            datetime(2026, 6, 30, tzinfo=UTC),
            datetime(2026, 6, 30, tzinfo=UTC),
            datetime(2026, 7, 30, tzinfo=UTC),
            provenance,
            "alphabet-capital-bridge.v1",
            ("pension_liabilities",),
        ),
        security_rights=tuple(
            CapturedSecurityRights(
                key,
                (
                    ("economic_units", Decimal("5800")),
                    ("votes_per_unit", Decimal(votes)),
                    ("conversion_ratio", Decimal("1")),
                    ("adr_ratio", Decimal("1")),
                    ("dividend_rights_per_unit", Decimal("1")),
                ),
                datetime(2026, 1, 1, tzinfo=UTC),
                None,
                provenance,
            )
            for key, votes in (("NASDAQ:GOOG", "0"), ("NASDAQ:GOOGL", "1"))
        ),
    )


def test_market_inputs_select_only_snapshots_available_by_cutoff(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)

    resolved = CompanyResearchMarketInputs(session).resolve(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
    )

    assert resolved.market_at <= CUTOFF
    assert resolved.security_external_keys == ("NASDAQ:GOOG", "NASDAQ:GOOGL")
    assert len(resolved.price_snapshot_ids) == 2
    assert len(resolved.fx_snapshot_ids) == 1
    assert len(resolved.security_rights_ids) == 2


def test_market_inputs_do_not_replace_missing_provider_data(session) -> None:
    initialized = _initialized(session)

    with pytest.raises(ValidationError, match="market inputs are incomplete"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )

    for model in (
        UnderwritingPriceSnapshot,
        UnderwritingFXSnapshot,
        UnderwritingCapitalStructureSnapshot,
    ):
        assert session.scalar(select(func.count()).select_from(model)) == 0


def test_market_inputs_reject_a_non_golden_cutoff(session) -> None:
    initialized = _initialized(session)

    with pytest.raises(ValidationError, match="exact Alphabet cutoff"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=datetime(2026, 8, 25, 9, tzinfo=UTC),
        )


def test_market_inputs_reject_hash_invalid_rows(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)
    price = session.scalar(select(UnderwritingPriceSnapshot).limit(1))
    assert price is not None
    set_committed_value(price, "content_hash", "f" * 64)

    with pytest.raises(ValidationError, match="hash-invalid"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )


def test_market_inputs_reject_duplicate_latest_prices(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)
    security = _securities(session, initialized)["NASDAQ:GOOG"]
    MarketSnapshotService(session, now=lambda: CUTOFF).freeze_price(
        PriceSnapshotInput(
            security.id,
            Decimal("208"),
            "USD",
            "official_close",
            "unadjusted",
            datetime(2026, 8, 25, 20, tzinfo=UTC),
            datetime(2026, 8, 25, 23, tzinfo=UTC),
            "test:duplicate-price",
            "1" * 64,
        )
    )

    with pytest.raises(ValidationError, match="duplicate price"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )


def test_market_inputs_ignore_post_cutoff_rows_and_fail_closed(session) -> None:
    initialized = _initialized(session)
    security = _securities(session, initialized)["NASDAQ:GOOG"]
    MarketSnapshotService(session, now=lambda: CUTOFF).freeze_price(
        PriceSnapshotInput(
            security.id,
            Decimal("208"),
            "USD",
            "official_close",
            "unadjusted",
            datetime(2026, 8, 26, 20, tzinfo=UTC),
            datetime(2026, 8, 26, 23, tzinfo=UTC),
            "test:post-cutoff-price",
            "2" * 64,
        )
    )

    with pytest.raises(ValidationError, match="market inputs are incomplete"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )


def test_market_inputs_reject_cross_company_capital(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)
    capital = session.scalar(select(UnderwritingCapitalStructureSnapshot).limit(1))
    foreign = session.scalar(
        select(UnderwritingResearchObject).where(
            UnderwritingResearchObject.external_key == "CN:300750:COMPANY"
        )
    )
    assert capital is not None and foreign is not None
    set_committed_value(capital, "company_id", foreign.id)
    set_committed_value(capital, "content_hash", capital_structure_snapshot_hash(capital))

    with pytest.raises(ValidationError, match="cross-company"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )


def test_market_inputs_reject_cross_security_price(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)
    price = session.scalar(select(UnderwritingPriceSnapshot).limit(1))
    foreign = session.scalar(
        select(UnderwritingResearchObject).where(
            UnderwritingResearchObject.external_key == "SZSE:300750"
        )
    )
    assert price is not None and foreign is not None
    set_committed_value(price, "security_identity_id", foreign.id)
    set_committed_value(price, "content_hash", price_snapshot_hash(price))

    with pytest.raises(ValidationError, match="cross-security"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )


def test_market_inputs_reject_wrong_price_currency(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)
    price = session.scalar(select(UnderwritingPriceSnapshot).limit(1))
    assert price is not None
    set_committed_value(price, "currency", "CNY")
    set_committed_value(price, "content_hash", price_snapshot_hash(price))

    with pytest.raises(ValidationError, match="wrong currency"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )


def test_market_inputs_return_exact_bindings_identity_and_reverse_request(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)

    resolved = CompanyResearchMarketInputs(session).resolve(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
    )

    assert tuple(item.snapshot_id for item in resolved.snapshot_bindings) == resolved.snapshot_ids
    assert {
        (item.role.value, item.security_external_key)
        for item in resolved.snapshot_bindings
    } == {
        ("price", "NASDAQ:GOOG"),
        ("price", "NASDAQ:GOOGL"),
        ("fx", None),
        ("capital_structure", None),
        ("security_rights", "NASDAQ:GOOG"),
        ("security_rights", "NASDAQ:GOOGL"),
    }
    assert tuple(item.component_key for item in resolved.equity_components) == (
        "class_a",
        "class_b",
        "class_c",
    )
    class_b = resolved.equity_components[1]
    assert class_b.economic_units == Decimal("400")
    assert class_b.price_proxy_security_external_key == "NASDAQ:GOOGL"
    assert resolved.reverse_dcf_request.target_enterprise_value == Decimal("2402250")


def test_frozen_context_rejects_missing_class_b_or_class_b_merged_into_listed_rights(
    session,
) -> None:
    initialized = _initialized(session)
    context = CompanyResearchMarketInputs(session).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )

    with pytest.raises(ValidationError, match="Class A-B-C"):
        replace(context, equity_components=(context.equity_components[0], context.equity_components[2]))

    bridge = context.market_bridge
    goog, googl = bridge.securities
    merged_googl = replace(
        googl,
        listed_class_economic_units=(
            googl.listed_class_economic_units
            + context.equity_components[1].economic_units
        ),
    )
    with pytest.raises(ValidationError, match="Class B"):
        replace(
            context,
            market_bridge=replace(bridge, securities=(goog, merged_googl)),
        )


def test_frozen_context_rejects_reverse_enterprise_value_that_omits_class_b(session) -> None:
    initialized = _initialized(session)
    context = CompanyResearchMarketInputs(session).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )
    class_b = context.equity_components[1]
    price = next(
        item.market_price_usd
        for item in context.market_bridge.securities
        if item.security_external_key == class_b.price_proxy_security_external_key
    )

    with pytest.raises(ValidationError, match="Class A-B-C market equity"):
        replace(
            context,
            reverse_dcf_request=replace(
                context.reverse_dcf_request,
                target_enterprise_value=(
                    context.reverse_dcf_request.target_enterprise_value
                    - class_b.economic_units * price
                ),
            ),
        )


def test_prepare_is_idempotent_and_patches_only_exact_internal_draft_refs(session) -> None:
    initialized = _initialized(session)
    resolver = CompanyResearchMarketInputs(session)

    first = resolver.prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )
    from app.underwriting.services.workspace_draft import WorkspaceDraftService

    first_draft = WorkspaceDraftService(session, now=lambda: CUTOFF).read(
        initialized.project.id
    )
    assert first_draft is not None
    second = resolver.prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )
    second_draft = WorkspaceDraftService(session, now=lambda: CUTOFF).read(
        initialized.project.id
    )
    assert second_draft is not None

    assert second.snapshot_ids == first.snapshot_ids
    assert second_draft.lock_version == first_draft.lock_version
    assert second_draft.content.price_snapshot_ids == first.price_snapshot_ids
    assert second_draft.content.fx_snapshot_ids == first.fx_snapshot_ids
    assert second_draft.content.capital_structure_snapshot_id == first.capital_structure_snapshot_id
    assert second_draft.content.security_rights_ids == first.security_rights_ids
    assert session.scalar(select(func.count()).select_from(UnderwritingPriceSnapshot)) == 2
    assert session.scalar(select(func.count()).select_from(UnderwritingFXSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(UnderwritingCapitalStructureSnapshot)) == 1
    assert session.scalar(select(func.count()).select_from(UnderwritingSecurityRightsVersion)) == 5


def test_prepare_rolls_back_every_market_write_on_failure(session, monkeypatch) -> None:
    initialized = _initialized(session)

    def fail_capital(*_args, **_kwargs):
        raise RuntimeError("injected capital write failure")

    monkeypatch.setattr(MarketSnapshotService, "freeze_capital_structure", fail_capital)
    with pytest.raises(RuntimeError, match="injected capital"):
        CompanyResearchMarketInputs(session).prepare(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
            market_inputs=_captured_bundle(),
        )

    assert session.scalar(select(func.count()).select_from(UnderwritingPriceSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(UnderwritingFXSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(UnderwritingCapitalStructureSnapshot)) == 0
    assert session.scalar(select(func.count()).select_from(UnderwritingSecurityRightsVersion)) == 3
    assert initialized.draft.content.price_snapshot_ids == ()
