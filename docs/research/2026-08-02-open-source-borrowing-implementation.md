# 开源借鉴落地实施计划

> 日期：2026-08-02
> 状态：待评审
> 前置调研：[2026-07-30 证据图谱调研](./2026-07-30-open-source-evidence-graph-projects.md) · [2026-07-31 后端参考项目](./2026-07-31-backend-reference-projects.md) · [2026-07-31 产品基准](./2026-07-31-evidence-research-product-benchmarks.md) · [2026-08-01 VCRA 模块清单](./2026-08-01-vcra-module-borrowing-plan.md)

## 本文档要解决什么

前面四份调研回答了"有哪些开源项目可借鉴、边界是什么"。本文档回答"具体做什么、怎么做、做到什么程度算完成"。每个任务都包含：目标、改哪些文件、代码骨架、不可逾越的边界、验收标准。

## 任务总览

| # | 任务 | 优先级 | 依赖 | 产出 |
|---|---|---|---|---|
| T1 | SourceLocatorV1 契约 | P0 | 无 | `app/documents/locators.py` |
| T2 | Docling 适配器替换 pypdf | P0 | T1 | `app/datasources/docling.py` + 改造 `pdf_text.py` |
| T3 | AKShare 适配器 | P1 | 无 | `app/datasources/akshare/` |
| T4 | LangGraph 合规 rewrite loop | P1 | 无 | 改造 `assessment_gen.py` |

执行顺序：T1 → T2（有依赖），T3、T4 可并行。

---

## T1：SourceLocatorV1 契约

### 目标

把当前 `SourceSpan.locator`（自由 JSON dict）收敛为可验证的结构化契约。让任意一条证据都能回到冻结文档的精确位置（页码 + 坐标 + 字符区间 + 逐字引用）。

### 现状

`SourceSpan.locator` 当前有多种形态：

- pypdf 解析的 PDF：`{"page": n, "paragraph": m, "parser": "pypdf-v1"}`
- Gildata 文本材料：`{"kind": "research_report", "title": "...", "org": "...", ...}`
- 宏观时序：`{"kind": "macro_series", "query": "...", "metric_name": "...", ...}`

问题是：PDF 的 locator 只有页码+段落序号，没有坐标和字符区间；文本材料的 locator 是元数据而非定位信息。两者混在一个无 schema 的 JSON 字段里，无法统一校验。

### 设计原则

1. **不破坏存量数据**：已有的 locator dict 继续可读；新契约通过 `schema` 字段区分版本。
2. **定位信息与元数据分离**：`SourceLocatorV1` 只负责"在文档中的位置"；`kind`/`title`/`org` 等元数据保留在 locator dict 里但不属于定位契约。
3. **可验证**：提供 round-trip 校验函数——按 locator 重取文本，规范化后必须与 `verbatim_text` 一致。
4. **渐进采用**：PDF 走新契约，文本材料暂不强制迁移。

### 改动文件

**新增 `backend/app/documents/__init__.py`**：空文件。

**新增 `backend/app/documents/locators.py`**：

```python
"""SourceLocatorV1: versioned, verifiable source span locator.

A locator answers "exactly where in which frozen document is this text".
It is NOT metadata (title, org, publish_date) — those stay in the locator
dict alongside the schema version but are not part of the verification
contract.

The contract is borrowed from W3C Web Annotation (TextQuoteSelector +
TextPositionSelector) and Docling's ProvenanceItem (page_no / bbox /
charspan).  All four signals must be combined: page alone or bbox alone
cannot survive a parser version change.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

LOCATOR_SCHEMA = "source-locator/v1"


class BoundingBox(BaseModel):
    """Page-space bounding box in pixel coordinates, top-left origin."""
    l: float = Field(description="left")
    t: float = Field(description="top")
    r: float = Field(description="right")
    b: float = Field(description="bottom")
    origin: str = Field(default="top-left")


class TextQuote(BaseModel):
    """W3C TextQuoteSelector: exact match + context anchors."""
    exact: str = Field(description="verbatim quote from the source")
    prefix: str = Field(default="", description="~20 chars before exact")
    suffix: str = Field(default="", description="~20 chars after exact")


class SourceLocatorV1(BaseModel):
    """Verifiable pointer into a frozen DocumentVersion.

    Combines page number, bounding box, character offset and text quote.
    document_sha256 ties the locator to a specific frozen version.
    """
    schema: str = Field(default=LOCATOR_SCHEMA)
    document_sha256: str = Field(description="DocumentVersion.content_sha256")
    page: int = Field(ge=1)
    bbox: BoundingBox | None = None
    text_position: tuple[int, int] | None = None  # [start, end) in page text
    text_quote: TextQuote | None = None
    parser_item_ref: str | None = None  # e.g. "#/texts/57" (Docling self_ref)
    parser_version: str = Field(description="e.g. 'docling==2.115.0'")

    def model_dump_locator(self) -> dict:
        """Serialize to a plain dict for the SourceSpan.locator JSON column."""
        return self.model_dump(mode="json")
```

