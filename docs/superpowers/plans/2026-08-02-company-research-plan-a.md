# Plan A · 公司研究实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端 `/companies` 占位入口接上真实读写闭环：补齐估值快照写 API，新建公司中心读模型（CompanyDossier），前端新增公司列表与公司档案两页。

**Spec:** [2026-08-02-theme-company-research.md](../specs/2026-08-02-theme-company-research.md)（§1–§3、§4.2、§4.3、§4.5、§5、§7）。

**Architecture:** 沿用模块化单体：命令侧走 `InstrumentService`（域校验 → 422）+ `InstrumentRepository`（append-only 写入）；读侧新建 `queries/companies.py`，全部读模型统一 `HistoricalBasis` cutoff；前端只扩展 `ResearchClient` 领域接口，页面不感知后端字段。

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2, React 18, TypeScript, Vitest, Playwright。

---

## Scope

Included:

- `POST /api/v1/stocks/{stock_id}/valuation-snapshots` 命令 API（公司/股票写 API 已存在，不重复建设）。
- `GET /api/v1/companies`（列表，q 过滤 + 游标分页）与 `GET /api/v1/companies/{company_id}?cutoff=`（CompanyDossier）。
- 前端 `CompanyListPage` / `CompanyDossierPage`，替换 `/companies` 占位路由。
- `ResearchClient` 扩展 + Mock/HTTP 双适配器 + 契约测试。

Not included（属于 Plan B 或后续）:

- `theme_tags` 迁移、主题标签命令、主题读模型与主题页面（Plan B）。
- CausalStep/CausalEdge 命令 API（走查缺陷 2 剩余项，另行承接）。
- InstrumentIdentifier / 实体对齐审核（backend-design 阶段 4 其余部分）。

## File map

```text
backend/app/
  schemas/v1/instrument_commands.py   # + CreateValuationSnapshotRequest, ValuationSnapshotDTO
  schemas/v1/companies.py             # 新增：列表与 dossier 线网 DTO
  services/instruments.py             # + add_valuation_snapshot 域校验
  api/v1/commands/instruments.py      # + POST /stocks/{stock_id}/valuation-snapshots
  queries/companies.py                # 新增：CompanyReadQueries（list + dossier 组装）
  api/v1/companies.py                 # 新增：读路由 companies-v1
  api/v1/router.py                    # 注册 companies_router
backend/tests/
  test_instrument_commands_api.py     # + 估值快照命令测试（沿用 cmd_* fixtures）
  test_company_read_api_v1.py         # 新增：列表/详情/ cutoff 语义/下钻完整性
frontend/src/
  domain/types.ts                     # + CompanyListItem, CompanyDossierView 等领域类型
  data/researchClient.ts              # + listCompanies / getCompanyDossier
  data/mockResearchAdapter.ts         # + 公司场景数据（含空公司、冲突、未复核透出）
  data/httpResearchAdapter.ts         # + v1 DTO → 领域类型映射
  pages/CompanyListPage.tsx           # 新增：高密度公司表格 + 右侧预览检查器
  pages/CompanyDossierPage.tsx        # 新增：身份/股票、主题角色、关联命题、估值、基金披露
  main.tsx                            # /companies、/companies/:id 路由替换占位
frontend/tests 或 e2e/
  公司两页 vitest + Playwright（mock 只读双模式）
```

## 读模型语义（实现时不得偏离）

- `theme_roles`：`applicable_from ≤ cutoff.date()` 且（`applicable_to` 为空或 `≥ cutoff.date()`），与 `InstrumentRepository.stock_has_theme_role` 的既定口径一致；每条携带 case 标题与 SourceStatement → SourceSpan 回链。
- `related_theses`：经 ThemeRole 反查案例的全部命题；判断 = cutoff 下最新 AIAssessment（`provisional` 原样透出）+ 最新 ReviewDecision（存在则带出 outcome/conclusion/reason）；**不合成有效结论字符串**，AI/人工两条记录分离返回，由前端 StatusMark 渲染。
- `valuations`：每股票每指标取 `as_of_date ≤ cutoff` 最新一条，如实返回 `source`/`definition` 口径。
- `fund_holders`：`published_at ≤ cutoff`（当日 23:59:59 UTC 口径，同 penetration），每 (fund, stock) 只计最新报告期；返回基金代码/名称、weight、report_period、published_at、acquired_at、source。
- 公司在 cutoff 之后创建 → 404（与 dossier 的案例时点语义一致）。

