# 主题研究与公司研究模块设计

> 状态：待评审
>
> 日期：2026-08-02
>
> 前置文档：[CONTEXT.md](../../../CONTEXT.md)（词汇表）、[backend-design.md](../../design/backend-design.md)（阶段 4 预留）、[frontend-design.md](../../design/frontend-design.md)（后续入口）、[2026-08-02 寒武纪走查](../../evaluation/walkthrough/2026-08-02-cambricon-full-pipeline-walkthrough.md)（缺陷 2 写路径断点）

## 1. 目标与产品边界

前端导航中的 `主题研究（/topics）` 与 `公司研究（/companies）` 目前是 `NotImplementedPage` 占位。本设计把这两个入口接上真实读写闭环，回答两类现有案例中心视图回答不了的问题：

1. **公司研究**：一家公司当前在哪些研究案例中扮演什么角色？这些角色由哪些命题、证据和原文支撑？它的股票估值快照和基金披露持仓是什么（在某个 cutoff 下）？
2. **主题研究（横切）**：一个横跨多个 ResearchCase 的主题下，有哪些公司、哪些命题被支持或反驳、整体基金暴露如何？

两个模块都**只是账本上的读投影加上少量补齐的写路径**，不引入新的判断主体：

- 公司层不产生任何独立结论；公司页展示的每个状态都必须能回链到案例层的 Thesis / AIAssessment / ReviewDecision / SourceStatement / SourceSpan。
- 主题层不是新的聚合根，不存储任何主题级结论；主题页的每个聚合数字都必须能展开还原到具体案例、命题和 ThemeRole。

### 非目标

- 不新建 `Theme` 聚合根或主题级 AIAssessment / ReviewDecision；主题视图永远从案例层有效状态派生。
- 不做公司对比排名、不输出"重点公司名单"等推荐性表达。
- 不给主题或公司计算成熟度、置信度或暴露评分之外的合成指标；敞口表达本身不是推荐。
- 不把基金披露持仓展示为实时或当前持仓；滞后提示沿用既有文案规范。
- 不做公司基本面工作台（财务三表、盈利预测、估值模型）；ValuationSnapshot 只按现有口径展示。
- 不在本轮做 InstrumentIdentifier / 实体对齐审核（backend-design 阶段 4 的其余部分另行设计）。
- 不改动既有五个页面的契约与断言；新页面以追加方式落地。

## 2. 领域语言补充

完整术语以 [CONTEXT.md](../../../CONTEXT.md) 为准。以下两条是本轮新增的工作定义，不进 CONTEXT 核心词汇表，除非评审通过。

**CompanyDossier（公司档案，读模型）**：
以 Company 为入口的逆向视图：身份与股票 → 跨案例 ThemeRole（含适用期间与来源回链）→ 关联 Thesis 及其有效判断 → ValuationSnapshot → 持有其股票的 HoldingDisclosure。它是组装结果，不落库。
_Avoid_：公司评级、公司结论

**ThemeView（横切主题视图，读模型）**：
对共享同一主题标签的所有 ResearchCase 的聚合投影：参与案例及其命题有效状态、跨案例 ThemeRole 按公司归组、基金暴露的加总与构成。主题身份来自案例级主题标签（见 4.1），不是独立实体。
_Avoid_：主题指数、主题推荐、主题级结论

## 3. 当前实现审计（本设计的起点）

| 能力 | 现状 | 本设计的处置 |
|---|---|---|
| Company / Stock / ValuationSnapshot 账本表 | 存在（`models/ledger.py:346-415`），仅种子脚本写入 | 补写 API（4.2） |
| ThemeRole 账本表 | 存在，含 `applicable_from/to`、`source_statement_id` 回链 | 读模型按 cutoff + 有效期过滤 |
| Fund / HoldingDisclosure 写 API | 已有（2026-08-02，instrument-commands-v1） | 复用 |
| ThemeRole 写 API | 已有（`POST /companies/{id}/theme-roles`） | 复用 |
| Company / Stock / ValuationSnapshot 写 API | **缺失**（走查缺陷 2 记录的断点） | 本轮补齐 |
| 案例中心读模型 | dossier / graph / fund-exposure / search（含 company/stock/fund 分组） | 不动 |
| 公司中心 / 主题中心读模型 | 不存在 | 本轮新建 |
| 前端 `/companies`、`/topics` 路由 | `NotImplementedPage` 占位 | 替换为真实页面 |

## 4. 后端设计

### 4.1 主题身份：案例级主题标签

