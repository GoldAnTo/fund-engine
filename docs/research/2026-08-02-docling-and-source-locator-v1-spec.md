# Docling 接入 + SourceLocatorV1 升级 — 实施方案

> 日期：2026-08-02
> 状态：待 review（进入 plan 阶段前需 spec 签字）
> 关联：[2026-07-30 调研 §4 Docling](./2026-07-30-open-source-evidence-graph-projects.md#4-docling) / [2026-07-31 后端调研 §2 W3C Web Annotation + Docling provenance](./2026-07-31-backend-reference-projects.md#2-w3c-web-annotation--docling-provenance) / [设计文档 §6.1 不可变证据账本调整](../design/backend-design.md#61-不可变证据账本)

## 0. 范围

**做：**

1. `SourceLocatorV1` 版本化 Pydantic schema（替代 free-form dict）
2. Docling 解析适配器（接真实 PDF 财报 / 公告 / 研报）
3. `SourceSpan` 增加 `text_sha256 / context_hash`（设计文档 §6.1 早就规划）
4. 解析 round-trip validator（按 locator 重取文本必须哈希一致）
5. 解析失败结构化（失败阶段、错误码、可重试信号）
6. pypdf 路径作为兜底保留（不强制删），但 `DocumentVersion.parser_version` 区分清楚
7. Graph 投影对 document/span 节点的 locator 元数据升级

**不做（写明避免 scope creep）：**

- Docling 的全文索引 / 向量化检索（属 P1 召回升级，独立 spec）
- XBRL 结构化财务指标提取（bbox 基础先有，单独 spec）
- 跨时间 basis 模块（XTDB 风格，单独 spec）
- `DocumentVersion` 剩余元数据字段（`publisher / document_type / mime_type / blob_ref / byte_size / language / page_count`）—— 优先 `blob_ref` + `byte_size` 两个，加 `language` 用于 OCR 决策
- 完整切到 Docling 替代 pypdf（pypdf 仍可作轻量 PDF 兜底；切换由调用方按 capability probe 决定）
- 截图 OCR / VLM 模型集成（用 Docling 默认 text layer + 表格抽取，不引入模型权重）

## 1. 现状差距

| 维度 | 现状 | 缺口 |
|---|---|---|
| PDF 解析 | `pypdf`（`parser_version="pypdf-v1"`）只能抽 text layer，无 bbox / charspan / reading order / 表格结构 | 复杂版面（双栏研报、跨页表格、XBRL 财报）需 Docling |
| `SourceSpan.locator` | free-form `JSON` dict，已用键 `page / paragraph / table_row / char_start / char_end / parser / title / sec_name` | 任意 dict，无 schema 校验，无 round-trip 验证 |
| `SourceSpan.verbatim_text` | 仅存 raw text | 缺 `text_sha256`（重取校验）/ `context_hash`（上下文稳定性） |
| `DocumentVersion` 元数据 | `id / content_sha256 / source_url / natural_key / published_at / available_at / acquired_at / parser_version / supersedes_id` | 缺 `title / blob_ref / byte_size / language`（最小集合） |
| 解析失败 | `PdfParseError("PDF has no extractable text layer")` 单条 | 无失败阶段、无错误码、无可重试信号 |
| 解析器版本 | `ingest.py:11` 有 `PARSER_VERSION = "docling-v1"`（未使用）+ `pdf_text.py:23` 有 `PARSER_VERSION = "pypdf-v1"`（实际生效） | 命名遗留 + 容易误以为已接 Docling |
| Round-trip | 无 | locator 写完后不能再验证回原文本——下游引用可信度悬空 |

## 2. 目标 & 验收标准

| 目标 | 验收 |
|---|---|
| 真实 PDF 解析 | 金标 20 份（10 财报 + 5 公告 + 5 研报，来源 `gildata` + 公开 PDF）通过率 ≥ 90%；失败有结构化原因 |
| Locator 强约束 | 所有新写入的 `SourceSpan.locator` 通过 `SourceLocatorV1` Pydantic 校验；旧 span 在查询路径兼容 |
| Round-trip 必过 | 解析写入的每一对 `(locator, verbatim_text)` 通过 round-trip 验证（重抽文本 sha256 == 写库前 sha256） |
| 失败可观察 | 失败分 `EMPTY_PDF / TEXT_LAYER_MISSING / PARSE_EXCEPTION / TIMEOUT / LOCATOR_INVALID` 五类；AIRun 写 `parser_failures[]` |
| 现有测试不退化 | 现有 347 passed 全绿；新增 ≥ 30 测试（locator schema / Docling adapter / round-trip / 金标 e2e） |
| 端到端 e2e | 发布门禁新增 `pdf_docling_gold_parse`：20 份金标全过；缺金标时 fail-closed |

## 3. 关键设计决策

### 3.1 `SourceLocatorV1` Pydantic schema

Pydantic model，放 `backend/app/documents/locators.py`：

```python
class LocatorBbox(BaseModel):
    l: float
    t: float
    r: float
    b: float
    origin: Literal["top-left"] = "top-left"

class TextPosition(BaseModel):
    start: int   # Unicode 字符流 [start, end)
    end: int

class TextQuote(BaseModel):
    exact: str
    prefix: str = ""
    suffix: str = ""

class SourceLocatorV1(BaseModel):
    schema: Literal["source-locator/v1"] = "source-locator/v1"
    document_sha256: str = Field(min_length=64, max_length=64)
    page: int = Field(ge=1)
    bbox: LocatorBbox | None = None
    text_position: TextPosition | None = None
    text_quote: TextQuote | None = None
    parser_item_ref: str | None = None   # "#/texts/57" 类内部引用
    parser_version: str                   # "docling-v2.115.0" / "pypdf-v1"
    table_row: int | None = None          # 表格内的行号（0-based）
    table_col: int | None = None          # 表格内的列号（0-based）
    extra: dict[str, Any] = Field(default_factory=dict)  # 兜底：title / sec_name / 等老键
```

**必填：** `schema / document_sha256 / page / parser_version`
**三选一组合定位（参考 W3C Web Annotation 原则）：** `text_position` / `text_quote` / `bbox+parser_item_ref` 三组中至少一组非空；只有 `page` 一项的不算可定位（拒绝写入）。

**版本策略：** 字符串类型，不做枚举——不同 parser 版本直接体现在 `parser_version`，下游按前缀路由（`docling-*` / `pypdf-*`）。

### 3.2 解析器版本命名规范

| 解析器 | 版本格式 | 例子 |
|---|---|---|
| Docling | `docling-v{major}.{minor}.{patch}` | `docling-v2.115.0` |
| pypdf | `pypdf-v{major}.{minor}` | `pypdf-v1` |
| 旧 fixture（手写） | `fixture-v1` | `fixture-v1` |

升级时 `PARSER_VERSION` 常量与 `parser_version` 字段保持一致。`ingest.py:11` 的遗留常量删掉（注释里指向 `pdf_text.PARSER_VERSION` 与新增 `docling.PARSER_VERSION`）。

### 3.3 Docling 适配器形态

放 `backend/app/datasources/docling.py`：

```python
class PdfParserAdapter(Protocol):
    parser_version: str

    def extract_spans(self, raw: bytes) -> list[ParsedSpan]: ...

@dataclass(frozen=True, slots=True)
class ParsedSpan:
    locator: SourceLocatorV1
    verbatim_text: str
    text_sha256: str          # sha256(normalized(verbatim_text))
    context_hash: str         # sha256(page+prev_span+next_span) 供邻接稳定性

class DoclingAdapter:
    def __init__(self, *, enable_ocr: bool = False, enable_vlm: bool = False) -> None: ...

class PypdfAdapter:  # 现 pdf_text 逻辑迁移到这里
    ...
```

**选型：**
- 默认走 `DoclingAdapter`（不开 OCR / 不开 VLM，避免模型权重下载与数据外发）
- 调用方在 `DocumentService.freeze(...)` 时按 `parser_hint` 选择（默认 docling）
- 失败自动 fallback 到 `PypdfAdapter` 记录 `parser_failures[]`，但落库 `parser_version` 标实际跑通的那一个
- 解析超时：默认 30s/页，超时计入 `PARSE_TIMEOUT` 失败类

**Docling 依赖：**
- `docling`（MIT，模型许可按需核验）
- 不引入 docling-parse（GPU 加速），CPU 默认
- `easyocr` 不引（OCR 默认关；扫描件场景另开 spec）

### 3.4 解析失败结构化

```python
class ParseFailureCode(str, Enum):
    EMPTY_PDF = "empty_pdf"                       # 字节为空
    TEXT_LAYER_MISSING = "text_layer_missing"     # 扫描件，未启 OCR
    PARSE_EXCEPTION = "parse_exception"           # Docling 内部异常
    TIMEOUT = "timeout"                           # 单页超时
    LOCATOR_INVALID = "locator_invalid"           # round-trip 失败 / 缺定位

@dataclass(frozen=True, slots=True)
class ParseFailure:
    code: ParseFailureCode
    page: int | None
    detail: str
    recoverable: bool
```

写入 `AIRun` 的 `output_summary` 与新加的 `parse_failures` JSON 字段。`DocumentVersion` 仍创建（保留字节），但若**所有页都失败**则 `parse_state="failed"`；若部分成功 `parse_state="partial"`；全成功 `parse_state="success"`。

### 3.5 兼容与迁移

旧 span 升级到 v1 的策略：**不主动改写旧 locator**（账本不可变）。读路径做两件事：

1. `SourceSpan.locator` 查询结果先尝试 `SourceLocatorV1.model_validate()`；失败则原样返回 + 加 warning log（业务读路径接受）
2. 写路径（`DocumentService.add_span`）只接受 v1，旧 dict 直接 raise `LocatorInvalidError`（422）

`pypdf-v1` span 的 locator 已有 `page / paragraph / parser` 三键，迁移时一次性补 `document_sha256 / parser_version`（从 `DocumentVersion.content_sha256` / `parser_version` 拿），并按 paragraph 推导 `text_position` 起点（不强求精确，作为 best-effort）。

迁移由一次性脚本 `backend/app/scripts/migrate_locators_v1.py` 跑（不阻塞启动；幂等；写新行不动旧行——给 `SourceSpan` 加 `locator_v1: Mapped[dict | None]`，读路径优先取 `locator_v1`，fallback 到 `locator`）。

> 备注：账本不可变与"升级 locator 表达"是天然矛盾。两种走法：
> - (a) **就地写回**（破坏不可变）：直接 UPDATE `SourceSpan.locator`
> - (b) **新增 `locator_v1` 字段**（保留旧值）：读路径双源、写路径单一
>
> 选 (b)。账本不可变是硬原则，locator 升级是可恢复的（重跑解析可得新 v1）。

### 3.6 Graph 投影升级

`backend/app/queries/graph.py` 当前已在 `(span_id, document_id)` 维度加 `document + span` 节点与 `contains / derived` 边。本 spec 在其上做：

- document 节点的属性补充 `parser_version` / `language` / `title`
- span 节点的属性补充 `text_sha256`（供引用反查时的"原文是否未变"快速断言）
- 不引入新节点类型 / 新边类型（保持当前 5 列布局）

## 4. 模块边界 / 文件清单

### 新增

```text
backend/app/documents/
  locators.py                  # SourceLocatorV1 + 校验器 + round-trip
backend/app/datasources/
  docling.py                  # DoclingAdapter + PypdfAdapter + PdfParserAdapter 协议
  parsing.py                  # 解析编排（fallback + 失败聚合）
backend/app/scripts/
  migrate_locators_v1.py      # 旧 pypdf-v1 locator 一次性补字段
backend/tests/
  test_locator_v1.py          # schema 校验 + round-trip
  test_docling_adapter.py     # DoclingAdapter 单元测试（mock docling）
  test_pdf_parser_fallback.py # Docling 失败 → Pypdf 兜底
docs/evaluation/datasets/
  pdf_docling_gold/           # 20 份金标 PDF（10 财报 + 5 公告 + 5 研报）
```

### 改动

```text
backend/app/models/ledger.py
  + SourceSpan.text_sha256 (String(64), nullable=True)
  + SourceSpan.context_hash (String(64), nullable=True)
  + SourceSpan.locator_v1 (JSON, nullable=True)  # 旧 locator 不动
  + DocumentVersion.title (String(512), nullable=True)
  + DocumentVersion.byte_size (Integer, nullable=True)
  + DocumentVersion.language (String(16), nullable=True)
  + DocumentVersion.parse_state (String(16), default="pending")

backend/app/services/ingest.py
  - 删除 PARSER_VERSION 残留常量
  + DocumentService.freeze(...) 接受 parser_hint 参数
  + DocumentService.add_span(...) 走 v1 locator 校验

backend/app/services/pdf_text.py
  - 删 PypdfAdapter 主体（迁移到 datasources/）
  + 仅保留兼容 re-export（让旧 import 不断）

backend/app/repositories/documents.py
  + insert_span 接受 locator_v1 / text_sha256 / context_hash
  + 旧 add_span 路径保持兼容（locator_v1 留空时仍可写）

backend/app/queries/documents.py
  + SourceSpanDTO 加 locator_v1 / text_sha256 字段
  + 读路径优先取 locator_v1，fallback 到 locator

backend/app/queries/graph.py
  + document 节点带 parser_version / language / title
  + span 节点带 text_sha256

backend/app/main.py / alembic 0009
  + alembic 迁移：SourceSpan 与 DocumentVersion 新字段
  + SQLite test 走 Base.metadata.create_all（与现状一致）

backend/tests/test_pdf_and_storage_seed.py
  - 移除 "parser == pypdf-v1" 硬编码断言（改成 parser_version 集合 + 至少 1 个 docling-v*）
  + 新增 20 份金标 e2e

docs/evaluation/reproduce.sh
  + 新增 pdf_docling_gold 门禁
  + 缺金标时 fail-closed
```

## 5. 实施步骤

按 TDD + 串行可验证顺序：

### S1 — `SourceLocatorV1` Pydantic + 校验器（半天）

**交付物：**
- `backend/app/documents/locators.py`：schema + validator + `text_sha256` 工具
- `backend/tests/test_locator_v1.py`：≥ 15 测试（必填字段、三选一定位、extra 兜底、非法 shape 拒绝）

**验收：** `pytest backend/tests/test_locator_v1.py -q` 全绿；现有 347 不退化。

### S2 — PypdfAdapter 迁移 + DoclingAdapter 骨架（半天）

**交付物：**
- `backend/app/datasources/docling.py`：协议 + PypdfAdapter（搬 `pdf_text.py` 主体）+ DoclingAdapter 骨架（不实际接，先把接口对齐）
- `backend/app/services/pdf_text.py`：变成 re-export 兼容层

**验收：** 现有 `test_pdf_and_storage_seed.py` 不变（locator 仍 `page/paragraph/parser`），迁移后跑同样 PDF 产出相同 span。

### S3 — DoclingAdapter 真实实现 + 金标（2 天）

**交付物：**
- `backend/app/datasources/docling.py`：`DoclingAdapter.extract_spans()` 真接 docling
- `docs/evaluation/datasets/pdf_docling_gold/`：20 份金标 PDF + manifest
- `backend/tests/test_docling_adapter.py`：mock + 真实 1-2 份 PDF 单测
- `backend/tests/test_pdf_parser_fallback.py`：Docling 抛错 → Pypdf 兜底

**验收：** 金标 20 份通过率 ≥ 90%；fallback 单测全过。

### S4 — ledger 模型扩展 + alembic 0009 + 旧 locator 迁移脚本（半天）

**交付物：**
- `backend/app/models/ledger.py`：SourceSpan + DocumentVersion 新字段
- `alembic/versions/0009_*.py`：PG 迁移 + trigger 不变；SQLite 测试走 `create_all`
- `backend/app/scripts/migrate_locators_v1.py`：幂等迁移
- `backend/tests/test_migrate_locators_v1.py`：迁移前后读路径行为一致

**验收：** PG 迁移可重放（up/down）；SQLite 测试不报 column missing。

### S5 — ingest / documents / graph 接通 + 端到端金标（1 天）

**交付物：**
- `DocumentService.freeze(..., parser_hint="docling")` 默认走 Docling
- `documents.py` 与 `graph.py` 读路径升级
- `docs/evaluation/reproduce.sh` 加 `pdf_docling_gold` 门禁
- `docs/evaluation/dataset-manifest.json` 升 v3

**验收：** `docs/evaluation/reproduce.sh` 9 PASS + 1 SKIP → 10 PASS + 1 SKIP（Neo4j 仍 skip）；`eval_recall_ab.py` 不退化；金标 20 份全过；现有 347 + 新增 30 = 377 passed。

## 6. 测试与门禁

| 类型 | 位置 | 关键 |
|---|---|---|
| 单元 | `tests/test_locator_v1.py` | schema 校验、round-trip、必填字段 |
| 单元 | `tests/test_docling_adapter.py` | 真实 PDF + mock docling |
| 单元 | `tests/test_pdf_parser_fallback.py` | Docling 失败 → Pypdf 兜底；失败结构化 |
| 集成 | `tests/test_migrate_locators_v1.py` | 旧 locator 迁移后读路径等价 |
| 集成 | `tests/test_pdf_and_storage_seed.py`（更新） | 至少 1 份 docling-v* + 1 份 pypdf-v* |
| 端到端 | `docs/evaluation/reproduce.sh` | `pdf_docling_gold` 门禁，缺金标 fail-closed |
| 端到端 | `scripts/eval_recall_ab.py` | recall@20 不退化（仍 ≥ 1.0000） |
| CI | `.github/workflows/backend.yml` | 跑新门禁；金标 fixture 走 LFS 或 gitattributes |

## 7. 风险与依赖

| 风险 | 应对 |
|---|---|
| Docling 模型许可 / 数据外发 | 默认不开 OCR / VLM；CPU text layer + 表格抽取；模型权重按需核验 |
| Docling 解析慢（CPU） | 单页 30s 超时计入失败类；fallback pypdf 仍能跑 |
| 金标材料版权 | 优先公开 PDF（公司年报、监管披露）；内部金标不进 git，用 git LFS 或独立 bucket |
| 账本不可变 vs locator 升级 | 用 `locator_v1` 双字段（方案 3.5 选 b） |
| Graph 投影一次到位 | document/span 节点属性扩展，不引入新节点类型 |
| 旧 locator 兼容 | 读路径 `model_validate` 失败原样返回 + warning，不阻塞 API |

## 8. 后续 spec 链接

- P1：Docling dense leg 召回（用 embedding 替代 char-n-gram）
- P1：N-PORT 风格持仓 adapter（接真实持仓披露）
- P1：faithfulness 评测（RAGAS / trulens 风格）
- P1：basis 模块集中化（XTDB 风格双时间）
- P2：XBRL 结构化财务指标提取
- P2：OCR / VLM 模型集成（扫描件场景）

## 9. 实施前 review 检查清单

- [ ] 用户 review 本 spec，签字
- [ ] 选 Docling 主版本（建议先固定 `docling>=2.115,<3.0`）
- [ ] 金标 20 份 PDF 落定（开源年报 + 公开公告优先）
- [ ] PG 迁移 0009 写好 + SQLite `create_all` 路径验证
- [ ] CI 工作流接受新增依赖（docling / easyocr 不在 wheel 列表里的话要单独处理）
- [ ] `test_pdf_and_storage_seed.py` 旧断言迁出方案对齐
