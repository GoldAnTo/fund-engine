from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy import func, select

from app.models.ledger import ValidationError
from app.models.ledger import ConflictError
from app.underwriting.domain.product_contracts import (
    PriceSnapshotInput,
)
from app.underwriting.fixtures.product_foundation import (
    load_product_foundation_fixture,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    AlphabetMarketInputBundle,
    CapturedCapitalStructure,
    CapturedMarketFX,
    CapturedMarketPrice,
    CapturedNonListedSecurityRights,
    CapturedProvenance,
    CapturedSecurityRights,
    load_alphabet_golden_case_fixture,
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
    _latest_exact,
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
INSTALL_TIME = datetime(2026, 8, 27, 12, tzinfo=UTC)


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
    CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )


def _securities(session, initialized):
    return {
        row.external_key: row
        for row in session.scalars(
            select(UnderwritingResearchObject).where(
                UnderwritingResearchObject.id.in_(
                    initialized.project.target_security_ids
                )
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
                "NASDAQ:GOOG",
                Decimal("207.5"),
                "USD",
                "official_close",
                "unadjusted",
                datetime(2026, 8, 25, 20, tzinfo=UTC),
                datetime(2026, 8, 25, 23, tzinfo=UTC),
                provenance,
            ),
            CapturedMarketPrice(
                "NASDAQ:GOOGL",
                Decimal("206.25"),
                "USD",
                "official_close",
                "unadjusted",
                datetime(2026, 8, 25, 20, tzinfo=UTC),
                datetime(2026, 8, 25, 23, tzinfo=UTC),
                provenance,
            ),
        ),
        fx=CapturedMarketFX(
            "USD",
            "CNY",
            Decimal("7.18"),
            "quote_per_base",
            datetime(2026, 8, 24, 16, tzinfo=UTC),
            datetime(2026, 8, 25, 22, tzinfo=UTC),
            provenance,
        ),
        capital_structure=CapturedCapitalStructure(
            "US:ALPHABET:COMPANY",
            "USD",
            tuple(
                (key, Decimal(value))
                for key, value in (
                    ("cash", "30000"),
                    ("debt", "46000"),
                    ("minority_interest", "0"),
                    ("investments", "96000"),
                    ("pension_liabilities", "0"),
                    ("other_adjustments", "0"),
                    ("basic_shares", "12000"),
                    ("diluted_shares", "12100"),
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
                datetime(2026, 7, 30, tzinfo=UTC),
                provenance,
            )
            for key, votes in (("NASDAQ:GOOG", "0"), ("NASDAQ:GOOGL", "1"))
        ),
        class_b_rights=CapturedNonListedSecurityRights(
            component_key="class_b",
            economic_units=Decimal("400"),
            votes_per_unit=Decimal("10"),
            conversion_to_security_external_key="NASDAQ:GOOGL",
            conversion_ratio=Decimal("1"),
            dividend_rights_per_unit=Decimal("1"),
            economic_rights_per_unit=Decimal("1"),
            effective_from=datetime(2026, 1, 1, tzinfo=UTC),
            effective_to=None,
            price_proxy_security_external_key="NASDAQ:GOOGL",
            price_proxy_policy_version="alphabet_class_b_googl_proxy.v1",
            available_at=datetime(2026, 7, 30, tzinfo=UTC),
            legal_provenance=provenance,
            unit_provenance=provenance,
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


def test_market_inputs_reject_duplicate_latest_prices() -> None:
    latest_at = datetime(2026, 8, 25, 20, tzinfo=UTC)
    with pytest.raises(ValidationError, match="duplicate price"):
        _latest_exact(
            (
                SimpleNamespace(market_at=latest_at),
                SimpleNamespace(market_at=latest_at),
            ),
            time_field="market_at",
            label="price for NASDAQ:GOOG",
        )


def test_market_inputs_reject_a_grandfathered_legacy_business_conflict(session) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)
    price = session.scalar(select(UnderwritingPriceSnapshot).limit(1))
    assert price is not None
    set_committed_value(price, "legacy_business_conflict", True)

    with pytest.raises(ValidationError, match="grandfathered legacy business-time"):
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
    set_committed_value(
        capital, "content_hash", capital_structure_snapshot_hash(capital)
    )

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


def test_market_inputs_return_exact_bindings_identity_and_reverse_request(
    session,
) -> None:
    initialized = _initialized(session)
    _freeze_complete_market(session, initialized)

    resolved = CompanyResearchMarketInputs(session).resolve(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
    )

    assert (
        tuple(item.snapshot_id for item in resolved.snapshot_bindings)
        == resolved.snapshot_ids
    )
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
    assert all(binding.snapshot_content_hash for binding in resolved.snapshot_bindings)
    assert all(binding.capture_envelope_id for binding in resolved.snapshot_bindings)
    assert all(binding.capture_content_hash for binding in resolved.snapshot_bindings)
    assert all(
        binding.provenance_role == "primary" for binding in resolved.snapshot_bindings
    )
    assert tuple(item.component_key for item in resolved.equity_components) == (
        "class_a",
        "class_b",
        "class_c",
    )
    class_b = resolved.equity_components[1]
    assert class_b.economic_units == Decimal("400")
    assert class_b.price_proxy_security_external_key == "NASDAQ:GOOGL"
    assert resolved.reverse_dcf_request.target_enterprise_value == Decimal("2402250")


def test_prepare_persists_and_resolver_restores_exact_capture_provenance(
    session,
) -> None:
    from app.underwriting.persistence.product_models import (
        UnderwritingMarketCaptureEnvelope,
    )

    initialized = _initialized(session)
    bundle = _captured_bundle()
    resolved = CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=bundle,
    )
    envelopes = tuple(session.scalars(select(UnderwritingMarketCaptureEnvelope)))

    assert envelopes
    assert all(row.acquired_at.replace(tzinfo=UTC) == INSTALL_TIME for row in envelopes)
    assert all(
        row.source_locator == "synthetic test locator"
        for row in envelopes
        if row.provenance_role == "primary"
    )
    assert all(
        row.provider_policy_version == "synthetic-provider.v1"
        for row in envelopes
        if row.provenance_role == "primary"
    )
    assert all(
        row.raw_components == []
        for row in envelopes
        if row.provenance_role == "primary"
    )
    assert {
        binding.source_ref.source_locator for binding in resolved.snapshot_bindings
    } == {"synthetic test locator"}


def test_prepare_uses_real_install_clock_and_resolves_historical_available_rows(
    session,
) -> None:
    initialized = _initialized(session)
    resolved = CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )
    assert resolved.market_at <= CUTOFF
    assert all(
        row.created_at.replace(tzinfo=UTC) == INSTALL_TIME
        for model in (
            UnderwritingPriceSnapshot,
            UnderwritingFXSnapshot,
            UnderwritingCapitalStructureSnapshot,
            UnderwritingSecurityRightsVersion,
        )
        for row in session.scalars(select(model))
        if row.created_at.replace(tzinfo=UTC) > CUTOFF
    )


