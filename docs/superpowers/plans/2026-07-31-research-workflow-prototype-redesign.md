# Research Workflow Prototype Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete, product-grade static prototype for the ResearchCase workflow, covering case creation, evidence collection, factor review, evidence-to-fund traversal, reusable data and knowledge assets, and reproducible snapshot comparison.

**Architecture:** Keep the prototype implementation isolated under `prototype/ui/` as a code-native HTML/CSS/JavaScript application with nine query-selected screens backed by one coherent AI-compute ResearchCase fixture. A small Node/Playwright capture harness serves the static files, runs semantic and layout assertions, and deterministically exports the approved PNG assets. The prototype illustrates the future API contract without importing or modifying production frontend code.

**Tech Stack:** Semantic HTML5, CSS custom properties and grid/flex layout, vanilla JavaScript, Node.js built-ins, Playwright Chromium from `frontend/node_modules`, Markdown design documentation.

---

## Implementation boundaries

- Treat `docs/superpowers/specs/2026-07-31-research-workflow-prototype-redesign.md` as the approved product contract.
- Preserve `prototype/文档1.md`; it remains product-intent source material.
- Do not modify `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/domain/types.ts`, `frontend/openapi.json`, or `frontend/src/contracts/`; those files contain unrelated concurrent work.
- Do not connect a real backend, Juyuan provider, vector store, or graph database in this plan. Show their product states and future contracts with deterministic fixture data.
- Do not introduce confidence percentages, maturity scores, `ready_for_review`, automatic key-factor promotion, causal claims without a reviewed mechanism, or investment recommendations.
- Overwrite the three existing PNGs only when their replacements have passed the capture assertions and visual review.
- Use one case throughout all screens: “生成式 AI 推理需求增长，是否正在成为 2025—2027 年国内算力产业链收入增长的主要驱动因素？”

## Target artifacts

| Screen ID | Product screen | PNG artifact |
|---|---|---|
| `overview` | 研究总览 | `prototype/设计原型1.png` |
| `new-research` | 新建研究 | `prototype/设计原型3-新建研究.png` |
| `plan` | 研究计划与证据收集 | `prototype/设计原型4-研究计划.png` |
| `case` | 案例研究工作台 | `prototype/设计原型2.png` |
| `graph` | 因素关系图 | `prototype/设计原型.png` |
| `review` | 审核工作区 | `prototype/设计原型5-审核工作区.png` |
| `library` | 资料与知识 | `prototype/设计原型6-资料与知识.png` |
| `data` | 数据中心 | `prototype/设计原型7-数据中心.png` |
| `versions` | 快照与版本比较 | `prototype/设计原型8-版本比较.png` |

## Prototype data contract

The fixture must expose these top-level keys so screen copy does not drift independently:

```js
window.PROTOTYPE_DATA = {
  case: {},
  theses: [],
  factors: [],
  documents: [],
  statements: [],
  evidenceLinks: [],
  metrics: [],
  companies: [],
  funds: [],
  reviewQueue: [],
  snapshots: [],
  providerRuns: []
};
```

Every evidence item shown in a formal or frozen context must include a source document/version, source span, publication/availability time, review state, and snapshot membership. Every fund holding must include a disclosure date. AI proposals must use the exact label `AI 草案 · 未经人工复核`.

## Task 1: Create the isolated prototype harness and visual language

**Files:**

- Create: `prototype/ui/index.html`
- Create: `prototype/ui/styles.css`
- Create: `prototype/ui/data.js`
- Create: `prototype/ui/app.js`
- Create: `prototype/ui/capture.mjs`
- Create: `prototype/ui/contract.test.mjs`
- Create: `prototype/ui/README.md`

- [ ] **Step 1: Write a failing prototype contract test**

Create `prototype/ui/contract.test.mjs` with a required-screen list and assertions that fail while the application files are absent:

```js
const screens = [
  "overview", "new-research", "plan", "case", "graph",
  "review", "library", "data", "versions"
];

for (const screen of screens) {
  await page.goto(`${baseUrl}/?screen=${screen}`);
  await page.locator(`[data-screen="${screen}"]`).waitFor();
  await expectNoHorizontalOverflow(page);
  await expectNoForbiddenScoring(page);
}
```

