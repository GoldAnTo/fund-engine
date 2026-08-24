"""Atomically install the minimal CATL/Alphabet identity foundation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ledger import ValidationError
from app.underwriting.domain.product_contracts import SecurityRightsInput
from app.underwriting.fixtures.product_foundation import (
    ProductFoundationFixture,
    validate_product_foundation_fixture,
)
from app.underwriting.persistence.models import (
    UnderwritingObjectRelation,
    UnderwritingResearchObject,
)
from app.underwriting.persistence.product_repository import ProductRepository
from app.underwriting.services.market_snapshots import MarketSnapshotService
from app.underwriting.services.product_project import ResearchProjectService


@dataclass(frozen=True, slots=True)
class ProductFoundationImport:
    objects: Mapping[str, UnderwritingResearchObject]
    identities: Mapping[str, object]
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
        head = self._repository.identity_head(research_object.id)
        if head is None:
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
        actual = (
            head.version,
            head.canonical_name,
            head.symbol,
            head.exchange,
            head.share_class,
            head.trading_currency,
            _stored_utc(head.effective_from),
            head.effective_to,
            head.supersedes_id,
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
        return head

    def _rights(self, fixture_rights, security: UnderwritingResearchObject):
        head = self._repository.rights_head(security.id)
        if head is None:
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
        actual = (
            head.version,
            head.economic_units,
            head.votes_per_unit,
            head.conversion_ratio,
            head.adr_ratio,
            head.dividend_rights_per_unit,
            _stored_utc(head.effective_from),
            head.effective_to,
            head.source_id,
            head.raw_hash,
            head.supersedes_id,
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
        return head

    def load(self, fixture: ProductFoundationFixture) -> ProductFoundationImport:
        validate_product_foundation_fixture(fixture)
        objects: dict[str, UnderwritingResearchObject] = {}
        identities: dict[str, object] = {}
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

            expected_relations = {
                (objects[item.company_key].id, objects[item.external_key].id)
                for item in fixture.securities
            }
            existing_relations = set(
                self._session.execute(
                    select(
                        UnderwritingObjectRelation.parent_id,
                        UnderwritingObjectRelation.child_id,
                    ).where(
                        UnderwritingObjectRelation.parent_id.in_(
                            [
                                objects[item.external_key].id
                                for item in fixture.companies
                            ]
                        ),
                        UnderwritingObjectRelation.relation_type
                        == "company_has_security",
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
            rights=MappingProxyType(rights),
            content_hash=fixture.content_hash,
        )