**round-trip 校验函数**（同文件）：

```python
def verify_locator(
    locator: SourceLocatorV1,
    page_text: str,
    verbatim_text: str,
) -> bool:
    """Return True iff the locator's text_quote.exact matches verbatim_text
    (after Unicode normalization) AND text_position falls within page_text.

    This is the fail-closed check: a locator that fails verification must
    never enter the evidence ledger.  Callers should treat a failed
    verification as a parse error, not a warning.
    """
    import unicodedata

    def norm(s: str) -> str:
        return unicodedata.normalize("NFKC", s).strip()

    if locator.text_quote is None:
        return False
    if norm(locator.text_quote.exact) != norm(verbatim_text):
        return False
    if locator.text_position is not None:
        start, end = locator.text_position
        if start < 0 or end > len(page_text) or start >= end:
            return False
        if norm(page_text[start:end]) != norm(verbatim_text):
            return False
    return True
```

### 边界

- **不改 `SourceSpan.locator` 列类型**：仍然是 JSON 列，不改为结构化类型。新 locator 序列化为 dict 存入。
- **不改数据库 schema / 不加迁移**：locator 是 JSON，新老格式共存，靠 `schema` 字段区分。
- **不改前端契约**：前端目前不直接消费 locator 内部结构，无需改 OpenAPI。
- **不迁移存量数据**：已有的 `{"page": n, "paragraph": m, "parser": "pypdf-v1"}` 继续可读；新 PDF 解析走新契约。
- **不做"元数据也 schema 化"**：`kind`/`title`/`org` 等业务元数据仍自由存放在 locator dict 里，不强制结构化。

### 验收标准

1. `SourceLocatorV1` Pydantic 模型可实例化、可 `model_dump` 为 dict、可从 dict `model_validate` 回来。
2. `verify_locator` 对"exact 匹配 + position 在范围内"返回 True；对任一不满足返回 False。
3. 新增单元测试 `tests/test_locators.py`：
   - 构造合法 locator，round-trip 序列化/反序列化一致。
   - `verify_locator` 正例（exact + position 都对）返回 True。
   - `verify_locator` 反例（exact 不匹配 / position 越界 / text_quote 为 None）返回 False。
4. `pytest tests/test_locators.py` 全绿。
5. 现有 347 条测试不受影响（不改任何已有文件逻辑）。

---

## T2：Docling 适配器替换 pypdf

### 目标

用 Docling 替换 pypdf 解析 PDF，输出 `SourceLocatorV1` 格式的 span。让财报表格、双栏研报、扫描件都能解析出页码、坐标、字符区间和表格结构。

### 现状

`app/services/pdf_text.py` 用 `pypdf` 抽文本层，按空行分段，CJK 软换行拼接。locator 是 `{page, paragraph, parser}`。无法获取 bbox、charspan、表格结构、阅读顺序。扫描件直接 fail-closed（无文本层则报错）。

### 依赖

- Docling v2.115.0（MIT 代码 + Apache 2.0 模型，商用安全）
- 模型首次下载 ~1.2GB（Heron + TableFormer），CPU 可运行
- 在 `backend/pyproject.toml` 的 `dependencies` 加 `"docling>=2.115"`，dev 不变

### 设计原则

1. **Docling 只负责解析，不碰账本**：适配器输出 `(SourceLocatorV1, verbatim_text)` 列表，写入账本仍走 `DocumentService.freeze` + `add_span`。
2. **parser_version 升级且可区分**：从 `pypdf-v1` 升到 `docling-v2.115.0`，`DocumentVersion.parser_version` 记录版本，旧版本数据不受影响。
3. **保留 pypdf 作 fallback**：不删除 `pdf_text.py`，但默认走 Docling；Docling 初始化失败或依赖缺失时降级到 pypdf 并记录 warning。
4. **fail-closed 不变**：扫描件无文本层仍然报错，不返回空列表。
5. **表格输出对接已有 `FinancialTableExtractor`**：Docling 的 `TableItem.data` 转换为行文本（保留表头+数值行结构），喂给 `FinancialTableExtractor.extract_numeric_values`。

### 改动文件

**新增 `backend/app/datasources/docling.py`**：

