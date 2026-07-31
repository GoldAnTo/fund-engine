"""Domain adapters that turn raw Gildata MCP payloads into typed dicts.

The Gildata ``tools/call`` response is double-wrapped:

1. JSON-RPC envelope ``{"result":{"content":[{"text": "..."}]}}`` -- the client
   returns ``content[0].text`` verbatim (a JSON *string*).
2. :func:`parse_content` decodes that string into the inner
   ``{"code":"0","results":[...]}`` payload and returns the ``results`` list.

Each result carries a ``table_markdown`` field whose content is one of three
shapes, parsed by :func:`parse_table_markdown_payload`:

* a JSON object/array (parsed first),
* a Markdown table (``|列|列|`` with a ``|---|`` separator), or
* a ``字段：值；`` key/value text block (used by the research-report tool).

The ``fetch_*`` helpers wire a :class:`~app.datasources.gildata.client.GildataMCPClient`
to these parsers and return plain dicts with canonical keys.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence

# Match a 6-digit A-share stock code embedded in free text (e.g. a title).
_SEC_CODE_RE = re.compile(r"(\d{6})")

# Header aliases: normalize various Chinese column names to canonical keys.
_QUOTE_ALIASES: dict[str, str] = {
    "股票名称": "stock_name",
    "证券简称": "stock_name",
    "名称": "stock_name",
    "股票代码": "stock_code",
    "证券代码": "stock_code",
    "代码": "stock_code",
    "最新价": "latest_price",
    "最新价(元)": "latest_price",
    "现价": "latest_price",
    "市盈率TTM": "pe_ttm",
    "市盈率(TTM)": "pe_ttm",
    "市盈率PE(TTM)": "pe_ttm",
    "市盈率": "pe_ttm",
    "市盈率(动态)": "pe_ttm",
    "市盈率LYR": "pe_lyr",
    "市盈率(LYR)": "pe_lyr",
    "市盈率PE(LYR)": "pe_lyr",
    "市盈率(静态)": "pe_lyr",
    "市净率": "pb",
    "市净率(MRQ)": "pb",
    "总市值": "total_mv",
    "总市值(元)": "total_mv",
    "总市值(亿元)": "total_mv",
    "流通市值": "negotiable_mv",
    "流通市值(元)": "negotiable_mv",
    "流通市值(亿元)": "negotiable_mv",
}

_ANNOUNCEMENT_ALIASES: dict[str, str] = {
    "公告标题": "title",
    "标题": "title",
    "公告名称": "title",
    "公告日期": "publish_date",
    "发布日期": "publish_date",
    "公告时间": "publish_date",
    "日期": "publish_date",
    "股票代码": "stock_code",
    "证券代码": "stock_code",
    "代码": "stock_code",
    "证券简称": "sec_name",
    "股票名称": "sec_name",
    "公告内容": "content",
    "正文": "content",
    "内容": "content",
}

# Research-report key/value field names -> canonical keys.
_REPORT_FIELD_ALIASES: dict[str, str] = {
    "报告标题": "title",
    "标题": "title",
    "撰写机构": "org",
    "机构": "org",
    "发布时间": "publish_date",
    "撰写时间": "write_date",
    "作者": "author",
    "证券简称": "sec_name",
    "证券代码": "sec_code",
    "行业": "industry",
    "原文": "content",
}


# ---------------------------------------------------------------------------
# Low-level markdown / text parsing
# ---------------------------------------------------------------------------


def _split_row(line: str) -> list[str]:
    """Split a Markdown table row into trimmed cell values.

    ``|a|b|`` -> ``["a", "b"]`` (empty border cells dropped).
    """
    parts = line.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip() for p in parts]


def _is_separator_row(cells: Sequence[str]) -> bool:
    """True when a row is the ``|---|---|`` alignment separator."""
    if not cells:
        return False
    return all(c.strip(":-").strip() == "" and "-" in c for c in cells)


def parse_table_markdown(table_markdown: str) -> list[dict[str, str]]:
    """Parse a Markdown table into a list of row dicts keyed by header name."""
    lines = [ln.strip() for ln in table_markdown.splitlines() if ln.strip()]
    if not lines:
        return []
    # A markdown table row must contain a pipe; otherwise this is key/value
    # text (handled by parse_kv_text), not a table.
    if "|" not in lines[0]:
        return []
    headers = _split_row(lines[0])
    if not headers:
        return []
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        cells = _split_row(line)
        if _is_separator_row(cells):
            continue
        # Pad/truncate to header width so dict assembly is positional.
        if len(cells) < len(headers):
            cells = cells + [""] * (len(headers) - len(cells))
        rows.append({headers[i]: cells[i] for i in range(len(headers))})
    return rows


def parse_kv_text(text: str) -> dict[str, str]:
    """Parse a ``字段：值；`` key/value block into a dict keyed by field name."""
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip().rstrip("；").rstrip(";").strip()
        if not line or "：" not in line and ":" not in line:
            continue
        key, sep, value = line.partition("：") if "：" in line else line.partition(":")
        key = key.strip()
        value = value.strip()
        if key:
            fields[key] = value
    return fields


def _normalize(row: dict[str, str], aliases: dict[str, str]) -> dict[str, str]:
    """Map Chinese keys to canonical keys; keep unknown headers under raw names."""
    out: dict[str, str] = {}
    for key, value in row.items():
        canonical = aliases.get(key, key)
        out[canonical] = value
    return out


def parse_table_markdown_payload(table_markdown: str) -> list[dict[str, str]]:
    """Parse a ``table_markdown`` value into row dicts.

    Tries JSON first, then a Markdown table, then a ``字段：值`` text block.
    """
    if not table_markdown:
        return []
    # 1. JSON object/array.
    try:
        parsed = json.loads(table_markdown)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    if isinstance(parsed, dict):
        return [parsed]
    # 2. Markdown table.
    rows = parse_table_markdown(table_markdown)
    if rows:
        return rows
    # 3. Key/value text.
    fields = parse_kv_text(table_markdown)
    if fields:
        return [fields]
    return []


def _extract_sec_code(text: str) -> str:
    """Pull the first 6-digit stock code out of free text (e.g. a report title)."""
    match = _SEC_CODE_RE.search(text or "")
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# Content (inner payload) parsing
# ---------------------------------------------------------------------------


def parse_content(text: str) -> list[dict]:
    """Decode the inner Gildata JSON string and return its ``results`` list.

    ``text`` is the ``result.content[0].text`` value returned by
    :meth:`GildataMCPClient.call_tool`, i.e. a JSON string shaped like
    ``{"code":"0","results":[...]}``.  Returns ``[]`` for blank/invalid input.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    try:
        inner = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(inner, dict):
        return []
    results = inner.get("results")
    if not isinstance(results, list):
        return []
    return [r for r in results if isinstance(r, dict)]


