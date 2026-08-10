"""The live Industrial Foxconn case may only use its predeclared source records."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest


def _payload(table_markdown: str) -> str:
    return json.dumps({"code": 0, "results": [{"table_markdown": table_markdown}]})


class FakeGildataClient:
    def __init__(self, *, report_text: str | None = None) -> None:
        self.report_text = report_text or (
            "盈利预测与投资建议。我们预计2024年归母净利润为251.49亿元。"
            "主要财务数据表显示2023年净利润21040百万元。"
            "AI服务器收入增长、高速交换机升级将改善产品结构。"
        )
        self.calls: list[tuple[str, str]] = []

    def call_tool(self, name: str, arguments: dict[str, str]) -> str:
        query = arguments["query"]
        self.calls.append((name, query))
        if name == "FinancialResearchReport":
            return _payload(
                "报告标题：工业富联(601138)：盈利整体平稳增长 AI业务表现强劲；\n"
                "发布时间：2024-03-25；\n撰写机构：海通证券；\n证券代码：601138；\n"
                f"原文：{self.report_text}"
            )
        if name == "AnnouncementData" and "工业富联" in query:
            return _payload(
                "公告标题：工业富联:富士康工业互联网股份有限公司2024年年度报告；\n"
                "发布时间：2025-04-30；\n证券代码：601138；\n"
                "原文：2024年归属于上市公司股东的净利润232.16亿元。"
                "云计算业务收入3193.77亿元，同比增长64.37%；AI服务器收入同比超过150%；"
                "400G、800G高速交换机同比增长数倍。"
            )
        if name == "AnnouncementData" and "华夏中证5G" in query:
            return _payload(
                "公告标题：华夏中证5G通信主题交易型开放式指数证券投资基金2024年年度报告；\n"
                "发布时间：2025-03-31；\n"
                "原文：本报告为华夏中证5G通信主题交易型开放式指数证券投资基金2024年年度报告。"
            )
        if name == "FinQuery":
            return _payload(
                "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
                "|---|---|---|---|---|---|\n"
                "|华夏中证5G通信主题ETF|515050.OF|2024-12-31|工业富联|601138.SH|5.33|"
            )
        raise AssertionError(f"unexpected provider request: {name} {query}")


def test_loads_only_the_predeclared_report_annual_report_and_fund_position() -> None:
    from app.services.industrial_foxconn_forecast_case import (
        load_industrial_foxconn_sources,
    )

    bundle = load_industrial_foxconn_sources(FakeGildataClient())

    assert bundle.report.title == "工业富联(601138)：盈利整体平稳增长 AI业务表现强劲"
    assert bundle.report.published_at == datetime(2024, 3, 25, tzinfo=timezone.utc)
    assert bundle.expected_profit == Decimal("25149000000")
    assert bundle.baseline_profit == Decimal("21040000000")
    assert bundle.annual_report.published_at == datetime(2025, 4, 30, tzinfo=timezone.utc)
    assert bundle.actual_profit == Decimal("23216000000")
    assert bundle.fund.code == "515050"
    assert bundle.fund.position_weight == Decimal("0.0533")
    assert bundle.fund.report_period == date(2024, 12, 31)


def test_rejects_a_provider_response_without_the_approved_numeric_prediction() -> None:
    from app.services.industrial_foxconn_forecast_case import (
        SourceSelectionError,
        load_industrial_foxconn_sources,
    )

    with pytest.raises(SourceSelectionError, match="251.49"):
        load_industrial_foxconn_sources(FakeGildataClient(report_text="预测不可解析"))
