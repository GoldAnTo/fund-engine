"""Resolve authenticated company-research market inputs from immutable rows only."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    SecurityRightsInput,
)
from app.underwriting.fixtures.alphabet_golden_case import AlphabetMarketInputBundle
from app.underwriting.domain.company_research import (
    CapitalStructureReference,
    MarketBridgeArtifact,
    ReverseDcfRequest,
    SecurityValuationReference,
    SourceLineageReference,
)
from app.underwriting.persistence.models import UnderwritingResearchObject
from app.underwriting.persistence.product_models import (
    UnderwritingCapitalStructureSnapshot,
    UnderwritingFXSnapshot,
    UnderwritingPriceSnapshot,
    UnderwritingSecurityRightsVersion,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_model_builder import (
    FrozenMarketContext,
    FrozenMarketEquityComponent,
    FrozenMarketSnapshotBinding,
    FrozenMarketSnapshotRole,
)
from app.underwriting.services.market_snapshots import (
    MarketSnapshotService,
    capital_structure_snapshot_hash,
    fx_snapshot_hash,
    price_snapshot_hash,
    security_rights_hash,
)
from app.underwriting.services.workspace_draft import (
    WorkspaceDraftPatch,
    WorkspaceDraftService,
)


_ALPHABET_COMPANY_KEY = "US:ALPHABET:COMPANY"
_ALPHABET_SECURITY_KEYS = ("NASDAQ:GOOG", "NASDAQ:GOOGL")
_MODEL_CURRENCY = "CNY"
_PRICE_TYPE = "official_close"
_ADJUSTMENT_BASIS = "unadjusted"
_ALPHABET_CUTOFF = datetime(2026, 8, 25, 23, 59, 59, tzinfo=UTC)


def _utc(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _latest_exact(
    rows: Iterable[object],
    *,
    time_field: str,
    label: str,
) -> object:
    values = tuple(rows)
    if not values:
        raise ValidationError(f"market inputs are incomplete: missing {label}")
    latest_at = max(_stored_utc(getattr(row, time_field)) for row in values)
    latest = tuple(
        row
        for row in values
        if _stored_utc(getattr(row, time_field)) == latest_at
    )
    if len(latest) != 1:
        raise ValidationError(f"market inputs contain duplicate {label}")
    return latest[0]


def _lineage(
    *,
    fact_key: str,
    source_id: str,
    source_locator: str,
    raw_hash: str,
) -> SourceLineageReference:
    return SourceLineageReference(
        fact_key=fact_key,
        source_role="frozen_market_snapshot",
        source_url=source_id,
        source_locator=source_locator,
        raw_hash=raw_hash,
    )


class CompanyResearchMarketInputs:
    """Read one exact Alphabet market context without fetching or fabricating data."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = ProductRepository(session)

    def prepare(
        self,
        *,
        project_id: UUID,
        cutoff_at: datetime,
        market_inputs: AlphabetMarketInputBundle,
    ) -> FrozenMarketContext:
        """Install one authenticated bundle atomically, then resolve exact refs."""
        cutoff = _utc(cutoff_at, "cutoff_at")
        if type(market_inputs) is not AlphabetMarketInputBundle:
            raise ValidationError("market_inputs must be an authenticated Alphabet bundle")
        if (
            market_inputs.company_external_key != _ALPHABET_COMPANY_KEY
            or market_inputs.security_external_keys != _ALPHABET_SECURITY_KEYS
        ):
            raise ValidationError("market input bundle identity does not match Alphabet")
        project_record = self._repository.project(project_id)
        if project_record is None:
            raise ValidationError("company research project not found")
        project, security_ids = project_record
        objects = tuple(
            self._session.get(UnderwritingResearchObject, item) for item in security_ids
        )
        by_key = {
            item.external_key: item for item in objects if item is not None
        }
        company = self._session.get(UnderwritingResearchObject, project.primary_company_id)
        if (
            company is None
            or company.external_key != market_inputs.company_external_key
            or tuple(sorted(by_key)) != market_inputs.security_external_keys
        ):
            raise ValidationError("market input bundle does not match the project identity")

        market = MarketSnapshotService(self._session, now=lambda: cutoff)
        drafts = WorkspaceDraftService(self._session, now=lambda: cutoff)
        with self._session.begin_nested():
            price_rows = tuple(
                market.freeze_price(
                    PriceSnapshotInput(
                        security_identity_id=by_key[item.security_external_key].id,
                        price=item.value,
                        currency=item.currency,
                        price_type=item.price_type,
                        adjustment_basis=item.adjustment_basis,
                        market_at=item.market_at,
                        available_at=item.available_at,
                        source_id=item.provenance.source_url,
                        raw_hash=item.provenance.raw_hash,
                    )
                )
                for item in market_inputs.prices
            )
            fx_item = market_inputs.fx
            fx_row = market.freeze_fx(
                FXSnapshotInput(
                    base_currency=fx_item.base_currency,
                    quote_currency=fx_item.quote_currency,
                    rate=fx_item.rate,
                    quote_direction=FxQuoteDirection.QUOTE_PER_BASE,
                    market_at=fx_item.market_at,
                    available_at=fx_item.available_at,
                    source_id=fx_item.provenance.source_url,
                    raw_hash=fx_item.provenance.raw_hash,
                )
            )
            capital_item = market_inputs.capital_structure
            if (
                capital_item.capital_bridge_policy_version
                != "alphabet-capital-bridge.v1"
                or capital_item.policy_excluded_adjustments
                != ("pension_liabilities",)
                or capital_item.value("pension_liabilities") != Decimal("0")
            ):
                raise ValidationError("capital bridge policy is unsupported or not closed")
            capital_row = market.freeze_capital_structure(
                CapitalStructureSnapshotInput(
                    company_id=company.id,
                    currency=capital_item.currency,
                    cash=capital_item.value("cash"),
                    debt=capital_item.value("debt"),
                    minority_interest=capital_item.value("minority_interest"),
                    investments=capital_item.value("investments"),
                    pension_liabilities=capital_item.value("pension_liabilities"),
                    other_adjustments=capital_item.value("other_adjustments"),
                    basic_shares=capital_item.value("basic_shares"),
                    diluted_shares=capital_item.value("diluted_shares"),
                    potential_dilution_descriptors=capital_item.potential_dilution_descriptors,
                    report_period_start=capital_item.report_period_start,
                    report_period_end=capital_item.report_period_end,
                    market_at=capital_item.market_at,
                    available_at=capital_item.available_at,
                    source_id=capital_item.provenance.source_url,
                    raw_hash=capital_item.provenance.raw_hash,
                )
            )
            rights_rows = tuple(
                self._freeze_rights(market, by_key[item.security_external_key].id, item)
                for item in market_inputs.security_rights
            )
            draft = drafts.read(project_id)
            if draft is None:
                raise ValidationError("workspace draft does not exist for project")
            price_ids = tuple(sorted((row.id for row in price_rows), key=str))
            rights_ids = tuple(sorted((row.id for row in rights_rows), key=str))
            if (
                draft.content.price_snapshot_ids != price_ids
                or draft.content.fx_snapshot_ids != (fx_row.id,)
                or draft.content.capital_structure_snapshot_id != capital_row.id
                or draft.content.security_rights_ids != rights_ids
            ):
                drafts.save(
                    project_id,
                    expected_lock_version=draft.lock_version,
                    patch=WorkspaceDraftPatch(
                        price_snapshot_ids=price_ids,
                        fx_snapshot_ids=(fx_row.id,),
                        capital_structure_snapshot_id=capital_row.id,
                        security_rights_ids=rights_ids,
                    ),
                )
            return self.resolve(project_id=project_id, cutoff_at=cutoff)

    @staticmethod
    def _freeze_rights(market: MarketSnapshotService, security_id: UUID, item):
        value = SecurityRightsInput(
            security_identity_id=security_id,
            economic_units=item.value("economic_units"),
            votes_per_unit=item.value("votes_per_unit"),
            conversion_ratio=item.value("conversion_ratio"),
            adr_ratio=item.value("adr_ratio"),
            dividend_rights_per_unit=item.value("dividend_rights_per_unit"),
            effective_from=item.effective_from,
            effective_to=item.effective_to,
            source_id=item.provenance.source_url,
            raw_hash=item.provenance.raw_hash,
        )
        expected_hash = security_rights_hash(value)
        head = market.security_rights_head(security_id)
        if head is not None and head.content_hash == expected_hash:
            return head
        if head is not None and _stored_utc(head.effective_from) == item.effective_from:
            raise ConflictError(
                "different security-rights hash already exists for the same identity/time"
            )
        return market.freeze_security_rights(
            value,
            expected_parent_id=head.id if head is not None else None,
        )

    def resolve(self, *, project_id: UUID, cutoff_at: datetime) -> FrozenMarketContext:
        if type(project_id) is not UUID:
            raise ValidationError("project_id must be a UUID")
        cutoff = _utc(cutoff_at, "cutoff_at")
        if cutoff != _ALPHABET_CUTOFF:
            raise ValidationError("market inputs require the exact Alphabet cutoff")
        project_record = self._repository.project(project_id)
        if project_record is None:
            raise ValidationError("company research project not found")
        project, security_ids = project_record
        company = self._session.get(UnderwritingResearchObject, project.primary_company_id)
        securities = tuple(
            self._session.get(UnderwritingResearchObject, security_id)
            for security_id in security_ids
        )
        if (
            company is None
            or company.kind != "company"
            or company.external_key != _ALPHABET_COMPANY_KEY
            or any(item is None or item.kind != "security" for item in securities)
        ):
            raise ValidationError("market inputs do not match the project Company")
        security_by_key = {
            item.external_key: item
            for item in securities
            if item is not None
        }
        if tuple(sorted(security_by_key)) != _ALPHABET_SECURITY_KEYS:
            raise ValidationError("market inputs do not match the project Securities")

        prices = tuple(
            self._price(
                security_id=security_by_key[key].id,
                security_key=key,
                cutoff=cutoff,
            )
            for key in _ALPHABET_SECURITY_KEYS
        )
        fx = self._fx(cutoff)
        capital = self._capital(project.primary_company_id, cutoff)
        rights = tuple(
            self._rights(
                security_id=security_by_key[key].id,
                security_key=key,
                cutoff=cutoff,
            )
            for key in _ALPHABET_SECURITY_KEYS
        )
        self._validate_hashes(prices=prices, fx=fx, capital=capital, rights=rights)
        return self._context(
            prices=prices,
            fx=fx,
            capital=capital,
            rights=rights,
        )

    def _price(self, *, security_id: UUID, security_key: str, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingPriceSnapshot)
            .where(
                UnderwritingPriceSnapshot.security_identity_id == security_id,
                UnderwritingPriceSnapshot.price_type == _PRICE_TYPE,
                UnderwritingPriceSnapshot.adjustment_basis == _ADJUSTMENT_BASIS,
                UnderwritingPriceSnapshot.market_at <= cutoff,
                UnderwritingPriceSnapshot.available_at <= cutoff,
                UnderwritingPriceSnapshot.created_at <= cutoff,
            )
            .order_by(
                UnderwritingPriceSnapshot.market_at.desc(),
                UnderwritingPriceSnapshot.id,
            )
        )
        row = _latest_exact(rows, time_field="market_at", label=f"price for {security_key}")
        if row.security_identity_id != security_id:
            raise ValidationError("market inputs contain a cross-security price")
        if row.currency != "USD":
            raise ValidationError(f"market inputs use the wrong currency for {security_key}")
        return row

    def _fx(self, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingFXSnapshot)
            .where(
                UnderwritingFXSnapshot.base_currency == "USD",
                UnderwritingFXSnapshot.quote_currency == _MODEL_CURRENCY,
                UnderwritingFXSnapshot.quote_direction == "quote_per_base",
                UnderwritingFXSnapshot.market_at <= cutoff,
                UnderwritingFXSnapshot.available_at <= cutoff,
                UnderwritingFXSnapshot.created_at <= cutoff,
            )
            .order_by(
                UnderwritingFXSnapshot.market_at.desc(),
                UnderwritingFXSnapshot.id,
            )
        )
        return _latest_exact(rows, time_field="market_at", label="USD/CNY FX")

    def _capital(self, company_id: UUID, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingCapitalStructureSnapshot)
            .where(
                UnderwritingCapitalStructureSnapshot.company_id == company_id,
                UnderwritingCapitalStructureSnapshot.market_at <= cutoff,
                UnderwritingCapitalStructureSnapshot.available_at <= cutoff,
                UnderwritingCapitalStructureSnapshot.created_at <= cutoff,
            )
            .order_by(
                UnderwritingCapitalStructureSnapshot.market_at.desc(),
                UnderwritingCapitalStructureSnapshot.id,
            )
        )
        row = _latest_exact(rows, time_field="market_at", label="capital structure")
        if row.company_id != company_id:
            raise ValidationError("market inputs contain a cross-company capital structure")
        if row.currency != "USD":
            raise ValidationError("market inputs use the wrong capital-structure currency")
        return row

    def _rights(self, *, security_id: UUID, security_key: str, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingSecurityRightsVersion)
            .where(
                UnderwritingSecurityRightsVersion.security_identity_id == security_id,
                UnderwritingSecurityRightsVersion.effective_from <= cutoff,
                or_(
                    UnderwritingSecurityRightsVersion.effective_to.is_(None),
                    UnderwritingSecurityRightsVersion.effective_to > cutoff,
                ),
                UnderwritingSecurityRightsVersion.created_at <= cutoff,
            )
            .order_by(
                UnderwritingSecurityRightsVersion.effective_from.desc(),
                UnderwritingSecurityRightsVersion.version.desc(),
                UnderwritingSecurityRightsVersion.id,
            )
        )
        row = _latest_exact(
            rows,
            time_field="effective_from",
            label=f"security rights for {security_key}",
        )
        if row.security_identity_id != security_id:
            raise ValidationError("market inputs contain cross-security rights")
        return row

    @staticmethod
    def _validate_hashes(*, prices, fx, capital, rights) -> None:
        checks = (
            *((row, price_snapshot_hash) for row in prices),
            (fx, fx_snapshot_hash),
            (capital, capital_structure_snapshot_hash),
            *((row, security_rights_hash) for row in rights),
        )
        if any(row.content_hash != hasher(row) for row, hasher in checks):
            raise ValidationError("market inputs contain a hash-invalid snapshot")

    @staticmethod
    def _context(*, prices, fx, capital, rights) -> FrozenMarketContext:
        # Resolve stable external keys once more for the bridge without trusting row order.
        # The caller has already proved there are exactly the two Alphabet securities.
        keys = _ALPHABET_SECURITY_KEYS
        price_by_key = dict(zip(keys, prices, strict=True))
        rights_by_key = dict(zip(keys, rights, strict=True))

        capital_ref = _lineage(
            fact_key="capital_structure_usd",
            source_id=capital.source_id,
            source_locator=(
                f"report_period={_stored_utc(capital.report_period_start).isoformat()}.."
                f"{_stored_utc(capital.report_period_end).isoformat()};"
                f"market_at={_stored_utc(capital.market_at).isoformat()};"
                f"available_at={_stored_utc(capital.available_at).isoformat()}"
            ),
            raw_hash=capital.raw_hash,
        )
        policy_ref = _lineage(
            fact_key="capital_bridge_policy",
            source_id="urn:company-research:capital-bridge-policy",
            source_locator=(
                "alphabet-capital-bridge.v1: investments include marketable and "
                "non-marketable securities; debt uses carrying value including "
                "current debt; pension_liabilities is policy-excluded"
            ),
            raw_hash=capital.content_hash,
        )
        fx_ref = _lineage(
            fact_key="usd_cny_fx",
            source_id=fx.source_id,
            source_locator=(
                f"USD/CNY;market_at={_stored_utc(fx.market_at).isoformat()};"
                f"available_at={_stored_utc(fx.available_at).isoformat()}"
            ),
            raw_hash=fx.raw_hash,
        )
        securities = []
        price_refs = {}
        rights_refs = {}
        for key in keys:
            suffix = key.lower().replace(":", "_")
            price = price_by_key[key]
            right = rights_by_key[key]
            price_ref = _lineage(
                fact_key=f"market_price_usd_{suffix}",
                source_id=price.source_id,
                source_locator=(
                    f"{price.price_type}/{price.adjustment_basis};"
                    f"market_at={_stored_utc(price.market_at).isoformat()};"
                    f"available_at={_stored_utc(price.available_at).isoformat()}"
                ),
                raw_hash=price.raw_hash,
            )
            rights_ref = _lineage(
                fact_key=f"security_rights_{suffix}",
                source_id=right.source_id,
                source_locator=(
                    f"effective_from={_stored_utc(right.effective_from).isoformat()};"
                    f"effective_to={_stored_utc(right.effective_to).isoformat() if right.effective_to else 'open'}"
                ),
                raw_hash=right.raw_hash,
            )
            price_refs[key] = price_ref
            rights_refs[key] = rights_ref
            securities.append(
                SecurityValuationReference(
                    security_external_key=key,
                    listed_class_economic_units=right.economic_units,
                    conversion_ratio=right.conversion_ratio,
                    adr_ratio=right.adr_ratio,
                    dividend_rights_per_unit=right.dividend_rights_per_unit,
                    market_price_usd=price.price,
                    usd_cny_rate=fx.rate,
                    rights_ref=rights_ref,
                    price_ref=price_ref,
                )
            )
        bridge = MarketBridgeArtifact(
            capital_structure=CapitalStructureReference(
                cash=capital.cash,
                debt=capital.debt,
                minority_interest=capital.minority_interest,
                investments=capital.investments,
                pension_liabilities=capital.pension_liabilities,
                other_adjustments=capital.other_adjustments,
                basic_shares=capital.basic_shares,
                diluted_shares=capital.diluted_shares,
                source_ref=capital_ref,
                capital_bridge_policy_version="alphabet-capital-bridge.v1",
                policy_ref=policy_ref,
                policy_excluded_adjustments=("pension_liabilities",),
            ),
            securities=tuple(securities),
            usd_cny_rate=fx.rate,
            fx_ref=fx_ref,
        )
        with localcontext() as context:
            context.prec = 60
            listed = {item.security_external_key: item for item in securities}
            class_b_units = +(
                capital.basic_shares
                - listed["NASDAQ:GOOGL"].listed_class_economic_units
                - listed["NASDAQ:GOOG"].listed_class_economic_units
            )
            if class_b_units < Decimal("0"):
                raise ValidationError(
                    "listed Class A/C units exceed company basic shares"
                )
            component_specs = (
                (
                    "class_a",
                    listed["NASDAQ:GOOGL"].listed_class_economic_units,
                    "NASDAQ:GOOGL",
                    listed["NASDAQ:GOOGL"].rights_ref,
                ),
                (
                    "class_b",
                    class_b_units,
                    "NASDAQ:GOOGL",
                    capital_ref,
                ),
                (
                    "class_c",
                    listed["NASDAQ:GOOG"].listed_class_economic_units,
                    "NASDAQ:GOOG",
                    listed["NASDAQ:GOOG"].rights_ref,
                ),
            )
            market_equity = sum(
                (
                    units * price_by_key[proxy].price
                    for _key, units, proxy, _source in component_specs
                ),
                start=Decimal("0"),
            )
            target_enterprise_value = +(
                market_equity
                + capital.debt
                + capital.minority_interest
                + capital.pension_liabilities
                + capital.other_adjustments
                - capital.cash
                - capital.investments
            )
        if target_enterprise_value <= Decimal("0"):
            raise ValidationError("market inputs imply a non-positive enterprise value")

        role_by_id = {
            **{
                row.id: (FrozenMarketSnapshotRole.PRICE, key, price_refs[key])
                for key, row in price_by_key.items()
            },
            fx.id: (FrozenMarketSnapshotRole.FX, None, fx_ref),
            capital.id: (
                FrozenMarketSnapshotRole.CAPITAL_STRUCTURE,
                None,
                capital_ref,
            ),
            **{
                row.id: (
                    FrozenMarketSnapshotRole.SECURITY_RIGHTS,
                    key,
                    rights_refs[key],
                )
                for key, row in rights_by_key.items()
            },
        }
        price_ids = tuple(sorted((row.id for row in prices), key=str))
        fx_ids = (fx.id,)
        rights_ids = tuple(sorted((row.id for row in rights), key=str))
        snapshot_ids = tuple(
            sorted((*price_ids, *fx_ids, capital.id, *rights_ids), key=str)
        )
        bindings = tuple(
            FrozenMarketSnapshotBinding(snapshot_id, *role_by_id[snapshot_id])
            for snapshot_id in snapshot_ids
        )
        market_at = max(
            _stored_utc(row.market_at) for row in (*prices, fx, capital)
        )
        equity_components = tuple(
            FrozenMarketEquityComponent(
                component_key=component_key,
                economic_units=units,
                price_proxy_security_external_key=proxy,
                unit_source_ref=unit_source_ref,
                price_snapshot_id=price_by_key[proxy].id,
                price_ref=price_refs[proxy],
            )
            for component_key, units, proxy, unit_source_ref in component_specs
        )
        return FrozenMarketContext(
            price_snapshot_ids=price_ids,
            fx_snapshot_ids=fx_ids,
            capital_structure_snapshot_id=capital.id,
            security_rights_ids=rights_ids,
            snapshot_ids=snapshot_ids,
            market_at=market_at,
            market_bridge=bridge,
            snapshot_bindings=bindings,
            equity_components=equity_components,
            reverse_dcf_request=ReverseDcfRequest(
                driver_key="fcff_multiplier",
                target_enterprise_value=target_enterprise_value,
                lower_bound=Decimal("0.01"),
                upper_bound=Decimal("10"),
                max_iterations=256,
            ),
        )
