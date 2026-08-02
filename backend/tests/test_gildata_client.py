"""Tests for the Gildata MCP client and adapters.

No real Gildata API is contacted: client HTTP is mocked with
``httpx.MockTransport``, and adapter tests use a tiny fake client that returns
canned ``call_tool`` text strings.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.datasources.gildata import adapters
from app.datasources.gildata.client import GildataMCPClient, GildataMCPError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _envelope(inner_text: str, *, rpc_id: int = 1) -> dict:
    """Wrap an inner content text string in the JSON-RPC result envelope."""
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {"content": [{"type": "text", "text": inner_text}]},
    }


def _mock_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class _FakeClient:
    """Fake client returning canned ``call_tool`` text strings, no network."""

    def __init__(self, research_results, announcement_results, quote_results,
                 news_results=()):
        # research_results: queue of result-lists (one per FinancialResearchReport call)
        self._research = list(research_results)
        self._announcement = announcement_results
        self._quote = quote_results
        self._news = list(news_results)
        self.calls: list[tuple[str, dict]] = []

    def call_tool(self, name, arguments, timeout=60):
        self.calls.append((name, dict(arguments)))
        if name == "FinancialResearchReport":
            results = self._research.pop(0) if self._research else []
            return json.dumps({"code": "0", "results": results}, ensure_ascii=False)
        if name == "AnnouncementData":
            return json.dumps({"code": "0", "results": self._announcement}, ensure_ascii=False)
        if name == "NewsDataQuery":
            return json.dumps({"code": "0", "results": self._news}, ensure_ascii=False)
        if name == "FinQuery":
            return json.dumps({"code": "0", "results": self._quote}, ensure_ascii=False)
        raise AssertionError(f"unexpected tool {name!r}")


RESEARCH_MD = (
    "报告标题：寒武纪(688256)2025年年报点评；\n"
    "撰写时间：2026-05-05；\n"
    "发布时间：2026-05-06；\n"
    "撰写机构：西部证券；\n"
    "作者：郑宏达；\n"
    "原文：寒武纪2025年实现营业收入64.97亿元，算力芯片出货同比增长，维持买入评级。"
)

QUOTE_MD = (
    "|股票名称|股票代码|最新价|市盈率TTM|市净率|总市值|\n"
    "|---|---|---|---|---|---|\n"
    "|寒武纪|688256|850.00|380.5|12.3|3.56e11|"
)

ANNOUNCEMENT_MD = (
    "|公告标题|公告日期|股票代码|公告内容|\n"
    "|---|---|---|---|\n"
    "|寒武纪定增预案|2026-03-15|688256|本次定增募资49.8亿元投向算力芯片项目。|"
)

NEWS_MD = (
    "报告标题：寒武纪获得发明专利授权；\n"
    "撰写时间：2026-08-01 03:40:45；\n"
    "新闻舆情来源：证券之星；\n"
    "原文：寒武纪获得发明专利授权，涉及卷积运算处理电路。"
)


# ---------------------------------------------------------------------------
# MCP client
# ---------------------------------------------------------------------------


def test_from_env_requires_token(monkeypatch):
    monkeypatch.delenv("GILDATA_TOKEN", raising=False)
    with pytest.raises(GildataMCPError):
        GildataMCPClient.from_env()


def test_empty_token_rejected():
    with pytest.raises(GildataMCPError):
        GildataMCPClient(token="")


def test_call_tool_returns_content_text(monkeypatch):
    inner = {
        "code": "0",
        "results": [{"api_name": "A股实时行情", "table_markdown": "|a|b|"}],
    }
    inner_text = json.dumps(inner, ensure_ascii=False)
    seen: dict = {}

    def handler(request):
        body = json.loads(request.content)
        seen["body"] = body
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json=_envelope(inner_text))

    client = GildataMCPClient(token="tok", transport=_mock_transport(handler))
    text = client.call_tool("FinQuery", {"query": "寒武纪"})

    # call_tool returns the raw content[0].text string (not parsed JSON).
    assert text == inner_text
    assert json.loads(text) == inner
    # token travels in the URL query string, not the body.
    assert "token=tok" in seen["url"]
    assert seen["body"]["method"] == "tools/call"
    assert seen["body"]["params"]["name"] == "FinQuery"
    assert seen["body"]["params"]["arguments"]["query"] == "寒武纪"
    assert seen["headers"]["content-type"] == "application/json"
    assert "text/event-stream" in seen["headers"]["accept"]
    client.close()


def test_call_tool_raises_on_jsonrpc_error():
    outer = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}}

    def handler(request):
        return httpx.Response(200, json=outer)

    client = GildataMCPClient(token="tok", transport=_mock_transport(handler))
    with pytest.raises(GildataMCPError):
        client.call_tool("FinQuery", {"query": "x"})
    client.close()


def test_call_tool_raises_on_non_200():
    def handler(request):
        return httpx.Response(500, text="server boom")

    client = GildataMCPClient(token="tok", transport=_mock_transport(handler))
    with pytest.raises(GildataMCPError):
        client.call_tool("FinQuery", {"query": "x"})
    client.close()


def test_list_tools(monkeypatch):
    tools = [{"name": "FinQuery"}, {"name": "FinancialResearchReport"}]

    def handler(request):
        body = json.loads(request.content)
        assert body["method"] == "tools/list"
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
        )

    client = GildataMCPClient(token="tok", transport=_mock_transport(handler))
    assert client.list_tools() == tools
    client.close()


# ---------------------------------------------------------------------------
# Adapters: parse_content
# ---------------------------------------------------------------------------


def test_parse_content_json():
    text = json.dumps(
        {
            "code": "0",
            "results": [
                {"api_name": "A股实时行情", "table_markdown": "|股票名称|股票代码|"}
            ],
        },
        ensure_ascii=False,
    )
    results = adapters.parse_content(text)
    assert len(results) == 1
    assert results[0]["api_name"] == "A股实时行情"


def test_parse_content_invalid_returns_empty():
    assert adapters.parse_content("") == []
    assert adapters.parse_content("not json {") == []
    assert adapters.parse_content(json.dumps({"code": "0"})) == []


# ---------------------------------------------------------------------------
# Adapters: fetch_research_report / fetch_quote
# ---------------------------------------------------------------------------


def test_parse_research_report():
    inner = {"code": "0", "results": [{"api_name": "研报库", "table_markdown": RESEARCH_MD}]}
    client = _FakeClient([[{"table_markdown": RESEARCH_MD}]], [], [])

    reports = adapters.fetch_research_report(client, "寒武纪")

    assert len(reports) == 1
    r = reports[0]
    assert r["title"] == "寒武纪(688256)2025年年报点评"
    assert r["publish_date"] == "2026-05-06"
    assert r["org"] == "西部证券"
    assert r["author"] == "郑宏达"
    assert "64.97亿元" in r["content"]
    # sec_code extracted from the title when no explicit 证券代码 field exists.
    assert r["sec_code"] == "688256"
    assert client.calls[0][0] == "FinancialResearchReport"


def test_fetch_quote():
    inner = {"code": "0", "results": [{"api_name": "A股实时行情", "table_markdown": QUOTE_MD}]}
    client = _FakeClient([], [], [{"table_markdown": QUOTE_MD}])

    quotes = adapters.fetch_quote(client, "寒武纪最新股价行情")

    assert len(quotes) == 1
    q = quotes[0]
    assert q["stock_name"] == "寒武纪"
    assert q["stock_code"] == "688256"
    assert q["latest_price"] == "850.00"
    assert q["pe_ttm"] == "380.5"
    assert q["pb"] == "12.3"
    assert q["total_mv"] == "3.56e11"
    assert client.calls[0][0] == "FinQuery"


def test_fetch_quote_empty_when_no_results():
    client = _FakeClient([], [], [])
    assert adapters.fetch_quote(client, "x") == []


# ---------------------------------------------------------------------------
# Ingest script (uses the in-memory session fixture, mocked client)
# ---------------------------------------------------------------------------


def _make_client():
    report1 = {"table_markdown": RESEARCH_MD}
    report2 = {
        "table_markdown": (
            "报告标题：工业富联(601138)AI服务器收入点评；\n"
            "发布时间：2026-04-20；\n"
            "撰写机构：中信证券；\n"
            "作者：李五；\n"
            "原文：工业富联AI服务器收入高速增长。"
        )
    }
    announcement = {"table_markdown": ANNOUNCEMENT_MD}
    news = {"table_markdown": NEWS_MD}
    quote = {"table_markdown": QUOTE_MD}
    # Two research queries -> one report list each.
    return _FakeClient([[report1], [report2]], [announcement], [quote],
                       news_results=[news])


def test_ingest_freezes_documents_and_valuations(session):
    from app.scripts.ingest_real_data import ingest

    summary = ingest(session, _make_client())

    assert summary["research_reports"] == 2
    assert summary["announcements"] == 1
    assert summary["news"] == 1
    assert summary["spans"] == 4
    assert summary["valuations_written"] == 3  # PE(TTM), PB, 总市值
    assert summary["valuations_skipped"] == 0
    assert summary["stock_id"] is not None

    # A Stock should have been created for 688256.
    from sqlalchemy import select

    from app.models.ledger import Stock

    stock = session.scalar(select(Stock).where(Stock.code == "688256.SH"))
    assert stock is not None
    assert stock.name == "寒武纪"


def test_ingest_is_idempotent(session):
    from sqlalchemy import select

    from app.models.ledger import DocumentVersion, ValuationSnapshot

    from app.scripts.ingest_real_data import ingest

    first = ingest(session, _make_client())
    session.flush()
    docs_after_first = len(list(session.scalars(select(DocumentVersion))))
    vals_after_first = len(list(session.scalars(select(ValuationSnapshot))))
    assert first["valuations_written"] == 3

    second = ingest(session, _make_client())
    session.flush()
    docs_after_second = len(list(session.scalars(select(DocumentVersion))))
    vals_after_second = len(list(session.scalars(select(ValuationSnapshot))))

    # Document content-hash dedup: no new document versions on re-run.
    assert docs_after_second == docs_after_first
    # Valuation guard: second run skips all three metrics.
    assert second["valuations_written"] == 0
    assert second["valuations_skipped"] == 3
    assert vals_after_second == vals_after_first


def test_ingest_decimal_parsing():
    from app.scripts.ingest_real_data import _parse_decimal, _parse_date

    assert _parse_decimal("850.00") == Decimal("850.00")
    assert _parse_decimal("3.56e11") == Decimal("3.56E11")
    assert _parse_decimal("--") is None
    assert _parse_decimal("") is None
    assert _parse_decimal("1,234.5") == Decimal("1234.5")
    assert _parse_date("2026-05-06") is not None
    assert _parse_date("not-a-date") is None