The forbidden scoring check must reject `置信度`, `成熟度`, `ready_for_review`, and percentage-based evidence/relevance scores. It must not reject legitimate financial percentages such as holdings weights or growth rates; scope the check to elements carrying `[data-evidence-assessment]`.

- [ ] **Step 2: Run the test and confirm the expected failure**

Run:

```bash
node prototype/ui/contract.test.mjs
```

Expected: non-zero exit with a clear message that `prototype/ui/index.html` or the local test server is not available.

- [ ] **Step 3: Implement the shell, token system, and screen router**

Create a semantic shell in `index.html`, load classic scripts in this order, and avoid module/CORS issues:

```html
<link rel="stylesheet" href="./styles.css">
<main id="app"></main>
<script src="./data.js"></script>
<script src="./app.js"></script>
```

In `styles.css`, define named tokens for warm canvas, paper, ink, muted text, borders, support, contradict, warning, reviewed, AI-draft, spacing, radius, and shadow. Establish a fixed 1600px desktop composition with a 224px navigation rail, a compact utility header, and a fluid work area. Encode state with icon/label/shape as well as color.

In `app.js`, implement reusable primitives and explicit routing:

```js
const SCREEN_RENDERERS = { /* nine named renderers */ };
const screenId = new URLSearchParams(location.search).get("screen") || "overview";
document.querySelector("#app").innerHTML = renderShell(
  SCREEN_RENDERERS[screenId](),
  { activeNav: activeNavFor(screenId) }
);
```

The shell navigation must be: `工作台 / 研究案例 / 资料与知识 / 数据中心 / 审核中心 / 监测与更新`.

- [ ] **Step 4: Implement a reusable local server and capture contract**

In `capture.mjs`, use `node:http`, `node:fs`, and `node:path` to serve only `prototype/ui/` from an ephemeral localhost port. Import Chromium from `../../frontend/node_modules/playwright/index.mjs`. Fail with the exact remediation command `cd frontend && npm ci` when Playwright is unavailable.

Export a helper from `capture.mjs` so `contract.test.mjs` can reuse the same server and browser setup. Capture at viewport `{ width: 1600, height: 1000 }` and device scale factor `1`.

- [ ] **Step 5: Add the coherent base fixture**

Populate `data.js` with one internally consistent AI-compute case:

- cutoff: `2025-06-30`;
- snapshot: `RS-2025-06-30-v3`;
- three theses, each with support condition, falsifier, and next validation event;
- candidate factors covering demand, supply, transmission, constraints, alternatives, and contradictory observations;
- point-in-time documents and metrics;
- reviewed and pending evidence links;
- company and fund mappings with disclosure dates;
- provider successes, quota failure, permission gap, and one manually uploaded source;
- two prior frozen snapshots for comparison.

Do not include unrelated autonomous-driving, new-energy, or consumer cases in the selected case details.

- [ ] **Step 6: Make the harness test pass**

Run:

```bash
node prototype/ui/contract.test.mjs --screens shell
```

Expected: exit 0; shell renders at 1600×1000, navigation is present, and no horizontal overflow is detected.

- [ ] **Step 7: Document prototype operation**

In `prototype/ui/README.md`, document the query routes, fixture boundary, install preflight, test command, capture command, output mapping, and rule that PNGs are generated artifacts.

- [ ] **Step 8: Commit the harness**

```bash
git add prototype/ui
git commit -m "feat: add research prototype harness"
```

## Task 2: Build the research overview

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型1.png`

- [ ] **Step 1: Add failing overview assertions**

Assert presence of `新建研究`, `ResearchCase 队列`, `待审核关系`, `新反面证据`, `数据修订与缺口`, `Provider 状态`, `最近冻结版本`, and a next-action label for every visible case row.

- [ ] **Step 2: Run only the overview contract**

```bash
node prototype/ui/contract.test.mjs --screens overview
```

Expected: failure listing the missing overview regions.

- [ ] **Step 3: Implement the decision-oriented overview**

Render a compact work queue rather than a marketing dashboard. The selected case row must show question, cutoff, snapshot, review state, primary blocker, and one next action. Supporting lanes must surface pending relationship reviews, new contradiction, revised metric, provider failure, and recently frozen snapshot.

Use one primary action (`新建研究`) and avoid decorative KPI cards or evidence-score percentages.

- [ ] **Step 4: Verify and capture**

```bash
node prototype/ui/contract.test.mjs --screens overview
node prototype/ui/capture.mjs --screens overview
```

Expected: test exits 0 and creates a 1600×1000 `prototype/设计原型1.png`.

- [ ] **Step 5: Visually inspect the PNG**

Open `prototype/设计原型1.png` with the image viewer. Confirm the main queue is dominant, long Chinese text does not clip, state is readable without relying on color, and the next action is obvious within five seconds.

- [ ] **Step 6: Commit the overview**

```bash
git add prototype/ui/app.js prototype/ui/styles.css prototype/ui/contract.test.mjs prototype/设计原型1.png
git commit -m "feat: redesign research overview prototype"
```

## Task 3: Build the four-step new-research flow

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型3-新建研究.png`

