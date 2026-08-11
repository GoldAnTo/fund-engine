from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

from app.models.ledger import HoldingDisclosure
from app.repositories.instruments import InstrumentRepository


_FILING_KIND_PRECEDENCE = {
    "other": 0,
    "quarterly": 1,
    "annual": 2,
    "correction": 3,
}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def disclosure_version_key(disclosure: HoldingDisclosure) -> tuple:
    """Stable preference among immutable filings for the same holding fact."""
    return (
        disclosure.report_period,
        _FILING_KIND_PRECEDENCE.get(disclosure.filing_kind, 0),
        _as_utc(disclosure.published_at),
        _as_utc(disclosure.created_at),
        str(disclosure.id),
    )


def choose_latest_disclosure_per_key(
    disclosures: list[HoldingDisclosure], key,
) -> list[HoldingDisclosure]:
    latest: dict[object, HoldingDisclosure] = {}
    for disclosure in sorted(disclosures, key=disclosure_version_key, reverse=True):
        latest.setdefault(key(disclosure), disclosure)
    return list(latest.values())


@dataclass(frozen=True)
class ExposureRow:
    disclosure_id: uuid.UUID
    stock_id: uuid.UUID
    weight: Decimal
    report_period: date


@dataclass(frozen=True)
class FundExposure:
    fund_id: uuid.UUID
    as_of: date
    theme_weight: Decimal
    report_period: date | None
    rows: list[ExposureRow] = field(default_factory=list)


def choose_latest_disclosure_per_stock(
    disclosures: list[HoldingDisclosure],
) -> list[HoldingDisclosure]:
    """Pick the latest ``report_period`` disclosure for each stock.

    ``disclosures`` is expected to be ordered by ``report_period`` descending so
    that the first occurrence of each stock is the latest.
    """
    return choose_latest_disclosure_per_key(disclosures, lambda disclosure: disclosure.stock_id)


def choose_latest_disclosure_per_fund_stock(
    disclosures: list[HoldingDisclosure],
) -> list[HoldingDisclosure]:
    return choose_latest_disclosure_per_key(
        disclosures, lambda disclosure: (disclosure.fund_id, disclosure.stock_id)
    )


class ExposureService:
    """Computes point-in-time fund theme exposure from holding disclosures.

    Visibility is governed by the disclosure publication date (``published_at``),
    not the holding report period.  A disclosure whose ``published_at`` is after
    the requested ``as_of`` is invisible at that historical point in time.
    """

    def __init__(self, repository: InstrumentRepository) -> None:
        self._repo = repository

    def for_fund(self, fund_id: uuid.UUID, *, as_of: date) -> FundExposure:
        disclosures = self._repo.disclosures_visible_on_or_before(fund_id, as_of)
        latest_by_stock = choose_latest_disclosure_per_stock(disclosures)
        mapped = [
            d for d in latest_by_stock if self._repo.stock_has_theme_role(d.stock_id, as_of)
        ]
        rows = [
            ExposureRow(
                disclosure_id=d.id,
                stock_id=d.stock_id,
                weight=d.weight,
                report_period=d.report_period,
            )
            for d in mapped
        ]
        theme_weight = sum((row.weight for row in rows), Decimal("0"))
        report_period = max((row.report_period for row in rows), default=None)
        return FundExposure(
            fund_id=fund_id,
            as_of=as_of,
            theme_weight=theme_weight,
            report_period=report_period,
            rows=rows,
        )
