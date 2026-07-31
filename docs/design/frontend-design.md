# Fund Engine 前端设计文档

> 证据工作台前端。本文档独立描述前端架构与设计，仅通过前端查询接口与数据源适配层对接，页面不感知后端字段变化。
>
> 对应代码：`frontend/`
>
> 设计基线：[设计原型1.png](../../设计原型1.png)（研究总览）·[设计原型2.png](../../设计原型2.png)（案例档案）·[设计原型.png](../../设计原型.png)（关系画布）。本设计不重画骨架，而是把这三张原型作为母版，在其上统一视觉系统、补齐资料库与审核队列，并强化历史截点、AI/人工边界、响应式与无障碍状态。

---

## 1. 设计基线：原型继承方案

第一版首页简化为调度列表，丢失了"边研究、边处理任务"的工作台结构；案例页被改成三个普通内容栏，削弱了中央因果链与正反证据之间的空间关系；关系图被降为辅助视图，但原型中的五段连续图是产品最有辨识度的核心能力。

本设计撤回上述简化方案，回到三张原型的骨架：

- **研究总览**：中央保持当前研究摘要、核心结论、关键变化与研究框架；右侧三列分别承担任务队列、证据变化与活动记录。它既是首页，也是继续工作的入口。
- **案例档案**：左侧案例列表，中间依次呈现当前判断、横向因果链、支持与反证，右侧固定为原文定位与审核信息。
- **关系模式**：证据、命题、因果链、公司、基金保持一张连续画布；点击任何对象都在右侧检查器中显示来源、范围、日期、审核状态与引用记录。
- **资料库**：沿用同一产品壳。中央使用高密度文档表格，右侧检查器展示冻结版本、解析质量、原文结构与已关联案例。打开文档后采用"原文 + SourceSpan 标注 + 引用关系"三层浏览。
- **审核队列**：沿用案例页的三栏语法：左侧待审核项，中间冻结原文，右侧人工决定。支持、反驳、背景、范围和理由均在当前页面完成，不弹出模态框。

### 第一轮实现范围

左侧导航保留原型中的完整产品感，但首轮真正实现的范围仍是：研究总览、行业研究、证据库与审核队列；公司库、基金库、监控等先作为后续入口，不伪装成已完成能力。

### 统一布局规则

- 默认适配 1440–1920px 桌面研究环境。
- 左侧产品导航约 190–220px，右侧检查器约 320–380px。
- 中央研究区域获得剩余空间，关系图可以全屏展开。
- 浅暖底色、墨色文字、低饱和语义色；颜色只表达对象类型与状态。
- 高密度信息主要通过细分隔线、缩进、留白节奏和文字层级组织，减少重复卡片。

---

## 2. 视觉系统（受控调色板）

继承原型中"温暖的纸张感 + 专业研究气质"，使用受限配色而非卡片网格。

| 角色 | 取值 |
|------|------|
| 画布底 | `oklch(0.97 0.008 85)` |
| 内容面 | 略提亮，非纯白 |
| 主文字 | 带绿色倾向的墨色，非纯黑 |
| 主强调（选中/当前上下文） | 低饱和苔绿 |
| 支持 | 绿色 + 实线 + "支持"文字 |
| 反证 | 陶土红 + 不同线型或图标 + "反证"文字 |
| 因果链 | 赭黄色 |
| 公司 | 矿物蓝 |
| 基金 | 灰紫色 |
| AI 待复核 | 琥珀色文字与状态标记，非强警报色 |
| 人工已复核 | 稳定的深绿状态，不覆盖 AI 原始判断 |

字体：`-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif`；原文摘录可使用克制的中文衬线字体，明确它是"来源内容"而非界面说明。动效仅用于状态反馈和检查器展开，控制在 150–220ms；`prefers-reduced-motion` 时直接切换。

---

## 3. 信息架构与页面

```
AppShell
 ├─ 研究总览（WorkspaceOverview）
 │   ├─ 当前研究正文（摘要 / 核心结论 / 关键变化 / 研究框架）
 │   ├─ 任务队列（TaskQueue）
 │   ├─ 证据变化（EvidenceChanges）
 │   └─ 活动流（ActivityFeed）
 ├─ 行业研究 / 案例档案（ResearchCaseDossier）
 │   ├─ ResearchCaseNavigator
 │   ├─ ResearchCaseHeader
 │   ├─ ResearchDossier（当前判断 / 竞争解释 / 缺口 / 研究日志）
 │   ├─ CausalChain
 │   └─ EvidenceComparison（支持 / 反证）
 ├─ 关系模式（RelationshipCanvas）
 │   ├─ Evidence / Proposition / Causal / Company / Fund 五段连续画布
 │   └─ SourceInspector（右侧检查器）
 ├─ 证据库（DocumentLibrary）
 │   ├─ 高密度文档表格
 │   └─ DocumentViewer（原文 + SourceSpan 标注 + 引用关系）
 └─ 审核队列（ReviewWorkbench）
     ├─ ReviewQueueList
     ├─ FrozenSource
     └─ ReviewDecisionPanel
```

后续入口（公司库、基金库、监控中心等）保留在导航，但首轮不假装实现。

