from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    AgendaGenerationMethod,
    AgendaGeneratorInput,
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    ProductHistoricalBasisInput,
    ResearchAgendaInput,
    ResearchScopeInput,
    RevisionBoundaryInput,
    SecurityRightsInput,
    agenda_items_hash,
)
from app.underwriting.domain.types import InvestmentMandateInput
from app.underwriting.persistence.models import (
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingPriceSnapshot,
)
from app.underwriting.services.market_snapshots import (
    BoundaryContext,
    MarketSnapshotService,
    capital_structure_snapshot_hash,
    fx_snapshot_hash,
    price_snapshot_hash,
    security_rights_hash,
    validate_market_coverage,
)
from app.underwriting.services.product_project import ResearchProjectService


NOW = datetime(2026, 8, 24, 9, tzinfo=UTC)
MARKET = datetime(2026, 8, 20, 7, 30, tzinfo=UTC)
EFFECTIVE = datetime(2020, 1, 1, tzinfo=UTC)
A64 = "a" * 64
B64 = "b" * 64
C64 = "c" * 64
D64 = "d" * 64


@pytest.fixture
def services(session):
    return type(
        "Services",
        (),
        {
            "projects": ResearchProjectService(session, now=lambda: NOW),
            "market": MarketSnapshotService(session, now=lambda: NOW),
        },
    )()


def _object(session, kind: str, key: str) -> UnderwritingResearchObject:
    row = UnderwritingResearchObject(
        kind=kind,
        external_key=key,
        canonical_name=key,
        created_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _relation(session, company_id: UUID, security_id: UUID) -> None:
    session.add(
        UnderwritingObjectRelation(
            parent_id=company_id,
            child_id=security_id,
            relation_type="company_has_security",
            created_at=NOW,
        )
    )
    session.flush()


def _identity(projects, security: UnderwritingResearchObject, currency: str = "CNY"):
    return projects.append_identity_version(
        object_id=security.id,
        canonical_name=security.canonical_name,
        symbol=security.external_key.split(".", 1)[0],
        exchange="TEST",
        share_class="ordinary",
        trading_currency=currency,
        effective_from=EFFECTIVE,
        effective_to=None,
        expected_parent_id=None,
    )


def _seed_project(session, services, *, currency: str = "CNY"):
    company = _object(session, "company", f"company:{uuid4()}")
    security = _object(session, "security", f"SEC-{uuid4()}.TEST")
    _relation(session, company.id, security.id)
    _identity(services.projects, security, currency)
    project = services.projects.create_project(company.id, (security.id,))
    return company, security, project


def _price(security_id: UUID, **overrides: object) -> PriceSnapshotInput:
    values: dict[str, object] = {
        "security_identity_id": security_id,
        "price": Decimal("123.4500000000"),
        "currency": "CNY",
        "price_type": "close",
        "adjustment_basis": "unadjusted",
        "market_at": MARKET,
        "available_at": MARKET + timedelta(minutes=5),
        "source_id": "exchange-feed",
        "raw_hash": A64,
    }
    values.update(overrides)
    return PriceSnapshotInput(**values)  # type: ignore[arg-type]


def _fx(**overrides: object) -> FXSnapshotInput:
    values: dict[str, object] = {
        "base_currency": "USD",
        "quote_currency": "CNY",
        "rate": Decimal("7.200000000000"),
        "quote_direction": FxQuoteDirection.QUOTE_PER_BASE,
        "market_at": MARKET,
        "available_at": MARKET + timedelta(minutes=5),
        "source_id": "fx-feed",
        "raw_hash": B64,
    }
    values.update(overrides)
    return FXSnapshotInput(**values)  # type: ignore[arg-type]


def _capital(company_id: UUID, **overrides: object) -> CapitalStructureSnapshotInput:
    values: dict[str, object] = {
        "company_id": company_id,
        "currency": "CNY",
        "cash": Decimal("100.0000000000"),
        "debt": Decimal("50.0000000000"),
        "minority_interest": Decimal("10.0000000000"),
        "investments": Decimal("5.0000000000"),
        "pension_liabilities": Decimal("2.0000000000"),
        "other_adjustments": Decimal("-0"),
        "basic_shares": Decimal("1000.0000000000"),
        "diluted_shares": Decimal("1100.0000000000"),
        "potential_dilution_descriptors": ("employee options",),
        "report_period_start": datetime(2026, 1, 1, tzinfo=UTC),
        "report_period_end": datetime(2026, 6, 30, tzinfo=UTC),
        "market_at": MARKET,
        "available_at": MARKET + timedelta(minutes=5),
        "source_id": "annual-report",
        "raw_hash": C64,
    }
    values.update(overrides)
    return CapitalStructureSnapshotInput(**values)  # type: ignore[arg-type]


def _rights(security_id: UUID, **overrides: object) -> SecurityRightsInput:
    values: dict[str, object] = {
        "security_identity_id": security_id,
        "economic_units": Decimal("1.0000000000"),
        "votes_per_unit": Decimal("0"),
        "conversion_ratio": Decimal("1.0000000000"),
        "adr_ratio": Decimal("1.0000000000"),
        "dividend_rights_per_unit": Decimal("-0"),
        "effective_from": EFFECTIVE,
        "effective_to": None,
        "source_id": "listing-rules",
        "raw_hash": D64,
    }
    values.update(overrides)
    return SecurityRightsInput(**values)  # type: ignore[arg-type]


def test_freeze_price_normalizes_utc_decimal_hash_and_exactly_deduplicates(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    value = _price(
        security.id,
        market_at=datetime.fromisoformat("2026-08-20T15:30:00+08:00"),
        available_at=datetime.fromisoformat("2026-08-20T15:35:00+08:00"),
    )

    first = services.market.freeze_price(value)
    second = services.market.freeze_price(value)
    session.commit()
    session.expire_all()
    reloaded = services.market.price(first.id)

    assert second.id == first.id
    assert reloaded is not None
    assert reloaded.price == Decimal("123.4500000000")
    assert reloaded.market_at.replace(tzinfo=UTC) == MARKET
    assert reloaded.content_hash == price_snapshot_hash(reloaded)
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingPriceSnapshot)) == 1
    )


