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


class _FundClient:
    def __init__(self, announcement: str) -> None:
        self._announcement = announcement
        self.calls: list[tuple[str, str]] = []

    def call_tool(self, name: str, arguments: dict, timeout: int = 60) -> str:
        query = arguments["query"]
        self.calls.append((name, query))
        table_markdown = HOLDINGS if name == "FinQuery" else self._announcement
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
    assert disclosure.source_document_version_id is not None
    assert disclosure.provider_record_id is not None
    contract = session.scalar(select(SourceContract))
    provider_record = session.scalar(select(ProviderRecord))
    assert contract is not None and contract.allow_display is True
    assert provider_record is not None
    assert provider_record.provider_name == "gildata"
    assert provider_record.provider_record_id == "005827:2025-06-30"


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
