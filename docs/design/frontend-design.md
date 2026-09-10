# Fund Engine 前端设计文档

> 证据工作台前端。本文档独立描述前端架构与设计，仅通过 HTTP API 契约与后端对接，不依赖后端代码。
>
> 对应代码：`frontend/`

---

## 1. 概述

前端是一个**一体化证据关系画布**：把行业命题、原文证据、公司股票估值、基金持仓披露渲染在同一张连通图上，让研究员能从 AI 判断一路下钻到原文片段和基金披露。

核心体验：
- 一张连续相连的关系图，不是隔离的资料列表和基金列表
- 每条结论可点开看到原文逐字片段和定位
- AI 临时判断显著标注"未经人工复核"
- 估值与持仓披露带日期，明确滞后与口径

**非目标**：买卖推荐、目标价、实时持仓伪装、卡片网格。

---

## 2. 架构

```
┌─────────────────────────────────────────────┐
│  Page: ResearchWorkbenchPage                │
│  ├── AssessmentHeader   （AI 判断 + 标注）    │
│  ├── EvidenceGraph      （Cytoscape 画布）    │
│  ├── EvidenceDrawer     （证据原文下钻）       │
│  └── ExposurePanel      （估值 + 持仓披露）    │
├─────────────────────────────────────────────┤
│  api.ts   HTTP client  ──► GET /api/.../workbench
│  types.ts 类型契约     ◄── JSON WorkbenchResponse
└─────────────────────────────────────────────┘
```

技术栈：React 18 + Vite 5 + TypeScript 5 + Cytoscape.js 3 + Vitest + Playwright。

**与后端的边界**：前端只通过 `GET /api/research-cases/{id}/workbench` 的 JSON 响应对接，[types.ts](../frontend/src/types.ts) 手工对应 API 契约，不 import 任何后端代码。API 契约变更需同步两边。

---

## 3. 组件设计

### 3.1 ResearchWorkbenchPage（[pages/](../frontend/src/pages/ResearchWorkbenchPage.tsx)）
- 顶层容器，管理状态：`data` / `error` / `selectedEvidenceId` / `selectedNodeId`
- `useEffect` 调 `fetchWorkbench(caseId)` 加载
- 布局：顶部 AssessmentHeader，主区 EvidenceGraph + EvidenceDrawer，侧/底 ExposurePanel
- 选中证据时显示 EvidenceDrawer，选中节点时 ExposurePanel 过滤到该节点

### 3.2 AssessmentHeader（[components/](../frontend/src/components/AssessmentHeader.tsx)）
- 显示 case 标题、焦点命题
- AI 判断 conclusion + rationale
- **`provisional && !review` 时显示"AI 临时判断，未经人工复核"**
- 有 review 时显示"已复核：{outcome}"
- 显示 major_gap（主要阻塞）

### 3.3 EvidenceGraph（[components/](../frontend/src/components/EvidenceGraph.tsx)）-- 核心
- **一体化 Cytoscape 画布**，所有节点连续相连
- 节点 kind：case / thesis / statement / step / company / stock / fund
- 边按 kind 着色（见第 6 节）
- 点击 edge -> `onSelectEvidence(linkId)`；点击 node -> `onSelectNode(nodeId)`
- **降级**：jsdom/无渲染环境下降级为证据按钮列表（每条 evidence 一个 button `查看证据：{statement_text}`），保证可访问性和可测试性

### 3.4 EvidenceDrawer（[components/](../frontend/src/components/EvidenceDrawer.tsx)）
- 选中证据时显示
- 字段：role / reason / scope / period / review_state
- **原文定位**：locatorText(locator) 渲染"第 X 页，第 Y 段"等可读定位
- **原文逐字片段**：verbatim_text blockquote 展示

### 3.5 ExposurePanel（[components/](frontend/src/components/ExposurePanel.tsx)）
- 估值快照：stock_name / metric_name=metric_value @ as_of_date + 口径 definition
- 基金持仓披露：fund_name 持有 stock_name 权重 weight + 报告期 / 披露日
- **免责声明**："披露持仓存在滞后；主题暴露不等于实时持仓；命题被支持不等于推荐买入"
- 选中节点时过滤到该 stock/fund

---

## 4. 数据流与状态

```
URL ?case=xxx ──► ResearchWorkbenchPage
                   │
                   ▼ fetchWorkbench(caseId)
              api.ts ──HTTP──► 后端 /workbench
                   │
                   ▼ WorkbenchResponse
              setData(data)
                   │
      ┌────────────┼────────────┐
      ▼            ▼            ▼
 AssessmentHeader  EvidenceGraph  ExposurePanel
                   │
                   ▼ onSelectEvidence(linkId)
              setSelectedEvidenceId
                   │
                   ▼ find record
              EvidenceDrawer
```

状态全部在 Page 层（单层状态），子组件纯展示 + 回调。无全局状态库（MVP 不需要）。

---

## 5. API 契约对接

[types.ts](../frontend/src/types.ts) 定义 `WorkbenchResponse` 及子类型，手工对应后端 JSON：