def test_prepare_rejects_security_rights_authenticated_after_cutoff(session) -> None:
    initialized = _initialized(session)
    bundle = _captured_bundle()
    future_right = replace(
        bundle.security_rights[0],
        available_at=datetime(2026, 8, 26, tzinfo=UTC),
    )
    with pytest.raises(ValidationError, match="market inputs are incomplete"):
        CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME).prepare(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
            market_inputs=replace(
                bundle,
                security_rights=(future_right, bundle.security_rights[1]),
            ),
        )


@pytest.mark.parametrize("role", ("price", "fx", "capital"))
def test_resolver_rejects_primary_capture_available_after_cutoff(
    session, role: str
) -> None:
    initialized = _initialized(session)
    context = CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )
    from app.underwriting.persistence.product_models import (
        UnderwritingMarketCaptureEnvelope,
    )

    snapshot_id = {
        "price": context.price_snapshot_ids[0],
        "fx": context.fx_snapshot_ids[0],
        "capital": context.capital_structure_snapshot_id,
    }[role]
    envelope = session.scalar(
        select(UnderwritingMarketCaptureEnvelope).where(
            UnderwritingMarketCaptureEnvelope.snapshot_id == snapshot_id,
            UnderwritingMarketCaptureEnvelope.provenance_role == "primary",
        )
    )
    assert envelope is not None
    envelope.authenticated_available_at = datetime(2026, 8, 26, tzinfo=UTC)
    from app.underwriting.hashing import canonical_hash

    envelope.content_hash = canonical_hash(
        {
            "schema_version": "product.market-capture-envelope.v1",
            "snapshot_kind": envelope.snapshot_kind,
            "snapshot_id": str(envelope.snapshot_id),
            "provenance_role": envelope.provenance_role,
            "source_url": envelope.source_url,
            "source_locator": envelope.source_locator,
            "provider_policy_version": envelope.provider_policy_version,
            "raw_hash": envelope.raw_hash,
            "raw_components": envelope.raw_components,
            "authenticated_available_at": "2026-08-26T00:00:00+00:00",
        }
    )
    session.flush()
    session.expire_all()
    with pytest.raises(ValidationError, match="market inputs are incomplete"):
        CompanyResearchMarketInputs(session).resolve(
            project_id=initialized.project.id,
            cutoff_at=CUTOFF,
        )


def test_database_rejects_different_price_at_same_business_identity_time(
    session,
) -> None:
    initialized = _initialized(session)
    resolver = CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME)
    resolver.prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=_captured_bundle(),
    )
    security = _securities(session, initialized)["NASDAQ:GOOG"]
    with pytest.raises(ConflictError, match="natural identity"):
        MarketSnapshotService(session, now=lambda: INSTALL_TIME).freeze_price(
            PriceSnapshotInput(
                security.id,
                Decimal("999"),
                "USD",
                "official_close",
                "unadjusted",
                datetime(2026, 8, 25, 20, tzinfo=UTC),
                datetime(2026, 8, 25, 23, tzinfo=UTC),
                "https://different.example/price",
                "9" * 64,
            )
        )