```python
"""Docling adapter: PDF -> (SourceLocatorV1, verbatim_text) spans.

Wraps Docling's DocumentConverter and maps its ProvenanceItem (page_no,
bbox, charspan) to our SourceLocatorV1 contract.  Table items are
serialized as row-preserved text blocks for FinancialTableExtractor.

The adapter is a pure function: input bytes, output spans.  It does NOT
touch the database, the ledger, or any domain model.  Freeze + add_span
stays in DocumentService.

parser_version is stamped as "docling-v{docling.__version__}" on every
DocumentVersion so re-runs can distinguish parser generations.
"""
from __future__ import annotations

from app.documents.locators import (
    BoundingBox,
    SourceLocatorV1,
    TextQuote,
)

PARSER_VERSION = "docling-v2.115.0"


class DoclingParseError(Exception):
    """Raised when Docling yields no extractable content."""


def extract_spans(raw: bytes) -> list[tuple[dict, str]]:
    """Return (locator_dict, verbatim_text) pairs for a PDF byte string.

    Raises DoclingParseError when no text or table content is extractable.
    """
    from docling.document_converter import DocumentConverter

    import hashlib

    doc_sha256 = hashlib.sha256(raw).hexdigest()
    converter = DocumentConverter()
    result = converter.convert(raw_bytes_or_path(raw))
    doc = result.document

    spans: list[tuple[dict, str]] = []
    for item in doc.texts:
        verbatim = item.text
        if not verbatim.strip():
            continue
        prov = item.prov[0] if item.prov else None
        if prov is None:
            continue
        locator = SourceLocatorV1(
            document_sha256=doc_sha256,
            page=prov.page_no,
            bbox=BoundingBox(
                l=prov.bbox.l, t=prov.bbox.t,
                r=prov.bbox.r, b=prov.bbox.b,
            ),
            text_position=tuple(prov.charspan) if prov.charspan else None,
            text_quote=TextQuote(
                exact=verbatim,
                prefix="",
                suffix="",
            ),
            parser_item_ref=item.self_ref,
            parser_version=PARSER_VERSION,
        )
        spans.append((locator.model_dump_locator(), verbatim))

    # Table items -> row-preserved text blocks for FinancialTableExtractor.
    for table in doc.tables:
        verbatim = _table_to_text(table)
        if not verbatim.strip():
            continue
        prov = table.prov[0] if table.prov else None
        page = prov.page_no if prov else 1
        bbox = None
        if prov and prov.bbox:
            bbox = BoundingBox(
                l=prov.bbox.l, t=prov.bbox.t,
                r=prov.bbox.r, b=prov.bbox.b,
            )
        locator = SourceLocatorV1(
            document_sha256=doc_sha256,
            page=page,
            bbox=bbox,
            text_position=None,
            text_quote=TextQuote(exact=verbatim, prefix="", suffix=""),
            parser_item_ref=table.self_ref,
            parser_version=PARSER_VERSION,
        )
        spans.append((locator.model_dump_locator(), verbatim))

    if not spans:
        raise DoclingParseError("Docling yielded no text or table content")
    return spans


def _table_to_text(table) -> str:
    """Serialize a Docling TableItem to newline-preserved row text.

    FinancialTableExtractor expects year-header + label+number rows
    preserved verbatim (see _is_table_block in pdf_text.py).  We output
    one row per line, cells joined by spaces.
    """
    data = table.data
    if data is None or not data.table_cells:
        return ""
    lines: list[str] = []
    for row_idx in range(data.num_rows):
        cells = []
        for col_idx in range(data.num_cols):
            cell = next(
                (
                    c
                    for c in data.table_cells
                    if c.start_row_offset_idx == row_idx
                    and c.end_row_offset_idx == row_idx + 1
                    and c.start_col_offset_idx == col_idx
                    and c.end_col_offset_idx == col_idx + 1
                ),
                None,
            )
            cells.append(cell.text if cell and cell.text else "")
        line = " ".join(c for c in cells if c)
        if line:
            lines.append(line)
    return "\n".join(lines)


def raw_bytes_or_path(raw: bytes):
    """Docling accepts a file path or a BytesIO-like object."""
    import io
    return io.BytesIO(raw)
```

**改造 `backend/app/services/pdf_text.py`**：

```python
"""PDF text-layer extraction with Docling (primary) and pypdf (fallback).

Docling provides page_no, bbox, charspan and table structure; pypdf is
retained as a zero-dependency fallback for environments where Docling's
model download is unavailable.  The parser_version stamped on each
DocumentVersion distinguishes the two paths.
"""
from __future__ import annotations

import warnings

DEFAULT_PARSER = "docling"


def extract_spans(raw: bytes, *, parser: str = DEFAULT_PARSER) -> list[tuple[dict, str]]:
    """Return (locator, verbatim_text) spans for a PDF byte string.

    parser="docling" (default) uses Docling's DocumentConverter.
    parser="pypdf" uses the legacy text-layer extractor.
    """
    if parser == "docling":
        try:
            from app.datasources.docling import (
                PARSER_VERSION as DOCLING_VERSION,
                DoclingParseError,
                extract_spans as _docling_extract,
            )
            return _docling_extract(raw)
        except ImportError:
            warnings.warn(
                "docling not installed; falling back to pypdf",
                stacklevel=2,
            )
        except DoclingParseError:
            raise
        except Exception as exc:
            warnings.warn(
                f"docling failed ({exc}); falling back to pypdf",
                stacklevel=2,
            )
    # Legacy pypdf path (unchanged).
    return _pypdf_extract(raw)


def _pypdf_extract(raw: bytes) -> list[tuple[dict, str]]:
    # ... 现有 extract_spans 逻辑原样搬到这里 ...
```