- [ ] **Step 1: Add failing creation-flow assertions**

Require a visible four-step rail: `研究问题 / 初始命题 / 已有资产 / 研究计划`. On the captured state, step 2 must show 1–3 Thesis editors with observation period, support condition, falsifier, and next validation event. Require `AI 协助拆分` to be secondary to `确认命题并继续`.

- [ ] **Step 2: Run the focused test and confirm failure**

```bash
node prototype/ui/contract.test.mjs --screens new-research
```

Expected: non-zero exit for missing step rail and Thesis fields.

- [ ] **Step 3: Implement the full-page creation workflow**

Render a full page, not a modal. Show step-one research fields as a completed summary, step-two as the active form, and compact previews of steps three and four. Include time range and evidence cutoff as separate fields. Label all AI-authored suggestions `AI 草案 · 未经人工复核`.

- [ ] **Step 4: Verify and capture**

```bash
node prototype/ui/contract.test.mjs --screens new-research
node prototype/ui/capture.mjs --screens new-research
```

Expected: exit 0 and a 1600×1000 `prototype/设计原型3-新建研究.png`.

- [ ] **Step 5: Visually inspect and commit**

Confirm the page explains how a new industry proposition is created, distinguishes support from falsification, and does not imply the system has already reached a conclusion.

```bash
git add prototype/ui prototype/设计原型3-新建研究.png
git commit -m "feat: add new research workflow prototype"
```

## Task 4: Build research planning and evidence collection

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型4-研究计划.png`

- [ ] **Step 1: Add failing plan-screen assertions**

Require the six regions: `已有资料与数据`, `Provider 查询计划`, `正在获取并冻结`, `待审核结果`, `证据缺口`, and `失败、额度与权限`. Require actions `复用`, `移除`, `重试`, `调整范围`, `上传材料`, and `暂时无法获得`.

- [ ] **Step 2: Run the focused test**

```bash
node prototype/ui/contract.test.mjs --screens plan
```

Expected: failure for missing plan and gap controls.

- [ ] **Step 3: Implement transparent orchestration states**

Present the plan as an inspectable pipeline, not a hidden AI chat. Separate internal reuse from external acquisition. Each provider row must show query purpose, date scope, intended artifact, run state, and failure meaning. Explicitly show that an interface catalog entry can be unavailable or quota-limited.

- [ ] **Step 4: Verify, capture, inspect, and commit**

```bash
node prototype/ui/contract.test.mjs --screens plan
node prototype/ui/capture.mjs --screens plan
git add prototype/ui prototype/设计原型4-研究计划.png
git commit -m "feat: add evidence collection plan prototype"
```

Expected: the user can see what is reused, what will be searched, what failed, and what remains unknowable.

## Task 5: Rebuild the ResearchCase workbench

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型2.png`

- [ ] **Step 1: Add failing dossier assertions**

Require a fixed case header with question, object, time range, cutoff, snapshot, AI-draft state, and human-review state. Require tabs `研究档案 / 命题与证据 / 因素分析 / 关系路径 / 公司与基金 / 历史版本`, plus the `探索模式` and `已冻结版本` view switch.

Require visible sections for formal judgment, AI draft, main contradiction, largest gap, next validation event, Thesis evidence, factor comparison, mechanism, alternatives, falsifier, and source spans.

- [ ] **Step 2: Run the focused test and confirm failure**

```bash
node prototype/ui/contract.test.mjs --screens case
```

- [ ] **Step 3: Implement the dossier hierarchy**

