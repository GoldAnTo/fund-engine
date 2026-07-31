# Fund Engine 后端设计文档

> 行业主题证据账本与投研工作台后端。本文档独立描述后端架构，不依赖前端实现。
>
> 对应代码：`backend/`

---

## 1. 概述

后端是一个**证据优先的投研账本**：把公告/财报/研报冻结为不可变材料，组织成可验证的行业命题，穿透到公司、股票、估值与基金持仓披露。

后端要回答三个问题：
1. 一个行业命题在当前证据快照下是 `supported` / `contradicted` / `insufficient_evidence`？
2. 这个判断由哪些原文、指标、因果环节和反证组成？
3. 这些关联穿透到哪些公司、股票和基金披露持仓？

**非目标**：自动买卖、目标价、投资建议；自动判断证据成熟或自动触发审核；将基金披露持仓伪装为实时持仓。

---

## 2. 架构分层

```
┌──────────────────────────────────────────────────────┐
│  API 层        app/api/         FastAPI 路由          │
├──────────────────────────────────────────────────────┤
│  Service 层    app/services/    业务校验与编排         │
├──────────────────────────────────────────────────────┤
│  Repository 层 app/repositories/ 只写持久化（仅 INSERT）│
├──────────────────────────────────────────────────────┤
│  Model 层      app/models/      SQLAlchemy 2 ORM      │
├──────────────────────────────────────────────────────┤
│  持久层        PostgreSQL 16    不可变证据账本（唯一真相）│
└──────────────────────────────────────────────────────┘
        │                              │
        │ 读账本组装                    │ 可重建投影
        ▼                              ▼
  WorkbenchService               Neo4j 5（投影，非真相）
```

关键原则：
- **PostgreSQL 是唯一写入真相**，Neo4j/前端都是可重建投影。
- **Repository 只暴露 INSERT**，不暴露 UPDATE/DELETE。
- **Service 层做校验**，Repository 不校验。
- **API 层只编排**，不含业务逻辑。

---

## 3. 数据模型（18 张表）

定义在 [app/models/ledger.py](../backend/app/models/ledger.py)。按领域分四组：

### 3.1 文档源组（材料冻结）
| 表 | 说明 | 关键字段 |
|----|------|----------|
| `document_versions` | 冻结材料版本（内容哈希寻址） | content_sha256(唯一), source_url, published_at, available_at, acquired_at, parser_version, supersedes_id |
| `source_spans` | 原文可复现位置 | document_version_id, locator(JSON), verbatim_text |

### 3.2 研究证据组（命题与论证）
| 表 | 说明 | 关键字段 |
|----|------|----------|
| `research_cases` | 行业研究档案 | title, industry_topic |
| `theses` | 可验证命题 | research_case_id, statement |
| `causal_steps` | 因果环节 | thesis_id, description, sequence |
| `causal_edges` | 传导关系（独立证据门槛） | source_step_id, target_step_id, rationale, creator_type, review_state |
| `source_statements` | 来源原子陈述 | source_span_id, kind, normalized_text, observed_period |
| `evidence_links` | 证据论证关系 | thesis_id, source_statement_id, role, reason, scope, available_at, creator_type, review_state |

### 3.3 判断组（AI/人工边界）
| 表 | 说明 | 关键字段 |
|----|------|----------|
| `evidence_snapshots` | 冻结证据集合 | thesis_id, cutoff, evidence_link_ids(JSON) |
| `ai_assessments` | AI 临时判断（不可变） | snapshot_id, conclusion, rationale, gaps, displayed_as_provisional |
| `review_decisions` | 人工复核（追加不覆盖） | ai_assessment_id, outcome, conclusion, reason, reviewer |

### 3.4 证券穿透组（时点暴露）
| 表 | 说明 | 关键字段 |
|----|------|----------|
| `companies` | 公司 | code, name, type |
| `stocks` | 股票（不放可变估值列） | company_id, code, name, market |
| `fund_companies` | 基金管理公司 | code, name |
| `funds` | 基金 | code, name, fund_type, scale, management_company_id |
| `valuation_snapshots` | 估值快照（不可变） | stock_id, as_of_date, metric_name, metric_value, source, definition |
| `holding_disclosures` | 基金持仓披露 | fund_id, stock_id, weight, report_period, published_at, acquired_at |
| `theme_roles` | 公司主题角色 | company_id, research_case_id, role, scope, applicable_from/to |