---

## 4. 核心交互

- **全局搜索**：`⌘K / Ctrl+K`，按案例、命题、证据、公司、股票、基金分组展示；选择结果后直接进入对应页面并高亮上下文。
- **研究总览三列可点击**：任务、变化、活动进入对应详情；返回时恢复滚动、筛选与选中状态。
- **案例页内容标签**：研究摘要、关键图表、核心观点、风险与假设、相关公司、研究日志；当前命题内部再切换"档案 / 关系图 / 预测与证伪 / 股票与基金"。tab / view / focus / step / cutoff 全部写入 URL，便于分享、返回与回放。
- **因果环节**：点击后高亮与其相关的支持、反证与缺口；其他内容降低对比度，但不隐藏。键盘支持 `ArrowUp` / `ArrowDown` 在因果链中切换，`Enter` / `Space` 触发聚焦，`Escape` 取消聚焦。
- **证据或关系边**：右侧检查器展示冻结原文、定位、时间、范围、证据角色、AI 提议与人工审核历史。
- **关系图**：框选、缩放、路径聚焦、对象类型过滤与"恢复全图"；并提供结构化路径列表，保证键盘与辅助技术可用。`canvas-grid__col` 切片渲染，>200 节点进入"large"分支并在 canvas 中保留前 60 个 / 列、200 条边的可见子图，避免一次性渲染上万个 SVG 节点。
- **历史截点**：全局上下文。切换 cutoff 后，页面整体进入明确的"历史回放"状态（顶栏出现黄色 banner），隐藏截止日后材料，不允许把历史视图误当当前状态。
- **审核连续处理**：确认、修改或驳回后调用 `researchClient.submitReviewDecision()`，追加历史 → 自动选中下一条；可跳过；无批量按钮，AI 提议永不直通。
- **正式关系创建**：所有正式关系创建都要求人工动作。AI 可以提议 Statement、EvidenceLink 和实体对齐，不能因为相似度或模型置信度自动转为正式关系。

---

## 5. 必须设计的页面状态

| 状态 | 触发条件 | UI 表现 |
|------|----------|---------|
| 首次使用 | `scenario === "empty"` 或 dossier 中 supports/contradicts/causal_chain 都为空 | 中央显示"建立首个命题 / 导入资料 / 等待审核"三步入口，并解释 AI 结果为何未经人工复核 |
| 加载中 | 请求未返回 | 与真实布局 1:1 对齐的骨架屏（按页面占位），不使用中央旋转图标 |
| 空案例 | 同上 | 三步入口；右侧检查器保持可见但内容为空态 |
| 无搜索结果 | `docs.length === 0 && query` 或 `docs.length === 0 && cutoff` | 保留筛选上下文，明确指出"资料不存在" / "权限不足" / "cutoff 后不可见"中哪一种 |
| 证据冲突 | supports + contradicts 都 ≥ 1 | 同时显示支持与反证（双栏 + 一致性提示），不可只显示多数方 |
| 证据不足 | `conclusion === "insufficient_evidence"` | 显示缺失证据类型 / 范围 / 期间；不展示模糊成熟度分数 |
| 解析失败 | `parse_quality === "failed"` | 资料仍保留冻结版本；显示失败阶段 + 重试按钮 + 受影响引用列表 |
| 数据过时 | `as_of_date` 早于 cutoff | 估值 / 持仓旁显示"报告期 / 披露日 / 采集日"，文案"披露持仓存在滞后" |
| 后端不可用 | adapter 抛 `PageStateError("backend_unavailable")` | 顶栏 `banner-offline`：保留导航与已缓存只读内容；写操作禁用；不闪现空白页 |
| 权限不足 | adapter 抛 `PageStateError("permission_denied")` | 顶栏 `banner-permission`：隐藏不可执行操作；保留有权查看的研究上下文；审核页面禁用确认 / 修改 / 驳回按钮（仅跳过可见） |
| 小屏幕（< 1180px） | viewport width | 折叠左侧导航为图标条；右侧检查器变覆盖式侧栏；关系图不压缩 |

页面状态通过统一 hook `useResearchQuery()` 暴露 `{data, error, loading, reload}`，`PageStateBanners` 组件渲染对应横幅，避免每个页面单独解析 `Error.message`。

---

## 6. 组件边界

每个组件只接收前端领域类型，不直接调用 `fetch`。页面通过查询层获取数据，便于 mock adapter 与未来 HTTP adapter 互换。

| 组件 | 职责 |
|------|------|
| `AppShell` | 左侧导航、顶部搜索、用户与时间上下文 |
| `WorkspaceOverview` | 研究正文、任务、变化和活动编排 |
| `ResearchCaseNavigator` | 行业案例与命题导航 |
| `ResearchCaseHeader` | 状态、时间、版本与人工复核摘要 |
| `ResearchDossier` | 当前判断、竞争解释、缺口与研究日志 |
| `CausalChain` | 可检验的因果环节，不承担整张关系图 |
| `EvidenceComparison` | 支持、反证和背景证据的对照 |
| `RelationshipCanvas` | 证据到基金的连续关系图 |
| `SourceInspector` | 冻结原文、定位、时间、范围与引用记录 |
| `DocumentLibrary` | 资料检索、版本、解析与案例关联 |
| `ReviewWorkbench` | 队列、原文与人工决策 |
| `HistoricalCutoffControl` | 历史截点与回放状态 |
| `StatusMark` | 统一表达 AI、人工、冲突、过时与失败状态 |