**改造 `backend/app/scripts/seed_*.py` 中的 PDF seed 脚本**：

三个 seed 脚本（`seed_ai_compute_case.py`、`seed_semiconductor_case.py`、`seed_storage_chain_case.py`）中调用 `pdf_text.extract_spans` 的地方，改为传 `parser="docling"`（默认值即如此，但显式标注以便测试可控制）。

**`backend/pyproject.toml`**：在 `dependencies` 加 `"docling>=2.115"`。

### 边界

- **不删除 pypdf**：保留作 fallback，CI 环境若未装 Docling 模型可降级。
- **不自动迁移存量 fixture**：三个冻结案例的 `parser_version` 是 `pypdf-v1`，不回填。新解析的文档才打 `docling-v2.115.0`。发布门禁的 `pdf_fixture_parse_gold` 检查若依赖 parser_version，需确认它接受新旧两种版本。
- **不引入 Docling Graph**：只用 `docling` 核心包，不引入 `docling-graph`。provenance ledger 的 `item_geometry` 设计思想已在 T1 的 `SourceLocatorV1` 中体现，但不需要 docling-graph 依赖。
- **不把 Docling 表格结构直接入库**：表格仍序列化为文本喂给 `FinancialTableExtractor`，不新建 `TableItem` 表。Docling 的 `column_header`/`row_span` 等结构信息不持久化到账本。
- **不改变 fail-closed 语义**：扫描件无文本层仍报错，不返回空列表伪装成功。
- **不做 OCR 质量评估**：OCR 结果的质量由 `content_quality.py` 现有的 `assess_span_texts` 守门，不在 Docling 适配器里重复判断。

### 验收标准

1. `pip install docling` 后，`from app.datasources.docling import extract_spans` 可正常导入。
2. 对 `tests/fixtures/storage_chain/06_sungrow_annual_summary.pdf`（现有唯一真实 PDF fixture）调用 `extract_spans`，返回非空 span 列表，每个 span 的 locator 包含 `page`、`bbox`、`text_quote.exact`、`parser_version="docling-v2.115.0"`。
3. `verify_locator` 对每个 span 返回 True（exact 匹配 verbatim_text）。
4. 未安装 docling 时，`extract_spans(raw, parser="docling")` 降级到 pypdf 并发出 warning，不崩溃。
5. 新增测试 `tests/test_docling_adapter.py`：
   - 真实 PDF fixture 解析出 span，locator 字段齐全。
   - `verify_locator` 全部通过。
   - 空/损坏 PDF 抛 `DoclingParseError`。
6. 现有 347 条后端测试不回归（pypdf fallback 路径保持不变）。
7. 发布门禁 `docs/evaluation/reproduce.sh` 仍然 9 PASS / 1 SKIP。

---

## T3：AKShare 适配器

### 目标

新建 `app/datasources/akshare/` 适配器包，获取 A 股列表、基金列表、基金持仓披露数据，填补账本中 `Fund` / `HoldingDisclosure` 表无数据源填充的缺口。

### 现状

- `app/datasources/gildata/` 已覆盖公告、研报、行情、宏观时序。
- 账本已有 `Fund`、`FundCompany`、`HoldingDisclosure`、`Stock`、`Company` 模型和 `InstrumentRepository` 写入方法。
- 但没有任何数据源填充基金和持仓数据——`HoldingDisclosure` 表在所有冻结案例中都是 seed 脚本手工写的。
- `ingest_real_data.py` 只调 Gildata，不涉及基金/持仓。

### 设计原则

1. **完全复刻 gildata 包结构**：`client.py`（薄封装 akshare 库）+ `adapters.py`（raw DataFrame → canonical dict）。
2. **绝不直接写账本**：适配器只返回 canonical dict，写入一律走 `InstrumentRepository.add_*` / `DocumentService.freeze`。
3. **时点语义严格分离**：持仓必须映射到 `report_period` / `published_at` / `acquired_at` 三字段，不允许合并。
4. **接口不稳定性隔离**：AKShare 接口频繁更名（上游东财/新浪改版），适配器层做版本锁定 + 失败降级，不让接口变更穿透到账本。
5. **不替代 Gildata**：AKShare 是补充源，用于交叉验证和填补 Gildata 不覆盖的基金/持仓数据。

### 改动文件

**新增 `backend/app/datasources/akshare/__init__.py`**：空文件。

**新增 `backend/app/datasources/akshare/client.py`**：