Use a sticky context strip and a reading-oriented central column. Make the formal reviewed judgment visually distinct from the provisional AI draft. In the factor table, use role/status labels from the approved state model and text explanations for time order, mechanism, direct evidence, alternatives, difference explanation, scope, and falsifier—never a synthetic score.

Source citations must display document version, publication date, source span, review state, and an affordance labeled `查看原文定位`.

- [ ] **Step 4: Verify and capture**

```bash
node prototype/ui/contract.test.mjs --screens case
node prototype/ui/capture.mjs --screens case
```

Expected: exit 0 and replacement of `prototype/设计原型2.png` only after the page passes.

- [ ] **Step 5: Visually inspect and commit**

Confirm a reviewer can distinguish official conclusion, AI proposal, contradiction, gap, and next event without opening another page.

```bash
git add prototype/ui prototype/设计原型2.png
git commit -m "feat: rebuild research case workbench prototype"
```

## Task 6: Rebuild the connected factor relationship graph

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型.png`

- [ ] **Step 1: Add failing graph assertions**

Require one connected path containing `DocumentVersion`, `SourceStatement`, support/contradict evidence, `FactorCandidate` or reviewed factor, `CausalStep`, company, stock, and fund holding. Require a right-side inspector with source span, relation semantics, review status, scope, as-of date, disclosure date, and `提交审核` or `撤回提议` actions.

- [ ] **Step 2: Run the focused test**

```bash
node prototype/ui/contract.test.mjs --screens graph
```

Expected: failure until all graph layers and inspector details exist.

- [ ] **Step 3: Implement the continuous graph**

Use semantic HTML nodes and CSS-positioned edges so the screenshot is deterministic. Do not split evidence, factors, and funds into disconnected columns. Encode relation type with label, line style, arrow, and color. Clearly distinguish source fact, AI-proposed relation, reviewed relation, and projection node.

Fund nodes must say `披露持仓`, show `as_of_date`, and avoid implying current exposure or recommendation.

- [ ] **Step 4: Verify, capture, inspect, and commit**

```bash
node prototype/ui/contract.test.mjs --screens graph
node prototype/ui/capture.mjs --screens graph
git add prototype/ui prototype/设计原型.png
git commit -m "feat: rebuild evidence to fund graph prototype"
```

Expected: the main path can be followed visually from frozen source to disclosed holding, with provisional relationships visibly provisional.

## Task 7: Build the review workbench

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型5-审核工作区.png`

- [ ] **Step 1: Add failing review assertions**

Require a queue, source comparison, AI proposal, target Thesis/factor, relation selection, factor-role selection, applicability boundary, review rationale, and actions `确认并写入审核知识`, `驳回`, and `要求补充证据`.

- [ ] **Step 2: Run the focused test**

```bash
node prototype/ui/contract.test.mjs --screens review
```

- [ ] **Step 3: Implement a single-decision review layout**

Use a three-part layout: queue on the left, evidence and proposed relationship in the center, decision form on the right. The selected item must compare the original source span with the AI-normalized statement. A reviewer must explicitly choose relation, factor role, boundary, and rationale; acceptance must not be a one-click implicit promotion.

- [ ] **Step 4: Verify, capture, inspect, and commit**

```bash
node prototype/ui/contract.test.mjs --screens review
node prototype/ui/capture.mjs --screens review
git add prototype/ui prototype/设计原型5-审核工作区.png
git commit -m "feat: add evidence review workbench prototype"
```

Expected: the AI/human boundary and the formal-write action are unmistakable.

## Task 8: Validate the first-batch journey and rewrite the design document

**Files:**

- Modify: `prototype/设计文档.md`
- Create: `DESIGN.md`
- Modify: `prototype/ui/contract.test.mjs`
- Modify: `prototype/ui/README.md`

- [ ] **Step 1: Add a cross-screen journey assertion**

Verify that the same case title, cutoff, snapshot ID, Thesis IDs, factor names, and review states remain consistent across `overview`, `new-research`, `plan`, `case`, `graph`, and `review`.

- [ ] **Step 2: Run the six-screen contract**

```bash
node prototype/ui/contract.test.mjs --screens overview,new-research,plan,case,graph,review
```

Expected: exit 0 with a summary for six screens and no forbidden assessment language.

- [ ] **Step 3: Rewrite `prototype/设计文档.md` around the approved domain**

Replace the outdated Theme/Claim/confidence-first architecture with:

