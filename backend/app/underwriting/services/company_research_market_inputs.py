"""Resolve authenticated company-research market inputs from immutable rows only."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    SecurityRightsInput,
)
from app.underwriting.fixtures.alphabet_golden_case import (
    AlphabetMarketInputBundle,
    CapturedProvenance,
)
from app.underwriting.hashing import canonical_hash
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
    UnderwritingMarketCaptureEnvelope,
    UnderwritingPriceSnapshot,
    UnderwritingSecurityRightsVersion,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.company_research_model_builder import (
    FrozenMarketContext,
    FrozenMarketEquityComponent,
    FrozenRawComponentReference,
    FrozenMarketSnapshotBinding,
    FrozenMarketSnapshotRole,
)
from app.underwriting.services.market_snapshots import (
    MarketSnapshotService,
    capital_structure_snapshot_hash,
    fx_snapshot_hash,
    market_capture_envelope_hash,
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
    if any(getattr(row, "legacy_business_conflict", False) for row in values):
        raise ValidationError(
            f"market inputs contain a grandfathered legacy business-time conflict for {label}"
        )
    latest_at = max(_stored_utc(getattr(row, time_field)) for row in values)
    latest = tuple(
        row for row in values if _stored_utc(getattr(row, time_field)) == latest_at
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

    def __init__(
        self,
        session: Session,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._repository = ProductRepository(session)
        self._now = now or (lambda: datetime.now(UTC))

    def _clock(self) -> datetime:
        return _utc(self._now(), "now")

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
            raise ValidationError(
                "market_inputs must be an authenticated Alphabet bundle"
            )
        if (
            market_inputs.company_external_key != _ALPHABET_COMPANY_KEY
            or market_inputs.security_external_keys != _ALPHABET_SECURITY_KEYS
        ):
            raise ValidationError(
                "market input bundle identity does not match Alphabet"
            )
        project_record = self._repository.project(project_id)
        if project_record is None:
            raise ValidationError("company research project not found")
        project, security_ids = project_record
        objects = tuple(
            self._session.get(UnderwritingResearchObject, item) for item in security_ids
        )
        by_key = {item.external_key: item for item in objects if item is not None}
        company = self._session.get(
            UnderwritingResearchObject, project.primary_company_id
        )
        if (
            company is None
            or company.external_key != market_inputs.company_external_key
            or tuple(sorted(by_key)) != market_inputs.security_external_keys
        ):
            raise ValidationError(
                "market input bundle does not match the project identity"
            )

        market = MarketSnapshotService(self._session, now=self._clock)
        drafts = WorkspaceDraftService(self._session, now=self._clock)
        with self._session.begin_nested():
            price_values = tuple(
                (
                    item,
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
                    ),
                )
                for item in market_inputs.prices
            )
            fx_item = market_inputs.fx
            fx_value = FXSnapshotInput(
                base_currency=fx_item.base_currency,
                quote_currency=fx_item.quote_currency,
                rate=fx_item.rate,
                quote_direction=FxQuoteDirection.QUOTE_PER_BASE,
                market_at=fx_item.market_at,
                available_at=fx_item.available_at,
                source_id=fx_item.provenance.source_url,
                raw_hash=fx_item.provenance.raw_hash,
            )
            capital_item = market_inputs.capital_structure
            capital_value = CapitalStructureSnapshotInput(
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
            rights_values = tuple(
                (
                    item,
                    SecurityRightsInput(
                        security_identity_id=by_key[item.security_external_key].id,
                        economic_units=item.value("economic_units"),
                        votes_per_unit=item.value("votes_per_unit"),
                        conversion_ratio=item.value("conversion_ratio"),
                        adr_ratio=item.value("adr_ratio"),
                        dividend_rights_per_unit=item.value("dividend_rights_per_unit"),
                        effective_from=item.effective_from,
                        effective_to=item.effective_to,
                        source_id=item.provenance.source_url,
                        raw_hash=item.provenance.raw_hash,
                    ),
                )
                for item in market_inputs.security_rights
            )
            self._precheck_business_conflicts(
                prices=tuple(value for _, value in price_values),
                fx=fx_value,
                capital=capital_value,
                rights=tuple(value for _, value in rights_values),
            )
            class_b = market_inputs.class_b_rights
            listed_units = sum(
                (value.economic_units for _item, value in rights_values),
                start=Decimal("0"),
            )
            if (
                class_b.component_key != "class_b"
                or class_b.economic_units != capital_value.basic_shares - listed_units
                or class_b.economic_units < Decimal("0")
                or class_b.votes_per_unit != Decimal("10")
                or class_b.conversion_to_security_external_key != "NASDAQ:GOOGL"
                or class_b.conversion_ratio != Decimal("1")
                or class_b.dividend_rights_per_unit != Decimal("1")
                or class_b.economic_rights_per_unit != Decimal("1")
                or class_b.price_proxy_security_external_key != "NASDAQ:GOOGL"
                or class_b.price_proxy_policy_version
                != "alphabet_class_b_googl_proxy.v1"
            ):
                raise ValidationError(
                    "Class B must remain a separate typed legal-rights component"
                )
            price_rows = tuple(
                self._converge_snapshot_insert(
                    lambda value=value: market.freeze_price(value),
                    UnderwritingPriceSnapshot,
                    (
                        UnderwritingPriceSnapshot.security_identity_id
                        == value.security_identity_id,
                        UnderwritingPriceSnapshot.price_type == value.price_type,
                        UnderwritingPriceSnapshot.adjustment_basis
                        == value.adjustment_basis,
                        UnderwritingPriceSnapshot.market_at == value.market_at,
                    ),
                    price_snapshot_hash(value),
                )
                for _item, value in price_values
            )
            fx_row = self._converge_snapshot_insert(
                lambda: market.freeze_fx(fx_value),
                UnderwritingFXSnapshot,
                (
                    UnderwritingFXSnapshot.base_currency == fx_value.base_currency,
                    UnderwritingFXSnapshot.quote_currency == fx_value.quote_currency,
                    UnderwritingFXSnapshot.quote_direction
                    == fx_value.quote_direction.value,
                    UnderwritingFXSnapshot.market_at == fx_value.market_at,
                ),
                fx_snapshot_hash(fx_value),
            )
            if (
                capital_item.capital_bridge_policy_version
                != "alphabet-capital-bridge.v1"
                or capital_item.policy_excluded_adjustments != ("pension_liabilities",)
                or capital_item.value("pension_liabilities") != Decimal("0")
            ):
                raise ValidationError(
                    "capital bridge policy is unsupported or not closed"
                )
            capital_row = self._converge_snapshot_insert(
                lambda: market.freeze_capital_structure(capital_value),
                UnderwritingCapitalStructureSnapshot,
                (
                    UnderwritingCapitalStructureSnapshot.company_id
                    == capital_value.company_id,
                    UnderwritingCapitalStructureSnapshot.report_period_start
                    == capital_value.report_period_start,
                    UnderwritingCapitalStructureSnapshot.report_period_end
                    == capital_value.report_period_end,
                    UnderwritingCapitalStructureSnapshot.market_at
                    == capital_value.market_at,
                ),
                capital_structure_snapshot_hash(capital_value),
            )
            rights_rows = tuple(
                self._converge_snapshot_insert(
                    lambda value=value: self._freeze_rights(market, value),
                    UnderwritingSecurityRightsVersion,
                    (
                        UnderwritingSecurityRightsVersion.security_identity_id
                        == value.security_identity_id,
                        UnderwritingSecurityRightsVersion.effective_from
                        == value.effective_from,
                    ),
                    security_rights_hash(value),
                )
                for _item, value in rights_values
            )
            for (item, _value), row in zip(price_values, price_rows, strict=True):
                self._persist_capture(
                    "price", row.id, "primary", item.provenance, item.available_at
                )
            self._persist_capture(
                "fx", fx_row.id, "primary", fx_item.provenance, fx_item.available_at
            )
            self._persist_capture(
                "capital_structure",
                capital_row.id,
                "primary",
                capital_item.provenance,
                capital_item.available_at,
            )
            for (item, _value), row in zip(rights_values, rights_rows, strict=True):
                self._persist_capture(
                    "security_rights",
                    row.id,
                    "primary",
                    item.provenance,
                    item.available_at,
                )
            self._persist_capture(
                "capital_structure",
                capital_row.id,
                "class_b_legal_rights",
                market_inputs.class_b_rights.legal_provenance,
                market_inputs.class_b_rights.available_at,
            )
            self._persist_capture(
                "capital_structure",
                capital_row.id,
                "class_b_units",
                market_inputs.class_b_rights.unit_provenance,
                market_inputs.class_b_rights.available_at,
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
    def _freeze_rights(market: MarketSnapshotService, value: SecurityRightsInput):
        expected_hash = security_rights_hash(value)
        head = market.security_rights_head(value.security_identity_id)
        if head is not None and head.content_hash == expected_hash:
            return head
        if (
            head is not None
            and _stored_utc(head.effective_from) == value.effective_from
        ):
            raise ConflictError(
                "different security-rights hash already exists for the same identity/time"
            )
        return market.freeze_security_rights(
            value,
            expected_parent_id=head.id if head is not None else None,
        )

    def _precheck_business_conflicts(
        self,
        *,
        prices: tuple[PriceSnapshotInput, ...],
        fx: FXSnapshotInput,
        capital: CapitalStructureSnapshotInput,
        rights: tuple[SecurityRightsInput, ...],
    ) -> None:
        checks: tuple[tuple[type, tuple[object, ...], str], ...] = (
            *(
                (
                    UnderwritingPriceSnapshot,
                    (
                        UnderwritingPriceSnapshot.security_identity_id
                        == value.security_identity_id,
                        UnderwritingPriceSnapshot.price_type == value.price_type,
                        UnderwritingPriceSnapshot.adjustment_basis
                        == value.adjustment_basis,
                        UnderwritingPriceSnapshot.market_at == value.market_at,
                    ),
                    price_snapshot_hash(value),
                )
                for value in prices
            ),
            (
                UnderwritingFXSnapshot,
                (
                    UnderwritingFXSnapshot.base_currency == fx.base_currency,
                    UnderwritingFXSnapshot.quote_currency == fx.quote_currency,
                    UnderwritingFXSnapshot.quote_direction == fx.quote_direction.value,
                    UnderwritingFXSnapshot.market_at == fx.market_at,
                ),
                fx_snapshot_hash(fx),
            ),
            (
                UnderwritingCapitalStructureSnapshot,
                (
                    UnderwritingCapitalStructureSnapshot.company_id
                    == capital.company_id,
                    UnderwritingCapitalStructureSnapshot.report_period_start
                    == capital.report_period_start,
                    UnderwritingCapitalStructureSnapshot.report_period_end
                    == capital.report_period_end,
                    UnderwritingCapitalStructureSnapshot.market_at == capital.market_at,
                ),
                capital_structure_snapshot_hash(capital),
            ),
            *(
                (
                    UnderwritingSecurityRightsVersion,
                    (
                        UnderwritingSecurityRightsVersion.security_identity_id
                        == value.security_identity_id,
                        UnderwritingSecurityRightsVersion.effective_from
                        == value.effective_from,
                    ),
                    security_rights_hash(value),
                )
                for value in rights
            ),
        )
        for model, predicates, expected_hash in checks:
            rows = tuple(
                self._session.scalars(
                    select(model).where(*predicates).with_for_update()
                )
            )
            if rows and (len(rows) != 1 or rows[0].content_hash != expected_hash):
                raise ConflictError(
                    "different content already exists for the same business identity/time"
                )

    def _converge_snapshot_insert(
        self,
        insert: Callable[[], object],
        model: type,
        predicates: tuple[object, ...],
        expected_hash: str,
    ) -> object:
        try:
            with self._session.begin_nested():
                return insert()
        except IntegrityError:
            row = self._session.scalar(select(model).where(*predicates))
            if row is None or row.content_hash != expected_hash:
                raise ConflictError(
                    "different content already exists for the same business identity/time"
                ) from None
            return row

    def _persist_capture(
        self,
        snapshot_kind: str,
        snapshot_id: UUID,
        provenance_role: str,
        provenance: CapturedProvenance,
        authenticated_available_at: datetime,
    ) -> UnderwritingMarketCaptureEnvelope:
        components = [component.payload() for component in provenance.raw_components]
        payload = {
            "schema_version": "product.market-capture-envelope.v1",
            "snapshot_kind": snapshot_kind,
            "snapshot_id": str(snapshot_id),
            "provenance_role": provenance_role,
            "source_url": provenance.source_url,
            "source_locator": provenance.source_locator,
            "provider_policy_version": provenance.provider_policy_version,
            "raw_hash": provenance.raw_hash,
            "raw_components": components,
            "authenticated_available_at": _utc(
                authenticated_available_at, "authenticated_available_at"
            ).isoformat(),
        }
        content_hash = canonical_hash(payload)
        existing = self._session.scalar(
            select(UnderwritingMarketCaptureEnvelope)
            .where(
                UnderwritingMarketCaptureEnvelope.snapshot_kind == snapshot_kind,
                UnderwritingMarketCaptureEnvelope.snapshot_id == snapshot_id,
                UnderwritingMarketCaptureEnvelope.provenance_role == provenance_role,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.content_hash != content_hash:
                raise ConflictError(
                    "different capture provenance exists for the same snapshot role"
                )
            return existing
        row = UnderwritingMarketCaptureEnvelope(
            snapshot_kind=snapshot_kind,
            snapshot_id=snapshot_id,
            provenance_role=provenance_role,
            source_url=provenance.source_url,
            source_locator=provenance.source_locator,
            provider_policy_version=provenance.provider_policy_version,
            raw_hash=provenance.raw_hash,
            raw_components=components,
            content_hash=content_hash,
            authenticated_available_at=authenticated_available_at,
            acquired_at=self._clock(),
        )
        try:
            with self._session.begin_nested():
                self._session.add(row)
                self._session.flush()
            return row
        except IntegrityError:
            existing = self._session.scalar(
                select(UnderwritingMarketCaptureEnvelope).where(
                    UnderwritingMarketCaptureEnvelope.snapshot_kind == snapshot_kind,
                    UnderwritingMarketCaptureEnvelope.snapshot_id == snapshot_id,
                    UnderwritingMarketCaptureEnvelope.provenance_role
                    == provenance_role,
                )
            )
            if existing is None or existing.content_hash != content_hash:
                raise ConflictError(
                    "different capture provenance exists for the same snapshot role"
                ) from None
            return existing

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
        company = self._session.get(
            UnderwritingResearchObject, project.primary_company_id
        )
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
            item.external_key: item for item in securities if item is not None
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
        captures = {
            **{
                ("price", row.id, "primary"): self._capture("price", row.id)
                for row in prices
            },
            ("fx", fx.id, "primary"): self._capture("fx", fx.id),
            ("capital_structure", capital.id, "primary"): self._capture(
                "capital_structure", capital.id
            ),
            **{
                ("security_rights", row.id, "primary"): self._capture(
                    "security_rights", row.id
                )
                for row in rights
            },
            (
                "capital_structure",
                capital.id,
                "class_b_legal_rights",
            ): self._capture("capital_structure", capital.id, "class_b_legal_rights"),
            ("capital_structure", capital.id, "class_b_units"): self._capture(
                "capital_structure", capital.id, "class_b_units"
            ),
        }
        return self._context(
            prices=prices,
            fx=fx,
            capital=capital,
            rights=rights,
            captures=captures,
        )

    def _capture(
        self,
        snapshot_kind: str,
        snapshot_id: UUID,
        provenance_role: str = "primary",
    ) -> UnderwritingMarketCaptureEnvelope:
        rows = tuple(
            self._session.scalars(
                select(UnderwritingMarketCaptureEnvelope).where(
                    UnderwritingMarketCaptureEnvelope.snapshot_kind == snapshot_kind,
                    UnderwritingMarketCaptureEnvelope.snapshot_id == snapshot_id,
                    UnderwritingMarketCaptureEnvelope.provenance_role
                    == provenance_role,
                )
            )
        )
        if len(rows) != 1:
            raise ValidationError(
                "market inputs require one exact persisted capture provenance envelope"
            )
        row = rows[0]
        expected_hash = market_capture_envelope_hash(row)
        if row.content_hash != expected_hash:
            raise ValidationError("market capture provenance envelope hash is invalid")
        return row

    def _price(self, *, security_id: UUID, security_key: str, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingPriceSnapshot)
            .join(
                UnderwritingMarketCaptureEnvelope,
                (
                    UnderwritingMarketCaptureEnvelope.snapshot_id
                    == UnderwritingPriceSnapshot.id
                )
                & (UnderwritingMarketCaptureEnvelope.snapshot_kind == "price")
                & (UnderwritingMarketCaptureEnvelope.provenance_role == "primary"),
            )
            .where(
                UnderwritingPriceSnapshot.security_identity_id == security_id,
                UnderwritingPriceSnapshot.price_type == _PRICE_TYPE,
                UnderwritingPriceSnapshot.adjustment_basis == _ADJUSTMENT_BASIS,
                UnderwritingPriceSnapshot.market_at <= cutoff,
                UnderwritingPriceSnapshot.available_at <= cutoff,
                UnderwritingMarketCaptureEnvelope.authenticated_available_at <= cutoff,
            )
            .order_by(
                UnderwritingPriceSnapshot.market_at.desc(),
                UnderwritingPriceSnapshot.id,
            )
            .limit(2)
        )
        row = _latest_exact(
            rows, time_field="market_at", label=f"price for {security_key}"
        )
        if row.security_identity_id != security_id:
            raise ValidationError("market inputs contain a cross-security price")
        if row.currency != "USD":
            raise ValidationError(
                f"market inputs use the wrong currency for {security_key}"
            )
        return row

    def _fx(self, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingFXSnapshot)
            .join(
                UnderwritingMarketCaptureEnvelope,
                (
                    UnderwritingMarketCaptureEnvelope.snapshot_id
                    == UnderwritingFXSnapshot.id
                )
                & (UnderwritingMarketCaptureEnvelope.snapshot_kind == "fx")
                & (UnderwritingMarketCaptureEnvelope.provenance_role == "primary"),
            )
            .where(
                UnderwritingFXSnapshot.base_currency == "USD",
                UnderwritingFXSnapshot.quote_currency == _MODEL_CURRENCY,
                UnderwritingFXSnapshot.quote_direction == "quote_per_base",
                UnderwritingFXSnapshot.market_at <= cutoff,
                UnderwritingFXSnapshot.available_at <= cutoff,
                UnderwritingMarketCaptureEnvelope.authenticated_available_at <= cutoff,
            )
            .order_by(
                UnderwritingFXSnapshot.market_at.desc(),
                UnderwritingFXSnapshot.id,
            )
            .limit(2)
        )
        return _latest_exact(rows, time_field="market_at", label="USD/CNY FX")

    def _capital(self, company_id: UUID, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingCapitalStructureSnapshot)
            .join(
                UnderwritingMarketCaptureEnvelope,
                (
                    UnderwritingMarketCaptureEnvelope.snapshot_id
                    == UnderwritingCapitalStructureSnapshot.id
                )
                & (
                    UnderwritingMarketCaptureEnvelope.snapshot_kind
                    == "capital_structure"
                )
                & (UnderwritingMarketCaptureEnvelope.provenance_role == "primary"),
            )
            .where(
                UnderwritingCapitalStructureSnapshot.company_id == company_id,
                UnderwritingCapitalStructureSnapshot.market_at <= cutoff,
                UnderwritingCapitalStructureSnapshot.available_at <= cutoff,
                UnderwritingMarketCaptureEnvelope.authenticated_available_at <= cutoff,
            )
            .order_by(
                UnderwritingCapitalStructureSnapshot.market_at.desc(),
                UnderwritingCapitalStructureSnapshot.id,
            )
            .limit(2)
        )
        row = _latest_exact(rows, time_field="market_at", label="capital structure")
        if row.company_id != company_id:
            raise ValidationError(
                "market inputs contain a cross-company capital structure"
            )
        if row.currency != "USD":
            raise ValidationError(
                "market inputs use the wrong capital-structure currency"
            )
        return row

    def _rights(self, *, security_id: UUID, security_key: str, cutoff: datetime):
        rows = self._session.scalars(
            select(UnderwritingSecurityRightsVersion)
            .join(
                UnderwritingMarketCaptureEnvelope,
                (
                    UnderwritingMarketCaptureEnvelope.snapshot_id
                    == UnderwritingSecurityRightsVersion.id
                )
                & (UnderwritingMarketCaptureEnvelope.snapshot_kind == "security_rights")
                & (UnderwritingMarketCaptureEnvelope.provenance_role == "primary"),
            )
            .where(
                UnderwritingSecurityRightsVersion.security_identity_id == security_id,
                UnderwritingSecurityRightsVersion.effective_from <= cutoff,
                or_(
                    UnderwritingSecurityRightsVersion.effective_to.is_(None),
                    UnderwritingSecurityRightsVersion.effective_to > cutoff,
                ),
                UnderwritingMarketCaptureEnvelope.authenticated_available_at <= cutoff,
            )
            .order_by(
                UnderwritingSecurityRightsVersion.effective_from.desc(),
                UnderwritingSecurityRightsVersion.version.desc(),
                UnderwritingSecurityRightsVersion.id,
            )
            .limit(2)
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
    def _context(*, prices, fx, capital, rights, captures) -> FrozenMarketContext:
        # Resolve stable external keys once more for the bridge without trusting row order.
        # The caller has already proved there are exactly the two Alphabet securities.
        keys = _ALPHABET_SECURITY_KEYS
        price_by_key = dict(zip(keys, prices, strict=True))
        rights_by_key = dict(zip(keys, rights, strict=True))

        capital_capture = captures[("capital_structure", capital.id, "primary")]
        capital_ref = _lineage(
            fact_key="capital_structure_usd",
            source_id=capital_capture.source_url,
            source_locator=capital_capture.source_locator,
            raw_hash=capital_capture.raw_hash,
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
        fx_capture = captures[("fx", fx.id, "primary")]
        fx_ref = _lineage(
            fact_key="usd_cny_fx",
            source_id=fx_capture.source_url,
            source_locator=fx_capture.source_locator,
            raw_hash=fx_capture.raw_hash,
        )
        securities = []
        price_refs = {}
        rights_refs = {}
        for key in keys:
            suffix = key.lower().replace(":", "_")
            price = price_by_key[key]
            right = rights_by_key[key]
            price_capture = captures[("price", price.id, "primary")]
            rights_capture = captures[("security_rights", right.id, "primary")]
            price_ref = _lineage(
                fact_key=f"market_price_usd_{suffix}",
                source_id=price_capture.source_url,
                source_locator=price_capture.source_locator,
                raw_hash=price_capture.raw_hash,
            )
            rights_ref = _lineage(
                fact_key=f"security_rights_{suffix}",
                source_id=rights_capture.source_url,
                source_locator=rights_capture.source_locator,
                raw_hash=rights_capture.raw_hash,
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
            class_b_legal_capture = captures[
                ("capital_structure", capital.id, "class_b_legal_rights")
            ]
            class_b_unit_capture = captures[
                ("capital_structure", capital.id, "class_b_units")
            ]
            class_b_legal_ref = _lineage(
                fact_key="security_rights_class_b",
                source_id=class_b_legal_capture.source_url,
                source_locator=class_b_legal_capture.source_locator,
                raw_hash=class_b_legal_capture.raw_hash,
            )
            class_b_unit_ref = _lineage(
                fact_key="economic_units_class_b",
                source_id=class_b_unit_capture.source_url,
                source_locator=class_b_unit_capture.source_locator,
                raw_hash=class_b_unit_capture.raw_hash,
            )
            class_b_proxy_ref = _lineage(
                fact_key="market_price_proxy_class_b",
                source_id=price_refs["NASDAQ:GOOGL"].source_url,
                source_locator=price_refs["NASDAQ:GOOGL"].source_locator,
                raw_hash=price_refs["NASDAQ:GOOGL"].raw_hash,
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
                    class_b_unit_ref,
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
        snapshot_by_id = {row.id: row for row in (*prices, fx, capital, *rights)}
        capture_kind = {
            FrozenMarketSnapshotRole.PRICE: "price",
            FrozenMarketSnapshotRole.FX: "fx",
            FrozenMarketSnapshotRole.CAPITAL_STRUCTURE: "capital_structure",
            FrozenMarketSnapshotRole.SECURITY_RIGHTS: "security_rights",
        }
        bindings = tuple(
            FrozenMarketSnapshotBinding(
                snapshot_id=snapshot_id,
                role=role_by_id[snapshot_id][0],
                security_external_key=role_by_id[snapshot_id][1],
                source_ref=role_by_id[snapshot_id][2],
                snapshot_content_hash=snapshot_by_id[snapshot_id].content_hash,
                capture_envelope_id=captures[
                    (capture_kind[role_by_id[snapshot_id][0]], snapshot_id, "primary")
                ].id,
                capture_content_hash=captures[
                    (capture_kind[role_by_id[snapshot_id][0]], snapshot_id, "primary")
                ].content_hash,
                provenance_role="primary",
                provider_policy_version=captures[
                    (capture_kind[role_by_id[snapshot_id][0]], snapshot_id, "primary")
                ].provider_policy_version,
                raw_components=tuple(
                    FrozenRawComponentReference(**component)
                    for component in captures[
                        (
                            capture_kind[role_by_id[snapshot_id][0]],
                            snapshot_id,
                            "primary",
                        )
                    ].raw_components
                ),
            )
            for snapshot_id in snapshot_ids
        )
        market_at = max(_stored_utc(row.market_at) for row in (*prices, fx, capital))
        equity_components = tuple(
            FrozenMarketEquityComponent(
                component_key=component_key,
                economic_units=units,
                price_proxy_security_external_key=proxy,
                unit_source_ref=unit_source_ref,
                price_snapshot_id=price_by_key[proxy].id,
                price_ref=price_refs[proxy],
                **(
                    {
                        "votes_per_unit": Decimal("10"),
                        "conversion_to_security_external_key": "NASDAQ:GOOGL",
                        "conversion_ratio": Decimal("1"),
                        "dividend_rights_per_unit": Decimal("1"),
                        "economic_rights_per_unit": Decimal("1"),
                        "legal_rights_ref": class_b_legal_ref,
                        "price_proxy_ref": class_b_proxy_ref,
                        "price_proxy_policy_version": "alphabet_class_b_googl_proxy.v1",
                    }
                    if component_key == "class_b"
                    else {}
                ),
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