### 数据契约（Literal，不可随意改）
```python
AssessmentStatus = "supported" | "contradicted" | "insufficient_evidence"
EvidenceRole = "supports" | "contradicts" | "contextualizes"
SourceStatementKind = "disclosed_fact" | "management_attribution" | "forecast" | "research_opinion"
ReviewOutcome = "confirmed" | "modified" | "rejected"
ReviewState = "machine_generated" | "reviewed" | "rejected"
```

---

## 4. 不可变账本

所有 18 张表都是 append-only，两层防御：

**第一层：应用层 event guard**（[ledger.py](../backend/app/models/ledger.py#L82-L92)）
- SQLAlchemy `before_execute` 事件拦截所有 `UPDATE`/`DELETE`
- 命中 `IMMUTABLE_TABLES` 的语句 raise `ImmutableLedgerError`
- DB 无关（SQLite/PG 都生效）

**第二层：PG 触发器**（Alembic 0001/0002/0003）
- `reject_mutable_ledger()` 函数 + 每表 `BEFORE UPDATE/DELETE` 触发器
- 防御绕过应用直连 DB 的修改

**纠错方式**：追加 `supersedes_id` 后继记录，不覆盖原记录。

---

## 5. 双时间模型

区分"事实发生时间"与"事实可见时间"，支持历史回放：

| 字段 | 语义 |
|------|------|
| `published_at` | 材料发布时间 / 披露日 |
| `available_at` | 材料可获取时间（EvidenceLink）/ 进入账本时间（DocumentVersion） |
| `acquired_at` | 系统采集时间 |
| `observed_period` | 陈述所观察的业务期间（如 2026Q1） |
| `report_period` | 持仓报告期 |
| `as_of_date` | 估值快照日期 |
| `cutoff` | 快照截止时间（时间旅行锚点） |

**历史回放规则**：`visible_links(thesis_id, cutoff)` 只返回 `available_at <= cutoff` 的证据；`disclosures_visible_on_or_before(fund_id, as_of)` 只返回 `published_at <= as_of` 的披露。未来的材料在历史 cutoff 不可见。

---

## 6. 核心领域逻辑

### 6.1 时间旅行（[assessment.py](../backend/app/services/assessment.py)）
`freeze_snapshot(thesis_id, cutoff)` 取 `available_at <= cutoff` 的 EvidenceLink，冻结成 EvidenceSnapshot。同一命题在不同 cutoff 下有不同快照，互不影响。

### 6.2 AI/人工边界
- `create_ai_assessment` 写 AIAssessment，`displayed_as_provisional=True`，结论三态之一。
- `review(assessment_id, ...)` **追加** ReviewDecision，**不修改** AIAssessment。
- `get(assessment_id)` 返回原始 AI 结论，人工复核独立可见。
- **永不**计算 `ready_for_review` / `maturity_score` / 自动触发审核。

### 6.3 时点穿透（[exposure.py](../backend/app/services/exposure.py)）
`for_fund(fund_id, as_of)` 计算基金主题暴露：
1. `disclosures_visible_on_or_before(fund_id, as_of)`：`published_at <= as_of` 的披露
2. 同一股票取 `report_period` 最新的披露
3. 只保留 `stock_has_theme_role(stock_id, as_of)` 为真的股票
4. `theme_weight = sum(已映射股票的披露权重)`

**关键**：用披露日 `published_at` 决定可见性，不是最新组合；未来披露在历史 as_of 不可见。

### 6.4 证据链溯源
完整链路：`AIAssessment → EvidenceSnapshot → EvidenceLink → SourceStatement → SourceSpan → verbatim_text`。每条结论可回到原文逐字片段。

---

## 7. 分层架构

### Repository（只 INSERT + 只读查询）
- [documents.py](../backend/app/repositories/documents.py)：DocumentVersion/SourceSpan 持久化（by_hash/latest_for_source/insert_version/insert_span）
- [research.py](../backend/app/repositories/research.py)：研究实体 + 只读 reader（visible_links/latest_thesis/latest_assessment/span_for_statement 等）
- [instruments.py](../backend/app/repositories/instruments.py)：证券实体 + 时点查询（disclosures_visible_on_or_before/stock_has_theme_role/latest_valuation）

### Service（校验 + 编排）
- [ingest.py](../backend/app/services/ingest.py)：`DocumentService.freeze` 内容哈希去重 + 版本追加
- [research.py](../backend/app/services/research.py)：`add_statement` 校验 kind；`link_evidence` 校验 role/reason/scope/available_at
- [assessment.py](../backend/app/services/assessment.py)：快照/评估/复核
- [exposure.py](../backend/app/services/exposure.py)：时点穿透
- [workbench.py](../backend/app/services/workbench.py)：组装 WorkbenchResponse
- [projection.py](../backend/app/services/projection.py)：Neo4j 投影重建

---

## 8. API 契约

### `GET /api/research-cases/{case_id}/workbench?cutoff=`
返回聚焦工作台视图（[cases.py](../backend/app/api/cases.py)）。响应结构（见 [workbench.py](../backend/app/services/workbench.py#L99-L138)）：

```json
{
  "case": {"id", "title", "industry_topic"},
  "focus_thesis": {"id", "statement"} | null,
  "assessment": {"id", "conclusion", "rationale", "gaps", "provisional"} | null,
  "review": {"outcome", "conclusion", "reason"} | null,
  "major_gap": string | null,
  "graph": {
    "nodes": [{"id", "kind", "label", ...}],
    "edges": [{"id", "kind", "source", "target", ...}]
  },
  "evidence_drawer_records": [{"link_id", "statement_text", "verbatim_text", "locator", "reason", "role", "scope", "period", "review_state"}],
  "stock_valuation_snapshots": [{"stock_id", "stock_code", "metric_name", "metric_value", "as_of_date", "definition"}],
  "fund_holding_disclosures": [{"fund_code", "stock_code", "weight", "report_period", "published_at"}]
}
```

**graph edge.kind** ∈ `evidence` | `causal` | `theme_role` | `holding`
**永不**暴露 `recommendation` 字段。

### `GET /health`
`{"service": "industry-evidence-workspace", "status": "ok"}`

---

## 9. 图投影（Neo4j 可重建）

[projection.py](../backend/app/services/projection.py)：
- `rebuild_all()`：从 ledger 读所有实体，`MERGE`（keyed by ledger UUID）写入 Neo4j
- `clear_projection()`：只删本应用标签节点（`:EvidenceLedger`），不删无关数据
- [rebuild_graph_projection.py](../backend/app/scripts/rebuild_graph_projection.py)：CLI 重建脚本

**Neo4j 不是真相源**，清空后可从 PostgreSQL 无损重建。工作台读 API 直接从 ledger 组装 graph，不依赖 Neo4j。

---

## 10. 测试策略

- **单元测试（SQLite 内存）**：28 passed。覆盖去重/版本追加/不可变/校验/时间旅行/AI边界/时点穿透/工作台 API/release gate。
- **`pg_only` 测试**：PG 触发器相关，需真实 PG（`TEST_DATABASE_URL`）。
- **`neo4j_only` 测试**：投影重建，需 Neo4j（`NEO4J_URL`）。
- **release gate**：[verify_ai_compute_slice.py](../backend/scripts/verify_ai_compute_slice.py)，6 个 check（document_versions_present / assessment_source_spans_complete / holding_disclosures_dated / future_material_excluded / ai_human_boundary_visible / projection_rebuilds）。
- **种子切片**：[seed_ai_compute_case.py](../backend/app/scripts/seed_ai_compute_case.py)，AI 算力链 7 份冻结材料/34 span/3 公司/2 基金。

---

## 11. 目录结构

```
backend/
  app/
    api/cases.py              # 工作台路由
    models/ledger.py          # 18 表 + event guard + 数据契约
    repositories/             # documents/research/instruments（只 INSERT）
    services/                 # ingest/research/assessment/exposure/workbench/projection
    scripts/                  # seed + rebuild_graph_projection
    db.py                     # engine + get_db 依赖
    main.py                   # FastAPI app
  scripts/verify_ai_compute_slice.py  # release gate
  alembic/versions/           # 0001/0002/0003 migration
  tests/                      # 28 测试 + fixtures
```

---

## 12. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 语言 | Python 3.11 | LLM/数据生态 |
| Web | FastAPI | 异步 + 类型 + OpenAPI |
| ORM | SQLAlchemy 2 | Mapped 声明式 |
| 迁移 | Alembic | 版本化 schema |
| 账本 | PostgreSQL 16 | 触发器 + 事务 + 审计 |
| 图投影 | Neo4j 5 | 多跳关系（可重建） |
| 测试 | pytest | TDD |

---

## 13. 运行

```bash
# 本地服务（需 Docker 起 PG/Neo4j）
docker compose up -d postgres neo4j
cd backend && alembic upgrade head
.venv/bin/uvicorn app.main:app --reload

# 测试（SQLite，无需外部依赖）
cd backend && .venv/bin/pytest tests -q

# PG 集成测试
TEST_DATABASE_URL=postgresql+psycopg://evidence:evidence@localhost:5432/evidence .venv/bin/pytest tests -q

# release gate
cd backend && .venv/bin/python scripts/verify_ai_compute_slice.py
```
