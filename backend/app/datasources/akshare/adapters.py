"""Domain adapters: AKShare ``DataFrame`` -> typed canonical dicts.

The client (:mod:`app.datasources.akshare.client`) returns raw
``pandas.DataFrame`` from upstream — column names are in Chinese and
shift whenever Eastmoney tweaks a page.  This module owns the canonical
key mapping so the rest of the codebase (``InstrumentRepository``,
``ingest_akshare.py``, etc.) sees stable dicts with English keys.

Time-point discipline
=====================

Every holding carries three time-point fields, kept strictly separate:

- ``report_period`` — the disclosure's reporting period (e.g. 2024Q3),
  sourced from the ``date`` parameter passed to
  ``fund_portfolio_hold_em``.  This is the **truth** of "what date is
  this snapshot describing?".
- ``published_at`` — when the fund company disclosed the holding to
  the public.  AKShare does not expose this, so the adapter falls
  back to ``acquired_at`` (the time we fetched the data) and labels
  it explicitly in the docstring; downstream code must not treat it
  as a true disclosure date.
- ``acquired_at`` — the time the ingest run pulled the row from
  AKShare.  Always UTC, always populated.

Collapsing the three into one (a common shortcut) loses the ability to
distinguish "the disclosure that was public last week" from "what we
happened to fetch today", so the adapter always emits all three.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.datasources.akshare.client import AkshareClient, AkshareError


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a tz-aware datetime.

    AKShare does not expose a disclosure publication date, so the
    adapter falls back to the wall-clock at fetch time.  UTC keeps the
    ledger consistent regardless of which region the ingest runner is
    hosted in.
    """
    return datetime.now(timezone.utc)


def _parse_date(value) -> date | None:
    """Parse a date-ish value into a ``date``.

    Tries three common shapes in order:

    - ``datetime.date`` / ``datetime.datetime`` (return as-is);
    - ISO-style strings (``%Y-%m-%d`` / ``%Y/%m/%d``);
    - Compact 8-digit strings (``%Y%m%d``).

    Returns ``None`` for any other shape so the caller's downstream
    code can decide whether to skip the row or report the malformed
    input.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_weight(value) -> float | None:
    """Parse a weight string like ``"4.52%"`` or ``"4.52"`` to a float.

    AKShare emits weights as ``"4.52%"`` strings (with the trailing
    percent sign).  ``"-"`` / ``"--"`` are placeholders for "no
    holding" — return ``None`` so the caller can filter rather than
    treating them as zero-weight positions.
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in {"-", "--", "—"}:
        return None
    s = s.rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def fetch_fund_list(client: AkshareClient) -> list[dict]:
    """Return ``[{code, name, type}]`` for all open-end funds.

    Calls ``ak.fund_name_em()`` (Eastmoney's open-end fund directory).
    The function returns a ``DataFrame`` whose column names are
    Chinese — we map them to canonical English keys here.

    Funds without a code (corrupt upstream rows) are dropped; an empty
    ``code`` would collide on the unique index downstream.
    """
    df = client.call("fund_name_em")
    results: list[dict] = []
    for _, row in df.iterrows():
        results.append({
            "code": str(row.get("基金代码", "")).strip(),
            "name": str(row.get("基金简称", "")).strip(),
            "type": str(row.get("基金类型", "")).strip(),
        })
    return [r for r in results if r["code"]]


def fetch_fund_holdings(
    client: AkshareClient,
    fund_code: str,
    date_str: str,
) -> list[dict]:
    """Return ``[{fund_code, stock_code, stock_name, weight, ...}]``.

    Calls ``ak.fund_portfolio_hold_em(symbol=fund_code, date=date_str)``
    and maps the Eastmoney column names to canonical keys.  Each
    returned dict carries the **three time-point fields** described
    in the module docstring:

    - ``report_period`` — from the ``date_str`` parameter (e.g.
      ``"2024-09-30"`` for the Q3 2024 disclosure);
    - ``published_at`` — same as ``acquired_at`` (AKShare does not
      expose the disclosure date);
    - ``acquired_at`` — UTC timestamp of the fetch.

    Rows without a stock code are dropped — the unique constraint on
    ``HoldingDisclosure(fund_id, stock_id, report_period)`` would
    reject them anyway.
    """
    df = client.call(
        "fund_portfolio_hold_em",
        symbol=fund_code,
        date=date_str,
    )
    acquired_at = _utcnow()
    report_period = _parse_date(date_str)
    results: list[dict] = []
    for _, row in df.iterrows():
        results.append({
            "fund_code": fund_code,
            "stock_code": str(row.get("股票代码", "")).strip(),
            "stock_name": str(row.get("股票名称", "")).strip(),
            "weight": _parse_weight(row.get("占净值比例", "")),
            "report_period": report_period,
            # AKShare does not expose a true disclosure publication
            # date; the fallback to acquired_at is documented in the
            # module docstring's "Time-point discipline" section.
            "published_at": acquired_at,
            "acquired_at": acquired_at,
        })
    return [r for r in results if r["stock_code"]]