# ---------------------------------------------------------------------------
# fetch_* adapters
# ---------------------------------------------------------------------------


def fetch_research_report(client, query: str) -> list[dict]:
    """Pull research reports and return ``[{title, publish_date, org, author, content, sec_code}]``.

    Calls ``FinancialResearchReport``; each result's ``table_markdown`` is a
    ``字段：值`` block (报告标题/撰写时间/发布时间/撰写机构/作者/原文).  When the
    report exposes no explicit 证券代码, ``sec_code`` is extracted from the title.
    """
    text = client.call_tool("FinancialResearchReport", {"query": query})
    reports: list[dict] = []
    for item in parse_content(text):
        for row in parse_table_markdown_payload(item.get("table_markdown", "")):
            normalized = _normalize(row, _REPORT_FIELD_ALIASES)
            title = normalized.get("title", "")
            sec_code = normalized.get("sec_code", "") or _extract_sec_code(title)
            reports.append(
                {
                    "title": title,
                    "publish_date": normalized.get("publish_date", ""),
                    "org": normalized.get("org", ""),
                    "author": normalized.get("author", ""),
                    "content": normalized.get("content", ""),
                    "sec_code": sec_code,
                }
            )
    return reports


def fetch_announcement(client, query: str) -> list[dict]:
    """Pull announcements and return ``[{title, publish_date, stock_code, content}]``.

    Calls ``AnnouncementData``; ``table_markdown`` is parsed as a table first,
    falling back to a ``字段：值`` block.
    """
    text = client.call_tool("AnnouncementData", {"query": query})
    announcements: list[dict] = []
    for item in parse_content(text):
        for row in parse_table_markdown_payload(item.get("table_markdown", "")):
            normalized = _normalize(row, _ANNOUNCEMENT_ALIASES)
            announcements.append(
                {
                    "title": normalized.get("title", ""),
                    "publish_date": normalized.get("publish_date", ""),
                    "stock_code": normalized.get("stock_code", ""),
                    "content": normalized.get("content", ""),
                }
            )
    return announcements


def fetch_quote(client, query: str) -> list[dict]:
    """Pull market quotes and return a list of normalized row dicts.

    Calls ``FinQuery``; each result's ``table_markdown`` is a Markdown table
    (one row per security).  Canonical keys include ``stock_code``,
    ``stock_name``, ``latest_price``, ``pe_ttm``, ``pe_lyr``, ``pb``,
    ``total_mv``, ``negotiable_mv``; any unmapped column is preserved under its
    raw name.
    """
    text = client.call_tool("FinQuery", {"query": query})
    quotes: list[dict] = []
    for item in parse_content(text):
        for row in parse_table_markdown_payload(item.get("table_markdown", "")):
            quotes.append(_normalize(row, _QUOTE_ALIASES))
    return quotes
