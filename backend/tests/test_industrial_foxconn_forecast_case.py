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
            if "中证全指" in query:
                return _payload(
                    "|指数名称|指数代码|交易日|收盘价(点)|昨收盘(点)|\n"
                    "|---|---|---|---|---|\n"
                    "|中证全指|000985|2025-04-30|4666.80|4649.04|\n"
                    "|中证全指|000985|2025-04-29|4649.04|4639.90|"
                )
            if "日度行情" in query:
                return _payload(
                    "|股票名称|股票代码|交易日|收盘价（元）|前收盘（元）|\n"
                    "|---|---|---|---|---|\n"
                    "|工业富联|601138|2025-04-30|17.41|17.44|\n"
                    "|工业富联|601138|2025-04-29|17.44|17.36|"
                )
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
    assert bundle.market_window.stock_start_close == Decimal("17.44")
    assert bundle.market_window.stock_end_close == Decimal("17.41")
    assert bundle.market_window.benchmark_start_close == Decimal("4649.04")
    assert bundle.market_window.benchmark_end_close == Decimal("4666.80")
    assert bundle.market_window.relative_return.quantize(Decimal("0.000001")) == Decimal("-0.005540")


def test_rejects_a_provider_response_without_the_approved_numeric_prediction() -> None:
    from app.services.industrial_foxconn_forecast_case import (
        SourceSelectionError,
        load_industrial_foxconn_sources,
    )

    with pytest.raises(SourceSelectionError, match="251.49"):
        load_industrial_foxconn_sources(FakeGildataClient(report_text="预测不可解析"))


def test_materialized_case_publishes_a_bounded_human_conclusion(cmd_session) -> None:
    """A verified single-case verdict must not remain in the generic pending state."""
    from sqlalchemy import select

    from app.models.event_research import EventResearchConclusion
    from app.models.operational import EventResearchLifecycle
    from app.models.research_expression import MarketObservation
    from app.services.industrial_foxconn_forecast_case import (
        load_industrial_foxconn_sources,
    )
    from app.services.live_industrial_foxconn_case import (
        append_live_industrial_foxconn_market_window,
        materialize_live_industrial_foxconn_case,
    )

    result = materialize_live_industrial_foxconn_case(
        cmd_session,
        bundle=load_industrial_foxconn_sources(FakeGildataClient()),
        tenant_id="test-team",
    )

    lifecycle = cmd_session.get(EventResearchLifecycle, result.case_id)
    published = cmd_session.scalar(
        select(EventResearchConclusion)
        .where(EventResearchConclusion.research_case_id == result.case_id)
        .where(EventResearchConclusion.state == "published")
    )

    assert lifecycle is not None and lifecycle.status == "published"
    assert published is not None
    assert "251.49" in published.text
    assert "不据此推断股票价格因果" in published.text
    observation = cmd_session.scalar(
        select(MarketObservation).where(MarketObservation.research_case_id == result.case_id)
    )
    assert observation is not None
    assert observation.relative_return is not None
    assert observation.source_statement_id is not None
    assert observation.window_label == "2025-04-29 收盘至 2025-04-30 收盘"
    replayed = append_live_industrial_foxconn_market_window(
        cmd_session,
        bundle=load_industrial_foxconn_sources(FakeGildataClient()),
    )
    assert replayed.id == observation.id
    assert cmd_session.query(MarketObservation).filter_by(research_case_id=result.case_id).count() == 1


def test_materialized_key_factors_are_linked_to_reviewed_case_theses(cmd_session, monkeypatch) -> None:
    """The market page may only start a factor run when its scope link is explicit."""
    from sqlalchemy import select

    from app.models.ledger import Thesis
    from app.models.research_monitor import CaseMonitorVersion
    from app.models.research_expression import KeyFactor
    from app.services.auto_research import AutoResearchService
    from app.services.industrial_foxconn_forecast_case import (
        load_industrial_foxconn_sources,
    )
    from app.services.live_industrial_foxconn_case import (
        materialize_live_industrial_foxconn_case,
    )

    result = materialize_live_industrial_foxconn_case(
        cmd_session,
        bundle=load_industrial_foxconn_sources(FakeGildataClient()),
        tenant_id="test-team",
    )
    thesis_ids = set(
        cmd_session.scalars(
            select(Thesis.id).where(Thesis.research_case_id == result.case_id)
        )
    )
    factors = list(
        cmd_session.scalars(
            select(KeyFactor).where(KeyFactor.research_case_id == result.case_id)
        )
    )

    assert factors
    assert all(factor.thesis_id in thesis_ids for factor in factors)
    monitor = cmd_session.scalar(
        select(CaseMonitorVersion)
        .where(CaseMonitorVersion.research_case_id == result.case_id)
        .order_by(CaseMonitorVersion.version.desc())
    )
    assert monitor is not None
    assert set(monitor.factor_ids) == {str(factor.thesis_id) for factor in factors}

    def unavailable_client():
        raise RuntimeError("worker model client is unavailable")

    from app.ai.client import LLMClient

    monkeypatch.setattr(LLMClient, "from_env", unavailable_client)
    run = AutoResearchService(cmd_session).start_from_key_factor(
        case_id=result.case_id,
        key_factor_id=factors[0].id,
    )
    assert run.status == "queued"
    assert run.monitor_version_id == monitor.id