```python
"""Thin wrapper around the akshare library.

AKShare is a Python library (MIT) that scrapes public financial data
from Eastmoney/Sina/Tonghuashun.  Its interfaces change frequently as
upstream sites evolve; this client isolates that instability.

The client is a thin transport: it calls akshare functions and returns
raw DataFrames.  Canonical key mapping happens in adapters.py.
"""
from __future__ import annotations

import os


class AkshareError(Exception):
    """Raised on any akshare call failure."""


class AkshareClient:
    """Wraps akshare with lazy import and error normalization.

    akshare is imported lazily so the rest of the codebase doesn't pay
    the import cost or require the dependency unless AKShare is actually
    used.  from_env() raises if akshare is not installed — same
    fail-closed discipline as GildataMCPClient.
    """

    def __init__(self) -> None:
        try:
            import akshare as ak  # noqa: F401
        except ImportError as exc:
            raise AkshareError(
                "akshare is not installed; pip install akshare to use "
                "this data source"
            ) from exc
        self._ak = ak

    @classmethod
    def from_env(cls) -> "AkshareClient":
        return cls()

    def call(self, func_name: str, **kwargs):
        """Call an akshare function by name, returning the raw DataFrame."""
        func = getattr(self._ak, func_name, None)
        if func is None:
            raise AkshareError(f"akshare has no function '{func_name}'")
        try:
            return func(**kwargs)
        except Exception as exc:
            raise AkshareError(f"akshare '{func_name}' failed: {exc}") from exc
```

**新增 `backend/app/datasources/akshare/adapters.py`**：

```python
"""Domain adapters: akshare DataFrames -> typed canonical dicts.

Each fetch_* function returns a list of plain dicts with canonical keys.
Callers (ingest scripts / API) pass these to InstrumentRepository /
DocumentService — never directly to the database.

Time-point discipline: every holding carries report_period, published_at,
and acquired_at.  AKShare's column names map to these three fields; no
date is collapsed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from app.datasources.akshare.client import AkshareClient


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fetch_fund_list(client: AkshareClient) -> list[dict]:
    """Return [{code, name, type}] for all open-end funds.

    Calls ak.fund_name_em(); maps 东财 columns to canonical keys.
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
    """Return [{fund_code, stock_code, stock_name, weight, report_period}].

    Calls ak.fund_portfolio_hold_em; maps 持仓明细 to canonical holding
    dict.  report_period comes from the date_str parameter (季度报告期);
    published_at is inferred from data freshness (set to acquired_at as
    fallback — AKShare does not expose disclosure publication date).
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
            "published_at": acquired_at,  # AKShare 不暴露披露日，用采集日兜底
            "acquired_at": acquired_at,
        })
    return [r for r in results if r["stock_code"]]


def _parse_weight(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().rstrip("%").strip()
    try:
        return float(s)
    except ValueError:
        return None
```

**新增 `backend/app/scripts/ingest_akshare.py`**（编排脚本，仿 `ingest_real_data.py`）：

```python
"""Ingest fund + holding data from AKShare into the evidence ledger.

CLI:
    python -m app.scripts.ingest_akshare [--fund-codes 005827,110011]

Pulls fund list and per-fund holding disclosures, resolves stock/fund
identities via InstrumentRepository, and writes through the same
append-only ledger as Gildata.  No data touches the database outside
of DocumentService.freeze / InstrumentRepository.add_*.
"""
```

脚本结构仿 `ingest_real_data.py`：
1. `AkshareClient.from_env()` 建立连接。
2. `fetch_fund_list` 获取基金列表，对每只基金 `fetch_fund_holdings`。
3. 用 `ensure_stock`（复用 `ingest_real_data.py` 的同名函数）解析股票身份。
4. 用 `InstrumentRepository.add_fund` / `add_fund_company` 创建基金实体。
5. 用 `InstrumentRepository.add_holding_disclosure` 写入持仓，映射 `report_period` / `published_at` / `acquired_at`。
6. 返回 summary dict。

**`backend/pyproject.toml`**：在 `[project.optional-dependencies]` 新增：

```toml
akshare = ["akshare>=1.18"]
```

不放入主 `dependencies`——AKShare 是可选数据源，不装也能跑。

### 边界

- **不把 AKShare DataFrame 直接写库**：必须经适配器转 canonical dict，再走 `InstrumentRepository`。
- **不依赖接口签名稳定**：AKShare 接口更名频繁，适配器层捕获异常并降级。不做"接口变了就崩"的硬依赖。
- **不把 AKShare 当唯一真源**：持仓数据必须有 `report_period` + `source="akshare"` 标注，与 Gildata 数据区分。同一基金同一报告期的持仓若已有 Gildata 来源，不覆盖。
- **不暴露 AKShare 字段名到账本**：canonical key 映射在适配器层完成，账本只认 `fund_code`/`stock_code`/`weight`/`report_period` 等标准字段。
- **不做实时持仓**：所有持仓都标注"披露持仓，截至报告期"，不伪装为实时。
- **不引入 AKShare 的 pandas 依赖到核心路径**：pandas 仅在 akshare 适配器内部使用，不泄漏到 service / repository 层。

### 验收标准