## Tasks

- [ ] **Task 1 · 估值快照命令 API**
  `CreateValuationSnapshotRequest`（as_of_date / metric_name / metric_value / source / definition）+ `ValuationSnapshotDTO`；`InstrumentService.add_valuation_snapshot`：metric_name/source/definition 非空与长度校验、同 stock+metric+as_of+source 重复 → 422；路由 404 存在性检查（stock 不存在）。测试沿用 `cmd_*` fixtures：201 落账、404、422（空字段/重复）。

- [ ] **Task 2 · 公司读模型（schemas + queries + routes）**
  `schemas/v1/companies.py`：CompanyListItemDTO / CompanyListResponse（CursorPage）/ ThemeRoleViewDTO / RelatedThesisDTO / ValuationViewDTO / FundHolderDTO / CompanyDossierResponse（含 basis）。`queries/companies.py` 按上述语义组装，复用 `ResearchRepository` 与 `InstrumentRepository` 现有 readers。`api/v1/companies.py`：`GET ""`（q/limit/cursor）与 `GET "/{company_id}"`（cutoff）。注册进 v1 router。

- [ ] **Task 3 · 公司读 API 测试**
  新增 `test_company_read_api_v1.py`（api_client + workbench_case fixtures）：列表过滤与分页契约；dossier 五段齐全且 ThemeRole 来源可下钻到 span；cutoff 后创建的公司 404；`applicable_to` 过期角色消失；cutoff 后估值/披露不可见；无角色无持仓的空公司返回空段而非报错；AI 未复核与人工复核记录分离透出。

- [ ] **Task 4 · 前端领域类型与适配器**
  `domain/types.ts` 新增公司领域类型；`researchClient.ts` 增 `listCompanies(query, cursor)` / `getCompanyDossier(companyId, {cutoff})`；Mock 场景补齐（含空公司、证据冲突、AI 未复核、历史回放）；HTTP 适配器映射 + 适配器契约测试。

- [ ] **Task 5 · 公司两页**
  `CompanyListPage`：高密度表格（代码/名称/类型/股票数/角色数/命题状态摘要/最新披露期），行点击进档案，右侧检查器预览；`CompanyDossierPage`：左侧身份与股票，中央 主题角色（跨案例）→ 关联命题及判断（AI 草案琥珀、人工深绿，双编码）→ 估值快照 → 基金披露持仓（报告期/披露日/采集日 + 滞后文案），右侧复用 SourceInspector；URL 写入 cutoff/focus；证据冲突双栏；空公司三步空态。

- [ ] **Task 6 · 前端测试与契约**
  vitest：状态语义与空态；Playwright：列表 → 档案主流程、cutoff 回放、AI/人工边界（mock 只读，双模式可跑）。重新生成 `contracts/v1.ts`（OpenAPI → openapi-typescript），tsc 全绿。

- [ ] **Task 7 · 质量闭环**
  `pytest -q` 全绿无回退；`docs/evaluation/reproduce.sh` 门禁 10 项不回退；前端 `tsc --noEmit` + `npm test` + `npm run e2e` 全绿。

## 停止条件

- 任一公司读模型字段无法回链到账本记录（角色→案例、判断→assessment/review、持仓→披露行），则 Task 未完成。
- 任一页面绕过 `ResearchClient` 直接 fetch，或 cutoff 语义与案例侧 dossier 不一致，则 plan 未完成。
