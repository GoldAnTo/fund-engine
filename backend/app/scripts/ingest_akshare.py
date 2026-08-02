"""Ingest fund + holding data from AKShare into the evidence ledger.

CLI::

    python -m app.scripts.ingest_akshare [--fund-codes 005827,110011] [--report-date 2024-09-30]

Pulls fund metadata and per-fund holding disclosures from AKShare
(Eastmoney-backed scraping library), resolves stock identities via
:class:`InstrumentRepository`, and writes through the same append-only
ledger as the Gildata path.  Nothing touches the database outside
:func:`InstrumentRepository.add_fund` / :func:`add_fund_company` /
:func:`add_holding_disclosure`.

The script mirrors :mod:`app.scripts.ingest_real_data` in structure
so operators only need to learn one CLI shape — the data source name
switches from ``gildata`` to ``akshare`` and the column mapping lives
in :mod:`app.datasources.akshare.adapters`.

Auth / dependency notes:

- ``akshare`` is an **optional** dependency.  When the package is not
  installed, :class:`AkshareClient.from_env` raises ``AkshareError``
  (an ``ImportError`` subclass); the script propagates it so the
  caller can decide whether to skip the AKShare data source or fail
  the whole ingest.
- AKShare uses no credentials (public endpoints), so no environment
  variable is required.

Time-point discipline: see :mod:`app.datasources.akshare.adapters`
module docstring.  In short, every holding carries
``report_period`` (the truth of "what date is this snapshot?"),
``published_at`` (fallback to ``acquired_at`` because AKShare does
not expose the disclosure date), and ``acquired_at`` (the wall-clock
at fetch time, UTC).
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datasources.akshare import AkshareClient, AkshareError
from app.datasources.akshare.adapters import (
    fetch_fund_holdings,
    fetch_fund_list,
)
from app.models.ledger import (
    Fund,
    FundCompany,
    HoldingDisclosure,
    Stock,
)
from app.repositories.instruments import InstrumentRepository

log = logging.getLogger(__name__)

SOURCE_AKSHARE = "akshare"
DEFAULT_REPORT_DATE = "2024-09-30"  # most recent quarter AKShare reliably covers


# ---------------------------------------------------------------------------
# Outcome + helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestStats:
    """Outcome of one ingest run.  Returned by :func:`ingest` for tests
    and for the CLI summary line."""

    funds_seen: int
    funds_written: int
    holdings_written: int
    holdings_skipped_duplicate: int
    holdings_skipped_missing_stock: int
    errors: tuple[str, ...]


def _find_stock_by_code(session: Session, code: str) -> Stock | None:
    """Return the first Stock whose code matches, trying common suffixed forms.

    AKShare emits bare 6-digit codes (e.g. ``"300750"``) while the
    ledger stores market-suffixed codes (e.g. ``"300750.SZ"``).  This
    helper matches the suffix variants so the ensure_stock path can
    reuse the existing Stock when one is already in the ledger.
    """
    base = code.strip()
    candidates = {base, f"{base}.SH", f"{base}.SZ", f"{base}.BJ"}
    return session.scalar(
        select(Stock).where(Stock.code.in_(candidates)).limit(1)
    )


def _ensure_stock(
    session: Session,
    instruments: InstrumentRepository,
    *,
    code: str,
    name: str,
) -> Stock | None:
    """Return an existing Stock, or create Company + Stock if missing.

    Returns ``None`` when the bare code is malformed (not 6 digits) so
    the caller can record the row in the error tally rather than
    crashing the whole run on a single bad upstream row.
    """
    code = (code or "").strip()
    if not code or not code.isdigit() or len(code) != 6:
        log.warning("akshare stock code looks malformed: %r", code)
        return None
    existing = _find_stock_by_code(session, code)
    if existing is not None:
        return existing
    market = "SH" if code.startswith(("6", "9")) else "SZ"
    suffixed = f"{code}.{market}"
    company = instruments.add_company(code=suffixed, name=name or suffixed, type="listed")
    return instruments.add_stock(
        company_id=company.id,
        code=suffixed,
        name=name or suffixed,
        market=market,
    )


def _ensure_fund_company(
    session: Session,
    instruments: InstrumentRepository,
    *,
    name: str,
) -> FundCompany | None:
    """Return an existing FundCompany by name, or create one.

    AKShare's fund list does not surface the management company's
    internal code; we use the display name as the dedup key.  When
    multiple fund companies share a name (rare but possible) the
    first match wins — operators who need strict company mapping
    should backfill the company_code column on FundCompany.
    """
    name = (name or "").strip()
    if not name:
        return None
    existing = session.scalar(
        select(FundCompany).where(FundCompany.name == name).limit(1)
    )
    if existing is not None:
        return existing
    # Synthesise a placeholder code from the name so the unique
    # constraint on FundCompany.code passes; operators can overwrite
    # once they have a real management-company code.
    placeholder = f"akshare-{name[:32]}"
    return instruments.add_fund_company(code=placeholder, name=name)


def _ensure_fund(
    session: Session,
    instruments: InstrumentRepository,
    *,
    code: str,
    name: str,
    fund_type: str,
    management_company_id: uuid.UUID | None,
) -> Fund | None:
    """Return an existing Fund by code, or create one.

    Returns ``None`` when the bare code is empty (rejects bad rows
    instead of crashing).
    """
    code = (code or "").strip()
    if not code:
        return None
    existing = session.scalar(select(Fund).where(Fund.code == code).limit(1))
    if existing is not None:
        return existing
    return instruments.add_fund(
        code=code,
        name=name or code,
        fund_type=fund_type or "unknown",
        management_company_id=management_company_id,
    )


def _disclosure_exists(
    session: Session,
    *,
    fund_id: uuid.UUID,
    stock_id: uuid.UUID,
    report_period: date,
    source: str,
) -> bool:
    """True iff a matching ``HoldingDisclosure`` row is already in the ledger.

    The schema does not declare a unique constraint on
    ``(fund_id, stock_id, report_period, source)`` — the table is
    append-only in principle but a re-run of the ingest script would
    otherwise produce duplicates.  This pre-check keeps re-runs
    idempotent without leaning on the schema to enforce it.
    """
    existing = session.scalar(
        select(HoldingDisclosure)
        .where(HoldingDisclosure.fund_id == fund_id)
        .where(HoldingDisclosure.stock_id == stock_id)
        .where(HoldingDisclosure.report_period == report_period)
        .where(HoldingDisclosure.source == source)
        .limit(1)
    )
    return existing is not None


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


def ingest(
    session: Session,
    client: AkshareClient,
    *,
    fund_codes: list[str] | None = None,
    report_date: str = DEFAULT_REPORT_DATE,
    fetch_fund_list_enabled: bool = True,
) -> IngestStats:
    """Ingest AKShare fund + holding data into *session*.

    Parameters
    ----------
    session
        An open SQLAlchemy session.  The caller owns the transaction
        boundary — this function does **not** commit so an operator
        can dry-run with a rollback.
    client
        An :class:`AkshareClient` (typically built via
        :meth:`AkshareClient.from_env`).
    fund_codes
        If non-empty, only ingest holdings for these fund codes;
        useful for smoke-testing without pulling the whole fund list.
        If ``None`` or empty, ingest the full fund list.
    report_date
        The quarter-end date string to pass to
        ``fund_portfolio_hold_em`` (e.g. ``"2024-09-30"``).  This is
        the canonical ``report_period`` recorded on every holding.
    fetch_fund_list_enabled
        When False, skip the ``fund_name_em`` call and only ingest
        holdings for the explicitly-passed ``fund_codes`` (defaults
        to the canonical list of test funds).  Useful for tests that
        only care about the holdings path.

    Returns
    -------
    IngestStats
        Counts of funds and holdings written, plus a tuple of error
        messages for individual rows that could not be processed.
    """
    instruments = InstrumentRepository(session)
    funds_seen = 0
    funds_written = 0
    holdings_written = 0
    holdings_skipped_duplicate = 0
    holdings_skipped_missing_stock = 0
    errors: list[str] = []

    # Build the working list of fund dicts.  When ``fund_codes`` is
    # given, we still call ``fund_name_em`` so the management-company
    # mapping is consistent with the rest of the ledger — but we
    # only process the requested subset.
    try:
        all_funds = fetch_fund_list(client) if fetch_fund_list_enabled else []
    except AkshareError as exc:
        # If the user gave us explicit codes we can still try to
        # ingest holdings without the list; record the error but
        # continue.  Without codes there is nothing to do.
        log.error("akshare fund list fetch failed: %s", exc)
        if not fund_codes:
            return IngestStats(
                funds_seen=0,
                funds_written=0,
                holdings_written=0,
                holdings_skipped_duplicate=0,
                holdings_skipped_missing_stock=0,
                errors=(str(exc),),
            )
        all_funds = []

    if fund_codes:
        wanted = {c.strip() for c in fund_codes if c.strip()}
        funds_to_process = [f for f in all_funds if f["code"] in wanted]
        # Synthesise placeholder dicts for codes that aren't in the
        # upstream list (could be a closed-end fund or a new fund
        # the directory hasn't picked up yet).
        for code in wanted - {f["code"] for f in funds_to_process}:
            funds_to_process.append({"code": code, "name": code, "type": "unknown"})
    else:
        funds_to_process = all_funds

    for fund_dict in funds_to_process:
        funds_seen += 1
        # 1. Resolve the management company.  We don't have a clean
        # company code in AKShare's list, so we leave
        # ``management_company_id`` as None for the first pass and
        # rely on operator-side backfill if needed.
        mgmt = _ensure_fund_company(session, instruments, name="")
        fund = _ensure_fund(
            session,
            instruments,
            code=fund_dict["code"],
            name=fund_dict.get("name", ""),
            fund_type=fund_dict.get("type", ""),
            management_company_id=mgmt.id if mgmt else None,
        )
        if fund is None:
            errors.append(f"could not resolve fund {fund_dict.get('code')!r}")
            continue
        funds_written += 1

        # 2. Pull the holding list for this fund + report period.
        try:
            holdings = fetch_fund_holdings(client, fund_dict["code"], report_date)
        except AkshareError as exc:
            log.warning(
                "akshare holdings fetch failed for fund %s: %s",
                fund_dict["code"],
                exc,
            )
            errors.append(
                f"holdings fetch failed for {fund_dict['code']}: {exc}"
            )
            continue

        for h in holdings:
            report_period = h["report_period"]
            if report_period is None:
                errors.append(
                    f"fund {fund_dict['code']} holding has unparseable "
                    f"report_period (date_str={report_date!r})"
                )
                continue
            stock = _ensure_stock(
                session,
                instruments,
                code=h["stock_code"],
                name=h.get("stock_name", ""),
            )
            if stock is None:
                holdings_skipped_missing_stock += 1
                continue
            # Idempotency: re-runs must not double-write.  The schema
            # does not enforce uniqueness on (fund, stock, period,
            # source) so we check explicitly.
            if _disclosure_exists(
                session,
                fund_id=fund.id,
                stock_id=stock.id,
                report_period=report_period,
                source=SOURCE_AKSHARE,
            ):
                holdings_skipped_duplicate += 1
                continue
            try:
                weight_decimal = (
                    Decimal(str(h["weight"])) if h["weight"] is not None else Decimal("0")
                )
            except (InvalidOperation, ValueError):
                errors.append(
                    f"fund {fund_dict['code']} stock {h['stock_code']} "
                    f"weight not parseable: {h['weight']!r}"
                )
                continue
            instruments.add_holding_disclosure(
                fund_id=fund.id,
                stock_id=stock.id,
                weight=weight_decimal,
                report_period=report_period,
                published_at=h["published_at"],
                source=SOURCE_AKSHARE,
                acquired_at=h["acquired_at"],
            )
            holdings_written += 1

    return IngestStats(
        funds_seen=funds_seen,
        funds_written=funds_written,
        holdings_written=holdings_written,
        holdings_skipped_duplicate=holdings_skipped_duplicate,
        holdings_skipped_missing_stock=holdings_skipped_missing_stock,
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--fund-codes",
        type=str,
        default="",
        help="Comma-separated list of fund codes to ingest (default: full list).",
    )
    parser.add_argument(
        "--report-date",
        type=str,
        default=DEFAULT_REPORT_DATE,
        help=(
            "Quarter-end date string for ``fund_portfolio_hold_em`` "
            f"(default: {DEFAULT_REPORT_DATE})."
        ),
    )
    parser.add_argument(
        "--skip-fund-list",
        action="store_true",
        help="Skip the full fund-list fetch (only ingest the codes in --fund-codes).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute the ingest stats but do not commit changes.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable INFO-level logs."
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from app.db import SessionLocal  # local import to keep the script hermetic

    fund_codes = [c.strip() for c in args.fund_codes.split(",") if c.strip()] or None
    try:
        client = AkshareClient.from_env()
    except AkshareError as exc:
        print(f"akshare unavailable: {exc}", file=sys.stderr)
        return 2

    with SessionLocal() as session:
        stats = ingest(
            session,
            client,
            fund_codes=fund_codes,
            report_date=args.report_date,
            fetch_fund_list_enabled=not args.skip_fund_list,
        )
        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    log.info(
        "akshare ingest: funds_seen=%d funds_written=%d holdings_written=%d "
        "skipped_duplicate=%d skipped_missing_stock=%d errors=%d",
        stats.funds_seen,
        stats.funds_written,
        stats.holdings_written,
        stats.holdings_skipped_duplicate,
        stats.holdings_skipped_missing_stock,
        len(stats.errors),
    )
    if stats.errors:
        for err in stats.errors[:10]:
            log.warning("  - %s", err)

    print(
        f"funds_seen={stats.funds_seen} funds_written={stats.funds_written} "
        f"holdings_written={stats.holdings_written} "
        f"skipped_duplicate={stats.holdings_skipped_duplicate} "
        f"skipped_missing_stock={stats.holdings_skipped_missing_stock} "
        f"errors={len(stats.errors)}"
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
