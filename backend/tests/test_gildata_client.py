"""Tests for the Gildata MCP client and adapters.

No real Gildata API is contacted: client HTTP is mocked with
``httpx.MockTransport``, and adapter tests use a tiny fake client that returns
canned ``call_tool`` text strings.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.datasources.gildata import adapters
from app.datasources.gildata.client import (
    GILDATA_RESPONSE_ERROR_MESSAGE,
    GildataMCPClient,
    GildataMCPError,
)
from app.datasources.gildata.governance import GildataEvidenceRights


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

    def __init__(
        self,
        research_results,
        announcement_results,
        quote_results,
        news_results=(),
        fund_holding_results=(),
        macro_results=(),
    ):
        # research_results: queue of result-lists (one per FinancialResearchReport call)
        self._research = list(research_results)
        self._announcement = announcement_results
        self._quote = quote_results
        self._news = list(news_results)
        self._fund_holdings = list(fund_holding_results)
        self._macro = list(macro_results)
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
            results = self._fund_holdings if "持仓" in arguments.get("query", "") else self._quote
            return json.dumps({"code": "0", "results": results}, ensure_ascii=False)
        if name == "MacroIndustryData":
            return json.dumps(
                {"code": "0", "results": self._macro}, ensure_ascii=False
            )
        raise AssertionError(f"unexpected tool {name!r}")


RESEARCH_BODY = (
    "寒武纪2025年实现营业收入64.97亿元，算力芯片出货同比增长，维持买入评级。"
)

RESEARCH_MD = (
    "报告标题：寒武纪(688256)2025年年报点评；\n"
    "撰写时间：2026-05-05；\n"
    "发布时间：2026-05-06；\n"
    "撰写机构：西部证券；\n"
    "作者：郑宏达；\n"
    f"原文：{RESEARCH_BODY}"
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

FUND_REPORT_ANNOUNCEMENT_MD = (
    "公告标题：易方达蓝筹精选混合型证券投资基金2025年第2季度报告；\n"
    "发布时间：2025-07-21；\n"
    "原文地址：https://fund.example/005827/2025q2.pdf；\n"
    "原文：本基金2025年第2季度报告。"
)

FUND_HOLDING_MD = (
    "|基金简称|基金代码|报告期|股票简称|股票代码|持仓市值占资产净值比(%)|\n"
    "|---|---|---|---|---|---|\n"
    "|易方达蓝筹精选混合|005827.OF|2025-06-30|腾讯控股|00700.HK|9.50|"
)

NEWS_MD = (
    "报告标题：寒武纪获得发明专利授权；\n"
    "撰写时间：2026-08-01 03:40:45；\n"
    "新闻舆情来源：证券之星；\n"
    "原文：寒武纪获得发明专利授权，涉及卷积运算处理电路。"
)

MACRO_MD = (
    "|指标代码|指标名称|频率|单位|值|日期|数据来源|\n"
    "|---|---|---|---|---|---|---|\n"
    "|M001|碳酸锂价格|日|元/吨|100000|2026-09-01|测试源|\n"
    "|M001|碳酸锂价格|日|元/吨|90000|2026-09-02|测试源|\n"
    "|M002|锂盐产量|月|吨|1000|2026-08-01|测试源|"
)


# ---------------------------------------------------------------------------
# Evidence governance
# ---------------------------------------------------------------------------


def test_gildata_rights_default_to_false(monkeypatch):
    monkeypatch.delenv("GILDATA_ALLOW_AI_PROCESSING", raising=False)
    monkeypatch.delenv("GILDATA_ALLOW_DISPLAY", raising=False)

    rights = GildataEvidenceRights.from_env()

    assert rights.allow_ai_processing is False
    assert rights.allow_display is False
    assert rights.formal_evidence_allowed is False


@pytest.mark.parametrize("value", ["1", "yes", "on", " true ", "false"])
def test_gildata_rights_accept_only_literal_true(monkeypatch, value):
    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", value)
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "TRUE")

    rights = GildataEvidenceRights.from_env()

    assert rights.allow_ai_processing is False
    assert rights.allow_display is True
    assert rights.formal_evidence_allowed is False


def test_gildata_rights_allow_formal_evidence_when_both_are_literal_true(
    monkeypatch,
):
    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "TRUE")

    rights = GildataEvidenceRights.from_env()

    assert rights.allow_ai_processing is True
    assert rights.allow_display is True
    assert rights.formal_evidence_allowed is True


def test_gildata_token_does_not_grant_evidence_rights(monkeypatch):
    monkeypatch.setenv("GILDATA_TOKEN", "configured-token")
    monkeypatch.delenv("GILDATA_ALLOW_AI_PROCESSING", raising=False)
    monkeypatch.delenv("GILDATA_ALLOW_DISPLAY", raising=False)

    rights = GildataEvidenceRights.from_env()

    assert rights.allow_ai_processing is False
    assert rights.allow_display is False
    assert rights.formal_evidence_allowed is False


@pytest.mark.parametrize(
    ("allow_ai_processing", "allow_display"),
    [
        ("false", False),
        (False, "false"),
        (1, False),
        (False, 1),
    ],
)
def test_gildata_rights_reject_non_boolean_values(
    allow_ai_processing,
    allow_display,
):
    with pytest.raises(TypeError):
        GildataEvidenceRights(
            allow_ai_processing=allow_ai_processing,
            allow_display=allow_display,
        )


def test_gildata_rights_are_immutable():
    rights = GildataEvidenceRights(
        allow_ai_processing=False,
        allow_display=False,
    )

    with pytest.raises(FrozenInstanceError):
        rights.allow_ai_processing = True


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
    outer = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32600, "message": "bad sentinel-secret"},
    }

    def handler(request):
        return httpx.Response(200, json=outer)

    client = GildataMCPClient(token="tok", transport=_mock_transport(handler))
    with pytest.raises(GildataMCPError) as exc_info:
        client.call_tool("FinQuery", {"query": "x"})
    assert str(exc_info.value) == "Gildata provider returned an invalid response"
    assert "sentinel-secret" not in str(exc_info.value)
    client.close()


def test_call_tool_raises_on_non_200():
    def handler(request):
        return httpx.Response(500, text="server boom sentinel-secret")

    client = GildataMCPClient(token="tok", transport=_mock_transport(handler))
    with pytest.raises(GildataMCPError) as exc_info:
        client.call_tool("FinQuery", {"query": "x"})
    assert str(exc_info.value) == "Gildata provider request failed"
    assert "sentinel-secret" not in str(exc_info.value)
    client.close()


def test_transport_error_does_not_echo_token_bearing_url():
    def handler(request):
        raise httpx.ConnectError(
            f"failed request {request.url} sentinel-secret", request=request
        )

    client = GildataMCPClient(
        token="token-sentinel", transport=_mock_transport(handler)
    )
    with pytest.raises(GildataMCPError) as exc_info:
        client.call_tool("FinQuery", {"query": "x"})

    assert str(exc_info.value) == "Gildata provider request failed"
    assert "token-sentinel" not in str(exc_info.value)
    assert "sentinel-secret" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
    client.close()


def test_transport_error_retries_once_before_succeeding():
    attempts = 0
    inner_text = json.dumps({"code": "0", "results": []})

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("transient disconnect", request=request)
        return httpx.Response(200, json=_envelope(inner_text))

    client = GildataMCPClient(
        token="tok",
        max_attempts=2,
        transport=_mock_transport(handler),
    )

    assert client.call_tool("FinQuery", {"query": "x"}) == inner_text
    assert attempts == 2
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


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        "[]",
        json.dumps({"code": "500", "results": []}),
        json.dumps({"code": "0", "results": [1]}),
    ],
)
def test_strict_content_parser_normalizes_malformed_provider_payload(content):
    with pytest.raises(GildataMCPError) as exc_info:
        adapters.parse_content_strict(content)

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"table_markdown": {}},
        {"table_markdown": ""},
        {"table_markdown": "totally malformed"},
    ],
)
@pytest.mark.parametrize(
    "fetcher",
    [adapters.fetch_fund_stock_holdings, adapters.fetch_announcement],
)
def test_live_fund_adapters_reject_malformed_table_payload(result, fetcher):
    class Client:
        def call_tool(self, name, arguments, timeout=60):
            return json.dumps({"code": "0", "results": [result]})

    with pytest.raises(GildataMCPError) as exc_info:
        fetcher(Client(), "query")

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


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


def test_fetch_fund_holdings_preserves_report_period_without_inventing_publish_date():
    client = _FakeClient(
        [], [], [], fund_holding_results=[{"table_markdown": FUND_HOLDING_MD}]
    )

    holdings = adapters.fetch_fund_stock_holdings(client, "查询基金005827最近一期公开披露的股票持仓明细")

    assert holdings == [{
        "fund_name": "易方达蓝筹精选混合",
        "fund_code": "005827.OF",
        "report_period": "2025-06-30",
        "stock_name": "腾讯控股",
        "stock_code": "00700.HK",
        "weight": "9.50",
    }]
    assert client.calls[0][0] == "FinQuery"


def test_fetch_fund_report_announcement_preserves_exact_publication_locator():
    client = _FakeClient(
        [], [{"table_markdown": FUND_REPORT_ANNOUNCEMENT_MD}], []
    )

    announcements = adapters.fetch_announcement(client, "易方达蓝筹精选混合 005827 2025年第二季度报告")

    assert announcements == [{
        "title": "易方达蓝筹精选混合型证券投资基金2025年第2季度报告",
        "publish_date": "2025-07-21",
        "stock_code": "",
        "sec_name": "",
        "content": "本基金2025年第2季度报告。",
        "source_url": "https://fund.example/005827/2025q2.pdf",
    }]


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


def _make_macro_client() -> _FakeClient:
    return _FakeClient(
        [],
        [],
        [],
        macro_results=[{"table_markdown": MACRO_MD}],
    )


def _make_duplicate_research_client() -> _FakeClient:
    report = {"table_markdown": RESEARCH_MD}
    return _FakeClient([[report, report], [report]], [], [])


def _make_research_only_client(*markdowns: str) -> _FakeClient:
    reports = [{"table_markdown": markdown} for markdown in markdowns]
    return _FakeClient([reports], [], [])


def _ingest_research_only(session, *, query: str, client: _FakeClient):
    from app.scripts.ingest_real_data import ingest

    return ingest(
        session,
        client,
        research_queries=[query],
        announcement_query="no-announcement",
        news_query="no-news",
        quote_query="no-quote",
    )


def _macro_client_for_rows(rows: list[dict[str, str]]) -> _FakeClient:
    headers = (
        "metric_code",
        "metric_name",
        "frequency",
        "unit",
        "value",
        "date",
        "source",
    )
    provider_headers = (
        "指标代码",
        "指标名称",
        "频率",
        "单位",
        "值",
        "日期",
        "数据来源",
    )
    lines = [
        f"|{'|'.join(provider_headers)}|",
        f"|{'|'.join('---' for _ in provider_headers)}|",
    ]
    lines.extend(
        f"|{'|'.join(str(row.get(field, '')) for field in headers)}|"
        for row in rows
    )
    return _FakeClient(
        [],
        [],
        [],
        macro_results=[{"table_markdown": "\n".join(lines)}],
    )


def _macro_window_rows() -> list[dict[str, str]]:
    return [
        {
            "metric_code": "M-WINDOW",
            "metric_name": "窗口指标",
            "frequency": "日",
            "unit": "点",
            "value": "9999" if day == 1 else str(day),
            "date": f"2026-08-{day:02d}",
            "source": "测试源",
        }
        for day in range(1, 26)
    ]


def _seed_polluted_research_document(
    session,
    *,
    span_locator: dict | None = None,
    **overrides,
):
    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import _provider_uri

    raw = RESEARCH_BODY.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    now = datetime.now(timezone.utc)
    document_values = {
        "content_sha256": digest,
        "source_url": _provider_uri("research-report", raw),
        "natural_key": f"gildata:{digest[:23]}",
        "published_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
        "available_at": now,
        "acquired_at": now,
        "parser_version": "gildata-mcp-1",
        "supersedes_id": None,
        "title": "寒武纪(688256)2025年年报点评",
        "byte_size": len(raw),
        "language": None,
        "parse_state": "success",
        "source_authority": "licensed_research",
        "supplements_document_version_id": None,
        "claimed_page_reference": None,
    }
    document_values.update(overrides)
    document = DocumentVersion(
        **document_values,
    )
    session.add(document)
    session.flush()
    session.add(
        SourceSpan(
            document_version_id=document.id,
            locator=span_locator
            or {
                "kind": "research_report",
                "title": "寒武纪(688256)2025年年报点评",
                "org": "西部证券",
                "publish_date": "2026-05-06",
                "sec_code": "688256",
                "page": 1,
                "paragraph": 1,
                "parser": "gildata-mcp-1",
            },
            verbatim_text=RESEARCH_BODY,
            text_sha256=digest,
        )
    )
    session.flush()
    document_id = document.id
    session.expire_all()
    return session.get(DocumentVersion, document_id)


def test_ingest_freezes_documents_and_valuations(session):
    from app.scripts.ingest_real_data import ingest

    summary = ingest(session, _make_client())

    assert summary["research_reports"] == 2
    assert summary["research_reports_reused"] == 0
    assert summary["announcements"] == 1
    assert summary["announcements_reused"] == 0
    assert summary["news"] == 1
    assert summary["news_reused"] == 0
    assert summary["spans"] == 4
    assert summary["spans_reused"] == 0
    assert summary["valuations_written"] == 3  # PE(TTM), PB, 总市值
    assert summary["valuations_skipped"] == 0
    assert summary["stock_id"] is not None
    assert summary["quote_identity_verified"] is True

    # A Stock should have been created for 688256.
    from sqlalchemy import select

    from app.models.ledger import Stock

    stock = session.scalar(select(Stock).where(Stock.code == "688256.SH"))
    assert stock is not None
    assert stock.name == "寒武纪"


def test_gildata_ingest_records_compatible_contract_and_record_per_document(
    session,
    monkeypatch,
):
    from app.models.ledger import DocumentVersion
    from app.models.source_governance import ProviderRecord, SourceContract
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import select

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")

    ingest(session, _make_client())
    session.flush()
    ingest(session, _make_client())
    session.flush()

    documents = list(
        session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.source_url.like("gildata://%")
            )
        )
    )
    document_ids = {document.id for document in documents}
    contracts = list(
        session.scalars(
            select(SourceContract).where(
                SourceContract.document_version_id.in_(document_ids)
            )
        )
    )
    records = list(
        session.scalars(
            select(ProviderRecord).where(
                ProviderRecord.document_version_id.in_(document_ids)
            )
        )
    )

    assert len(contracts) == len(records) == len(documents) == 4
    assert len({contract.document_version_id for contract in contracts}) == 4
    assert len({record.document_version_id for record in records}) == 4
    assert all(
        contract.source_type == contract.research_source_type == "licensed_provider"
        for contract in contracts
    )
    assert all(contract.provider_or_tenant == "gildata" for contract in contracts)
    assert all(contract.allow_ai_processing for contract in contracts)
    assert all(contract.allow_display for contract in contracts)
    assert all(not contract.allow_export for contract in contracts)
    assert all(not contract.allow_api for contract in contracts)
    assert all(
        contract.contract_version == "gildata-local-rights-v1"
        for contract in contracts
    )
    assert all(
        contract.downstream_restrictions == ["仅限当前 Case 研究与人工审核"]
        for contract in contracts
    )
    assert all(
        contract.declared_by == "system:gildata-ingest" for contract in contracts
    )

    records_by_document = {
        record.document_version_id: record for record in records
    }
    contracts_by_document = {
        contract.document_version_id: contract for contract in contracts
    }
    expected_query_sha256 = {
        "research-report": {
            hashlib.sha256(query.encode("utf-8")).hexdigest()
            for query in (
                "寒武纪算力芯片出货及估值研报观点",
                "工业富联AI服务器收入研报",
            )
        },
        "announcement": {
            hashlib.sha256("寒武纪近期公告".encode("utf-8")).hexdigest()
        },
        "news": {
            hashlib.sha256("寒武纪 AI算力芯片 最新消息".encode("utf-8")).hexdigest()
        },
    }
    for document in documents:
        source_kind = document.source_url.split("/", 3)[2]
        contract = contracts_by_document[document.id]
        record = records_by_document[document.id]
        assert record.provider_name == "gildata"
        assert record.provider_record_id == (
            f"{source_kind}:{document.content_sha256}"
        )
        assert record.request_scope["source_kind"] == source_kind
        assert (
            record.request_scope["query_sha256"]
            in expected_query_sha256[source_kind]
        )
        assert record.retrieval_reference == document.source_url
        assert record.content_sha256 == document.content_sha256
        assert record.contract_version == "gildata-local-rights-v1"
        assert contract.intake_metadata["provider_name"] == "gildata"
        assert contract.intake_metadata["provider_record_id"] == (
            record.provider_record_id
        )
        assert (
            contract.intake_metadata["retrieval_reference"] == document.source_url
        )
        assert contract.intake_metadata["request_scope"] == record.request_scope
        assert contract.intake_metadata["permissions"] == {
            "ai_processing": True,
            "display": True,
            "export": False,
            "api": False,
        }


def test_gildata_rights_are_resolved_once_and_false_rights_are_still_recorded(
    session,
    monkeypatch,
):
    from app.models.source_governance import ProviderRecord, SourceContract
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import select

    calls = 0

    def resolve_rights_once(cls):
        nonlocal calls
        calls += 1
        return cls(allow_ai_processing=False, allow_display=False)

    monkeypatch.setattr(
        GildataEvidenceRights,
        "from_env",
        classmethod(resolve_rights_once),
    )

    ingest(session, _make_client())
    session.flush()

    contracts = list(session.scalars(select(SourceContract)))
    records = list(session.scalars(select(ProviderRecord)))
    assert calls == 1
    assert len(contracts) == len(records) == 4
    assert all(not contract.allow_ai_processing for contract in contracts)
    assert all(not contract.allow_display for contract in contracts)


def test_gildata_replay_with_a_different_query_preserves_initial_request_scope(
    session,
    monkeypatch,
):
    from app.models.source_governance import ProviderRecord, SourceContract
    from sqlalchemy import select

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    first_query = "initial-licensed-query"
    second_query = "replay-query-with-identical-result"
    first = _ingest_research_only(
        session,
        query=first_query,
        client=_make_research_only_client(RESEARCH_MD),
    )
    session.flush()
    initial_record = session.scalar(select(ProviderRecord))
    initial_contract = session.scalar(select(SourceContract))
    assert initial_record is not None
    assert initial_contract is not None
    initial_record_id = initial_record.id
    initial_contract_id = initial_contract.id
    initial_scope = dict(initial_record.request_scope)

    second = _ingest_research_only(
        session,
        query=second_query,
        client=_make_research_only_client(RESEARCH_MD),
    )
    session.flush()

    records = list(session.scalars(select(ProviderRecord)))
    contracts = list(session.scalars(select(SourceContract)))
    assert first["research_reports"] == 1
    assert second["research_reports_reused"] == 1
    assert len(records) == len(contracts) == 1
    assert records[0].id == initial_record_id
    assert contracts[0].id == initial_contract_id
    assert records[0].request_scope == initial_scope
    assert initial_scope["source_kind"] == "research-report"
    assert initial_scope["query_sha256"] == hashlib.sha256(
        first_query.encode("utf-8")
    ).hexdigest()
    assert len(initial_scope["frozen_metadata_sha256"]) == 64


@pytest.mark.parametrize(
    ("provider_name", "replay_scope", "compatible"),
    [
        (
            "GILDATA",
            {
                "source_kind": "research-report",
                "query_sha256": "c" * 64,
                "dataset": "licensed-research",
            },
            True,
        ),
        (
            "gildata",
            {
                "source_kind": "research-report",
                "query_sha256": "c" * 64,
                "dataset": "changed-dataset",
            },
            False,
        ),
        (
            "other-provider",
            {
                "source_kind": "research-report",
                "query_sha256": "c" * 64,
                "dataset": "licensed-research",
            },
            False,
        ),
    ],
)
def test_query_hash_replay_exception_is_scoped_to_gildata_and_stable_fields(
    provider_name,
    replay_scope,
    compatible,
):
    from types import SimpleNamespace

    from app.services.source_governance import (
        SourceGovernanceCompatibilityError,
        SourceGovernanceService,
    )

    digest = "a" * 64
    stored_scope = {
        "source_kind": "research-report",
        "query_sha256": "b" * 64,
        "dataset": "licensed-research",
    }
    record = SimpleNamespace(
        provider_name=provider_name,
        provider_record_id=f"research-report:{digest}",
        request_scope=stored_scope,
        retrieval_reference=f"gildata://research-report/{digest}",
        content_sha256=digest,
        contract_version="gildata-local-rights-v1",
    )

    class OneProviderRecordSession:
        def scalars(self, statement):
            del statement
            return [record]

    def assert_compatible():
        SourceGovernanceService(
            OneProviderRecordSession()
        )._assert_existing_provider_record_compatible(
            existing_contract=SimpleNamespace(
                intake_metadata={"request_scope": stored_scope}
            ),
            document=SimpleNamespace(id="document-id", content_sha256=digest),
            source_type="licensed_provider",
            source_metadata={
                "provider_name": provider_name,
                "provider_record_id": f"research-report:{digest}",
                "request_scope": replay_scope,
                "retrieval_reference": f"gildata://research-report/{digest}",
                "contract_version": "gildata-local-rights-v1",
            },
        )

    if compatible:
        assert_compatible()
    else:
        with pytest.raises(SourceGovernanceCompatibilityError):
            assert_compatible()


def _gildata_source_metadata(document, *, query_sha256: str = "b" * 64):
    return {
        "research_source_type": "licensed_provider",
        "provider_name": "gildata",
        "provider_record_id": f"research-report:{document.content_sha256}",
        "retrieval_reference": document.source_url,
        "request_scope": {
            "source_kind": "research-report",
            "query_sha256": query_sha256,
        },
        "permissions": {
            "ai_processing": True,
            "display": True,
            "export": False,
            "api": False,
        },
        "contract_version": "gildata-local-rights-v1",
        "downstream_restrictions": ["仅限当前 Case 研究与人工审核"],
    }


def test_governance_unique_race_reuses_complete_compatible_winner(
    session,
    monkeypatch,
):
    from sqlalchemy import func, select

    from app.models.source_governance import ProviderRecord, SourceContract
    from app.services.source_governance import SourceGovernanceService

    document = _seed_polluted_research_document(session)
    metadata = _gildata_source_metadata(document)
    service = SourceGovernanceService(session)
    winner = service.record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata=metadata,
        declared_by="tenant:first",
        incoming_source_url=document.source_url,
    )
    session.commit()

    original_scalar = session.scalar
    original_scalars = session.scalars
    contract_reads = 0
    provider_reads = 0

    def miss_contract_once(statement, *args, **kwargs):
        nonlocal contract_reads
        contract_reads += 1
        if contract_reads == 1:
            return None
        return original_scalar(statement, *args, **kwargs)

    def miss_provider_once(statement, *args, **kwargs):
        nonlocal provider_reads
        provider_reads += 1
        if provider_reads == 1:
            return ()
        return original_scalars(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", miss_contract_once)
    monkeypatch.setattr(session, "scalars", miss_provider_once)

    recovered = service.record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata=metadata,
        declared_by="tenant:first",
        incoming_source_url=document.source_url,
    )

    assert recovered.id == winner.id
    assert session.scalar(select(func.count()).select_from(SourceContract)) == 1
    assert session.scalar(select(func.count()).select_from(ProviderRecord)) == 1


def test_governance_unique_race_rejects_an_incomplete_winner(
    session,
    monkeypatch,
):
    from sqlalchemy import func, select

    from app.models.source_governance import ProviderRecord, SourceContract
    from app.services.source_governance import (
        SourceGovernanceCompatibilityError,
        SourceGovernanceService,
    )

    document = _seed_polluted_research_document(session)
    metadata = _gildata_source_metadata(document)
    service = SourceGovernanceService(session)
    service.record_event_intake(
        document=document,
        source_type="licensed_provider",
        source_metadata=metadata,
        declared_by="tenant:first",
        incoming_source_url=document.source_url,
    )
    session.flush()
    provider_record = session.scalar(select(ProviderRecord))
    assert provider_record is not None
    session.connection().exec_driver_sql(
        "DELETE FROM provider_records WHERE id = ?",
        (provider_record.id.hex,),
    )
    session.commit()

    original_scalar = session.scalar
    contract_reads = 0

    def miss_contract_once(statement, *args, **kwargs):
        nonlocal contract_reads
        contract_reads += 1
        if contract_reads == 1:
            return None
        return original_scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", miss_contract_once)

    with pytest.raises(SourceGovernanceCompatibilityError):
        service.record_event_intake(
            document=document,
            source_type="licensed_provider",
            source_metadata=metadata,
            declared_by="tenant:first",
            incoming_source_url=document.source_url,
        )

    assert session.scalar(select(func.count()).select_from(SourceContract)) == 1
    assert session.scalar(select(func.count()).select_from(ProviderRecord)) == 0


def test_governance_rejects_orphan_provider_record_before_contract_insert(session):
    from sqlalchemy import func, select

    from app.models.source_governance import ProviderRecord, SourceContract
    from app.services.source_governance import (
        SourceGovernanceCompatibilityError,
        SourceGovernanceService,
    )

    document = _seed_polluted_research_document(session)
    metadata = _gildata_source_metadata(document)
    now = datetime.now(timezone.utc)
    session.add(
        ProviderRecord(
            document_version_id=document.id,
            provider_name="gildata",
            provider_record_id=metadata["provider_record_id"],
            request_scope=metadata["request_scope"],
            retrieval_reference=document.source_url,
            content_sha256=document.content_sha256,
            retrieved_at=now,
            contract_version="gildata-local-rights-v1",
            created_at=now,
        )
    )
    session.flush()

    with pytest.raises(SourceGovernanceCompatibilityError):
        SourceGovernanceService(session).record_event_intake(
            document=document,
            source_type="licensed_provider",
            source_metadata=metadata,
            declared_by="tenant:first",
            incoming_source_url=document.source_url,
        )

    assert session.scalar(select(func.count()).select_from(SourceContract)) == 0
    assert session.scalar(select(func.count()).select_from(ProviderRecord)) == 1


def test_gildata_replay_fails_closed_when_provider_record_is_missing(
    session,
    monkeypatch,
):
    from app.models.source_governance import ProviderRecord
    from sqlalchemy import select

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    query = "provider-record-missing-query"
    _ingest_research_only(
        session,
        query=query,
        client=_make_research_only_client(RESEARCH_MD),
    )
    session.flush()
    provider_record = session.scalar(select(ProviderRecord))
    assert provider_record is not None
    session.connection().exec_driver_sql(
        "DELETE FROM provider_records WHERE id = ?",
        (provider_record.id.hex,),
    )
    session.expire_all()

    with pytest.raises(GildataMCPError) as exc_info:
        _ingest_research_only(
            session,
            query=query,
            client=_make_research_only_client(RESEARCH_MD),
        )

    assert str(exc_info.value) == GILDATA_RESPONSE_ERROR_MESSAGE


def test_gildata_replay_fails_closed_when_provider_records_are_duplicated():
    from types import SimpleNamespace

    from app.services.source_governance import (
        SourceGovernanceCompatibilityError,
        SourceGovernanceService,
    )

    digest = "a" * 64
    document = SimpleNamespace(id="document-id", content_sha256=digest)
    record = SimpleNamespace(
        provider_name="gildata",
        provider_record_id=f"research-report:{digest}",
        request_scope={
            "source_kind": "research-report",
            "query_sha256": "b" * 64,
        },
        retrieval_reference=f"gildata://research-report/{digest}",
        content_sha256=digest,
        contract_version="gildata-local-rights-v1",
    )

    class DuplicateProviderRecordSession:
        def scalars(self, statement):
            del statement
            return [record, record]

    service = SourceGovernanceService(DuplicateProviderRecordSession())
    with pytest.raises(SourceGovernanceCompatibilityError):
        service._assert_existing_provider_record_compatible(
            existing_contract=SimpleNamespace(
                intake_metadata={"request_scope": record.request_scope}
            ),
            document=document,
            source_type="licensed_provider",
            source_metadata={
                "provider_name": "gildata",
                "provider_record_id": f"research-report:{digest}",
                "request_scope": {
                    "source_kind": "research-report",
                    "query_sha256": "b" * 64,
                },
                "retrieval_reference": f"gildata://research-report/{digest}",
                "contract_version": "gildata-local-rights-v1",
            },
        )


def test_gildata_governance_does_not_mask_an_unexpected_value_error(
    session,
    monkeypatch,
):
    from app.services.source_governance import SourceGovernanceService

    def raise_programming_error(*args, **kwargs):
        del args, kwargs
        raise ValueError("unexpected-governance-programming-error")

    monkeypatch.setattr(
        SourceGovernanceService,
        "record_event_intake",
        raise_programming_error,
    )

    with pytest.raises(
        ValueError,
        match="unexpected-governance-programming-error",
    ):
        _ingest_research_only(
            session,
            query="programming-error-query",
            client=_make_research_only_client(RESEARCH_MD),
        )


@pytest.mark.parametrize(
    ("column", "bad_value"),
    [
        ("provider_name", "polluted-provider"),
        ("provider_record_id", "research-report:polluted-record"),
        ("content_sha256", "0" * 64),
        ("retrieval_reference", "gildata://research-report/polluted-reference"),
        ("contract_version", "polluted-contract"),
        (
            "request_scope",
            json.dumps(
                {
                    "source_kind": "polluted-kind",
                    "query_sha256": "0" * 64,
                }
            ),
        ),
        (
            "request_scope",
            json.dumps(
                {
                    "source_kind": "research-report",
                    "query_sha256": "not-a-sha256",
                }
            ),
        ),
        (
            "request_scope",
            json.dumps(
                {
                    "source_kind": "research-report",
                    "query_sha256": "f" * 64,
                }
            ),
        ),
    ],
)
def test_gildata_replay_fails_closed_when_provider_record_identity_is_polluted(
    session,
    monkeypatch,
    column,
    bad_value,
):
    from app.models.source_governance import ProviderRecord
    from sqlalchemy import select

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    query = "provider-record-integrity-query"
    _ingest_research_only(
        session,
        query=query,
        client=_make_research_only_client(RESEARCH_MD),
    )
    session.flush()
    provider_record = session.scalar(select(ProviderRecord))
    assert provider_record is not None
    session.connection().exec_driver_sql(
        f"UPDATE provider_records SET {column} = ? WHERE id = ?",
        (bad_value, provider_record.id.hex),
    )
    session.expire_all()

    with pytest.raises(GildataMCPError) as exc_info:
        _ingest_research_only(
            session,
            query=query,
            client=_make_research_only_client(RESEARCH_MD),
        )

    assert str(exc_info.value) == GILDATA_RESPONSE_ERROR_MESSAGE


def _quote_client_with_stock_code(stock_code: object) -> _FakeClient:
    quote = json.dumps(
        {
            "股票名称": "寒武纪",
            "股票代码": stock_code,
            "最新价": "850.00",
            "市盈率TTM": "380.5",
            "市净率": "12.3",
            "总市值": "3.56e11",
        },
        ensure_ascii=False,
    )
    return _FakeClient([], [], [{"table_markdown": quote}])


def _ingest_quote_only(session, client, *, quote_stock_code="688256"):
    from app.scripts.ingest_real_data import ingest

    return ingest(
        session,
        client,
        research_queries=["无研报"],
        announcement_query="无公告",
        news_query="无新闻",
        quote_query="指定证券行情",
        quote_stock_code=quote_stock_code,
    )


def test_ingest_quote_identity_mismatch_rejects_wrong_security(session):
    from app.scripts.ingest_real_data import find_stock_by_code

    mismatched_quote = (
        "|股票名称|股票代码|最新价|市盈率TTM|市净率|总市值|\n"
        "|---|---|---|---|---|---|\n"
        "|工业富联|601138.SH|50.00|20|3|2.0e11|"
    )
    client = _FakeClient([], [], [{"table_markdown": mismatched_quote}])

    with pytest.raises(GildataMCPError, match=GILDATA_RESPONSE_ERROR_MESSAGE):
        _ingest_quote_only(session, client)

    assert find_stock_by_code(session, "601138") is None


@pytest.mark.parametrize(
    ("requested_code", "returned_code"),
    [
        ("688256.SH", "688256"),
        (" 688256.sh ", " 688256.SH "),
    ],
)
def test_ingest_quote_identity_accepts_same_bare_code(
    session,
    requested_code,
    returned_code,
):
    from app.scripts.ingest_real_data import find_stock_by_code

    summary = _ingest_quote_only(
        session,
        _quote_client_with_stock_code(returned_code),
        quote_stock_code=requested_code,
    )

    assert summary["valuations_written"] == 3
    assert summary["quote_identity_verified"] is True
    assert find_stock_by_code(session, "688256") is not None


@pytest.mark.parametrize("returned_code", [None, ""])
def test_ingest_quote_identity_missing_provider_code_uses_requested_code(
    session,
    returned_code,
):
    from app.scripts.ingest_real_data import find_stock_by_code

    summary = _ingest_quote_only(
        session,
        _quote_client_with_stock_code(returned_code),
        quote_stock_code="688256.SH",
    )

    assert summary["valuations_written"] == 3
    assert summary["quote_identity_verified"] is True
    assert find_stock_by_code(session, "688256") is not None


def test_ingest_quote_identity_is_not_attested_when_provider_returns_no_quote(session):
    summary = _ingest_quote_only(session, _FakeClient([], [], []))

    assert summary["valuations_written"] == 0
    assert summary["quote_identity_verified"] is False


@pytest.mark.parametrize("invalid_code", [688256, ["688256"], {"code": "688256"}])
def test_ingest_quote_identity_rejects_non_string_provider_code(
    session,
    invalid_code,
):
    with pytest.raises(GildataMCPError, match=GILDATA_RESPONSE_ERROR_MESSAGE):
        _ingest_quote_only(session, _quote_client_with_stock_code(invalid_code))


@pytest.mark.parametrize("invalid_code", ["   ", ".SH"])
def test_ingest_quote_identity_rejects_empty_normalized_provider_code(
    session,
    invalid_code,
):
    with pytest.raises(GildataMCPError, match=GILDATA_RESPONSE_ERROR_MESSAGE):
        _ingest_quote_only(session, _quote_client_with_stock_code(invalid_code))


@pytest.mark.parametrize(
    "invalid_code",
    ["ABC", "688 256", "12345", "1234567", "６８８２５６", "68825A"],
)
def test_quote_identity_normalizer_rejects_malformed_provider_code(invalid_code):
    from app.scripts.ingest_real_data import _bare_security_code

    with pytest.raises(ValueError):
        _bare_security_code(invalid_code)


@pytest.mark.parametrize(
    "invalid_code",
    ["ABC", "688 256", "12345", "1234567", "６８８２５６", "68825A"],
)
def test_ingest_quote_identity_rejects_malformed_provider_code(
    session,
    invalid_code,
):
    with pytest.raises(GildataMCPError, match=GILDATA_RESPONSE_ERROR_MESSAGE):
        _ingest_quote_only(session, _quote_client_with_stock_code(invalid_code))


@pytest.mark.parametrize(
    "invalid_code",
    ["", "   ", "ABC", "688 256", "12345", "1234567", "６８８２５６", "68825A", 688256],
)
def test_ingest_quote_identity_rejects_invalid_requested_code(
    session,
    invalid_code,
):
    from app.scripts.ingest_real_data import GildataRequestValidationError

    client = _quote_client_with_stock_code(None)

    with pytest.raises(
        GildataRequestValidationError,
        match="quote_stock_code is invalid",
    ):
        _ingest_quote_only(
            session,
            client,
            quote_stock_code=invalid_code,
        )

    assert client.calls == []


def test_ingest_quote_identity_validates_request_before_any_partial_write(session):
    from sqlalchemy import func, select

    from app.models.ledger import CaseDocumentVersion, DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import GildataRequestValidationError, ingest

    client = _make_client()

    with pytest.raises(
        GildataRequestValidationError,
        match="quote_stock_code is invalid",
    ):
        ingest(session, client, quote_stock_code="ABC")

    assert client.calls == []
    assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 0
    assert session.scalar(select(func.count()).select_from(SourceSpan)) == 0
    assert session.scalar(select(func.count()).select_from(CaseDocumentVersion)) == 0


def test_ingest_without_case_does_not_adopt_the_first_global_case(session):
    from sqlalchemy import select

    from app.models.ledger import CaseDocumentVersion, ResearchCase
    from app.scripts.ingest_real_data import ingest

    session.add(
        ResearchCase(
            title="不应被自动归入的 Case",
            industry_topic="test",
            created_by="test",
            created_at=datetime.now(timezone.utc),
        )
    )
    session.flush()

    summary = ingest(session, _make_client())

    assert summary["case_id"] is None
    assert list(session.scalars(select(CaseDocumentVersion))) == []


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


def test_ingest_replay_reuses_document_and_span_without_false_supersession(
    session,
):
    from sqlalchemy import select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    first = ingest(session, _make_client())
    session.flush()
    gildata_documents = list(
        session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.source_url.like("gildata://%")
            )
        )
    )
    document_ids = {document.id for document in gildata_documents}
    spans_before = list(
        session.scalars(
            select(SourceSpan).where(
                SourceSpan.document_version_id.in_(document_ids)
            )
        )
    )

    second = ingest(session, _make_client())
    session.flush()
    gildata_documents_after = list(
        session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.source_url.like("gildata://%")
            )
        )
    )
    spans_after = list(
        session.scalars(
            select(SourceSpan).where(
                SourceSpan.document_version_id.in_(document_ids)
            )
        )
    )

    assert len(gildata_documents_after) == len(gildata_documents)
    assert len(spans_after) == len(spans_before)
    assert all(document.supersedes_id is None for document in gildata_documents_after)
    assert all(
        document.source_url
        == f"gildata://{document.source_url.split('/')[2]}/{document.content_sha256}"
        for document in gildata_documents_after
    )
    assert first["spans"] == len(spans_before) == 4
    assert second["spans"] == 0
    assert second["spans_reused"] == len(spans_before)
    assert second["research_reports_reused"] == 2
    assert second["announcements_reused"] == 1
    assert second["news_reused"] == 1


def test_gildata_duplicate_hits_count_unique_documents_and_spans(session):
    from sqlalchemy import func, select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    queries = ["重复命中一", "重复命中二"]
    first = ingest(
        session,
        _make_duplicate_research_client(),
        research_queries=queries,
    )
    second = ingest(
        session,
        _make_duplicate_research_client(),
        research_queries=queries,
    )

    assert first["research_reports"] == 1
    assert first["research_reports_reused"] == 0
    assert first["spans"] == 1
    assert first["spans_reused"] == 0
    assert second["research_reports"] == 0
    assert second["research_reports_reused"] == 1
    assert second["spans"] == 0
    assert second["spans_reused"] == 1
    assert session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(DocumentVersion.source_url.like("gildata://research-report/%"))
    ) == 1
    assert session.scalar(select(func.count()).select_from(SourceSpan)) == 1


def test_gildata_same_body_metadata_drift_reuses_first_frozen_document(session):
    from app.models.ledger import DocumentVersion, SourceSpan
    from app.models.source_governance import ProviderRecord
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import func, select

    def report(title: str, publish_date: str, org: str) -> dict[str, str]:
        return {
            "table_markdown": (
                f"报告标题：{title}；\n"
                f"发布时间：{publish_date}；\n"
                f"撰写机构：{org}；\n"
                "股票代码：688256；\n"
                f"原文：{RESEARCH_BODY}"
            )
        }

    first_title = "寒武纪年报点评"
    alias_title = "寒武纪：年报点评"
    first_org = "西部证券"
    alias_org = "西证"
    first_query = "标题别名首次命中"
    alias_query = "标题别名再次命中"
    first = ingest(
        session,
        _FakeClient(
            [[report(first_title, "2026-05-06", first_org)]],
            [],
            [],
        ),
        research_queries=[first_query],
        announcement_query="无公告",
        news_query="无新闻",
        quote_query="无行情",
    )
    second = ingest(
        session,
        _FakeClient(
            [[report(alias_title, "2026-05-05", alias_org)]],
            [],
            [],
        ),
        research_queries=[alias_query],
        announcement_query="无公告",
        news_query="无新闻",
        quote_query="无行情",
    )

    assert first["research_reports"] == 1
    assert first["research_reports_reused"] == 0
    assert first["spans"] == 1
    assert first["spans_reused"] == 0
    assert second["research_reports"] == 0
    assert second["research_reports_reused"] == 1
    assert second["spans"] == 0
    assert second["spans_reused"] == 1
    assert session.scalar(select(func.count()).select_from(DocumentVersion)) == 1
    assert session.scalar(select(func.count()).select_from(SourceSpan)) == 1
    document = session.scalar(select(DocumentVersion))
    span = session.scalar(select(SourceSpan))
    provider_record = session.scalar(select(ProviderRecord))
    assert document is not None
    assert span is not None
    assert provider_record is not None
    assert document.title == first_title
    assert document.published_at.date().isoformat() == "2026-05-06"
    assert span.locator["title"] == first_title
    assert span.locator["org"] == first_org
    assert span.locator["publish_date"] == "2026-05-06"
    assert provider_record.request_scope["query_sha256"] == hashlib.sha256(
        first_query.encode("utf-8")
    ).hexdigest()


def test_gildata_announcement_body_alias_reuses_first_metadata(session):
    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import func, select

    def announcement(title: str, publish_date: str, sec_name: str) -> dict[str, str]:
        return {
            "table_markdown": (
                "|公告标题|公告日期|股票代码|证券简称|公告内容|\n"
                "|---|---|---|---|---|\n"
                f"|{title}|{publish_date}|688256|{sec_name}|同一公告正文|"
            )
        }

    first = announcement("首次公告标题", "2026-05-06", "寒武纪")
    alias = announcement("公告标题别名", "2026-05-05", "中科寒武纪")
    initial = ingest(
        session,
        _FakeClient([], [first, alias], []),
        research_queries=["无研报"],
        announcement_query="公告首次命中",
        news_query="无新闻",
        quote_query="无行情",
    )
    replay = ingest(
        session,
        _FakeClient([], [alias], []),
        research_queries=["无研报"],
        announcement_query="公告别名重放",
        news_query="无新闻",
        quote_query="无行情",
    )

    assert initial["announcements"] == 1
    assert initial["announcements_reused"] == 0
    assert replay["announcements"] == 0
    assert replay["announcements_reused"] == 1
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://announcement/%")
        )
    )
    assert document is not None
    span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert document.title == "首次公告标题"
    assert span.locator["sec_name"] == "寒武纪"
    assert session.scalar(select(func.count()).select_from(SourceSpan)) == 1


def test_gildata_news_title_fallback_alias_reuses_first_metadata(session):
    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import func, select

    def news(publish_date: str, source: str) -> dict[str, str]:
        return {
            "table_markdown": (
                "报告标题：同一短讯标题；\n"
                f"发布时间：{publish_date}；\n"
                f"新闻舆情来源：{source}"
            )
        }

    first = news("2026-05-06", "首次来源")
    alias = news("2026-05-05", "来源别名")
    initial = ingest(
        session,
        _FakeClient([], [], [], news_results=[first, alias]),
        research_queries=["无研报"],
        announcement_query="无公告",
        news_query="短讯首次命中",
        quote_query="无行情",
    )
    replay = ingest(
        session,
        _FakeClient([], [], [], news_results=[alias]),
        research_queries=["无研报"],
        announcement_query="无公告",
        news_query="短讯别名重放",
        quote_query="无行情",
    )

    assert initial["news"] == 1
    assert initial["news_reused"] == 0
    assert replay["news"] == 0
    assert replay["news_reused"] == 1
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://news/%")
        )
    )
    assert document is not None
    span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert document.title == "同一短讯标题"
    assert span.verbatim_text == "同一短讯标题"
    assert span.locator["source"] == "首次来源"
    assert session.scalar(select(func.count()).select_from(SourceSpan)) == 1


def test_gildata_same_body_across_source_kinds_fails_closed(session):
    from sqlalchemy import select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    shared_body = "同一正文不得伪装成两个来源"
    announcement = {
        "table_markdown": (
            "|公告标题|公告日期|股票代码|公告内容|\n"
            "|---|---|---|---|\n"
            f"|同文公告|2026-09-03|688256|{shared_body}|"
        )
    }
    news = {
        "table_markdown": (
            "报告标题：同文新闻；\n"
            "发布时间：2026-09-03；\n"
            "新闻舆情来源：测试媒体；\n"
            f"原文：{shared_body}"
        )
    }
    client = _FakeClient(
        [],
        [announcement],
        [],
        news_results=[news],
    )

    # DocumentVersion/content governance is one-to-one today. Identical bytes
    # carrying a different provider kind must fail rather than silently reuse
    # the first source's provenance.
    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, client)

    assert str(exc_info.value) == "Gildata provider returned an invalid response"
    documents = list(
        session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.source_url.like("gildata://%")
            )
        )
    )
    assert len(documents) == 1
    assert documents[0].source_url.startswith("gildata://announcement/")
    spans = list(session.scalars(select(SourceSpan)))
    assert len(spans) == 1
    assert spans[0].document_version_id == documents[0].id


def test_gildata_variant_body_never_attaches_to_unrelated_frozen_document(
    session,
):
    from sqlalchemy import select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    def client_for(body: str) -> _FakeClient:
        markdown = (
            "报告标题：同名研报；\n"
            "发布时间：2026-09-03；\n"
            "撰写机构：测试机构；\n"
            f"原文：{body}"
        )
        return _FakeClient([[{"table_markdown": markdown}]], [], [])

    ingest(
        session,
        client_for("正文甲"),
        research_queries=["同名研报"],
        announcement_query="无公告",
        news_query="无新闻",
        quote_query="无行情",
    )
    ingest(
        session,
        client_for("正文乙"),
        research_queries=["同名研报"],
        announcement_query="无公告",
        news_query="无新闻",
        quote_query="无行情",
    )
    session.flush()

    documents = list(
        session.scalars(
            select(DocumentVersion).where(
                DocumentVersion.source_url.like("gildata://research-report/%")
            )
        )
    )
    assert len(documents) == 2
    for document in documents:
        spans = list(
            session.scalars(
                select(SourceSpan).where(
                    SourceSpan.document_version_id == document.id
                )
            )
        )
        assert len(spans) == 1
        digest = hashlib.sha256(spans[0].verbatim_text.encode("utf-8")).hexdigest()
        assert digest == document.content_sha256
        assert spans[0].text_sha256 == digest


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("parser_version", "polluted-parser"),
        ("source_authority", "unknown"),
        ("natural_key", "gildata:polluted-natural-key"),
        ("byte_size", 1),
        ("parse_state", "partial"),
        ("language", "zh"),
        ("claimed_page_reference", "第 1 页"),
        ("supplements_document_version_id", "related-document"),
        ("title", "被污染的标题"),
        ("published_at", datetime(2026, 5, 7, tzinfo=timezone.utc)),
    ],
)
def test_gildata_replay_rejects_polluted_frozen_document_metadata(
    session,
    field,
    bad_value,
):
    from app.repositories.documents import DocumentRepository
    from app.scripts.ingest_real_data import ingest
    from app.services.ingest import DocumentService

    if bad_value == "related-document":
        related = DocumentService(DocumentRepository(session)).freeze(
            raw=b"valid related document",
            source_url="https://example.test/related",
        )
        bad_value = related.id

    document = _seed_polluted_research_document(
        session,
        **{field: bad_value},
    )
    assert document is not None
    persisted_value = getattr(document, field)
    if field == "published_at":
        assert persisted_value.date() == bad_value.date()
    else:
        assert persisted_value == bad_value

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


def test_gildata_replay_rejects_polluted_full_body_locator(session):
    from sqlalchemy import select

    from app.models.ledger import SourceSpan
    from app.scripts.ingest_real_data import ingest

    document = _seed_polluted_research_document(
        session,
        span_locator={
            "kind": "research_report",
            "title": "寒武纪(688256)2025年年报点评",
            "org": "被污染的机构",
            "publish_date": "2026-05-06",
            "sec_code": "688256",
            "page": 1,
            "paragraph": 1,
            "parser": "gildata-mcp-1",
        },
    )
    assert document is not None
    span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert span.locator["org"] == "被污染的机构"

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


def test_gildata_replay_rejects_polluted_supersedes_link(session):
    from app.models.ledger import DocumentVersion
    from app.repositories.documents import DocumentRepository
    from app.scripts.ingest_real_data import ingest
    from app.services.ingest import DocumentService

    predecessor = DocumentService(DocumentRepository(session)).freeze(
        raw=b"valid predecessor",
        source_url="https://example.test/predecessor",
    )
    document = _seed_polluted_research_document(
        session,
        supersedes_id=predecessor.id,
    )
    assert document is not None
    document_id = document.id
    session.expire_all()
    persisted = session.get(DocumentVersion, document_id)
    assert persisted is not None
    assert persisted.supersedes_id == predecessor.id

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


def test_gildata_macro_replay_reuses_deterministic_metric_spans(session):
    from sqlalchemy import select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    first = ingest(session, _make_macro_client(), macro_queries=["锂行业时序"])
    session.flush()
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://macro-industry/%")
        )
    )
    assert document is not None
    spans_before = list(
        session.scalars(
            select(SourceSpan).where(SourceSpan.document_version_id == document.id)
        )
    )

    second = ingest(session, _make_macro_client(), macro_queries=["锂行业时序"])
    session.flush()
    spans_after = list(
        session.scalars(
            select(SourceSpan).where(SourceSpan.document_version_id == document.id)
        )
    )

    assert first["macro_series"] == 1
    assert first["spans"] == 2
    assert second["macro_series"] == 0
    assert second["macro_series_reused"] == 1
    assert second["spans"] == 0
    assert second["spans_reused"] == 2
    assert len(spans_after) == len(spans_before) == 2
    assert document.supersedes_id is None
    assert all(
        span.text_sha256
        == hashlib.sha256(span.verbatim_text.encode("utf-8")).hexdigest()
        for span in spans_after
    )


def test_gildata_macro_freeze_records_licensed_provider_governance(
    session,
    monkeypatch,
):
    from app.models.ledger import DocumentVersion
    from app.models.source_governance import ProviderRecord, SourceContract
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import select

    monkeypatch.setenv("GILDATA_ALLOW_AI_PROCESSING", "true")
    monkeypatch.setenv("GILDATA_ALLOW_DISPLAY", "true")
    query = "锂行业治理时序"

    ingest(session, _make_macro_client(), macro_queries=[query])
    session.flush()

    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://macro-industry/%")
        )
    )
    assert document is not None
    contract = session.scalar(
        select(SourceContract).where(
            SourceContract.document_version_id == document.id
        )
    )
    record = session.scalar(
        select(ProviderRecord).where(
            ProviderRecord.document_version_id == document.id
        )
    )
    assert contract is not None
    assert record is not None
    assert (
        contract.source_type
        == contract.research_source_type
        == "licensed_provider"
    )
    assert record.provider_record_id == f"macro-industry:{document.content_sha256}"
    assert record.request_scope == {
        "source_kind": "macro-industry",
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
    }


def test_gildata_macro_uses_one_bounded_window_for_body_and_span(session):
    from sqlalchemy import select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    rows = _macro_window_rows()
    first = ingest(
        session,
        _macro_client_for_rows(rows),
        macro_queries=["窗口一致性"],
    )
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://macro-industry/%")
        )
    )
    assert document is not None
    span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert first["macro_series"] == 1
    assert span.locator["n_points"] == 20
    assert span.locator["peak_date"] == "2026-08-25"
    assert len(span.verbatim_text.splitlines()) == 20
    assert "2026-08-01" not in span.verbatim_text

    outside_window_changed = [dict(row) for row in rows]
    outside_window_changed[0]["value"] = "8888"
    second = ingest(
        session,
        _macro_client_for_rows(outside_window_changed),
        macro_queries=["窗口一致性"],
    )

    assert second["macro_series"] == 0
    assert second["macro_series_reused"] == 1
    assert second["spans_reused"] == 1


def test_gildata_macro_change_inside_window_gets_new_content_identity(session):
    from sqlalchemy import func, select

    from app.models.ledger import DocumentVersion
    from app.scripts.ingest_real_data import ingest

    rows = _macro_window_rows()
    ingest(
        session,
        _macro_client_for_rows(rows),
        macro_queries=["窗口内变化"],
    )
    inside_window_changed = [dict(row) for row in rows]
    inside_window_changed[5]["value"] = "7777"

    second = ingest(
        session,
        _macro_client_for_rows(inside_window_changed),
        macro_queries=["窗口内变化"],
    )

    assert second["macro_series"] == 1
    assert second["macro_series_reused"] == 0
    assert second["spans"] == 1
    assert session.scalar(
        select(func.count())
        .select_from(DocumentVersion)
        .where(DocumentVersion.source_url.like("gildata://macro-industry/%"))
    ) == 2


def test_gildata_macro_permutation_reuses_same_document_and_spans(session):
    from app.scripts.ingest_real_data import ingest

    rows = _macro_window_rows()[-3:] + [
        {
            "metric_code": "A-METRIC",
            "metric_name": "另一个指标",
            "frequency": "月",
            "unit": "吨",
            "value": "42",
            "date": "2026-07-01",
            "source": "另一测试源",
        }
    ]
    first = ingest(
        session,
        _macro_client_for_rows(rows),
        macro_queries=["顺序确定性"],
    )
    second = ingest(
        session,
        _macro_client_for_rows(list(reversed(rows))),
        macro_queries=["顺序确定性"],
    )

    assert first["macro_series"] == 1
    assert first["spans"] == 2
    assert second["macro_series"] == 0
    assert second["macro_series_reused"] == 1
    assert second["spans"] == 0
    assert second["spans_reused"] == 2


def test_gildata_macro_peak_accepts_thousands_separators(session):
    from sqlalchemy import select

    from app.models.ledger import SourceSpan
    from app.scripts.ingest_real_data import ingest

    rows = [
        {
            "metric_code": "M-PEAK",
            "metric_name": "峰值指标",
            "frequency": "日",
            "unit": "点",
            "value": value,
            "date": f"2026-08-0{index}",
            "source": "测试源",
        }
        for index, value in enumerate(("1,000", "900"), start=1)
    ]

    ingest(session, _macro_client_for_rows(rows), macro_queries=["千分位峰值"])

    span = session.scalar(select(SourceSpan))
    assert span is not None
    assert span.locator["peak_date"] == "2026-08-01"
    assert span.locator["peak_value"] == "1,000"


def test_gildata_macro_peak_ignores_invalid_values_when_valid_value_exists(session):
    from sqlalchemy import select

    from app.models.ledger import SourceSpan
    from app.scripts.ingest_real_data import ingest

    rows = [
        {
            "metric_code": "M-MIXED",
            "metric_name": "混合指标",
            "frequency": "日",
            "unit": "点",
            "value": value,
            "date": f"2026-08-0{index}",
            "source": "测试源",
        }
        for index, value in enumerate(("", "无效", "900"), start=1)
    ]

    ingest(session, _macro_client_for_rows(rows), macro_queries=["混合值峰值"])

    span = session.scalar(select(SourceSpan))
    assert span is not None
    assert span.locator["peak_date"] == "2026-08-03"
    assert span.locator["peak_value"] == "900"


def test_gildata_macro_rejects_window_without_valid_numeric_value(session):
    from sqlalchemy import func, select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    rows = [
        {
            "metric_code": "M-INVALID",
            "metric_name": "无效指标",
            "frequency": "日",
            "unit": "点",
            "value": value,
            "date": f"2026-08-0{index}",
            "source": "测试源",
        }
        for index, value in enumerate(("", "无效", "--"), start=1)
    ]

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _macro_client_for_rows(rows), macro_queries=["全无效峰值"])

    assert str(exc_info.value) == "Gildata provider returned an invalid response"
    assert session.scalar(
        select(func.count()).select_from(DocumentVersion)
    ) == 0
    assert session.scalar(select(func.count()).select_from(SourceSpan)) == 0


def test_gildata_macro_replay_rejects_polluted_locator_identity(
    session,
    monkeypatch,
):
    from sqlalchemy import func, select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest
    from app.services.ingest import DocumentService

    original_add_span = DocumentService.add_span
    locator_polluted = False

    def add_polluted_macro_span(
        service,
        document_version_id,
        locator,
        verbatim_text,
        **kwargs,
    ):
        nonlocal locator_polluted
        if locator.get("kind") == "macro_series" and not locator_polluted:
            locator = {**locator, "metric_code": "POLLUTED-METRIC"}
            locator_polluted = True
        return original_add_span(
            service,
            document_version_id,
            locator,
            verbatim_text,
            **kwargs,
        )

    with monkeypatch.context() as patch:
        patch.setattr(DocumentService, "add_span", add_polluted_macro_span)
        ingest(session, _make_macro_client(), macro_queries=["锂行业时序"])
    assert locator_polluted is True
    session.flush()
    session.expire_all()
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://macro-industry/%")
        )
    )
    assert document is not None
    span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert span.locator["metric_code"] == "POLLUTED-METRIC"
    spans_before = session.scalar(select(func.count()).select_from(SourceSpan))

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_macro_client(), macro_queries=["锂行业时序"])

    assert str(exc_info.value) == "Gildata provider returned an invalid response"
    assert session.scalar(select(func.count()).select_from(SourceSpan)) == spans_before


def test_gildata_replay_rejects_polluted_full_body_spans(session):
    from sqlalchemy import select

    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest

    ingest(session, _make_client())
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://research-report/%"),
            DocumentVersion.title == "寒武纪(688256)2025年年报点评",
        )
    )
    assert document is not None
    session.add(
        SourceSpan(
            document_version_id=document.id,
            locator={"kind": "polluted"},
            verbatim_text="不相关正文",
            text_sha256=hashlib.sha256("不相关正文".encode("utf-8")).hexdigest(),
        )
    )
    session.flush()

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


def test_gildata_attestation_rejects_governed_locator_metadata_pollution(session):
    from app.models.ledger import DocumentVersion, SourceSpan
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import select

    ingest(session, _make_client())
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://research-report/%"),
            DocumentVersion.title == "寒武纪(688256)2025年年报点评",
        )
    )
    assert document is not None
    span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    polluted_locator = dict(span.locator)
    polluted_locator["org"] = "被污染的机构"
    session.connection().connection.driver_connection.execute(
        "UPDATE source_spans SET locator = ? WHERE id = ?",
        (json.dumps(polluted_locator, ensure_ascii=False), span.id.hex),
    )
    session.expire_all()

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


@pytest.mark.parametrize("field", ["available_at", "acquired_at"])
def test_gildata_attestation_rejects_document_visibility_time_pollution(
    session,
    field,
):
    from app.models.ledger import DocumentVersion
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import select

    ingest(session, _make_client())
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://research-report/%"),
            DocumentVersion.title == "寒武纪(688256)2025年年报点评",
        )
    )
    assert document is not None
    session.connection().connection.driver_connection.execute(
        f"UPDATE document_versions SET {field} = ? WHERE id = ?",
        ("2000-01-01 00:00:00.000000", document.id.hex),
    )
    session.expire_all()

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


def test_gildata_attestation_rejects_governed_contract_metadata_pollution(session):
    from app.models.ledger import DocumentVersion
    from app.models.source_governance import SourceContract
    from app.scripts.ingest_real_data import ingest
    from sqlalchemy import select

    ingest(session, _make_client())
    document = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.source_url.like("gildata://research-report/%"),
            DocumentVersion.title == "寒武纪(688256)2025年年报点评",
        )
    )
    assert document is not None
    contract = session.scalar(
        select(SourceContract).where(
            SourceContract.document_version_id == document.id
        )
    )
    assert contract is not None
    polluted_metadata = dict(contract.intake_metadata)
    polluted_metadata["provider_name"] = "polluted-provider"
    session.connection().connection.driver_connection.execute(
        "UPDATE source_contracts SET intake_metadata = ? WHERE id = ?",
        (json.dumps(polluted_metadata, ensure_ascii=False), contract.id.hex),
    )
    session.expire_all()

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


def test_gildata_replay_rejects_non_object_locator_json(session):
    from sqlalchemy import select

    from app.models.ledger import SourceSpan
    from app.scripts.ingest_real_data import ingest

    document = _seed_polluted_research_document(
        session,
        span_locator=["not", "an", "object"],
    )
    assert document is not None
    span = session.scalar(
        select(SourceSpan).where(SourceSpan.document_version_id == document.id)
    )
    assert span is not None
    assert span.locator == ["not", "an", "object"]

    with pytest.raises(GildataMCPError) as exc_info:
        ingest(session, _make_client())

    assert str(exc_info.value) == "Gildata provider returned an invalid response"


def test_ingest_decimal_parsing():
    from app.scripts.ingest_real_data import _parse_decimal, _parse_date

    assert _parse_decimal("850.00") == Decimal("850.00")
    assert _parse_decimal("3.56e11") == Decimal("3.56E11")
    assert _parse_decimal("--") is None
    assert _parse_decimal("") is None
    assert _parse_decimal("1,234.5") == Decimal("1234.5")
    assert _parse_date("2026-05-06") is not None
    assert _parse_date("not-a-date") is None