- product goal and non-goals;
- single ResearchCase workflow;
- ResearchCase, document library, data center, and reviewed knowledge layers;
- immutable evidence ledger versus rebuildable projections;
- SourceStatement/EvidenceLink/FactorCandidate/review boundaries;
- point-in-time semantics and snapshot reproducibility;
- company, stock, valuation, and disclosed-fund-holding mapping;
- all nine screen descriptions and prototype operation.

Preserve links to `prototype/文档1.md` where they still support the intent, but remove unsupported claims that Neo4j is the source of truth or that confidence determines evidence quality.

- [ ] **Step 4: Add a concise root design index**

Create `DESIGN.md` linking to the approved spec, implementation plan, prototype design document, prototype source, and generated images. State that the evidence ledger is authoritative and the PNGs are generated product artifacts.

- [ ] **Step 5: Check for obsolete semantics and commit**

Run:

```bash
rg -n "置信度|成熟度|ready_for_review|Neo4j.*唯一|历史研究案例|当前研究案例" prototype/设计文档.md DESIGN.md prototype/ui
git diff --check
```

Expected: no prohibited product semantics; any legitimate quoted non-goal is explicitly marked as prohibited.

```bash
git add prototype/设计文档.md DESIGN.md prototype/ui
git commit -m "docs: align prototype with research case workflow"
```

## Task 9: Build the shared library and reviewed knowledge screen

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型6-资料与知识.png`

- [ ] **Step 1: Add failing library assertions**

Require filters for document type, source, publish date, version, review state, linked cases, and entity. Require side-by-side sections for frozen documents/source spans and reviewed statements/relationships. Require reuse counts and `引用到研究案例` without duplicating the source document.

- [ ] **Step 2: Run the focused test**

```bash
node prototype/ui/contract.test.mjs --screens library
```

- [ ] **Step 3: Implement source-versus-knowledge separation**

Show immutable DocumentVersion records as the source layer and reviewed SourceStatement/EvidenceLink records as the knowledge layer. Pending AI proposals may appear only in a clearly separated queue. Include provenance, version lineage, linked ResearchCases, and source-span inspection.

- [ ] **Step 4: Verify, capture, inspect, and commit**

```bash
node prototype/ui/contract.test.mjs --screens library
node prototype/ui/capture.mjs --screens library
git add prototype/ui prototype/设计原型6-资料与知识.png
git commit -m "feat: add research library prototype"
```

## Task 10: Build the point-in-time data center

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型7-数据中心.png`

- [ ] **Step 1: Add failing data-center assertions**

Require metric name, entity, value/unit, period or as-of date, `published_at`, `available_at`, `acquired_at`, source, methodology, revision, provider run, and failure meaning. Require a revision comparison and a visible distinction between “not available at cutoff” and “available now”.

- [ ] **Step 2: Run the focused test**

```bash
node prototype/ui/contract.test.mjs --screens data
```

- [ ] **Step 3: Implement the data provenance workspace**

Render a metric catalog, time-series detail, revision history, and provider run log. The selected metric must show whether each observation was usable at the case cutoff. Add actions to attach a frozen data series to a ResearchCase and inspect the exact provider run.

- [ ] **Step 4: Verify, capture, inspect, and commit**

```bash
node prototype/ui/contract.test.mjs --screens data
node prototype/ui/capture.mjs --screens data
git add prototype/ui prototype/设计原型7-数据中心.png
git commit -m "feat: add point in time data center prototype"
```

## Task 11: Build reproducible snapshot comparison

**Files:**

- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/styles.css`
- Modify: `prototype/ui/contract.test.mjs`
- Generate: `prototype/设计原型8-版本比较.png`

- [ ] **Step 1: Add failing version assertions**

Require two selected snapshot IDs, freeze times, cutoffs, input document/data versions, reviewed relation changes, factor-role changes, conclusion changes, new contradiction, resolved/unresolved gaps, and recorded human rationale.

- [ ] **Step 2: Run the focused test**

```bash
node prototype/ui/contract.test.mjs --screens versions
```

- [ ] **Step 3: Implement semantic diff, not generic activity history**

Render a before/after comparison with a central change rail. Explain why the formal judgment changed and which reviewed evidence or data revision caused it. Make unchanged inputs collapsible and keep AI rerun information separate from formal snapshot changes.

- [ ] **Step 4: Verify, capture, inspect, and commit**

```bash
node prototype/ui/contract.test.mjs --screens versions
node prototype/ui/capture.mjs --screens versions
git add prototype/ui prototype/设计原型8-版本比较.png
git commit -m "feat: add research snapshot comparison prototype"
```

## Task 12: Run full visual and semantic acceptance

**Files:**

- Modify if required: `prototype/ui/app.js`
- Modify if required: `prototype/ui/styles.css`
- Modify if required: `prototype/ui/data.js`
- Modify if required: `prototype/ui/contract.test.mjs`
- Modify if required: `prototype/ui/README.md`
- Modify if required: `prototype/设计文档.md`
- Regenerate: all nine PNG artifacts

- [ ] **Step 1: Run all contracts from a clean prototype server**

```bash
node prototype/ui/contract.test.mjs --screens all
```

Expected: nine screens pass semantic, consistency, forbidden-language, console-error, and horizontal-overflow checks.

- [ ] **Step 2: Regenerate all PNGs in one deterministic run**

```bash
node prototype/ui/capture.mjs --screens all
```

Expected: exactly nine mapped PNGs, each 1600×1000, and no unmapped screenshot files.

- [ ] **Step 3: Verify dimensions and file integrity**

```bash
file prototype/*.png
sips -g pixelWidth -g pixelHeight prototype/*.png
```

Expected: all nine target files are valid PNGs at 1600×1000.

- [ ] **Step 4: Visually inspect every PNG**

Use the image viewer on all nine files. Check:

- hierarchy and dense-data readability at normal scale;
- no clipping, overflow, accidental nested-card wall, or decorative empty zones;
- consistent case facts, dates, statuses, and terminology;
- support/contradict/pending/reviewed states remain distinguishable without color alone;
- AI drafts are always provisional;
- fund information is disclosure-based and never presented as a recommendation;
- the end-to-end path is understandable from creation through frozen version comparison.

- [ ] **Step 5: Run documentation and repository hygiene checks**

```bash
rg -n "TBD|TODO|FIXME|implement later|similar to" prototype/ui prototype/设计文档.md DESIGN.md
git diff --check
git status --short
```

Expected: no placeholders or whitespace errors. `git status` may still show the pre-existing unrelated frontend and research-document changes, which must remain unstaged.

- [ ] **Step 6: Commit only final prototype corrections**

```bash
git add prototype/ui prototype/设计原型.png prototype/设计原型1.png prototype/设计原型2.png prototype/设计原型3-新建研究.png prototype/设计原型4-研究计划.png prototype/设计原型5-审核工作区.png prototype/设计原型6-资料与知识.png prototype/设计原型7-数据中心.png prototype/设计原型8-版本比较.png prototype/设计文档.md DESIGN.md
git diff --cached --check
git commit -m "feat: complete auditable research product prototype"
```

Expected: the commit includes only the prototype source, nine generated images, and the aligned design documentation.

## Acceptance gate

The implementation is complete only when all conditions below are true:

- All nine screens render from one code-native prototype and one coherent fixture.
- The first six screens form a comprehensible workflow: create → plan → collect → analyze → traverse → review.
- Historical and current research use the same ResearchCase model; cutoff and snapshots provide time boundaries.
- Formal judgment, AI proposal, reviewed relationship, source fact, and graph projection are visually and semantically distinct.
- A reviewer can trace every formal claim to a frozen source span or versioned metric.
- Candidate factors are compared by explicit reasoning dimensions and human rationale, not confidence or evidence-count scores.
- The path to companies, stocks, valuation, and funds uses reviewed relationships and point-in-time disclosures.
- Reused knowledge avoids repeated searches while preserving immutable provenance.
- Re-running AI cannot silently mutate a frozen snapshot or formal conclusion.
- Automated contracts pass, all PNGs are 1600×1000, and manual visual inspection finds no clipping or misleading state.

## Deferred beyond this prototype

- Production React component extraction and frontend route integration.
- Backend API/OpenAPI implementation and persistence migrations.
- Live Juyuan capability probes, quota handling, and provider adapters.
- Immutable evidence ledger implementation and projection rebuild jobs.
- Authentication, permissions, collaborative review, and audit-log services.
- Production accessibility, browser-matrix, performance, and end-to-end tests.
- Recommendation, portfolio construction, target prices, or personalized investment advice.
