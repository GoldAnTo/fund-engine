"""Gildata fund holdings only become disclosures after report matching."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.ledger import HoldingDisclosure
from app.models.source_governance import ProviderRecord, SourceContract


MATCHED_REPORT = (
    "公告标题：易方达蓝筹精选混合型证券投资基金2025年第2季度报告；\n"
    "发布时间：2025-07-21；\n"
    "原文地址：https://fund.example/005827/2025q2.pdf；\n"
    "原文：本基金2025年第2季度报告。"
)
HOLDINGS = (
    "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
    "|---|---|---|---|---|---|\n"
    "|易方达蓝筹精选混合|005827.OF|2025-06-30|腾讯控股|00700.HK|9.50|"
)
HOLDINGS_2024_Q4 = (
    "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
    "|---|---|---|---|---|---|\n"
    "|华夏中证5G通信主题交易型开放式指数证券投资基金|515050.OF|2024-12-31|工业富联|601138.SH|5.33|"
)
MATCHED_2024_Q4_REPORT = (
    "公告标题：华夏中证5G通信主题交易型开放式指数证券投资基金2024年第4季度报告；\n"
    "发布时间：2025-01-22；\n"
    "原文地址：https://fund.example/515050/2024q4.pdf；\n"
    "原文：本基金2024年第4季度报告。"
)
MATCHED_2024_ANNUAL_REPORT = (
    "公告标题：华夏中证5G通信主题交易型开放式指数证券投资基金2024年年度报告；\n"
    "发布时间：2025-03-31；\n"
    "原文地址：https://fund.example/515050/2024annual.pdf；\n"
    "原文：本基金2024年年度报告。"
)
MATCHED_2024_Q4_LATER_DISCLOSURE = (
    "公告标题：华夏中证5G通信主题交易型开放式指数证券投资基金2024年第4季度公开披露股票持仓明细；\n"
    "发布时间：2025-03-31；\n"
    "原文地址：https://fund.example/515050/2024q4-later.pdf；\n"
    "原文：本基金2024年第4季度公开披露股票持仓明细。"
)


class _FundClient:
    def __init__(self, announcement: str, *, holdings: str = HOLDINGS) -> None:
        self._announcement = announcement
        self._holdings = holdings
        self.calls: list[tuple[str, str]] = []

    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        query = arguments["query"]
        self.calls.append((name, query))
        table_markdown = self._holdings if name == "FinQuery" else self._announcement
        return json.dumps(
            {"code": "0", "results": [{"table_markdown": table_markdown}]},
            ensure_ascii=False,
        )


def test_ingest_writes_partial_disclosure_only_after_exact_fund_report_match(session):
    from app.scripts.ingest_gildata_fund_holdings import ingest

    result = ingest(
        session,
        _FundClient(MATCHED_REPORT),
        fund_codes=["005827"],
        report_period=date(2025, 6, 30),
        permissions={"display": True},
    )

    assert result.matched_reports == 1
    assert result.pending_match_rows == 0
    assert result.holding_disclosures_written == 1
    disclosure = session.scalar(select(HoldingDisclosure))
    assert disclosure is not None
    assert disclosure.report_period == date(2025, 6, 30)
    assert disclosure.weight == Decimal("0.095")
    assert disclosure.published_at.date() == date(2025, 7, 21)
    assert disclosure.coverage_status == "partial"
    assert disclosure.filing_kind == "quarterly"
    assert disclosure.source_document_version_id is not None
    assert disclosure.provider_record_id is not None
    contract = session.scalar(select(SourceContract))
    provider_record = session.scalar(select(ProviderRecord))
    assert contract is not None and contract.allow_display is True
    assert provider_record is not None
    assert provider_record.provider_name == "gildata"
    assert provider_record.provider_record_id == "005827:2025-06-30"


def test_ingest_annual_report_supersedes_frozen_quarterly_disclosure(session):
    from app.scripts.ingest_gildata_fund_holdings import ingest

    quarterly_result = ingest(
        session,
        _FundClient(MATCHED_2024_Q4_REPORT, holdings=HOLDINGS_2024_Q4),
        fund_codes=["515050"],
        report_period=date(2024, 12, 31),
        permissions={"display": True},
    )
    assert quarterly_result.holding_disclosures_written == 1
    quarterly = session.scalar(select(HoldingDisclosure))
    assert quarterly is not None
    assert quarterly.filing_kind == "quarterly"

    annual_result = ingest(
        session,
        _FundClient(MATCHED_2024_ANNUAL_REPORT, holdings=HOLDINGS_2024_Q4),
        fund_codes=["515050"],
        report_period=date(2024, 12, 31),
        permissions={"display": True},
    )

    assert annual_result.holding_disclosures_written == 1
    disclosures = list(session.scalars(select(HoldingDisclosure)))
    assert len(disclosures) == 2
    annual = next(item for item in disclosures if item.filing_kind == "annual")
    assert annual.supersedes_disclosure_id == quarterly.id


def test_ingest_later_quarterly_disclosure_supersedes_earlier_quarterly_disclosure(session):
    from app.scripts.ingest_gildata_fund_holdings import ingest

    first_result = ingest(
        session,
        _FundClient(MATCHED_2024_Q4_REPORT, holdings=HOLDINGS_2024_Q4),
        fund_codes=["515050"],
        report_period=date(2024, 12, 31),
        permissions={"display": True},
    )
    assert first_result.holding_disclosures_written == 1
    first = session.scalar(select(HoldingDisclosure))
    assert first is not None

    later_result = ingest(
        session,
        _FundClient(MATCHED_2024_Q4_LATER_DISCLOSURE, holdings=HOLDINGS_2024_Q4),
        fund_codes=["515050"],
        report_period=date(2024, 12, 31),
        permissions={"display": True},
    )

    assert later_result.holding_disclosures_written == 1
    disclosures = list(session.scalars(select(HoldingDisclosure)))
    later = max(disclosures, key=lambda item: item.published_at)
    assert later.filing_kind == "quarterly"
    assert later.supersedes_disclosure_id == first.id


def test_ingest_keeps_unmatched_holding_out_of_formal_exposure(session):
    from app.scripts.ingest_gildata_fund_holdings import ingest

    result = ingest(
        session,
        _FundClient(MATCHED_REPORT.replace("2025年第2季度", "2025年第1季度")),
        fund_codes=["005827"],
        report_period=date(2025, 6, 30),
        permissions={"display": True},
    )

    assert result.matched_reports == 0
    assert result.pending_match_rows == 1
    assert result.holding_disclosures_written == 0
    assert session.scalar(select(HoldingDisclosure)) is None


def test_ingest_keeps_matched_holding_out_of_formal_exposure_without_display_permission(session):
    from app.scripts.ingest_gildata_fund_holdings import ingest

    result = ingest(
        session,
        _FundClient(MATCHED_REPORT),
        fund_codes=["005827"],
        report_period=date(2025, 6, 30),
        permissions={"display": False},
    )

    assert result.matched_reports == 1
    assert result.pending_permission_rows == 1
    assert result.holding_disclosures_written == 0
    assert session.scalar(select(HoldingDisclosure)) is None


def test_cli_arguments_require_explicit_funds_and_permission_declaration():
    from app.scripts.ingest_gildata_fund_holdings import _parse_args

    args = _parse_args(
        [
            "--fund-codes",
            "005827,110011.OF",
            "--report-period",
            "2025-06-30",
            "--allow-display",
            "--dry-run",
        ]
    )

    assert args.fund_codes == ["005827", "110011.OF"]
    assert args.report_period == date(2025, 6, 30)
    assert args.allow_display is True
    assert args.dry_run is True