---

## 7. 前端领域模型与 mock 数据边界

第一阶段由前端维护一套稳定的领域 mock，不直接复制当前后端响应：

```text
WorkspaceOverview
ResearchCaseSummary
ResearchCaseDossier
ThesisAssessment
CausalStepView
EvidenceRecord
SourceDocumentView
ReviewQueueItem
CompanyExposure
FundDisclosure
ActivityEvent
HistoricalSnapshotContext
```

mock 通过 `MockScenario` 枚举提供 10 个固定场景，每个场景对应一组页面状态：

| Scenario | 关键差异 | 触发的页面状态 |
|----------|---------|---------------|
| `typical` | 默认 | 正常 |
| `empty` | overview 与 dossier 都为空 | 首次使用 / 空案例 |
| `conflict` | supports + contradicts 同时存在 | 证据冲突 |
| `insufficient` | `conclusion === "insufficient_evidence"` | 证据不足 |
| `parse_failed` | 所有 `parse_quality === "failed"` | 解析失败 |
| `historical` | `cutoff=2024-04-15` 时 dossier 隐藏 post-cutoff 数据 | 历史回放 |
| `large` | 关系图扩展到 1428 节点 / 3264 边 | 大数据量（启用切片） |
| `offline` | 所有方法抛 `PageStateError("backend_unavailable")` | 后端不可用 |
| `permission` | `submitReviewDecision` 抛 `PageStateError("permission_denied")` | 权限不足 |
| `stale` | valuation `as_of_date = 2023-12-31` | 数据过时 |

页面只通过 `researchClient` 读取：

```ts
researchClient.getOverview()
researchClient.getCaseDossier(caseId, { thesisId, cutoff })
researchClient.getRelationshipGraph(caseId, { cutoff })
researchClient.getDocuments(query, cutoff)
researchClient.getReviewQueue()
researchClient.submitReviewDecision(itemId, { outcome, conclusion, reason })
```

`researchClient` 内部由 `MockResearchAdapter` 注入；将来替换为 `HttpResearchAdapter` 时，页面组件不需要改动。视觉和交互不能被当前单一 `/workbench` 响应绑死。

测试与 Storyboard 通过 `setResearchClient(new MockResearchAdapter({ scenario }))` 切换场景，页面层只读取 `ResearchClient` 接口。

---

## 8. 验证策略

- **Vitest + Testing Library**：状态语义、键盘行为、筛选与审核流程。
- **Playwright**：四页面主流程、历史回放、关系图下钻与响应式布局。
- **axe**：WCAG 2.2 AA 自动检查。
- **Playwright 截图回归**：以三张现有原型为视觉基准。
- **mock 场景测试**：空、加载、失败、冲突、证据不足、过期、权限不足与大数据量。
- **性能检查**：资料列表虚拟化；关系图切片加载；非关系页面不提前加载 Cytoscape。
- **契约测试**：未来 HTTP adapter 必须与前端领域模型转换层对齐，页面组件不感知后端字段变化。

---

## 9. 明确非目标

- 不在这一阶段设计后端表结构或正式 API。
- 不实现自动投资建议、目标价、仓位或买卖按钮。
- 不增加聊天作为主导航或核心操作。
- 不用成熟度分数或模型置信度决定送审。
- 不把关系图可见性当作证据可信度。
- 不把基金披露持仓展示为实时持仓。
- 不为了"完整"一次性实现宏观、公司、基金、监控等所有后续模块。

---

## 10. 落地说明

本次变更以**追加**方式落地，不破坏已有 `ResearchWorkbenchPage` / `AssessmentHeader` / `EvidenceGraph` / `EvidenceDrawer` / `ExposurePanel` 的现有断言与契约（这些组件的语义与 prototype 3 中"证据 → 命题 → 因果链 → 公司/基金"的画布完全一致）。

新增内容：

- `AppShell`：左侧产品导航 + 顶部搜索 + 用户上下文；用 outlet 装载各页面。
- `WorkspaceOverviewPage`：研究总览（原型 1）。
- `ResearchCaseDossierPage`：案例档案（原型 2），包含 `CausalChain`、`EvidenceComparison` 与内嵌 `SourceInspector`。
- `RelationshipCanvasPage`：关系模式（原型 3），复用并强化 `EvidenceGraph`，配合右侧检查器。
- `DocumentLibraryPage` / `DocumentViewer`：资料库（新增）。
- `ReviewWorkbenchPage`：审核队列（新增）。
- `researchClient` + `mockAdapter`：领域类型与稳定 mock。

保留并继续演进的旧组件：`AssessmentHeader`、`EvidenceGraph`、`EvidenceDrawer`、`ExposurePanel`。它们在原型 3 的连续画布中已经验证有效。