横切主题需要一个稳定的分组键。主题身份 = 标签字符串本身，案例与标签的关联通过新建的 append-only 事件表 `case_theme_tag_events`（op = add/remove，有效标签由事件折叠派生）管理。理由：

- 避免引入 Theme 聚合根和第二套版本/审核语义；
- `research_cases` 是 append-only 账本表，直接加列 UPDATE 会被 guard 拒绝；事件表与账本"只追加、可审计"原则一致，标签变更天然留痕；
- 标签是分类元数据，不是研究判断，不参与有效状态派生；
- 同一案例可属于多个横切主题（如"AI 算力链"同时挂 `算力国产化`、`云厂商CapEx`）。

Alembic 迁移 0007 建新表；既有金标数据集与发布门禁不受影响。标签受控词汇为代码内显式集合（版本控制、可评审），不在词汇内 → 422。

### 4.2 前置写路径（命令 API 补齐）

沿用 `instrument-commands-v1` 的既有模式（路由层 404 存在性检查、`services/instruments.py` 域校验抛 `ValidationError → 422`）：

- `POST /api/v1/companies`：建公司（code 重复、空 name/type → 422）。
- `POST /api/v1/companies/{company_id}/stocks`：建股票（company 不存在 → 404；同 company 下 code 重复、空 market → 422）。
- `POST /api/v1/stocks/{stock_id}/valuation-snapshots`：录估值快照（stock 不存在 → 404；`as_of_date` 缺省、`metric_value` 非正口径校验、`definition` 必填 → 422；同 stock+metric+as_of+source 重复 → 422）。
- `PATCH /api/v1/research-cases/{case_id}/theme-tags`：受控更新主题标签（标签不在受控词汇 → 422；变更写入审计事件）。

CausalStep/CausalEdge 命令 API 仍缺，但两个新模块的读路径不依赖手工建因果链，**不在本轮范围**，继续由后续计划承接。

### 4.3 公司中心读模型

新增 `queries/companies.py` 与路由 `api/v1/companies.py`：

- `GET /api/v1/companies?q=&cursor=`：公司列表（id、code、name、type、股票数、ThemeRole 数），支持按名称/代码过滤，游标分页契约与 documents 一致（`limit+1` / `has_more`）。
- `GET /api/v1/companies/{company_id}?cutoff=`：CompanyDossier，统一走 `HistoricalBasis`：
  - `identity`：公司与全部 Stock；
  - `theme_roles`：该公司全部 ThemeRole，按 `applicable_from/to` 与 cutoff 双重过滤（落实 backend-design 问题 13 的时间语义集中管理），每条携带所属案例、来源 SourceStatement 回链（可下钻到 SourceSpan / DocumentVersion）；
  - `related_theses`：经 ThemeRole 反查的命题及其**有效判断**（effective review state 派生，与 dossier/compare 同源；AI 未复核状态必须原样透出）；
  - `valuations`：每只股票 `as_of_date ≤ cutoff` 的 ValuationSnapshot，按指标分组、按日期降序，如实返回 `source` 与 `definition` 口径；
  - `fund_holders`：持有该公司股票的 HoldingDisclosure，`published_at ≤ cutoff`，携带报告期、披露日、采集日与 `source`；权重口径如实记录数据源语义（占流通 A 股 vs 占净值并存，不强行统一）。

### 4.4 主题中心读模型

新增 `queries/themes.py` 与路由 `api/v1/themes.py`：

- `GET /api/v1/themes`：主题列表（标签、案例数、公司数、命题数），从 `theme_tags` 聚合，空标签不出现。
- `GET /api/v1/themes/{tag}?cutoff=`：ThemeView：
  - `cases`：参与案例清单，各案例命题的有效状态计数（supported / contradicted / insufficient_evidence / 未复核 AI 草案），**不合成主题级总结论**；
  - `company_roles`：跨案例 ThemeRole 按公司归组，每行 = 公司 × 案例 × 角色 × 适用期间 × 来源回链；
  - `fund_exposure`：对主题内已映射股票的 HoldingDisclosure 做聚合（复用 penetration 的口径与水位逻辑），返回构成明细而非单一数字；
  - 所有聚合字段携带 `derived_from` 引用列表（case_id / thesis_id / theme_role_id），保证每个数字可展开还原。

### 4.5 时点语义