1. `pip install "akshare>=1.18"` 后，`from app.datasources.akshare.adapters import fetch_fund_list` 可导入。
2. 未安装 akshare 时，`AkshareClient.from_env()` 抛 `AkshareError`（fail-closed），不静默降级。
3. `fetch_fund_holdings` 返回的每条 dict 都包含 `report_period`、`published_at`、`acquired_at` 三字段（后者用采集日兜底）。
4. 新增测试 `tests/test_akshare_client.py`：
   - `AkshareClient` 未安装 akshare 时抛 `AkshareError`。
   - `call` 对不存在函数名抛 `AkshareError`。
   - `fetch_fund_holdings` 的 canonical key 映射正确（用 mock DataFrame 验证）。
   - `_parse_weight("4.52%")` 返回 `4.52`，`_parse_weight("--")` 返回 `None`。
5. `ingest_akshare.py` 可独立运行（需 akshare 已安装 + `DATABASE_URL`），写入的 `HoldingDisclosure` 行 `source="akshare"`。
6. 现有测试不回归（akshare 是可选依赖，未安装时所有已有测试不受影响）。

---

## T4：LangGraph 合规 rewrite loop

### 目标

把 `AssessmentGenerator._ensure_compliant` 的手写单次重试循环改造为 LangGraph 的可中断图节点。让合规 rewrite 变成可暂停、可恢复、可观测的编排节点，而非藏在方法里的 if-else。

### 现状

`assessment_gen.py` 的 `_ensure_compliant` 逻辑：
1. 对 `rationale` + `gaps` 逐条跑 `evaluate_compliance`。
2. 如果有 REFUSE 类命中 → 直接抛 `ComplianceRefusedError`。
3. 如果全是 REWRITE 类命中 → 调 LLM 重写一次，重写后再跑合规检查，任何残留命中 → 拒绝。
4. 循环最多一次。

问题：重试逻辑硬编码在方法里，无法暂停（不能人工介入 rewrite）、无法观测中间状态、无法扩展为多步。

### 设计原则

1. **只编排 AI 链，不碰业务审核状态**：LangGraph State 只放"当前节点、待审文本、rewrite 尝试次数"，不放 `ReviewDecision` / `EvidenceReview` 等账本事实。
2. **checkpoint 复用现有 PG**：用 `PostgresSaver`，不引入 Redis 等新基础设施。checkpoint 表由 LangGraph 自管，不进 `IMMUTABLE_TABLES`。
3. **不替代 `AIRun` 审计**：`AIRun` 是不可变账本表，记录每次 AI 调用的最终结果；LangGraph checkpoint 是运行态，记录编排进度。两者职责不同。
4. **渐进改造**：先只改 `_ensure_compliant`，不改 `extract` / `propose` 的调用方式。当前这三步是同步直连的，暂不串成图。
5. **可回退**：如果 LangGraph 引入问题，`_ensure_compliant` 的原始逻辑保留为 fallback。

### 改动文件

**`backend/pyproject.toml`**：在 `[project.optional-dependencies]` 新增：

```toml
langgraph = ["langgraph>=1.2.6"]
```

不放主 dependencies——LangGraph 是可选编排层。

**新增 `backend/app/ai/compliance_graph.py`**：

```python
"""LangGraph-based compliance rewrite loop.

Replaces the hand-written single-retry if-else in
AssessmentGenerator._ensure_compliant with a checkpointed, interruptible
graph node.  The graph:

  assess_text -> [compliance_check] -> pass? -> done
                                  -> refuse? -> raise ComplianceRefusedError
                                  -> rewrite? -> [rewrite_node] -> [compliance_check]

State holds ONLY orchestration data: texts, attempt count, current
texts.  It does NOT hold ReviewDecision, AIRun, or any ledger fact.
"""
from __future__ import annotations

from typing import TypedDict

from app.services.compliance import (
    ComplianceAction,
    ComplianceDecision,
    ComplianceRefusedError,
    evaluate_compliance,
)


class ComplianceState(TypedDict, total=False):
    """Orchestration-only state.  No ledger facts here."""
    texts: list[str]           # [rationale, gap1, gap2, ...]
    rewritten: list[str]       # after rewrite attempt
    attempt: int                # 0 = original, 1 = after one rewrite
    max_attempts: int           # always 1 (bounded rewrite)
    status: str                 # "pass" | "refused" | "needs_rewrite"
    client: object              # LLMClient (not serialized in checkpoint)


def compliance_check(state: ComplianceState) -> ComplianceState:
    """Evaluate compliance for all texts.  Route to pass/refuse/rewrite."""
    texts = state.get("rewritten", state["texts"])
    decisions = [evaluate_compliance(t) for t in texts]
    first_hit = next((d for d in decisions if d.is_hit), None)
    if first_hit is None:
        return {**state, "status": "pass", "rewritten": texts}
    for d in decisions:
        if d.is_hit and d.action is ComplianceAction.REFUSE:
            raise ComplianceRefusedError(d)
    # All hits are REWRITE category -> route to rewrite.
    return {**state, "status": "needs_rewrite", "rewritten": texts}


def rewrite_node(state: ComplianceState) -> ComplianceState:
    """Call LLM to neutralize REWRITE-category hits.  One attempt only."""
    import json
    from app.ai.prompts import REWRITE_SYSTEM

    client = state["client"]
    texts = state.get("rewritten", state["texts"])
    messages = [
        {"role": "system", "content": REWRITE_SYSTEM},
        {"role": "user", "content": json.dumps({"texts": texts}, ensure_ascii=False)},
    ]
    result = client.chat_json(messages, schema_hint="rewrite")
    rewritten = result.get("texts", [])
    if not isinstance(rewritten, list) or len(rewritten) != len(texts):
        # Malformed rewrite -> refuse with original decision.
        raise ComplianceRefusedError(
            ComplianceDecision(
                is_hit=True,
                action=ComplianceAction.REFUSE,
                category="malformed_rewrite",
                keyword="",
            )
        )
    return {**state, "rewritten": [str(t) for t in rewritten], "attempt": 1}


def should_rewrite(state: ComplianceState) -> str:
    """Conditional edge: route after compliance_check."""
    status = state.get("status", "")
    if status == "pass":
        return "done"
    if status == "needs_rewrite" and state.get("attempt", 0) < state.get("max_attempts", 1):
        return "rewrite"
    # Exhausted retries or unknown status -> refuse.
    raise ComplianceRefusedError(
        ComplianceDecision(
            is_hit=True,
            action=ComplianceAction.REFUSE,
            category="rewrite_exhausted",
            keyword="",
        )
    )


def build_compliance_graph(client):
    """Build the compliance rewrite graph.

    Returns a compiled graph.  When langgraph is not installed, returns
    None and callers fall back to the original _ensure_compliant logic.
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        return None

    graph = StateGraph(ComplianceState)
    graph.add_node("compliance_check", compliance_check)
    graph.add_node("rewrite", rewrite_node)

    graph.set_entry_point("compliance_check")
    graph.add_conditional_edges(
        "compliance_check",
        should_rewrite,
        {"done": END, "rewrite": "rewrite"},
    )
    graph.add_edge("rewrite", "compliance_check")

    return graph.compile()
```