```typescript
interface WorkbenchResponse {
  case: { id; title; industry_topic };
  focus_thesis: { id; statement } | null;
  assessment: { id; conclusion; rationale; gaps; provisional } | null;
  review: { outcome; conclusion; reason } | null;
  major_gap: string | null;
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  evidence_drawer_records: EvidenceDrawerRecord[];
  stock_valuation_snapshots: ValuationSnapshot[];
  fund_holding_disclosures: HoldingDisclosure[];
}
```

[api.ts](../frontend/src/api.ts)：
```typescript
fetchWorkbench(caseId, cutoff?) -> GET /api/research-cases/{caseId}/workbench
```

**契约约束**：
- `edge.kind` ∈ `evidence` | `causal` | `theme_role` | `holding`
- `assessment.provisional` 为 true 时必须显示"未经人工复核"
- 响应不含 `recommendation`，前端也不渲染任何推荐语义
- Vite dev proxy `/api` -> `http://localhost:8000`

---

## 6. 视觉语义

### 边着色（[EvidenceGraph.tsx](../frontend/src/components/EvidenceGraph.tsx)）
```typescript
evidence:    绿色 #2e7a48 实线   （支持/反驳关系）
causal:      蓝色 #6f7cff 虚线   （因果传导）
theme_role:  棕色 #9a6a12 实线   （主题角色映射）
holding:     棕色 #9a6a12 点线   （持仓披露）
```
视觉上必须可区分，避免把"命题得到支持"误读成"该证券应被买入"。

### AI/人工边界
- AI 判断 + provisional 标注显著可见
- 有 review 时显示复核结果，但 AI 原判断仍可见（不替换）

### 一体化画布
- 所有节点在同一 Cytoscape canvas 连续相连
- 焦点 thesis 用独立样式
- 完整图按切片加载，避免一次渲染全图

---

## 7. 交互设计

| 操作 | 行为 |
|------|------|
| 点击 evidence edge / "查看证据"按钮 | 打开 EvidenceDrawer，显示原文 span + 定位 + 理由 |
| 点击 stock node | ExposurePanel 过滤到该股票估值 |
| 点击 fund node | ExposurePanel 过滤到该基金持仓披露 |
| URL `?case=xxx` | 指定 ResearchCase |
| `?cutoff=` | 时间旅行（未来支持） |

**核心下钻路径**：AI 判断 -> 证据边 -> 原文 span（页/段/表/行）-> verbatim_text。对应文档"每个结论可回到原文"。

---

## 8. 测试策略

- **单元测试**（[ResearchWorkbenchPage.test.tsx](../frontend/src/tests/ResearchWorkbenchPage.test.tsx)，Vitest + Testing Library + jsdom）：
  - `labels an unreviewed AI assessment as provisional`：mock API 返回 provisional assessment，断言"AI 临时判断，未经人工复核"可见
  - `opens the exact source span when an evidence edge is selected`：点击"查看证据"按钮，断言原文 span 可见
  - Cytoscape 在测试中 mock（jsdom 不支持 canvas）
- **typecheck**：`tsc --noEmit` 严格模式
- **build**：`vite build` 生产构建
- **e2e**（[workbench.spec.ts](../frontend/e2e/workbench.spec.ts)，Playwright）：前端可启动，root 渲染非空（后端未运行时降级）

---

## 9. 目录结构

```
frontend/
  src/
    main.tsx                          # 入口，从 URL ?case= 读 caseId
    api.ts                            # fetchWorkbench HTTP client
    types.ts                          # API 契约类型（对应后端 WorkbenchResponse）
    pages/ResearchWorkbenchPage.tsx   # 顶层容器 + 状态
    components/
      AssessmentHeader.tsx            # AI 判断 + provisional 标注
      EvidenceGraph.tsx               # Cytoscape 一体化画布 + 证据按钮
      EvidenceDrawer.tsx              # 原文 span 下钻
      ExposurePanel.tsx               # 估值 + 持仓披露
    tests/
      setup.ts                        # jest-dom
      ResearchWorkbenchPage.test.tsx  # Vitest 单测
  e2e/workbench.spec.ts               # Playwright
  vite.config.ts                      # Vite + Vitest + proxy
  tsconfig.json                       # 严格 TS
  package.json
```

---

## 10. 技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 框架 | React 18 | 生态 + 组件化 |
| 构建 | Vite 5 | 快速 HMR |
| 语言 | TypeScript 5 | 类型安全 |
| 图库 | Cytoscape.js 3 | 关系图 + 路径高亮 |
| 测试 | Vitest + Testing Library | 单测 |
| e2e | Playwright | 浏览器自动化 |

---

## 11. 与后端的边界

- **仅通过 HTTP API 对接**，不共享代码/类型 import
- `types.ts` 手工维护，与后端 [workbench.py](../backend/app/services/workbench.py) 的响应结构保持一致
- API 契约变更：后端改 `WorkbenchResponse` -> 前端同步改 `types.ts`
- 前端可独立 `npm run dev` 启动（proxy 到后端 8000）
- 前端不假设后端存储实现（PG/Neo4j 都可），只认 JSON 契约

---

## 12. 运行

```bash
cd frontend && npm install
npm run dev          # dev server :5173（proxy /api -> :8000）
npm run typecheck    # 类型检查
npm run build        # 生产构建
npm test             # Vitest 单测
npm run e2e          # Playwright（需后端运行）
```