def test_price_requires_security_kind_effective_identity_and_matching_currency(
    session, services
) -> None:
    company = _object(session, "company", "company:not-security")
    with pytest.raises(ValidationError, match="Security"):
        services.market.freeze_price(_price(company.id))

    security = _object(session, "security", "NO-IDENTITY.TEST")
    with pytest.raises(ValidationError, match="effective.*identity"):
        services.market.freeze_price(_price(security.id))

    _identity(services.projects, security, "USD")
    with pytest.raises(ValidationError, match="currency"):
        services.market.freeze_price(_price(security.id, currency="CNY"))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"price": Decimal("0")}, "positive"),
        ({"price": Decimal("NaN")}, "positive"),
        ({"currency": "cny"}, "currency"),
        ({"currency": "EUR"}, "supported"),
        ({"market_at": datetime(2026, 1, 1)}, "timezone-aware"),
        ({"available_at": MARKET - timedelta(seconds=1)}, "market_at"),
        ({"adjustment_basis": ""}, "adjustment_basis"),
    ],
)
def test_price_input_fails_closed(overrides, message) -> None:
    with pytest.raises(ValueError, match=message):
        _price(uuid4(), **overrides)


def test_price_rejects_meaningful_precision_beyond_database_scale(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    with pytest.raises(ValidationError, match="10 decimal places"):
        services.market.freeze_price(
            _price(security.id, price=Decimal("1.00000000001"))
        )


def test_every_accepted_decimal_survives_real_sqlite_commit_reload(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    dangerous = Decimal("1000000000000.1234567890")

    try:
        row = services.market.freeze_price(_price(security.id, price=dangerous))
    except ValidationError as exc:
        assert "SQLite" in str(exc) and "precision boundary" in str(exc)
        return

    original_hash = row.content_hash
    session.commit()
    session.expire_all()
    reloaded = services.market.price(row.id)

    assert reloaded.price == dangerous
    assert reloaded.content_hash == price_snapshot_hash(reloaded) == original_hash


@pytest.mark.parametrize(
    ("snapshot_kind", "field_name"),
    [
        ("price", "price"),
        ("fx", "rate"),
        ("capital", "cash"),
        ("capital", "debt"),
        ("capital", "minority_interest"),
        ("capital", "investments"),
        ("capital", "pension_liabilities"),
        ("capital", "other_adjustments"),
        ("capital", "basic_shares"),
        ("capital", "diluted_shares"),
        ("rights", "economic_units"),
        ("rights", "votes_per_unit"),
        ("rights", "conversion_ratio"),
        ("rights", "adr_ratio"),
        ("rights", "dividend_rights_per_unit"),
    ],
)
def test_freeze_rejects_decimal_that_sqlite_numeric_round_trip_would_change(
    session, services, snapshot_kind, field_name
) -> None:
    company, security, _project = _seed_project(session, services)
    scale = 12 if snapshot_kind == "fx" else 10
    dangerous = Decimal(
        "1000000000000.123456789012" if scale == 12 else "1000000000000.1234567890"
    )

    with pytest.raises(ValidationError, match="SQLite.*precision boundary"):
        if snapshot_kind == "price":
            services.market.freeze_price(_price(security.id, price=dangerous))
        elif snapshot_kind == "fx":
            services.market.freeze_fx(_fx(rate=dangerous))
        elif snapshot_kind == "capital":
            overrides = {field_name: dangerous}
            if field_name == "basic_shares":
                overrides["diluted_shares"] = dangerous + Decimal("1")
            services.market.freeze_capital_structure(_capital(company.id, **overrides))
        else:
            services.market.freeze_security_rights(
                _rights(security.id, **{field_name: dangerous}),
                expected_parent_id=None,
            )


@pytest.mark.parametrize(
    ("snapshot_kind", "field_name", "maximum"),
    [
        ("price", "price_type", 64),
        ("price", "adjustment_basis", 64),
        ("price", "source_id", 256),
        ("fx", "source_id", 256),
        ("capital", "source_id", 256),
        ("rights", "source_id", 256),
    ],
)
def test_snapshot_text_columns_reject_trimmed_values_over_declared_width(
    session, services, snapshot_kind, field_name, maximum
) -> None:
    company, security, _ = _seed_project(session, services)
    value = f" {'x' * (maximum + 1)} "

    with pytest.raises(ValidationError, match=field_name):
        if snapshot_kind == "price":
            services.market.freeze_price(_price(security.id, **{field_name: value}))
        elif snapshot_kind == "fx":
            services.market.freeze_fx(_fx(**{field_name: value}))
        elif snapshot_kind == "capital":
            services.market.freeze_capital_structure(
                _capital(company.id, **{field_name: value})
            )
        else:
            services.market.freeze_security_rights(
                _rights(security.id, **{field_name: value}),
                expected_parent_id=None,
            )


def test_snapshot_text_columns_accept_declared_width_boundaries(
    session, services
) -> None:
    company, security, _ = _seed_project(session, services)

    price = services.market.freeze_price(
        _price(
            security.id,
            price_type="p" * 64,
            adjustment_basis="a" * 64,
            source_id="s" * 256,
        )
    )
    fx = services.market.freeze_fx(_fx(source_id="s" * 256))
    capital = services.market.freeze_capital_structure(
        _capital(company.id, source_id="s" * 256)
    )
    rights = services.market.freeze_security_rights(
        _rights(security.id, source_id="s" * 256), expected_parent_id=None
    )

    assert len(price.price_type) == len(price.adjustment_basis) == 64
    assert all(len(row.source_id) == 256 for row in (price, fx, capital, rights))


def test_natural_identity_conflict_does_not_reuse_row_and_session_remains_usable(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    first = services.market.freeze_price(_price(security.id))

    with pytest.raises(ConflictError, match="natural identity"):
        services.market.freeze_price(_price(security.id, price=Decimal("124")))

    assert services.market.price(first.id).id == first.id
    assert (
        session.scalar(select(func.count()).select_from(UnderwritingPriceSnapshot)) == 1
    )


def test_freeze_fx_preserves_quote_per_base_direction_and_scale(
    session, services
) -> None:
    value = _fx()
    row = services.market.freeze_fx(value)
    duplicate = services.market.freeze_fx(value)
    session.commit()
    session.expire_all()
    reloaded = services.market.fx(row.id)

    assert duplicate.id == row.id
    assert reloaded.base_currency == "USD"
    assert reloaded.quote_currency == "CNY"
    assert reloaded.quote_direction == "quote_per_base"
    assert reloaded.rate == Decimal("7.200000000000")
    assert reloaded.content_hash == fx_snapshot_hash(reloaded)
    assert services.market.fx_for_pair("CNY", "USD", MARKET) is None


def test_fx_rejects_inverse_inference_and_excess_precision(session, services) -> None:
    with pytest.raises(ValidationError, match="12 decimal places"):
        services.market.freeze_fx(_fx(rate=Decimal("7.2000000000001")))
    with pytest.raises(ValueError, match="quote direction"):
        _fx(quote_direction="base_per_quote")


def test_fx_for_pair_rejects_conflicting_source_at_the_same_business_time(
    session, services
) -> None:
    first = services.market.freeze_fx(_fx())

    with pytest.raises(ConflictError, match="natural identity"):
        services.market.freeze_fx(_fx(source_id="second-feed", raw_hash="e" * 64))

    assert services.market.fx_for_pair("USD", "CNY", MARKET).id == first.id


def test_freeze_capital_structure_requires_company_and_recomputable_hash(
    session, services
) -> None:
    company, security, _ = _seed_project(session, services)
    with pytest.raises(ValidationError, match="Company"):
        services.market.freeze_capital_structure(_capital(security.id))

    row = services.market.freeze_capital_structure(_capital(company.id))
    duplicate = services.market.freeze_capital_structure(_capital(company.id))
    session.commit()
    session.expire_all()
    reloaded = services.market.capital_structure(row.id)

    assert duplicate.id == row.id
    assert reloaded.other_adjustments == Decimal("0E-10")
    assert reloaded.content_hash == capital_structure_snapshot_hash(reloaded)
    assert (
        session.scalar(
            select(func.count()).select_from(UnderwritingCapitalStructureSnapshot)
        )
        == 1
    )


def test_capital_structure_rejects_share_and_numeric_boundary_errors(
    session, services
) -> None:
    company, _, _ = _seed_project(session, services)
    with pytest.raises(ValueError, match="diluted_shares"):
        _capital(company.id, basic_shares=Decimal("10"), diluted_shares=Decimal("9"))
    with pytest.raises(ValidationError, match="10 decimal places"):
        services.market.freeze_capital_structure(
            _capital(company.id, cash=Decimal("1.00000000001"))
        )
    with pytest.raises(ValueError, match="report period"):
        _capital(
            company.id,
            report_period_start=datetime(2026, 7, 1, tzinfo=UTC),
            report_period_end=datetime(2026, 6, 30, tzinfo=UTC),
        )


def test_capital_structure_descriptors_are_unique_after_trim_and_fail_consistently(
    session, services
) -> None:
    company, _, _ = _seed_project(session, services)
    with pytest.raises(ValidationError, match="descriptors.*duplicates"):
        services.market.freeze_capital_structure(
            _capital(
                company.id,
                potential_dilution_descriptors=(
                    " employee options ",
                    "employee options",
                ),
            )
        )

    malformed = _capital(company.id)
    object.__setattr__(malformed, "potential_dilution_descriptors", ["options"])
    with pytest.raises(ValidationError, match="descriptors.*tuple"):
        services.market.freeze_capital_structure(malformed)


def test_security_rights_are_versioned_with_exact_parent_and_effective_lookup(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    first = services.market.freeze_security_rights(
        _rights(security.id, effective_to=MARKET), expected_parent_id=None
    )
    second_value = _rights(
        security.id,
        effective_from=MARKET,
        raw_hash="e" * 64,
        votes_per_unit=Decimal("1"),
    )
    second = services.market.freeze_security_rights(
        second_value, expected_parent_id=first.id
    )
    duplicate = services.market.freeze_security_rights(
        second_value, expected_parent_id=first.id
    )
    session.commit()
    session.expire_all()
    reloaded = services.market.security_rights(second.id)

    assert duplicate.id == second.id
    assert second.version == 2
    assert (
        services.market.effective_security_rights(
            security.id, MARKET - timedelta(seconds=1)
        ).id
        == first.id
    )
    assert (
        services.market.effective_security_rights(security.id, MARKET).id == second.id
    )
    assert reloaded.votes_per_unit == Decimal("1.0000000000")
    assert reloaded.dividend_rights_per_unit == Decimal("0E-10")
    assert reloaded.content_hash == security_rights_hash(reloaded)

    with pytest.raises(ConflictError, match="parent|head"):
        services.market.freeze_security_rights(
            _rights(
                security.id,
                effective_from=MARKET + timedelta(days=1),
                raw_hash="f" * 64,
            ),
            expected_parent_id=first.id,
        )


def test_security_rights_resolution_forbids_backfill_before_head_and_allows_explicit_successor(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    head_from = MARKET
    head_to = MARKET + timedelta(days=1)
    head = services.market.freeze_security_rights(
        _rights(
            security.id,
            effective_from=head_from,
            effective_to=head_to,
        ),
        expected_parent_id=None,
    )

    before = services.market.resolve_security_rights(
        security.id, head_from - timedelta(seconds=1)
    )
    assert before.effective is None
    assert before.head.id == head.id
    assert before.append_allowed is False
    assert before.expected_parent_id is None
    assert before.minimum_effective_from is None
    assert before.reason_code == "before_head"
    assert before.reason_action == "adjust_market_at"

    after = services.market.resolve_security_rights(security.id, head_to)
    assert after.effective is None
    assert after.head.id == head.id
    assert after.append_allowed is True
    assert after.expected_parent_id == head.id
    assert after.minimum_effective_from == head_to
    assert after.reason_code == "successor_required"
    assert after.reason_action == "append_successor"

    successor = services.market.freeze_security_rights(
        _rights(
            security.id,
            effective_from=after.minimum_effective_from,
            raw_hash="e" * 64,
        ),
        expected_parent_id=after.expected_parent_id,
    )
    assert successor.supersedes_id == head.id
    assert (
        services.market.resolve_security_rights(security.id, head_to).effective.id
        == successor.id
    )


def test_security_rights_resolution_allows_explicit_initial_version_only_without_history(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)

    resolution = services.market.resolve_security_rights(security.id, MARKET)

    assert resolution.effective is None
    assert resolution.head is None
    assert resolution.append_allowed is True
    assert resolution.expected_parent_id is None
    assert resolution.minimum_effective_from is None
    assert resolution.reason_code == "no_history"
    assert resolution.reason_action == "create_initial"


def test_security_rights_reject_wrong_kind_overlap_and_excess_precision(
    session, services
) -> None:
    company, security, _ = _seed_project(session, services)
    with pytest.raises(ValidationError, match="Security"):
        services.market.freeze_security_rights(
            _rights(company.id), expected_parent_id=None
        )
    with pytest.raises(ValidationError, match="10 decimal places"):
        services.market.freeze_security_rights(
            _rights(security.id, votes_per_unit=Decimal("1.00000000001")),
            expected_parent_id=None,
        )
    forged_zero_length = _rights(security.id)
    object.__setattr__(forged_zero_length, "effective_to", EFFECTIVE)
    with pytest.raises(ValidationError, match="effective_to.*later"):
        services.market.freeze_security_rights(
            forged_zero_length,
            expected_parent_id=None,
        )
    first = services.market.freeze_security_rights(
        _rights(security.id, effective_to=MARKET), expected_parent_id=None
    )
    with pytest.raises(ValidationError, match="overlap|advance"):
        services.market.freeze_security_rights(
            _rights(
                security.id,
                effective_from=MARKET - timedelta(days=1),
                raw_hash="e" * 64,
            ),
            expected_parent_id=first.id,
        )


def test_security_rights_open_ended_parent_switches_to_finite_successor_without_revival(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    first = services.market.freeze_security_rights(
        _rights(security.id), expected_parent_id=None
    )

    with pytest.raises(ValidationError, match="advance"):
        services.market.freeze_security_rights(
            _rights(
                security.id,
                effective_from=EFFECTIVE,
                raw_hash="e" * 64,
            ),
            expected_parent_id=first.id,
        )
    with pytest.raises(ValidationError, match="advance"):
        services.market.freeze_security_rights(
            _rights(
                security.id,
                effective_from=EFFECTIVE - timedelta(seconds=1),
                raw_hash="e" * 64,
            ),
            expected_parent_id=first.id,
        )

    successor = services.market.freeze_security_rights(
        _rights(
            security.id,
            effective_from=MARKET,
            effective_to=MARKET + timedelta(days=1),
            raw_hash="e" * 64,
        ),
        expected_parent_id=first.id,
    )
    session.refresh(first)

    assert first.effective_to is None
    assert (
        services.market.effective_security_rights(
            security.id, MARKET - timedelta(seconds=1)
        ).id
        == first.id
    )
    assert (
        services.market.effective_security_rights(security.id, MARKET).id
        == successor.id
    )
    assert (
        services.market.effective_security_rights(
            security.id, MARKET + timedelta(days=1)
        )
        is None
    )


def test_price_may_follow_evidence_cutoff_without_future_evidence(
    session, services
) -> None:
    _, security, _ = _seed_project(session, services)
    cutoff = MARKET - timedelta(days=15)
    basis = services.projects.create_historical_basis(
        ProductHistoricalBasisInput(cutoff, A64, B64, C64)
    )

    price = services.market.freeze_price(_price(security.id, market_at=MARKET))

    assert price.market_at > basis.cutoff
    assert basis.price_as_of is None
    assert basis.boundary_schema_version == "product.historical-basis.v1"


def _product_boundary(
    session,
    services,
    *,
    security_currency: str = "CNY",
    include_out_of_scope_security: bool = False,
    rights_open_ended: bool = False,
):
    if include_out_of_scope_security:
        company = _object(session, "company", f"company:{uuid4()}")
        security = _object(session, "security", f"SEC-{uuid4()}.TEST")
        extra_security = _object(session, "security", f"EXTRA-{uuid4()}.TEST")
        for target in (security, extra_security):
            _relation(session, company.id, target.id)
            _identity(services.projects, target, security_currency)
        project = services.projects.create_project(
            company.id, (security.id, extra_security.id)
        )
    else:
        company, security, project = _seed_project(
            session, services, currency=security_currency
        )
        extra_security = None
    mandate = services.projects.append_product_mandate(
        project_id=project.id,
        value=InvestmentMandateInput(
            mandate_key="ignored",
            horizon_years=5,
            base_currency="CNY",
            required_return=Decimal("0.10"),
            permanent_loss_limit=Decimal("0.30"),
            comparison_set=("CSI300",),
        ),
        benchmark_key=None,
        required_excess_return=None,
        effective_at=EFFECTIVE,
        expires_at=None,
        expected_parent_id=None,
    )
    scope = services.projects.append_scope(
        project.id,
        ResearchScopeInput(company.id, (security.id,), (), (), None, ()),
        None,
    )
    items = ("business",)
    agenda = services.projects.append_agenda(
        project.id,
        ResearchAgendaInput(
            scope.id,
            items,
            AgendaGeneratorInput(
                AgendaGenerationMethod.DETERMINISTIC_TEMPLATE,
                "company-v1",
                "1",
                None,
                None,
                A64,
                agenda_items_hash(items),
            ),
        ),
        None,
    )
    basis = services.projects.create_historical_basis(
        ProductHistoricalBasisInput(MARKET - timedelta(days=1), A64, B64, C64)
    )
    price = services.market.freeze_price(
        _price(security.id, currency=security_currency)
    )
    capital = services.market.freeze_capital_structure(_capital(company.id))
    rights = services.market.freeze_security_rights(
        _rights(
            security.id,
            effective_to=(None if rights_open_ended else MARKET + timedelta(days=1)),
        ),
        expected_parent_id=None,
    )
    boundary = RevisionBoundaryInput(
        basis.id,
        mandate.id,
        scope.id,
        agenda.id,
        (price.id,),
        (),
        capital.id,
        (rights.id,),
        None,
    )
    return locals()


def test_boundary_uses_only_exact_references_and_accepts_complete_coverage(
    session, services
) -> None:
    graph = _product_boundary(session, services)
    first_rights = graph["rights"]
    successor = services.market.freeze_security_rights(
        _rights(
            graph["security"].id,
            effective_from=MARKET + timedelta(days=1),
            raw_hash="e" * 64,
        ),
        expected_parent_id=first_rights.id,
    )

    context = services.market.boundary_context(
        graph["project"].id,
        graph["boundary"],
        as_of=MARKET,
        model_currency="CNY",
    )

    assert context.target_security_ids == (graph["security"].id,)
    assert context.rights_snapshot_ids == (first_rights.id,)
    assert successor.id not in context.rights_snapshot_ids
    validate_market_coverage(graph["boundary"], context)


def test_boundary_coverage_follows_frozen_scope_targets_not_all_project_members(
    session, services
) -> None:
    graph = _product_boundary(session, services, include_out_of_scope_security=True)

    context = services.market.boundary_context(
        graph["project"].id,
        graph["boundary"],
        as_of=MARKET,
        model_currency="CNY",
    )

    assert context.target_security_ids == (graph["security"].id,)
    assert graph["extra_security"].id not in context.target_security_ids


def test_boundary_context_does_not_revive_open_ended_predecessor_after_successor_expires(
    session, services
) -> None:
    graph = _product_boundary(session, services, rights_open_ended=True)
    services.market.freeze_security_rights(
        _rights(
            graph["security"].id,
            effective_from=MARKET + timedelta(days=1),
            effective_to=MARKET + timedelta(days=2),
            raw_hash="e" * 64,
        ),
        expected_parent_id=graph["rights"].id,
    )

    with pytest.raises(ValidationError, match="effective rights"):
        services.market.boundary_context(
            graph["project"].id,
            graph["boundary"],
            as_of=MARKET + timedelta(days=2),
            model_currency="CNY",
        )


def test_market_coverage_rejects_duplicate_target_mapping() -> None:
    security_id = uuid4()
    context = BoundaryContext(
        target_security_ids=(security_id,),
        price_security_ids=(security_id, security_id),
        rights_security_ids=(security_id,),
        requires_fx=False,
    )
    boundary = RevisionBoundaryInput(
        uuid4(), uuid4(), uuid4(), uuid4(), (uuid4(),), (), uuid4(), (uuid4(),), None
    )
    with pytest.raises(ValidationError, match="exactly one price"):
        validate_market_coverage(boundary, context)

    with pytest.raises(ValidationError, match="exactly one.*rights"):
        validate_market_coverage(
            boundary,
            BoundaryContext(
                target_security_ids=(security_id,),
                price_security_ids=(security_id,),
                rights_security_ids=(security_id, security_id),
                requires_fx=False,
            ),
        )


@pytest.mark.parametrize(
    ("required_pairs", "fx_pairs"),
    [
        ((("USD", "CNY"),), (("USD", "CNY"), ("USD", "CNY"))),
        ((("USD", "CNY"), ("USD", "CNY")), (("USD", "CNY"),)),
    ],
)
def test_market_coverage_rejects_duplicate_fx_pairs(required_pairs, fx_pairs) -> None:
    security_id = uuid4()
    fx_id = uuid4()
    boundary = RevisionBoundaryInput(
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        (uuid4(),),
        (fx_id,),
        uuid4(),
        (uuid4(),),
        None,
    )
    context = BoundaryContext(
        target_security_ids=(security_id,),
        price_security_ids=(security_id,),
        rights_security_ids=(security_id,),
        requires_fx=True,
        fx_snapshot_ids=(fx_id,),
        required_fx_pairs=required_pairs,
        fx_pairs=fx_pairs,
    )

    with pytest.raises(ValidationError, match="duplicate FX pair"):
        validate_market_coverage(boundary, context)


def test_boundary_context_rejects_two_snapshots_for_the_same_fx_pair(
    session, services
) -> None:
    graph = _product_boundary(session, services, security_currency="USD")
    first = services.market.freeze_fx(_fx())
    second = services.market.freeze_fx(
        _fx(
            market_at=MARKET + timedelta(minutes=1),
            available_at=MARKET + timedelta(minutes=6),
            source_id="second-feed",
            raw_hash="e" * 64,
        )
    )
    boundary = RevisionBoundaryInput(
        graph["basis"].id,
        graph["mandate"].id,
        graph["scope"].id,
        graph["agenda"].id,
        (graph["price"].id,),
        (first.id, second.id),
        graph["capital"].id,
        (graph["rights"].id,),
        None,
    )

    with pytest.raises(ValidationError, match="duplicate FX pair"):
        services.market.boundary_context(
            graph["project"].id,
            boundary,
            as_of=MARKET,
            model_currency="USD",
        )


def test_boundary_rejects_cross_project_company_security_and_missing_fx(
    session, services
) -> None:
    left = _product_boundary(session, services)
    right = _product_boundary(session, services)

    wrong_price = RevisionBoundaryInput(
        left["basis"].id,
        left["mandate"].id,
        left["scope"].id,
        left["agenda"].id,
        (right["price"].id,),
        (),
        left["capital"].id,
        (left["rights"].id,),
        None,
    )
    with pytest.raises(ValidationError, match="target Security|project"):
        services.market.boundary_context(
            left["project"].id, wrong_price, as_of=MARKET, model_currency="CNY"
        )

    wrong_capital = RevisionBoundaryInput(
        left["basis"].id,
        left["mandate"].id,
        left["scope"].id,
        left["agenda"].id,
        (left["price"].id,),
        (),
        right["capital"].id,
        (left["rights"].id,),
        None,
    )
    with pytest.raises(ValidationError, match="Company"):
        services.market.boundary_context(
            left["project"].id, wrong_capital, as_of=MARKET, model_currency="CNY"
        )

    usd = _product_boundary(session, services, security_currency="USD")
    with pytest.raises(ValidationError, match="FX"):
        services.market.boundary_context(
            usd["project"].id, usd["boundary"], as_of=MARKET, model_currency="USD"
        )


def test_boundary_rejects_component_from_other_project_and_ineffective_rights(
    session, services
) -> None:
    left = _product_boundary(session, services)
    right = _product_boundary(session, services)
    wrong_scope = RevisionBoundaryInput(
        left["basis"].id,
        left["mandate"].id,
        right["scope"].id,
        left["agenda"].id,
        (left["price"].id,),
        (),
        left["capital"].id,
        (left["rights"].id,),
        None,
    )
    with pytest.raises(ValidationError, match="scope.*project"):
        services.market.boundary_context(
            left["project"].id, wrong_scope, as_of=MARKET, model_currency="CNY"
        )

    future_rights = services.market.freeze_security_rights(
        _rights(
            left["security"].id,
            effective_from=MARKET + timedelta(days=1),
            raw_hash="e" * 64,
        ),
        expected_parent_id=left["rights"].id,
    )
    wrong_rights = RevisionBoundaryInput(
        left["basis"].id,
        left["mandate"].id,
        left["scope"].id,
        left["agenda"].id,
        (left["price"].id,),
        (),
        left["capital"].id,
        (future_rights.id,),
        None,
    )
    with pytest.raises(ValidationError, match="effective rights"):
        services.market.boundary_context(
            left["project"].id, wrong_rights, as_of=MARKET, model_currency="CNY"
        )


def test_market_reads_are_id_isolated(session, services) -> None:
    company, security, _ = _seed_project(session, services)
    price = services.market.freeze_price(_price(security.id))
    capital = services.market.freeze_capital_structure(_capital(company.id))
    fx = services.market.freeze_fx(_fx())
    rights = services.market.freeze_security_rights(
        _rights(security.id), expected_parent_id=None
    )

    assert services.market.price(capital.id) is None
    assert services.market.fx(price.id) is None
    assert services.market.capital_structure(fx.id) is None
    assert services.market.security_rights(uuid4()) is None
    assert {price.id, capital.id, fx.id, rights.id} == {
        price.id,
        capital.id,
        fx.id,
        rights.id,
    }