**改造 `backend/app/ai/assessment_gen.py`**：

在 `_ensure_compliant` 方法中：

```python
def _ensure_compliant(
    self, rationale: str, gaps: list
) -> tuple[str, list, bool]:
    """Non-investment-advice gate with one bounded rewrite attempt.

    Tries the LangGraph-based compliance graph first; falls back to the
    original hand-written loop if langgraph is not installed.
    """
    from app.ai.compliance_graph import build_compliance_graph

    graph = build_compliance_graph(self._client)
    if graph is not None:
        return self._ensure_compliant_via_graph(rationale, gaps, graph)
    return self._ensure_compliant_legacy(rationale, gaps)

def _ensure_compliant_via_graph(self, rationale, gaps, graph) -> tuple[str, list, bool]:
    """LangGraph path: invoke compiled graph, extract results."""
    texts = [rationale, *[str(g) for g in gaps]]
    initial_state: ComplianceState = {
        "texts": texts,
        "rewritten": texts,
        "attempt": 0,
        "max_attempts": 1,
        "status": "",
        "client": self._client,
    }
    # NOTE: client is not serializable; for checkpoint persistence, use
    # a factory or inject via config.  For sync in-process use, this is fine.
    final_state = graph.invoke(initial_state)
    rewritten = final_state.get("rewritten", texts)
    was_rewritten = final_state.get("attempt", 0) > 0
    return rewritten[0], rewritten[1:], was_rewritten

def _ensure_compliant_legacy(self, rationale, gaps) -> tuple[str, list, bool]:
    """Original hand-written loop (preserved as fallback)."""
    # ... 现有 _ensure_compliant 逻辑原样搬到这里 ...
```

### 边界

- **不把业务审核状态放进 State**：`ReviewDecision` / `EvidenceReview` / `review_state` 全部留在不可变账本表。LangGraph State 只放编排态（texts、attempt、status）。
- **不替代 `AIRun` 审计**：`AIRun` 记录最终结果（success/failed），LangGraph checkpoint 记录中间步骤。`AIRun` 不受影响。
- **不串 extract → propose → assess 为一张图**：当前只改 `_ensure_compliant` 这一个子环节。三个 generator 仍是同步独立调用。
- **不引入 checkpoint 持久化**：第一版用 in-process invoke（`graph.invoke`），不配 `PostgresSaver`。等需要跨进程恢复时再加。
- **不删除 legacy fallback**：`_ensure_compliant_legacy` 保留，未装 langgraph 时自动降级。
- **不改变合规语义**：REFUSE 立即拒绝、REWRITE 最多一次、重写后残留即拒绝——这些规则不变，只是执行方式从 if-else 变成图节点。
- **不把 `client`（LLMClient）序列化进 checkpoint**：in-process 调用时 client 是内存对象；若未来加 checkpoint 持久化，需用 config 注入而非 State 传递。

### 验收标准

