"""Freeze and validate market state independently from evidence cutoffs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from uuid import UUID

from sqlalchemy import Numeric
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import (
    CapitalStructureSnapshotInput,
    FXSnapshotInput,
    FxQuoteDirection,
    PriceSnapshotInput,
    RevisionBoundaryInput,
    SecurityRightsInput,
)
from app.underwriting.domain.types import ResearchObjectKind
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.persistence.repository import StaleParentError
from app.underwriting.services.kernel import canonical_hash


_SCALE_10 = Decimal("0.0000000001")
_SCALE_12 = Decimal("0.000000000001")
_SUPPORTED_CURRENCIES = frozenset({"CNY", "USD"})
_SQLITE_DIALECT = sqlite_dialect()
_SQLITE_NUMERIC_TYPES = {
    10: Numeric(28, 10, asdecimal=True),
    12: Numeric(28, 12, asdecimal=True),
}


def _utc(value: datetime, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _stored_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _uuid(value: UUID, field: str) -> UUID:
    if type(value) is not UUID:
        raise ValidationError(f"{field} must be a UUID")
    return value


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not (normalized := value.strip()):
        raise ValidationError(f"{field} must not be empty")
    return normalized


def _bounded_text(value: object, field: str, maximum: int) -> str:
    normalized = _text(value, field)
    if len(normalized) > maximum:
        raise ValidationError(f"{field} must be at most {maximum} characters")
    return normalized


def _currency(value: object, field: str) -> str:
    normalized = _text(value, field)
    if normalized not in _SUPPORTED_CURRENCIES:
        raise ValidationError(f"{field} must be a supported CNY/USD currency")
    return normalized


def _decimal_at_scale(value: Decimal, field: str, scale: int) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"{field} must be a finite Decimal")
    quantum = _SCALE_10 if scale == 10 else _SCALE_12
    try:
        with localcontext() as context:
            context.prec = max(50, len(value.as_tuple().digits) + scale + 1)
            normalized = value.quantize(quantum)
    except InvalidOperation as exc:
        raise ValidationError(f"{field} must fit Numeric(28, {scale})") from exc
    if normalized != value:
        raise ValidationError(
            f"{field} must fit {scale} decimal places without rounding"
        )
    integer_digits = (
        max(normalized.adjusted() + 1, 1) if not normalized.is_zero() else 1
    )
    if integer_digits + scale > 28:
        raise ValidationError(f"{field} must fit Numeric(28, {scale})")
    if normalized.is_zero():
        normalized = abs(normalized)

    numeric_type = _SQLITE_NUMERIC_TYPES[scale]
    bind_processor = numeric_type.bind_processor(_SQLITE_DIALECT)
    result_processor = numeric_type.result_processor(_SQLITE_DIALECT, None)
    bound = bind_processor(normalized) if bind_processor is not None else normalized
    round_tripped = result_processor(bound) if result_processor is not None else bound
    if round_tripped != normalized:
        raise ValidationError(f"{field} exceeds the SQLite/local precision boundary")
    return normalized


def _decimal_text(value: Decimal, scale: int) -> str:
    normalized = _decimal_at_scale(value, "persisted decimal", scale)
    return format(normalized, f".{scale}f")


def _time_text(value: datetime) -> str:
    return _stored_utc(value).isoformat()


def price_snapshot_hash(value: object) -> str:
    return canonical_hash(
        {
            "schema_version": "product.price-snapshot.v1",
            "security_identity_id": str(getattr(value, "security_identity_id")),
            "price": _decimal_text(getattr(value, "price"), 10),
            "currency": getattr(value, "currency"),
            "price_type": getattr(value, "price_type"),
            "adjustment_basis": getattr(value, "adjustment_basis"),
            "market_at": _time_text(getattr(value, "market_at")),
            "available_at": _time_text(getattr(value, "available_at")),
            "source_id": getattr(value, "source_id"),
            "raw_hash": getattr(value, "raw_hash"),
        }
    )


def fx_snapshot_hash(value: object) -> str:
    direction = getattr(value, "quote_direction")
    if isinstance(direction, FxQuoteDirection):
        direction = direction.value
    return canonical_hash(
        {
            "schema_version": "product.fx-snapshot.v1",
            "base_currency": getattr(value, "base_currency"),
            "quote_currency": getattr(value, "quote_currency"),
            "rate": _decimal_text(getattr(value, "rate"), 12),
            "quote_direction": direction,
            "market_at": _time_text(getattr(value, "market_at")),
            "available_at": _time_text(getattr(value, "available_at")),
            "source_id": getattr(value, "source_id"),
            "raw_hash": getattr(value, "raw_hash"),
        }
    )


def capital_structure_snapshot_hash(value: object) -> str:
    return canonical_hash(
        {
            "schema_version": "product.capital-structure-snapshot.v1",
            "company_id": str(getattr(value, "company_id")),
            "currency": getattr(value, "currency"),
            "cash": _decimal_text(getattr(value, "cash"), 10),
            "debt": _decimal_text(getattr(value, "debt"), 10),
            "minority_interest": _decimal_text(getattr(value, "minority_interest"), 10),
            "investments": _decimal_text(getattr(value, "investments"), 10),
            "pension_liabilities": _decimal_text(
                getattr(value, "pension_liabilities"), 10
            ),
            "other_adjustments": _decimal_text(getattr(value, "other_adjustments"), 10),
            "basic_shares": _decimal_text(getattr(value, "basic_shares"), 10),
            "diluted_shares": _decimal_text(getattr(value, "diluted_shares"), 10),
            "potential_dilution_descriptors": list(
                getattr(value, "potential_dilution_descriptors")
            ),
            "report_period_start": _time_text(getattr(value, "report_period_start")),
            "report_period_end": _time_text(getattr(value, "report_period_end")),
            "market_at": _time_text(getattr(value, "market_at")),
            "available_at": _time_text(getattr(value, "available_at")),
            "source_id": getattr(value, "source_id"),
            "raw_hash": getattr(value, "raw_hash"),
        }
    )


def security_rights_hash(value: object) -> str:
    effective_to = getattr(value, "effective_to")
    return canonical_hash(
        {
            "schema_version": "product.security-rights.v1",
            "security_identity_id": str(getattr(value, "security_identity_id")),
            "economic_units": _decimal_text(getattr(value, "economic_units"), 10),
            "votes_per_unit": _decimal_text(getattr(value, "votes_per_unit"), 10),
            "conversion_ratio": _decimal_text(getattr(value, "conversion_ratio"), 10),
            "adr_ratio": _decimal_text(getattr(value, "adr_ratio"), 10),
            "dividend_rights_per_unit": _decimal_text(
                getattr(value, "dividend_rights_per_unit"), 10
            ),
            "effective_from": _time_text(getattr(value, "effective_from")),
            "effective_to": _time_text(effective_to) if effective_to else None,
            "source_id": getattr(value, "source_id"),
            "raw_hash": getattr(value, "raw_hash"),
        }
    )


@dataclass(frozen=True, slots=True)
class BoundaryContext:
    target_security_ids: tuple[UUID, ...]
    price_security_ids: tuple[UUID, ...]
    rights_security_ids: tuple[UUID, ...]
    requires_fx: bool
    price_snapshot_ids: tuple[UUID, ...] = ()
    rights_snapshot_ids: tuple[UUID, ...] = ()
    fx_snapshot_ids: tuple[UUID, ...] = ()
    capital_structure_snapshot_id: UUID | None = None
    required_fx_pairs: tuple[tuple[str, str], ...] = ()
    fx_pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class SecurityRightsResolution:
    effective: object | None
    head: object | None
    append_allowed: bool
    expected_parent_id: UUID | None
    minimum_effective_from: datetime | None
    reason_code: str
    reason_action: str


def validate_market_coverage(
    boundary: RevisionBoundaryInput, context: BoundaryContext
) -> None:
    targets = context.target_security_ids
    if len(targets) != len(set(targets)):
        raise ValidationError("target Securities must not contain duplicates")
    if len(context.price_security_ids) != len(targets) or set(
        context.price_security_ids
    ) != set(targets):
        raise ValidationError(
            "every target security requires exactly one price snapshot"
        )
    if len(context.rights_security_ids) != len(targets) or set(
        context.rights_security_ids
    ) != set(targets):
        raise ValidationError(
            "every target security requires exactly one effective rights version"
        )
    if context.price_snapshot_ids and set(context.price_snapshot_ids) != set(
        boundary.price_snapshot_ids
    ):
        raise ValidationError("boundary price references do not match exact snapshots")
    if context.rights_snapshot_ids and set(context.rights_snapshot_ids) != set(
        boundary.security_rights_ids
    ):
        raise ValidationError("boundary rights references do not match exact versions")
    if context.fx_snapshot_ids and set(context.fx_snapshot_ids) != set(
        boundary.fx_snapshot_ids
    ):
        raise ValidationError("boundary FX references do not match exact snapshots")
    if (
        context.capital_structure_snapshot_id is not None
        and context.capital_structure_snapshot_id
        != boundary.capital_structure_snapshot_id
    ):
        raise ValidationError("boundary capital structure reference does not match")
    if context.requires_fx and not boundary.fx_snapshot_ids:
        raise ValidationError("FX snapshot is required for cross-currency valuation")
    if len(context.fx_pairs) != len(set(context.fx_pairs)) or len(
        context.required_fx_pairs
    ) != len(set(context.required_fx_pairs)):
        raise ValidationError("boundary contains a duplicate FX pair")
    if len(context.fx_pairs) != len(context.required_fx_pairs) or set(
        context.fx_pairs
    ) != set(context.required_fx_pairs):
        raise ValidationError(
            "boundary FX pairs must exactly cover required quote directions"
        )


class MarketSnapshotService:
    def __init__(self, session: Session, now: Callable[[], datetime]) -> None:
        self._repository = ProductRepository(session)
        self._now = now

    def _created_at(self) -> datetime:
        return _utc(self._now(), "clock")

    def _security(self, security_id: UUID):
        row = self._repository.object(_uuid(security_id, "security_identity_id"))
        if row is None or row.kind != ResearchObjectKind.SECURITY.value:
            raise ValidationError("security_identity_id must identify a Security")
        return row

    def _company(self, company_id: UUID):
        row = self._repository.object(_uuid(company_id, "company_id"))
        if row is None or row.kind != ResearchObjectKind.COMPANY.value:
            raise ValidationError("company_id must identify a Company")
        return row

    def freeze_price(self, value: PriceSnapshotInput):
        if type(value) is not PriceSnapshotInput:
            raise ValidationError("value must be a PriceSnapshotInput")
        self._security(value.security_identity_id)
        market_at = _utc(value.market_at, "market_at")
        available_at = _utc(value.available_at, "available_at")
        effective = self._repository.effective_identity(
            value.security_identity_id, market_at
        )
        if effective is None:
            raise ValidationError(
                "Security requires an effective identity at market_at"
            )
        currency = _currency(value.currency, "currency")
        if effective.trading_currency != currency:
            raise ValidationError(
                "price currency must match the effective Security identity"
            )
        price = _decimal_at_scale(value.price, "price", 10)
        normalized = PriceSnapshotInput(
            value.security_identity_id,
            price,
            currency,
            _bounded_text(value.price_type, "price_type", 64),
            _bounded_text(value.adjustment_basis, "adjustment_basis", 64),
            market_at,
            available_at,
            _bounded_text(value.source_id, "source_id", 256),
            value.raw_hash,
        )
        return self._repository.freeze_price(
            security_identity_id=normalized.security_identity_id,
            price=normalized.price,
            currency=normalized.currency,
            price_type=normalized.price_type,
            adjustment_basis=normalized.adjustment_basis,
            market_at=normalized.market_at,
            available_at=normalized.available_at,
            source_id=normalized.source_id,
            raw_hash=normalized.raw_hash,
            content_hash=price_snapshot_hash(normalized),
            created_at=self._created_at(),
        )

    def price(self, snapshot_id: UUID):
        return self._repository.price(_uuid(snapshot_id, "snapshot_id"))

    def freeze_fx(self, value: FXSnapshotInput):
        if type(value) is not FXSnapshotInput:
            raise ValidationError("value must be an FXSnapshotInput")
        if value.quote_direction is not FxQuoteDirection.QUOTE_PER_BASE:
            raise ValidationError("FX quote direction must be quote_per_base")
        normalized = FXSnapshotInput(
            _currency(value.base_currency, "base_currency"),
            _currency(value.quote_currency, "quote_currency"),
            _decimal_at_scale(value.rate, "rate", 12),
            FxQuoteDirection.QUOTE_PER_BASE,
            _utc(value.market_at, "market_at"),
            _utc(value.available_at, "available_at"),
            _bounded_text(value.source_id, "source_id", 256),
            value.raw_hash,
        )
        return self._repository.freeze_fx(
            base_currency=normalized.base_currency,
            quote_currency=normalized.quote_currency,
            rate=normalized.rate,
            quote_direction=normalized.quote_direction.value,
            market_at=normalized.market_at,
            available_at=normalized.available_at,
            source_id=normalized.source_id,
            raw_hash=normalized.raw_hash,
            content_hash=fx_snapshot_hash(normalized),
            created_at=self._created_at(),
        )

    def fx(self, snapshot_id: UUID):
        return self._repository.fx(_uuid(snapshot_id, "snapshot_id"))

    def fx_for_pair(self, base_currency: str, quote_currency: str, market_at: datetime):
        return self._repository.fx_for_pair(
            _currency(base_currency, "base_currency"),
            _currency(quote_currency, "quote_currency"),
            _utc(market_at, "market_at"),
        )

    def freeze_capital_structure(self, value: CapitalStructureSnapshotInput):
        if type(value) is not CapitalStructureSnapshotInput:
            raise ValidationError("value must be a CapitalStructureSnapshotInput")
        self._company(value.company_id)
        numbers = {
            name: _decimal_at_scale(getattr(value, name), name, 10)
            for name in (
                "cash",
                "debt",
                "minority_interest",
                "investments",
                "pension_liabilities",
                "other_adjustments",
                "basic_shares",
                "diluted_shares",
            )
        }
        if not isinstance(value.potential_dilution_descriptors, tuple):
            raise ValidationError("potential_dilution_descriptors must be a tuple")
        descriptors = tuple(
            _text(item, "potential_dilution_descriptors item")
            for item in value.potential_dilution_descriptors
        )
        if len(descriptors) != len(set(descriptors)):
            raise ValidationError(
                "potential_dilution_descriptors must not contain duplicates"
            )
        normalized = CapitalStructureSnapshotInput(
            value.company_id,
            _currency(value.currency, "currency"),
            numbers["cash"],
            numbers["debt"],
            numbers["minority_interest"],
            numbers["investments"],
            numbers["pension_liabilities"],
            numbers["other_adjustments"],
            numbers["basic_shares"],
            numbers["diluted_shares"],
            descriptors,
            _utc(value.report_period_start, "report_period_start"),
            _utc(value.report_period_end, "report_period_end"),
            _utc(value.market_at, "market_at"),
            _utc(value.available_at, "available_at"),
            _bounded_text(value.source_id, "source_id", 256),
            value.raw_hash,
        )
        return self._repository.freeze_capital_structure(
            company_id=normalized.company_id,
            currency=normalized.currency,
            cash=normalized.cash,
            debt=normalized.debt,
            minority_interest=normalized.minority_interest,
            investments=normalized.investments,
            pension_liabilities=normalized.pension_liabilities,
            other_adjustments=normalized.other_adjustments,
            basic_shares=normalized.basic_shares,
            diluted_shares=normalized.diluted_shares,
            potential_dilution_descriptors=list(
                normalized.potential_dilution_descriptors
            ),
            report_period_start=normalized.report_period_start,
            report_period_end=normalized.report_period_end,
            market_at=normalized.market_at,
            available_at=normalized.available_at,
            source_id=normalized.source_id,
            raw_hash=normalized.raw_hash,
            content_hash=capital_structure_snapshot_hash(normalized),
            created_at=self._created_at(),
        )

    def capital_structure(self, snapshot_id: UUID):
        return self._repository.capital_structure(_uuid(snapshot_id, "snapshot_id"))

    def freeze_security_rights(
        self,
        value: SecurityRightsInput,
        *,
        expected_parent_id: UUID | None,
    ):
        if type(value) is not SecurityRightsInput:
            raise ValidationError("value must be a SecurityRightsInput")
        self._security(value.security_identity_id)
        numbers = {
            name: _decimal_at_scale(getattr(value, name), name, 10)
            for name in (
                "economic_units",
                "votes_per_unit",
                "conversion_ratio",
                "adr_ratio",
                "dividend_rights_per_unit",
            )
        }
        effective_from = _utc(value.effective_from, "effective_from")
        effective_to = (
            _utc(value.effective_to, "effective_to")
            if value.effective_to is not None
            else None
        )
        head = self._repository.rights_head(value.security_identity_id)
        if head is not None and head.id == expected_parent_id:
            if effective_from <= _stored_utc(head.effective_from):
                raise ValidationError("rights effective_from must advance")
            if head.effective_to is not None and effective_from < _stored_utc(
                head.effective_to
            ):
                raise ValidationError("rights effective interval must not overlap")
        normalized = SecurityRightsInput(
            value.security_identity_id,
            numbers["economic_units"],
            numbers["votes_per_unit"],
            numbers["conversion_ratio"],
            numbers["adr_ratio"],
            numbers["dividend_rights_per_unit"],
            effective_from,
            effective_to,
            _bounded_text(value.source_id, "source_id", 256),
            value.raw_hash,
        )
        try:
            return self._repository.append_security_rights(
                security_identity_id=normalized.security_identity_id,
                economic_units=normalized.economic_units,
                votes_per_unit=normalized.votes_per_unit,
                conversion_ratio=normalized.conversion_ratio,
                adr_ratio=normalized.adr_ratio,
                dividend_rights_per_unit=normalized.dividend_rights_per_unit,
                effective_from=normalized.effective_from,
                effective_to=normalized.effective_to,
                source_id=normalized.source_id,
                raw_hash=normalized.raw_hash,
                content_hash=security_rights_hash(normalized),
                expected_parent_id=expected_parent_id,
                created_at=self._created_at(),
            )
        except StaleParentError as exc:
            raise ConflictError(str(exc)) from exc

    def security_rights(self, rights_id: UUID):
        return self._repository.security_rights(_uuid(rights_id, "rights_id"))

    def effective_security_rights(self, security_id: UUID, as_of: datetime):
        self._security(security_id)
        return self._repository.effective_security_rights(
            security_id, _utc(as_of, "as_of")
        )

    def resolve_security_rights(
        self, security_id: UUID, as_of: datetime
    ) -> SecurityRightsResolution:
        self._security(security_id)
        boundary_at = _utc(as_of, "as_of")
        effective = self._repository.effective_security_rights(security_id, boundary_at)
        head = self._repository.rights_head(security_id)
        if effective is not None:
            return SecurityRightsResolution(
                effective=effective,
                head=head,
                append_allowed=False,
                expected_parent_id=None,
                minimum_effective_from=None,
                reason_code="effective_version_found",
                reason_action="reuse_effective",
            )
        if head is None:
            return SecurityRightsResolution(
                effective=None,
                head=None,
                append_allowed=True,
                expected_parent_id=None,
                minimum_effective_from=None,
                reason_code="no_history",
                reason_action="create_initial",
            )
        head_from = _stored_utc(head.effective_from)
        if boundary_at < head_from:
            return SecurityRightsResolution(
                effective=None,
                head=head,
                append_allowed=False,
                expected_parent_id=None,
                minimum_effective_from=None,
                reason_code="before_head",
                reason_action="adjust_market_at",
            )
        minimum_effective_from = (
            _stored_utc(head.effective_to)
            if head.effective_to is not None
            else head_from
        )
        return SecurityRightsResolution(
            effective=None,
            head=head,
            append_allowed=True,
            expected_parent_id=head.id,
            minimum_effective_from=minimum_effective_from,
            reason_code="successor_required",
            reason_action="append_successor",
        )

    def security_rights_head(self, security_id: UUID):
        self._security(security_id)
        return self._repository.rights_head(security_id)

    def boundary_context(
        self,
        project_id: UUID,
        boundary: RevisionBoundaryInput,
        *,
        as_of: datetime,
        model_currency: str,
    ) -> BoundaryContext:
        project_id = _uuid(project_id, "project_id")
        if type(boundary) is not RevisionBoundaryInput:
            raise ValidationError("boundary must be a RevisionBoundaryInput")
        boundary_at = _utc(as_of, "as_of")
        model_currency = _currency(model_currency, "model_currency")
        record = self._repository.project(project_id)
        if record is None:
            raise ValidationError("project does not exist")
        project, project_security_ids = record
        if self._repository.product_basis(boundary.historical_basis_id) is None:
            raise ValidationError("historical basis does not exist")
        mandate = self._repository.product_mandate(project_id, boundary.mandate_id)
        if mandate is None:
            raise ValidationError("mandate must belong to the project")
        scope = self._repository.scope_by_id(boundary.scope_id)
        if scope is None or scope.project_id != project_id:
            raise ValidationError("scope must belong to the project")
        raw_scope_targets = scope.payload.get("target_security_ids")
        if not isinstance(raw_scope_targets, list) or not raw_scope_targets:
            raise ValidationError("scope must freeze target Security identities")
        try:
            target_security_ids = tuple(UUID(value) for value in raw_scope_targets)
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "scope target Security identities are invalid"
            ) from exc
        if len(target_security_ids) != len(set(target_security_ids)) or not set(
            target_security_ids
        ).issubset(project_security_ids):
            raise ValidationError("scope target Securities must belong to the project")
        agenda = self._repository.agenda(project_id, boundary.agenda_id)
        if agenda is None:
            raise ValidationError("agenda must belong to the project")
        if agenda.scope_id != scope.id:
            raise ValidationError("agenda must reference the boundary scope")

        prices = self._repository.prices(boundary.price_snapshot_ids)
        if len(prices) != len(boundary.price_snapshot_ids):
            raise ValidationError("boundary price snapshot does not exist")
        rights = self._repository.security_rights_many(boundary.security_rights_ids)
        if len(rights) != len(boundary.security_rights_ids):
            raise ValidationError("boundary rights version does not exist")
        capital = self._repository.capital_structure(
            boundary.capital_structure_snapshot_id
        )
        if capital is None:
            raise ValidationError("boundary capital structure does not exist")
        fxs = self._repository.fxs(boundary.fx_snapshot_ids)
        if len(fxs) != len(boundary.fx_snapshot_ids):
            raise ValidationError("boundary FX snapshot does not exist")

        target_set = set(target_security_ids)
        if any(price.security_identity_id not in target_set for price in prices):
            raise ValidationError(
                "price must reference a target Security in the project"
            )
        if any(item.security_identity_id not in target_set for item in rights):
            raise ValidationError(
                "rights must reference a target Security in the project"
            )
        if capital.company_id != project.primary_company_id:
            raise ValidationError(
                "capital structure must reference the project Company"
            )
        for item in rights:
            effective = self._repository.effective_security_rights(
                item.security_identity_id, boundary_at
            )
            if effective is None or effective.id != item.id:
                raise ValidationError("boundary requires effective rights at as_of")

        base_currency = mandate.base_currency
        currencies = {price.currency for price in prices}
        currencies.add(capital.currency)
        currencies.add(model_currency)
        required_pairs = tuple(
            sorted(
                (
                    (currency, base_currency)
                    for currency in currencies
                    if currency != base_currency
                ),
                key=lambda pair: pair,
            )
        )
        fx_pairs = tuple((fx.base_currency, fx.quote_currency) for fx in fxs)
        context = BoundaryContext(
            target_security_ids=tuple(sorted(target_security_ids, key=str)),
            price_security_ids=tuple(price.security_identity_id for price in prices),
            rights_security_ids=tuple(item.security_identity_id for item in rights),
            requires_fx=bool(required_pairs),
            price_snapshot_ids=tuple(price.id for price in prices),
            rights_snapshot_ids=tuple(item.id for item in rights),
            fx_snapshot_ids=tuple(fx.id for fx in fxs),
            capital_structure_snapshot_id=capital.id,
            required_fx_pairs=required_pairs,
            fx_pairs=fx_pairs,
        )
        validate_market_coverage(boundary, context)
        return context
