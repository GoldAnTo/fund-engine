"""Atomically install the minimal CATL/Alphabet identity foundation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from types import MappingProxyType

from sqlalchemy import bindparam, func, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.ledger import ConflictError, ValidationError
from app.underwriting.domain.product_contracts import SecurityRightsInput
from app.underwriting.domain.search_terms import SearchTermIntegrityError
from app.underwriting.fixtures.product_foundation import (
    ProductFoundationFixture,
    validate_product_foundation_fixture,
)
from app.underwriting.persistence.models import (
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_models import (
    UnderwritingObjectIdentityVersion,
    UnderwritingResearchObjectAlias,
    UnderwritingSecurityRightsVersion,
    UnderwritingWorkspaceDraft,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.kernel import canonical_hash
from app.underwriting.services.market_snapshots import (
    MarketSnapshotService,
    security_rights_hash,
)
from app.underwriting.services.product_project import ResearchProjectService


_FOUNDATION_LOAD_LOCK_ID = int.from_bytes(
    hashlib.sha256(b"fund-engine:product-foundation-load:v1").digest()[:8],
    byteorder="big",
    signed=True,
)


@dataclass(frozen=True, slots=True)
class ProductFoundationImport:
    objects: Mapping[str, UnderwritingResearchObject]
    identities: Mapping[str, object]
    aliases: Mapping[tuple[str, str], UnderwritingResearchObjectAlias]
    rights: Mapping[str, object]
    content_hash: str


def _stored_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ProductFoundationFixtureService:
    """Caller-owned transaction with an internal savepoint for all-or-none loading."""

    def __init__(self, session: Session, *, now: Callable[[], datetime]) -> None:
        self._session = session
        self._now = now
        self._projects = ResearchProjectService(session, now=now)
        self._market = MarketSnapshotService(session, now=now)
        self._repository = ProductRepository(session)

    @staticmethod
    def postgresql_load_lock_statement() -> tuple[object, int]:
        """Return the stable transaction-scoped PostgreSQL fixture load lock."""
        return (
            select(
                func.pg_advisory_xact_lock(
                    bindparam(
                        "product_foundation_load_lock_id",
                        value=_FOUNDATION_LOAD_LOCK_ID,
                    )
                )
            ),
            _FOUNDATION_LOAD_LOCK_ID,
        )

    def _serialize_load(self) -> None:
        """Reserve the caller-owned transaction before any fixture-state read."""
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            statement, _ = self.postgresql_load_lock_statement()
            self._session.execute(statement)
            return
        if dialect != "sqlite":
            raise RuntimeError("database dialect cannot serialize foundation loading")
        connection = self._session.connection()
        raw_connection = connection.connection.driver_connection
        try:
            if not raw_connection.in_transaction:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                self._session.execute(
                    update(UnderwritingWorkspaceDraft)
                    .where(UnderwritingWorkspaceDraft.id.is_(None))
                    .values(lock_version=UnderwritingWorkspaceDraft.lock_version)
                    .execution_options(synchronize_session=False)
                )
        except OperationalError as exc:
            message = str(getattr(exc, "orig", exc)).lower()
            if "locked" in message or "busy" in message:
                raise ConflictError(
                    "product foundation load lock is busy; retry"
                ) from exc
            raise

    @staticmethod
    def _validate_successor_chain(rows: tuple[object, ...], label: str) -> None:
        previous = None
        for expected_version, row in enumerate(rows, start=1):
            if row.version != expected_version or row.supersedes_id != (
                previous.id if previous is not None else None
            ):
                raise ValidationError(f"product foundation {label} chain conflict")
            if previous is not None:
                row_from = _stored_utc(row.effective_from)
                previous_from = _stored_utc(previous.effective_from)
                previous_to = (
                    _stored_utc(previous.effective_to)
                    if previous.effective_to is not None
                    else None
                )
                if row_from <= previous_from or (
                    previous_to is not None and row_from < previous_to
                ):
                    raise ValidationError(f"product foundation {label} chain conflict")
            previous = row

    @staticmethod
    def _identity_hash(row, research_object: UnderwritingResearchObject) -> str:
        return canonical_hash(
            {
                "schema_version": "product.object-identity.v1",
                "object_id": str(research_object.id),
                "kind": research_object.kind,
                "canonical_name": row.canonical_name,
                "symbol": row.symbol,
                "exchange": row.exchange,
                "share_class": row.share_class,
                "trading_currency": row.trading_currency,
                "effective_from": _stored_utc(row.effective_from).isoformat(),
                "effective_to": (
                    _stored_utc(row.effective_to).isoformat()
                    if row.effective_to is not None
                    else None
                ),
            }
        )

    def _object(
        self, *, kind: str, external_key: str, canonical_name: str
    ) -> UnderwritingResearchObject:
        rows = tuple(
            self._session.scalars(
                select(UnderwritingResearchObject).where(
                    UnderwritingResearchObject.external_key == external_key
                )
            )
        )
        if len(rows) > 1:
            raise ValidationError(
                f"product foundation object conflict for {external_key}"
            )
        if rows:
            row = rows[0]
            if row.kind != kind:
                raise ValidationError(
                    f"product foundation object conflict for {external_key}"
                )
            return row
        row = UnderwritingResearchObject(
            kind=kind,
            external_key=external_key,
            canonical_name=canonical_name,
            created_at=self._now(),
        )
        self._session.add(row)
        self._session.flush()
        return row

    def _identity(
        self,
        *,
        research_object: UnderwritingResearchObject,
        canonical_name: str,
        symbol: str | None,
        exchange: str | None,
        share_class: str | None,
        currency: str | None,
        effective_from: datetime,
    ):
        versions = tuple(
            self._session.scalars(
                select(UnderwritingObjectIdentityVersion)
                .where(
                    UnderwritingObjectIdentityVersion.object_id == research_object.id
                )
                .order_by(
                    UnderwritingObjectIdentityVersion.version,
                    UnderwritingObjectIdentityVersion.id,
                )
            )
        )
        if not versions:
            return self._projects.append_identity_version(
                object_id=research_object.id,
                canonical_name=canonical_name,
                symbol=symbol,
                exchange=exchange,
                share_class=share_class,
                trading_currency=currency,
                effective_from=effective_from,
                effective_to=None,
                expected_parent_id=None,
            )
        self._validate_successor_chain(versions, "identity")
        if any(
            row.content_hash != self._identity_hash(row, research_object)
            for row in versions
        ):
            raise ValidationError("product foundation identity chain conflict")
        root = versions[0]
        actual = (
            root.version,
            root.canonical_name,
            root.symbol,
            root.exchange,
            root.share_class,
            root.trading_currency,
            _stored_utc(root.effective_from),
            root.effective_to,
            root.supersedes_id,
        )
        expected = (
            1,
            canonical_name,
            symbol,
            exchange,
            share_class,
            currency,
            effective_from,
            None,
            None,
        )
        if actual != expected:
            raise ValidationError(
                f"product foundation identity conflict for {research_object.external_key}"
            )
        try:
            for version in versions:
                self._repository.ensure_identity_search_terms(
                    research_object,
                    version,
                    repair_missing=True,
                )
        except SearchTermIntegrityError as exc:
            raise ValidationError("product foundation search term conflict") from exc
        return root

    def _rights(self, fixture_rights, security: UnderwritingResearchObject):
        versions = tuple(
            self._session.scalars(
                select(UnderwritingSecurityRightsVersion)
                .where(
                    UnderwritingSecurityRightsVersion.security_identity_id
                    == security.id
                )
                .order_by(
                    UnderwritingSecurityRightsVersion.version,
                    UnderwritingSecurityRightsVersion.id,
                )
            )
        )
        if not versions:
            return self._market.freeze_security_rights(
                SecurityRightsInput(
                    security_identity_id=security.id,
                    economic_units=fixture_rights.economic_units,
                    votes_per_unit=fixture_rights.votes_per_unit,
                    conversion_ratio=Decimal("1"),
                    adr_ratio=Decimal("1"),
                    dividend_rights_per_unit=Decimal("1"),
                    effective_from=fixture_rights.effective_from,
                    effective_to=None,
                    source_id=fixture_rights.source_id,
                    raw_hash=fixture_rights.raw_hash,
                ),
                expected_parent_id=None,
            )
        self._validate_successor_chain(versions, "rights")
        if any(row.content_hash != security_rights_hash(row) for row in versions):
            raise ValidationError("product foundation rights chain conflict")
        root = versions[0]
        actual = (
            root.version,
            root.economic_units,
            root.votes_per_unit,
            root.conversion_ratio,
            root.adr_ratio,
            root.dividend_rights_per_unit,
            _stored_utc(root.effective_from),
            root.effective_to,
            root.source_id,
            root.raw_hash,
            root.supersedes_id,
        )
        expected = (
            1,
            fixture_rights.economic_units,
            fixture_rights.votes_per_unit,
            Decimal("1"),
            Decimal("1"),
            Decimal("1"),
            fixture_rights.effective_from,
            None,
            fixture_rights.source_id,
            fixture_rights.raw_hash,
            None,
        )
        if actual != expected:
            raise ValidationError(
                f"product foundation rights conflict for {security.external_key}"
            )
        return root

    def _aliases(
        self,
        fixture: ProductFoundationFixture,
        objects: Mapping[str, UnderwritingResearchObject],
    ) -> dict[tuple[str, str], UnderwritingResearchObjectAlias]:
        expected_by_object = {key: [] for key in objects}
        for item in fixture.aliases:
            expected_by_object[item.object_key].append(item)

        expected_normalized = {
            item.normalized_alias: objects[item.object_key].id
            for item in fixture.aliases
        }
        if expected_normalized:
            conflicting_rows = tuple(
                self._session.scalars(
                    select(UnderwritingResearchObjectAlias).where(
                        UnderwritingResearchObjectAlias.normalized_alias.in_(
                            expected_normalized
                        )
                    )
                )
            )
            if any(
                row.object_id != expected_normalized[row.normalized_alias]
                for row in conflicting_rows
            ):
                raise ValidationError("product foundation alias conflict")

        aliases: dict[tuple[str, str], UnderwritingResearchObjectAlias] = {}
        for object_key, research_object in objects.items():
            expected = expected_by_object[object_key]
            existing = tuple(
                self._session.scalars(
                    select(UnderwritingResearchObjectAlias)
                    .where(
                        UnderwritingResearchObjectAlias.object_id == research_object.id
                    )
                    .order_by(
                        UnderwritingResearchObjectAlias.normalized_alias,
                        UnderwritingResearchObjectAlias.id,
                    )
                )
            )
            expected_values = {
                (item.alias, item.normalized_alias, item.locale) for item in expected
            }
            existing_values = {
                (row.alias, row.normalized_alias, row.locale) for row in existing
            }
            if existing and existing_values != expected_values:
                raise ValidationError(
                    f"product foundation alias conflict for {object_key}"
                )
            if not existing:
                for item in expected:
                    row = UnderwritingResearchObjectAlias(
                        object_id=research_object.id,
                        alias=item.alias,
                        normalized_alias=item.normalized_alias,
                        locale=item.locale,
                        created_at=self._now(),
                    )
                    self._session.add(row)
                    existing += (row,)
                self._session.flush()
            aliases.update(
                {(object_key, row.normalized_alias): row for row in existing}
            )
        return aliases

    def load(self, fixture: ProductFoundationFixture) -> ProductFoundationImport:
        validate_product_foundation_fixture(fixture)
        self._serialize_load()
        objects: dict[str, UnderwritingResearchObject] = {}
        identities: dict[str, object] = {}
        aliases: dict[tuple[str, str], UnderwritingResearchObjectAlias] = {}
        rights: dict[str, object] = {}
        with self._session.begin_nested():
            for company in fixture.companies:
                row = self._object(
                    kind="company",
                    external_key=company.external_key,
                    canonical_name=company.canonical_name,
                )
                objects[company.external_key] = row
                identities[company.external_key] = self._identity(
                    research_object=row,
                    canonical_name=company.canonical_name,
                    symbol=None,
                    exchange=None,
                    share_class=None,
                    currency=None,
                    effective_from=company.effective_from,
                )
            for security in fixture.securities:
                row = self._object(
                    kind="security",
                    external_key=security.external_key,
                    canonical_name=security.canonical_name,
                )
                objects[security.external_key] = row
                identities[security.external_key] = self._identity(
                    research_object=row,
                    canonical_name=security.canonical_name,
                    symbol=security.symbol,
                    exchange=security.exchange,
                    share_class=security.share_class,
                    currency=security.currency,
                    effective_from=security.effective_from,
                )

            aliases = self._aliases(fixture, objects)

            expected_relations = {
                (objects[item.company_key].id, objects[item.external_key].id)
                for item in fixture.securities
            }
            fixture_object_ids = [row.id for row in objects.values()]
            existing_relations = set(
                self._session.execute(
                    select(
                        UnderwritingObjectRelation.parent_id,
                        UnderwritingObjectRelation.child_id,
                    ).where(
                        UnderwritingObjectRelation.relation_type
                        == "company_has_security",
                        or_(
                            UnderwritingObjectRelation.parent_id.in_(
                                fixture_object_ids
                            ),
                            UnderwritingObjectRelation.child_id.in_(fixture_object_ids),
                        ),
                    )
                )
            )
            if not existing_relations.issubset(expected_relations):
                raise ValidationError("product foundation relation conflict")
            for parent_id, child_id in sorted(
                expected_relations - existing_relations,
                key=lambda pair: (str(pair[0]), str(pair[1])),
            ):
                self._session.add(
                    UnderwritingObjectRelation(
                        parent_id=parent_id,
                        child_id=child_id,
                        relation_type="company_has_security",
                        created_at=self._now(),
                    )
                )
            self._session.flush()

            for fixture_rights in fixture.rights:
                rights[fixture_rights.security_key] = self._rights(
                    fixture_rights, objects[fixture_rights.security_key]
                )

        return ProductFoundationImport(
            objects=MappingProxyType(objects),
            identities=MappingProxyType(identities),
            aliases=MappingProxyType(aliases),
            rights=MappingProxyType(rights),
            content_hash=fixture.content_hash,
        )