- 两个读模型全部接入统一 `HistoricalBasis`；cutoff 语义 = 系统账本时间（走查缺陷 6 的既有诚实边界），页面文案不得暗示市场时间回放。
- ThemeRole 过滤规则：`applicable_from ≤ cutoff.date()` 且（`applicable_to` 为空或 `> cutoff.date()`），与 `plans/2026-07-31-backend-live-read-slice.md` 第 704 行既定规则一致。
- 估值、持仓的"数据过时"状态沿用前端既有规范（展示报告期/披露日/采集日 + 滞后文案）。

## 5. 前端设计

遵循 frontend-design.md 的既有套路：先领域类型与 mock，后 HTTP 适配，页面不感知后端字段。

### 5.1 ResearchClient 扩展

```ts
researchClient.listCompanies(query, cursor)
researchClient.getCompanyDossier(companyId, { cutoff })
researchClient.listThemes()
researchClient.getThemeView(tag, { cutoff })
```

新增前端领域类型 `CompanyListItem`、`CompanyDossierView`、`ThemeListItem`、`ThemeView`，由 `MockResearchAdapter` 提供场景数据，`HttpResearchAdapter` 负责 v1 DTO 映射；契约测试覆盖双向映射。

### 5.2 页面

沿用产品壳三栏语法，替换 `NotImplementedPage`：

- **公司研究 `/companies`**：中央高密度公司表格（代码、名称、角色数、命题状态摘要、最新披露期）；右侧检查器预览角色与来源。
- **公司档案 `/companies/:id`**：左侧身份与股票，中央依次为主题角色（跨案例）、关联命题及有效判断、估值快照、基金披露持仓；右侧 SourceInspector 复用（角色来源可下钻到冻结原文）。证据冲突时支持/反证双栏同时呈现，遵守既有页面状态规范。
- **主题研究 `/topics`**：主题列表 + 每个主题的案例/公司/命题概览。
- **主题视图 `/topics/:tag`**：中央按案例分组的命题状态矩阵（有效状态用颜色+文字双编码）、公司×角色表、基金暴露构成；每个聚合数字可展开到 `derived_from` 明细。页面顶部固定提示：主题视图是案例层判断的聚合投影，不构成主题级结论。

URL 状态（tab / focus / cutoff / cursor）写入地址栏，与既有页面一致；`?client=mock` 下全部可 e2e。

### 5.3 必须覆盖的页面状态

在既有 10 个 MockScenario 之上，新页面至少验证：空主题（无标签案例）、空公司（无角色无持仓）、证据冲突、AI 未复核透出、历史回放（cutoff banner + ThemeRole 过期隐藏）、数据过时（估值/披露滞后文案）、后端不可用、权限不足（只读可见、无写入口）。

## 6. 拆分与交付顺序

一个 spec、两个独立可部署的 plan：

- **Plan A · 公司研究**：4.2 写路径（公司/股票/估值快照）→ 4.3 读模型 → 5.2 公司两页。前置写路径单独成第一个 task，不依赖任何页面。
- **Plan B · 主题研究**：4.1 迁移与标签命令 → 4.4 读模型 → 5.2 主题两页。依赖 Plan A 的公司读模型组装逻辑（`company_roles` 复用），不宜并行。

每个 plan 按 `docs/superpowers/plans/` 既有格式输出 file map、checkbox 任务与停止条件。

## 7. 验收标准

1. 走查断点闭环：不借助种子脚本，纯 API 完成"建公司 → 建股票 → 录估值 → 标主题角色 → 公司页可见全链路"。
2. 时点一致性：同一 cutoff 下，公司档案的 ThemeRole / 估值 / 持仓集合与案例侧 dossier、graph、fund-exposure 的对应切片完全一致；`applicable_to` 已过期的角色在所有视图中同步消失。
3. 可追溯：公司页任一有效判断可下钻到案例层 AIAssessment / ReviewDecision；主题页任一聚合数字可展开到 `derived_from` 明细并继续下钻到冻结原文。
4. AI/人工边界：公司页与主题页透出的 AI 未复核状态与案例页语义完全一致，不出现"公司层面看起来已确认"的误读。
5. 无推荐语义：全文不含排名、目标价、买卖暗示；持仓一律标注报告期/披露日与滞后提示。
6. 质量门禁：后端 pytest 全绿（新增 companies/themes 读 API 与命令 API 测试）、发布门禁 10 项不回退、前端 tsc + vitest + e2e 全绿（新页面含 mock 写闭环与只读双模式）、OpenAPI 契约与前端类型重新生成。
7. CONTEXT.md 实现状态与 frontend-api-binding 文档同步更新。