@pytest.mark.parametrize("kind", ("price", "fx", "capital", "rights"))
def test_prepare_conflicts_on_different_content_for_same_business_identity_time(
    session, kind: str
) -> None:
    initialized = _initialized(session)
    resolver = CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME)
    bundle = _captured_bundle()
    resolver.prepare(
        project_id=initialized.project.id, cutoff_at=CUTOFF, market_inputs=bundle
    )
    if kind == "price":
        changed = replace(bundle.prices[0], value=bundle.prices[0].value + 1)
        bundle = replace(bundle, prices=(changed, bundle.prices[1]))
    elif kind == "fx":
        bundle = replace(bundle, fx=replace(bundle.fx, rate=bundle.fx.rate + 1))
    elif kind == "capital":
        values = dict(bundle.capital_structure.values)
        values["cash"] += 1
        bundle = replace(
            bundle,
            capital_structure=replace(
                bundle.capital_structure, values=tuple(values.items())
            ),
        )
    else:
        values = dict(bundle.security_rights[0].values)
        values["economic_units"] += 1
        changed = replace(bundle.security_rights[0], values=tuple(values.items()))
        bundle = replace(bundle, security_rights=(changed, bundle.security_rights[1]))

    counts = tuple(
        session.scalar(select(func.count()).select_from(model))
        for model in (
            UnderwritingPriceSnapshot,
            UnderwritingFXSnapshot,
            UnderwritingCapitalStructureSnapshot,
            UnderwritingSecurityRightsVersion,
        )
    )
    with pytest.raises(ConflictError, match="same business identity/time"):
        resolver.prepare(
            project_id=initialized.project.id, cutoff_at=CUTOFF, market_inputs=bundle
        )
    assert (
        tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (
                UnderwritingPriceSnapshot,
                UnderwritingFXSnapshot,
                UnderwritingCapitalStructureSnapshot,
                UnderwritingSecurityRightsVersion,
            )
        )
        == counts
    )


def test_class_b_is_a_typed_nonlisted_legal_rights_and_proxy_component(session) -> None:
    initialized = _initialized(session)
    context = CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=load_alphabet_golden_case_fixture().market_inputs,
    )
    class_b = context.equity_components[1]
    assert class_b.component_key == "class_b"
    assert class_b.economic_units == Decimal("835")
    assert class_b.votes_per_unit == Decimal("10")
    assert class_b.conversion_to_security_external_key == "NASDAQ:GOOGL"
    assert class_b.conversion_ratio == Decimal("1")
    assert class_b.dividend_rights_per_unit == Decimal("1")
    assert class_b.economic_rights_per_unit == Decimal("1")
    assert class_b.legal_rights_ref.fact_key == "security_rights_class_b"
    assert class_b.unit_source_ref.fact_key == "economic_units_class_b"
    assert class_b.price_proxy_ref.fact_key == "market_price_proxy_class_b"
    assert class_b.price_proxy_policy_version == "alphabet_class_b_googl_proxy.v1"


def test_frozen_snapshot_raw_components_are_recursively_immutable_typed_values(
    session,
) -> None:
    initialized = _initialized(session)
    context = CompanyResearchMarketInputs(session, now=lambda: INSTALL_TIME).prepare(
        project_id=initialized.project.id,
        cutoff_at=CUTOFF,
        market_inputs=load_alphabet_golden_case_fixture().market_inputs,
    )
    component = next(
        binding.raw_components[0]
        for binding in context.snapshot_bindings
        if binding.raw_components
    )
    assert type(component).__name__ == "FrozenRawComponentReference"
    with pytest.raises(FrozenInstanceError):
        component.raw_hash = "0" * 64


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
        replace(
            context,
            equity_components=(
                context.equity_components[0],
                context.equity_components[2],
            ),
        )

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


def test_frozen_context_rejects_reverse_enterprise_value_that_omits_class_b(
    session,
) -> None:
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


def test_prepare_is_idempotent_and_patches_only_exact_internal_draft_refs(
    session,
) -> None:
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
    assert (
        second_draft.content.capital_structure_snapshot_id
        == first.capital_structure_snapshot_id
    )
    assert second_draft.content.security_rights_ids == first.security_rights_ids
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingPriceSnapshot)) == 2
    )
    assert session.scalar(select(func.count()).select_from(UnderwritingFXSnapshot)) == 1
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingCapitalStructureSnapshot)
        )
        == 1
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 5
    )


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

    assert (
        session.scalar(select(func.count()).select_from(UnderwritingPriceSnapshot)) == 0
    )
    assert session.scalar(select(func.count()).select_from(UnderwritingFXSnapshot)) == 0
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingCapitalStructureSnapshot)
        )
        == 0
    )
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingSecurityRightsVersion)
        )
        == 3
    )
    assert initialized.draft.content.price_snapshot_ids == ()
