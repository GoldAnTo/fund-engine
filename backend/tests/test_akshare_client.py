"""Tests for the AKShare adapter package.

The ``akshare`` package is an **optional** dependency — these tests
cover both the always-available code paths (failure modes, canonical
key mapping, time-point parsing) and the integration paths (mocked
``akshare`` module to exercise :class:`AkshareClient` end-to-end).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.datasources.akshare import AkshareClient, AkshareError
from app.datasources.akshare.adapters import (
    _parse_date,
    _parse_weight,
    fetch_fund_holdings,
    fetch_fund_list,
)


# ---------------------------------------------------------------------------
# AkshareClient — fail-closed / lazy import
# ---------------------------------------------------------------------------


def test_akshare_error_is_import_error():
    """``AkshareError`` must subclass ``ImportError`` so callers can use
    a single ``except ImportError`` for "optional dep missing"."""
    assert issubclass(AkshareError, ImportError)
    err = AkshareError("akshare is not installed")
    assert "akshare" in str(err).lower()


def test_constructor_raises_when_akshare_missing(monkeypatch):
    """Hiding ``akshare`` from ``sys.modules`` must produce ``AkshareError``.

    The constructor's lazy import must surface the missing dependency
    as a clean ``AkshareError`` (and *not* a generic ``ModuleNotFoundError``)
    so callers can branch on a single type.
    """
    hidden = {
        name: mod
        for name, mod in __import__("sys").modules.items()
        if name == "akshare" or name.startswith("akshare.")
    }
    for name in list(hidden):
        monkeypatch.delitem(__import__("sys").modules, name)
    __import__("sys").modules["akshare"] = None  # type: ignore[assignment]
    try:
        with pytest.raises(AkshareError) as ei:
            AkshareClient()
        msg = str(ei.value).lower()
        assert "akshare" in msg
        assert "pip install" in msg
    finally:
        __import__("sys").modules.pop("akshare", None)
        for name, mod in hidden.items():
            __import__("sys").modules[name] = mod


def test_call_raises_for_unknown_function(monkeypatch):
    """A typo in the function name must produce a clean AkshareError, not
    an AttributeError leak."""
    fake = SimpleNamespace()  # no attributes -> any function lookup fails
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    client = AkshareClient()
    with pytest.raises(AkshareError) as ei:
        client.call("nonexistent_func_xyz")
    assert "nonexistent_func_xyz" in str(ei.value)


def test_call_wraps_arbitrary_exceptions(monkeypatch):
    """A function that raises an arbitrary exception must be wrapped in
    ``AkshareError`` (preserving the original via ``__cause__``) so the
    rest of the codebase can rely on a single exception type."""
    def _boom(**kwargs):
        raise RuntimeError("network blip")

    fake = SimpleNamespace(broken_func=_boom)
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    client = AkshareClient()
    with pytest.raises(AkshareError) as ei:
        client.call("broken_func", symbol="005827")
    assert "broken_func" in str(ei.value)
    assert "network blip" in str(ei.value)
    # __cause__ must point at the original RuntimeError for log post-mortem.
    assert isinstance(ei.value.__cause__, RuntimeError)


# ---------------------------------------------------------------------------
# _parse_weight — helper
# ---------------------------------------------------------------------------


def test_parse_weight_handles_percent_strings():
    """AKShare emits weights as ``"4.52%"`` strings; the parser must
    strip the percent sign and return a float."""
    assert _parse_weight("4.52%") == 4.52
    assert _parse_weight("0.13%") == 0.13
    # Tolerate stray whitespace.
    assert _parse_weight("  3.7%  ") == 3.7


def test_parse_weight_handles_bare_numbers():
    """Some AKShare columns omit the ``%`` sign — treat as float either way."""
    assert _parse_weight("4.52") == 4.52
    assert _parse_weight(4.52) == 4.52


def test_parse_weight_returns_none_for_dashes_and_blank():
    """``"--"`` / ``"-"`` / ``"`` are placeholders for "no holding" — they
    must not be parsed as zero."""
    assert _parse_weight("--") is None
    assert _parse_weight("-") is None
    assert _parse_weight("—") is None
    assert _parse_weight("") is None
    assert _parse_weight(None) is None


def test_parse_weight_returns_none_for_garbage():
    """Anything that does not parse as a float must return ``None`` (so
    the caller can skip the row rather than crash)."""
    assert _parse_weight("not a number") is None
    assert _parse_weight("4.5.6%") is None


# ---------------------------------------------------------------------------
# _parse_date — helper
# ---------------------------------------------------------------------------


def test_parse_date_accepts_iso_and_compact():
    """ISO-style, slash-style, and 8-digit compact strings all parse."""
    assert _parse_date("2024-09-30") == date(2024, 9, 30)
    assert _parse_date("2024/09/30") == date(2024, 9, 30)
    assert _parse_date("20240930") == date(2024, 9, 30)


def test_parse_date_accepts_datetime_and_date():
    """``datetime`` / ``date`` inputs are returned as-is (or with the
    time component stripped)."""
    assert _parse_date(date(2024, 9, 30)) == date(2024, 9, 30)
    assert _parse_date(datetime(2024, 9, 30, 12, 0, 0)) == date(2024, 9, 30)


def test_parse_date_returns_none_for_garbage():
    """Unknown formats must not raise — return ``None`` so the caller
    can decide whether to skip the row."""
    assert _parse_date("not a date") is None
    assert _parse_date("") is None
    assert _parse_date(None) is None


# ---------------------------------------------------------------------------
# fetch_fund_list — canonical key mapping
# ---------------------------------------------------------------------------


class _FakeDataFrame:
    """Minimal DataFrame stub — just enough for ``.iterrows()``."""

    def __init__(self, rows):
        self._rows = list(rows)

    def iterrows(self):
        for idx, row in enumerate(self._rows):
            yield idx, row


def _install_fake_akshare(monkeypatch, *, fund_list=None, holdings=None):
    """Wire a fake ``akshare`` module whose ``fund_name_em`` and
    ``fund_portfolio_hold_em`` return the supplied rows.

    Both functions return ``_FakeDataFrame`` instances; the adapter
    calls ``.iterrows()`` on them, so any object exposing that
    interface works.
    """

    def fund_name_em():
        return _FakeDataFrame(fund_list or [])

    def fund_portfolio_hold_em(*, symbol, date):
        return _FakeDataFrame(holdings or [])

    fake = SimpleNamespace(
        fund_name_em=fund_name_em,
        fund_portfolio_hold_em=fund_portfolio_hold_em,
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)


def test_fetch_fund_list_maps_chinese_columns(monkeypatch):
    """The Eastmoney column names (基金代码 / 基金简称 / 基金类型) must
    be mapped to canonical English keys."""
    _install_fake_akshare(
        monkeypatch,
        fund_list=[
            {"基金代码": "005827", "基金简称": "易方达蓝筹精选", "基金类型": "混合型"},
            {"基金代码": "110011", "基金简称": "易方达中小盘", "基金类型": "股票型"},
        ],
    )
    client = AkshareClient()
    funds = fetch_fund_list(client)
    assert funds == [
        {"code": "005827", "name": "易方达蓝筹精选", "type": "混合型"},
        {"code": "110011", "name": "易方达中小盘", "type": "股票型"},
    ]


def test_fetch_fund_list_drops_rows_without_code(monkeypatch):
    """Upstream sometimes returns malformed rows with no code — these
    must be filtered out, not silently produce empty-code dicts that
    later collide on the unique index."""
    _install_fake_akshare(
        monkeypatch,
        fund_list=[
            {"基金代码": "005827", "基金简称": "valid", "基金类型": "混合型"},
            {"基金代码": "", "基金简称": "no code", "基金类型": "混合型"},
            {"基金代码": "  ", "基金简称": "whitespace", "基金类型": "混合型"},
        ],
    )
    client = AkshareClient()
    funds = fetch_fund_list(client)
    assert len(funds) == 1
    assert funds[0]["code"] == "005827"


# ---------------------------------------------------------------------------
# fetch_fund_holdings — three time-point fields
# ---------------------------------------------------------------------------


def test_fetch_fund_holdings_emits_all_three_time_fields(monkeypatch):
    """Every holding must carry ``report_period``, ``published_at``,
    ``acquired_at`` — collapsing any two would lose audit trail."""
    _install_fake_akshare(
        monkeypatch,
        holdings=[
            {
                "股票代码": "300750",
                "股票名称": "宁德时代",
                "占净值比例": "8.45%",
            },
            {
                "股票代码": "600519",
                "股票名称": "贵州茅台",
                "占净值比例": "5.20%",
            },
        ],
    )
    client = AkshareClient()
    holdings = fetch_fund_holdings(client, "005827", "2024-09-30")
    assert len(holdings) == 2
    for h in holdings:
        assert h["report_period"] == date(2024, 9, 30)
        assert isinstance(h["published_at"], datetime)
        assert isinstance(h["acquired_at"], datetime)
        assert h["published_at"].tzinfo is not None
        assert h["acquired_at"].tzinfo is not None
        # published_at is the AKShare fallback (= acquired_at); document
        # this in code so callers don't mistake it for a real disclosure
        # date.
        assert h["published_at"] == h["acquired_at"]
    # Weight is parsed as a float (no percent sign leaks through).
    assert holdings[0]["weight"] == 8.45
    assert holdings[1]["weight"] == 5.20


def test_fetch_fund_holdings_handles_dash_weights(monkeypatch):
    """``"-"`` weight rows must produce ``None`` (caller skips them),
    not 0.0 (which would be a fake zero-weight position)."""
    _install_fake_akshare(
        monkeypatch,
        holdings=[
            {
                "股票代码": "300750",
                "股票名称": "宁德时代",
                "占净值比例": "8.45%",
            },
            {
                "股票代码": "002594",
                "股票名称": "比亚迪",
                "占净值比例": "--",
            },
        ],
    )
    client = AkshareClient()
    holdings = fetch_fund_holdings(client, "005827", "2024-09-30")
    assert len(holdings) == 2
    assert holdings[0]["weight"] == 8.45
    assert holdings[1]["weight"] is None


def test_fetch_fund_holdings_drops_rows_without_stock_code(monkeypatch):
    """Upstream sometimes returns rows with an empty 股票代码 — these
    must be dropped, not stored as phantom holdings."""
    _install_fake_akshare(
        monkeypatch,
        holdings=[
            {"股票代码": "300750", "股票名称": "宁德时代", "占净值比例": "8.45%"},
            {"股票代码": "", "股票名称": "no code", "占净值比例": "1.00%"},
        ],
    )
    client = AkshareClient()
    holdings = fetch_fund_holdings(client, "005827", "2024-09-30")
    assert len(holdings) == 1
    assert holdings[0]["stock_code"] == "300750"


def test_fetch_fund_holdings_report_period_from_param_not_akshare(monkeypatch):
    """The ``date_str`` parameter is the canonical truth of the report
    period — even if AKShare returns rows with their own date field,
    we use the param so the ingest run is reproducible from the CLI
    alone."""
    _install_fake_akshare(
        monkeypatch,
        holdings=[
            {
                "股票代码": "300750",
                "股票名称": "宁德时代",
                "占净值比例": "8.45%",
                # Even if AKShare leaked a different date field, we
                # must use the CLI-provided date.
                "季报日期": "2024-12-31",
            },
        ],
    )
    client = AkshareClient()
    holdings = fetch_fund_holdings(client, "005827", "2024-09-30")
    assert holdings[0]["report_period"] == date(2024, 9, 30)


# ---------------------------------------------------------------------------
# Ingest orchestration — exercises the full happy path
# ---------------------------------------------------------------------------


def test_ingest_writes_holdings_with_source_akshare(monkeypatch, session):
    """The ingest script writes through InstrumentRepository and stamps
    every HoldingDisclosure with source='akshare'.  This test exercises
    the full code path against a mocked akshare module and a real
    in-memory SQLite session so we know the wiring is correct."""
    from app.scripts.ingest_akshare import ingest, SOURCE_AKSHARE
    from app.models.ledger import HoldingDisclosure, Stock, Fund, FundCompany
    from sqlalchemy import select

    # Pre-seed a fund (skip fund_name_em) and a stock so the ingest
    # doesn't need to create Company / FundCompany from scratch.
    from app.repositories.instruments import InstrumentRepository
    from app.models.ledger import Company

    instruments = InstrumentRepository(session)
    company = instruments.add_company(code="300750.SZ", name="宁德时代", type="listed")
    stock = instruments.add_stock(
        company_id=company.id, code="300750.SZ", name="宁德时代", market="SZ"
    )
    fund_co = instruments.add_fund_company(code="akshare-test-co", name="Test Mgmt")
    fund = instruments.add_fund(
        code="005827", name="Test Fund", fund_type="混合型",
        management_company_id=fund_co.id,
    )
    session.flush()

    # Inject a fake akshare that returns a single holding for our fund.
    _install_fake_akshare(
        monkeypatch,
        holdings=[
            {
                "股票代码": "300750",
                "股票名称": "宁德时代",
                "占净值比例": "8.45%",
            },
        ],
    )
    # fund_name_em returns an empty list so the code path is "explicit
    # fund_codes only".
    monkeypatch.setattr(
        "app.datasources.akshare.adapters.fetch_fund_list",
        lambda _client: [],
    )

    client = AkshareClient()
    stats = ingest(
        session,
        client,
        fund_codes=["005827"],
        report_date="2024-09-30",
    )
    session.flush()

    # Verify the holding landed with the right metadata.
    rows = list(
        session.scalars(
            select(HoldingDisclosure)
            .where(HoldingDisclosure.fund_id == fund.id)
            .where(HoldingDisclosure.source == SOURCE_AKSHARE)
        )
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.stock_id == stock.id
    assert row.report_period == date(2024, 9, 30)
    # Three time-point fields are all populated as datetimes.  Note
    # that SQLite (test env) doesn't preserve tzinfo on round-trip,
    # so we only assert the type — PostgreSQL preserves it in prod.
    assert isinstance(row.published_at, datetime)
    assert isinstance(row.acquired_at, datetime)
    # Weight was parsed and stored as Decimal.
    assert float(row.weight) == 8.45
    # Stats surface the write.
    assert stats.holdings_written == 1
    assert stats.funds_written == 1
    assert stats.errors == ()


def test_ingest_is_idempotent_on_rerun(monkeypatch, session):
    """Re-running ingest with the same (fund, stock, report_period,
    source) tuple must not produce duplicate rows — the
    ``_disclosure_exists`` check keeps re-runs safe even though the
    schema does not enforce uniqueness."""
    from app.scripts.ingest_akshare import ingest, SOURCE_AKSHARE
    from app.models.ledger import HoldingDisclosure
    from app.repositories.instruments import InstrumentRepository
    from sqlalchemy import select, func

    instruments = InstrumentRepository(session)
    company = instruments.add_company(code="300750.SZ", name="宁德时代", type="listed")
    stock = instruments.add_stock(
        company_id=company.id, code="300750.SZ", name="宁德时代", market="SZ"
    )
    fund_co = instruments.add_fund_company(code="akshare-test-co", name="Test Mgmt")
    fund = instruments.add_fund(
        code="005827", name="Test Fund", fund_type="混合型",
        management_company_id=fund_co.id,
    )
    session.flush()

    _install_fake_akshare(
        monkeypatch,
        holdings=[
            {"股票代码": "300750", "股票名称": "宁德时代", "占净值比例": "8.45%"},
        ],
    )
    monkeypatch.setattr(
        "app.datasources.akshare.adapters.fetch_fund_list",
        lambda _client: [],
    )

    client = AkshareClient()
    first = ingest(session, client, fund_codes=["005827"], report_date="2024-09-30")
    second = ingest(session, client, fund_codes=["005827"], report_date="2024-09-30")
    session.flush()

    # First run writes 1; second run dedupes via the existence check.
    assert first.holdings_written == 1
    assert second.holdings_written == 0
    assert second.holdings_skipped_duplicate == 1

    # Exactly one row in the table.
    count = session.scalar(
        select(func.count())
        .select_from(HoldingDisclosure)
        .where(HoldingDisclosure.fund_id == fund.id)
        .where(HoldingDisclosure.source == SOURCE_AKSHARE)
    )
    assert count == 1


def test_ingest_skips_malformed_stock_code(monkeypatch, session):
    """A row whose 股票代码 is not 6 digits must be skipped (and
    tallied in ``skipped_missing_stock``), not crash the whole run."""
    from app.scripts.ingest_akshare import ingest
    from app.repositories.instruments import InstrumentRepository

    instruments = InstrumentRepository(session)
    fund_co = instruments.add_fund_company(code="akshare-test-co", name="Test Mgmt")
    instruments.add_fund(
        code="005827", name="Test Fund", fund_type="混合型",
        management_company_id=fund_co.id,
    )
    session.flush()

    _install_fake_akshare(
        monkeypatch,
        holdings=[
            # Garbage code — should be skipped, not crash.
            {"股票代码": "ABC", "股票名称": "garbage", "占净值比例": "1.0%"},
        ],
    )
    monkeypatch.setattr(
        "app.datasources.akshare.adapters.fetch_fund_list",
        lambda _client: [],
    )
    client = AkshareClient()
    stats = ingest(session, client, fund_codes=["005827"], report_date="2024-09-30")
    assert stats.holdings_skipped_missing_stock == 1
    assert stats.holdings_written == 0
    assert stats.errors == ()