1. `pip install "langgraph>=1.2.6"` 后，`build_compliance_graph` 返回 compiled graph（非 None）。
2. 未安装 langgraph 时，`build_compliance_graph` 返回 None，`_ensure_compliant` 降级到 legacy 逻辑，行为不变。
3. 无合规命中时：graph 走 `compliance_check → done`，返回原始 texts，`rewritten=False`。
4. 有 REFUSE 类命中时：`compliance_check` 抛 `ComplianceRefusedError`，行为与 legacy 一致。
5. 有 REWRITE 类命中时：graph 走 `compliance_check → rewrite → compliance_check → done`，返回重写后 texts，`rewritten=True`。
6. 重写后仍有命中时：`should_rewrite` 抛 `ComplianceRefusedError`（attempt 已达 max）。
7. 重写响应畸形（长度不匹配）时：`rewrite_node` 抛 `ComplianceRefusedError`。
8. 新增测试 `tests/test_compliance_graph.py`：
   - 上述 5 种场景各一条测试（用 mock LLMClient）。
   - 未安装 langgraph 时 fallback 路径测试。
9. 现有 `test_ai_engine.py` / `test_compliance.py` 不回归（legacy fallback 保证行为一致）。
10. `AIRun` 审计记录不变（仍记录 `conclusion` / `rewritten_for_compliance` 标记）。

---

## 跨任务约束

### 不做的事

1. **不引入第二个业务真相库**：Docling 的 DoclingDocument、LangGraph 的 checkpoint、AKShare 的 DataFrame 都不替代 PostgreSQL 账本。
2. **不把 LLM 自动抽取结果直接入库**：Docling 解析的结构化数据、AKShare 的持仓都走 Proposal/Review 流程或 `InstrumentService` 域校验，不绕过账本守卫。
3. **不改变 fail-closed 原则**：解析失败、provider 缺 key、数据源不可用都直接报错，不静默降级到空数据。
4. **不改变不可变账本原则**：新增的 checkpoint 表（LangGraph）不进 `IMMUTABLE_TABLES`；账本写入仍走 append-only + PG trigger 保护。

### 测试策略

- 每个任务的测试独立运行，不互相依赖。
- Docling 测试需安装 docling（CI 可选 job）；未安装时跳过，不影响主 CI。
- AKShare 测试用 mock DataFrame，不依赖网络。
- LangGraph 测试用 mock LLMClient，不依赖网络。
- 发布门禁 `reproduce.sh` 在所有任务完成后仍保持 9 PASS / 1 SKIP。

### CI 影响

- `backend-ci` 的 pytest job 需要能处理可选依赖：docling/akshare/langgraph 未安装时相关测试 skip（用 `pytest.importorskip`），不 fail。
- 不新增独立 CI job——这些是可选增强，不是必装依赖。

---

## 实施检查清单

按任务顺序，每完成一项打勾：

### T1：SourceLocatorV1 契约
- [ ] `app/documents/locators.py` 创建，`SourceLocatorV1` + `verify_locator` 实现完成
- [ ] `tests/test_locators.py` 创建，4+ 条测试全绿
- [ ] `pytest tests/test_locators.py` 通过
- [ ] 现有 347 条测试不回归

### T2：Docling 适配器
- [ ] `pyproject.toml` 加 `docling>=2.115`
- [ ] `app/datasources/docling.py` 创建，`extract_spans` + `_table_to_text` 实现完成
- [ ] `app/services/pdf_text.py` 改造为 Docling 优先 + pypdf fallback
- [ ] `tests/test_docling_adapter.py` 创建，用真实 PDF fixture 测试
- [ ] `pytest tests/test_docling_adapter.py` 通过（需安装 docling）
- [ ] 未安装 docling 时降级测试通过
- [ ] 现有测试不回归
- [ ] 发布门禁 `reproduce.sh` 仍 9 PASS / 1 SKIP

### T3：AKShare 适配器
- [ ] `pyproject.toml` 加 `akshare = ["akshare>=1.18"]` 到 optional-dependencies
- [ ] `app/datasources/akshare/client.py` 创建
- [ ] `app/datasources/akshare/adapters.py` 创建，`fetch_fund_list` + `fetch_fund_holdings` 实现完成
- [ ] `app/scripts/ingest_akshare.py` 创建，编排脚本可运行
- [ ] `tests/test_akshare_client.py` 创建，mock 测试全绿
- [ ] 未安装 akshare 时 fail-closed 测试通过
- [ ] 现有测试不回归

### T4：LangGraph 合规 loop
- [ ] `pyproject.toml` 加 `langgraph = ["langgraph>=1.2.6"]` 到 optional-dependencies
- [ ] `app/ai/compliance_graph.py` 创建，graph 构建 + 节点实现完成
- [ ] `app/ai/assessment_gen.py` 改造，`_ensure_compliant` 走 graph + legacy fallback
- [ ] `tests/test_compliance_graph.py` 创建，5+ 场景测试全绿
- [ ] 未安装 langgraph 时 fallback 测试通过
- [ ] 现有 `test_ai_engine.py` / `test_compliance.py` 不回归
- [ ] `AIRun` 审计记录不变
