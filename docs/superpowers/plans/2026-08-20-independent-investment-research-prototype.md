# Independent Investment Research Prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, high-fidelity, clickable three-screen prototype for starting and maintaining company investment research without inheriting the old Fund Engine shell or ResearchCase model.

**Architecture:** Create an isolated static application under `prototype/investment-research-prototype/`. A small in-memory state object owns the confirmed entity, research framing, and selected workbench factor. URL parameters select `search`, `setup`, or `workbench` and choose workbench variants A/B/C. The prototype uses no production APIs, persistence, old prototype scripts, or backend services.

**Tech Stack:** Semantic HTML, modern CSS with OKLCH tokens, vanilla JavaScript, Node.js static server and Playwright from `frontend/node_modules` for capture and interaction verification.

---

## File map

- Create `prototype/investment-research-prototype/index.html`: standalone document shell and script/style entry points.
- Create `prototype/investment-research-prototype/app.js`: route parsing, in-memory state, shared helpers and screen rendering.
- Create `prototype/investment-research-prototype/screens/search.js`: search/start screen and grouped entity results.
- Create `prototype/investment-research-prototype/screens/setup.js`: research framing screen and confirmed-object summary.
- Create `prototype/investment-research-prototype/screens/workbench.js`: workbench data model, factor expansion and A/B/C layouts.
- Create `prototype/investment-research-prototype/styles/tokens.css`: standalone visual tokens and reset.
- Create `prototype/investment-research-prototype/styles/app.css`: shell, shared components and screen layouts.
- Create `prototype/investment-research-prototype/styles/responsive.css`: laptop and mobile adaptations.
- Create `prototype/investment-research-prototype/contract.test.mjs`: DOM, interaction and forbidden-pattern checks.
- Create `prototype/investment-research-prototype/capture.mjs`: local static server and deterministic screenshots.
- Create `prototype/investment-research-prototype/README.md`: scope, routes, simulated-data boundary and one-command start.
- Remove only the rejected files and integrations added for `stock-research` in the old `prototype/ui` after the new prototype passes verification.

### Task 1: Standalone prototype contract and shell

**Files:**
- Create: `prototype/investment-research-prototype/index.html`
- Create: `prototype/investment-research-prototype/app.js`
- Create: `prototype/investment-research-prototype/contract.test.mjs`
- Create: `prototype/investment-research-prototype/styles/tokens.css`
- Create: `prototype/investment-research-prototype/styles/app.css`

- [ ] **Step 1: Write the failing route and isolation contract**

The test must load `index.html` and source files, then assert:

```js
assert.match(html, /data-prototype="independent-investment-research"/u);
assert.doesNotMatch(allSource, /ResearchCase|ResearchRun|Event Research|证据图谱|自动研究完成/u);
assert.deepEqual(routeNames, ["search", "setup", "workbench"]);
assert.equal(defaultRoute, "search");
```

- [ ] **Step 2: Run the contract and verify it fails**

Run: `node prototype/investment-research-prototype/contract.test.mjs`

Expected: FAIL because the standalone prototype files do not exist.

- [ ] **Step 3: Build the minimal standalone shell**

`index.html` must contain only the new prototype mount and entries:

```html
<body data-prototype="independent-investment-research">
  <div id="app"></div>
  <script src="./screens/search.js"></script>
  <script src="./screens/setup.js"></script>
  <script src="./screens/workbench.js"></script>
  <script src="./app.js"></script>
</body>
```

`app.js` must expose stable route helpers:

```js
const ROUTES = Object.freeze(["search", "setup", "workbench"]);
function requestedScreen(search = window.location.search) {
  const candidate = new URLSearchParams(search).get("screen") ?? "search";
  return ROUTES.includes(candidate) ? candidate : "search";
}
window.INVESTMENT_RESEARCH_APP = Object.freeze({ ROUTES, requestedScreen, render });
```

- [ ] **Step 4: Add the new visual foundation**

Use restrained OKLCH tokens, no old CSS imports, and a system Chinese sans stack:

```css
:root {
  --canvas: oklch(0.972 0.008 88);
  --surface: oklch(0.991 0.004 88);
  --ink: oklch(0.25 0.018 258);
  --muted: oklch(0.53 0.023 258);
  --line: oklch(0.88 0.016 82);
  --accent: oklch(0.48 0.13 272);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
}
```

- [ ] **Step 5: Run the contract**

Run: `node prototype/investment-research-prototype/contract.test.mjs`

Expected: PASS for isolation, route registry and default search route.

- [ ] **Step 6: Commit**

```bash
git add prototype/investment-research-prototype
git commit -m "prototype: add independent investment research shell"
```

### Task 2: Search and entity-confirmation screen

**Files:**
- Create: `prototype/investment-research-prototype/screens/search.js`
- Modify: `prototype/investment-research-prototype/app.js`
- Modify: `prototype/investment-research-prototype/styles/app.css`
- Test: `prototype/investment-research-prototype/contract.test.mjs`

- [ ] **Step 1: Add failing search-screen assertions**

```js
await page.goto(`${baseURL}/?screen=search`);
await expect(page.getByRole("heading", { name: /从一家公司开始/u })).toBeVisible();
await expect(page.getByRole("searchbox", { name: "搜索股票、公司或行业" })).toBeVisible();
await expect(page.locator("[data-market-news], [data-hot-ranking], [data-buy-sell]" )).toHaveCount(0);
```

- [ ] **Step 2: Verify the search assertion fails**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen search`

Expected: FAIL because the search renderer is missing.

- [ ] **Step 3: Implement the search state and grouped results**

Use an in-memory fixture with distinct company and security entities:

```js
const RESULTS = Object.freeze({
  securities: [
    { id: "GOOGL", companyId: "ALPHABET", label: "Alphabet Class A", exchange: "NASDAQ", match: "精确代码" },
    { id: "GOOG", companyId: "ALPHABET", label: "Alphabet Class C", exchange: "NASDAQ", match: "关联证券" },
  ],
  companies: [{ id: "ALPHABET", label: "Alphabet Inc.", business: "搜索广告、云计算与数字平台" }],
  industries: [{ id: "CLOUD-INFRA", label: "云计算基础设施", context: "从代表公司进入行业研究" }],
});
```

The empty state shows examples. Typing `GOOGL`, `Alphabet`, `谷歌` or `云计算` reveals grouped results. A result must show entity type, match method, coverage cutoff and data gap. Clicking GOOGL navigates to `?screen=setup&security=GOOGL`.

- [ ] **Step 4: Style the search screen as a distinct entry surface**

Use a compact top bar, central search composition, grouped result sheet and at most three recent studies. Do not add a sidebar, market ticker, news or investment summary.

- [ ] **Step 5: Verify search interactions**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen search`

Expected: PASS for keyboard input, grouped results and navigation to setup.

- [ ] **Step 6: Commit**

```bash
git add prototype/investment-research-prototype
git commit -m "prototype: add investment research entry"
```

### Task 3: Research-framing screen

**Files:**
- Create: `prototype/investment-research-prototype/screens/setup.js`
- Modify: `prototype/investment-research-prototype/app.js`
- Modify: `prototype/investment-research-prototype/styles/app.css`
- Test: `prototype/investment-research-prototype/contract.test.mjs`

- [ ] **Step 1: Add failing setup assertions**

```js
await page.goto(`${baseURL}/?screen=setup&security=GOOGL`);
await expect(page.getByText("Alphabet Inc.")).toBeVisible();
await expect(page.getByText("Class A · NASDAQ · USD")).toBeVisible();
await expect(page.getByRole("radio", { name: "3–5 年" })).toBeChecked();
await expect(page.getByText("系统建议，待确认")).toBeVisible();
await expect(page.getByRole("button", { name: "建立研究" })).toBeVisible();
```

- [ ] **Step 2: Verify setup assertions fail**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen setup`

Expected: FAIL because the setup renderer is missing.

- [ ] **Step 3: Implement minimal research framing**

Provide:

```js
const DEFAULT_FRAME = Object.freeze({
  question: "以 3–5 年视角，这家公司靠什么创造价值，当前价格要求哪些假设成立？",
  horizon: "3–5 年",
  counterQuestion: "什么事实会证明核心业务增长无法转化为每股自由现金流？",
  userHypothesis: "",
  userConcern: "",
});
```

Use one question template selector, one horizon selector and one collapsed optional section. The preview lists four research questions plus the default counter-question. Mark user text as `你的假设`; mark the counter-question as `系统建议，待确认`.

- [ ] **Step 4: Implement progressive transition**

Clicking “建立研究” writes the frame only to in-memory state and navigates to `?screen=workbench&security=GOOGL&variant=A`. No generated-report language or fake percentage is allowed.

- [ ] **Step 5: Verify framing and navigation**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen setup`

Expected: PASS for defaults, optional hypothesis state and workbench navigation.

- [ ] **Step 6: Commit**

```bash
git add prototype/investment-research-prototype
git commit -m "prototype: add research framing flow"
```

### Task 4: Workbench semantic model and default layout

**Files:**
- Create: `prototype/investment-research-prototype/screens/workbench.js`
- Modify: `prototype/investment-research-prototype/styles/app.css`
- Test: `prototype/investment-research-prototype/contract.test.mjs`

- [ ] **Step 1: Add failing semantic assertions**

```js
await page.goto(`${baseURL}/?screen=workbench&security=GOOGL&variant=A`);
await expect(page.getByText("研究员暂定判断")).toBeVisible();
await expect(page.getByRole("heading", { name: "最大反证" })).toBeVisible();
await expect(page.getByRole("heading", { name: "当前价格问题" })).toBeVisible();
await expect(page.locator("[data-key-factor]")).toHaveCount(4);
await expect(page.getByText("下一验证")).toBeVisible();
```

- [ ] **Step 2: Verify the workbench assertion fails**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen workbench`

Expected: FAIL because the workbench renderer is missing.

- [ ] **Step 3: Define typed simulated research data**

Each metric must include `kind`, `period`, `unit`, `asOf` and `sourceBoundary`. Each factor must include `mechanism`, `observations`, `counter`, `unknown`, `nextValidation`, `financialImpact` and `falsifier`. Use four factors: Search economics, Cloud unit economics, AI capital efficiency, competition/regulation.

- [ ] **Step 4: Implement Variant A, judgment first**

Order the page as:

```text
identity + research question + timestamps
current allowed judgment | strongest counter | current price question
four key factors with House / Consensus / Implied separation
how the company earns money | industry context
forecast and valuation bridge | unknowns | one next action
```

Use `证据冲突，暂定判断` when the simulated evidence does not justify a stronger conclusion. Do not show a buy/sell label, target price, composite score, source count or news feed.

- [ ] **Step 5: Implement inline factor expansion**

Clicking a factor toggles an inline detail containing:

```text
机制假设 → 观察事实 → 支持 / 反证 / 替代解释
→ 验证指标和日期 → 财务与估值影响 → 判断影响
```

The expansion is keyboard accessible and uses `aria-expanded`.

- [ ] **Step 6: Verify default workbench semantics and interaction**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen workbench`

Expected: PASS for the required first-screen content, four factors and factor expansion.

- [ ] **Step 7: Commit**

```bash
git add prototype/investment-research-prototype
git commit -m "prototype: add company research workbench"
```

### Task 5: Workbench variants and prototype switcher

**Files:**
- Modify: `prototype/investment-research-prototype/screens/workbench.js`
- Modify: `prototype/investment-research-prototype/styles/app.css`
- Test: `prototype/investment-research-prototype/contract.test.mjs`

- [ ] **Step 1: Add failing variant assertions**

```js
for (const variant of ["A", "B", "C"]) {
  await page.goto(`${baseURL}/?screen=workbench&security=GOOGL&variant=${variant}`);
  await expect(page.locator(`[data-workbench-variant="${variant}"]`)).toBeVisible();
}
await page.keyboard.press("ArrowRight");
await expect(page.locator("[data-workbench-variant=B]")).toBeVisible();
```

- [ ] **Step 2: Verify variant assertions fail**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen variants`

Expected: FAIL because B/C and the switcher are missing.

- [ ] **Step 3: Implement structurally distinct layouts**

- Variant A: judgment first, factors as the main table.
- Variant B: operating driver tree and House/Consensus/Implied differences before the judgment summary.
- Variant C: linear PM memo ordered as question, disagreement, valuation, falsifier and next action.

Shared data is allowed; shared layout wrappers are not. Each variant gets a distinct renderer and CSS grid.

- [ ] **Step 4: Implement the floating prototype switcher**

The switcher updates `variant` in the URL, wraps A/B/C and supports left/right arrow keys except while typing in an input, textarea or editable element.

- [ ] **Step 5: Verify variants**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen variants`

Expected: PASS for A/B/C DOM presence, keyboard switch and URL stability.

- [ ] **Step 6: Commit**

```bash
git add prototype/investment-research-prototype
git commit -m "prototype: add workbench layout variants"
```

### Task 6: Responsive behavior, capture and documentation

**Files:**
- Create: `prototype/investment-research-prototype/styles/responsive.css`
- Create: `prototype/investment-research-prototype/capture.mjs`
- Create: `prototype/investment-research-prototype/README.md`
- Modify: `prototype/investment-research-prototype/contract.test.mjs`

- [ ] **Step 1: Add viewport and forbidden-pattern assertions**

At 1600×1000, 1180×820 and 390×844, assert `scrollWidth === viewport.width`. Scan rendered text for:

```js
const forbidden = /ResearchCase|自动研究完成|买入|卖出|目标价|AI 置信度|综合评分|新闻导致/u;
assert.doesNotMatch(await page.locator("body").innerText(), forbidden);
```

- [ ] **Step 2: Verify responsive assertions fail**

Run: `node prototype/investment-research-prototype/contract.test.mjs --screen responsive`

Expected: FAIL until the responsive stylesheet and viewport rules exist.

- [ ] **Step 3: Implement structural responsive layouts**

Desktop uses the full non-symmetric composition. Small laptop collapses secondary rails below the main reasoning flow. Mobile order is identity, judgment, counter, price question, factors, company/industry, valuation, unknowns and next validation. Tables become labeled rows; no core interaction depends on hover.

- [ ] **Step 4: Implement deterministic capture**

`capture.mjs` starts a local static server, loads each screen/variant, verifies no horizontal overflow, and writes:

```text
prototype/investment-research-prototype/output/01-search.png
prototype/investment-research-prototype/output/02-setup.png
prototype/investment-research-prototype/output/03-workbench-a.png
prototype/investment-research-prototype/output/04-workbench-b.png
prototype/investment-research-prototype/output/05-workbench-c.png
prototype/investment-research-prototype/output/06-workbench-mobile.png
```

- [ ] **Step 5: Document one-command use**

README must expose:

```bash
cd prototype/investment-research-prototype
python3 -m http.server 8010
```

and the URL `http://localhost:8010/?screen=search`, plus the explicit simulated-data and throwaway boundaries.

- [ ] **Step 6: Run the full verification**

Run:

```bash
node prototype/investment-research-prototype/contract.test.mjs
node prototype/investment-research-prototype/capture.mjs
git diff --check
```

Expected: all contracts pass and all six PNGs are written without horizontal overflow.

- [ ] **Step 7: Perform browser critique and fix pass**

Inspect all six PNGs for hierarchy, clipping, density, copy length, factor readability and mobile order. Apply at least one critique-driven correction, rerun the full verification and recapture.

- [ ] **Step 8: Commit**

```bash
git add prototype/investment-research-prototype
git commit -m "prototype: verify independent investment research flow"
```

### Task 7: Remove the rejected old stock prototype integration

**Files:**
- Modify: `prototype/ui/index.html`
- Modify: `prototype/ui/app.js`
- Modify: `prototype/ui/capture.mjs`
- Modify: `prototype/ui/README.md`
- Delete: `prototype/ui/stock-research-prototype.js`
- Delete: `prototype/ui/stock-research-prototype.css`
- Delete only generated `prototype/设计原型12*.png` artifacts from the rejected prototype.

- [ ] **Step 1: Remove only the changes introduced by the rejected prototype**

Restore the old prototype shell to its prior route registry, six-item navigation, fixed historical cutoff and README route list. Do not modify unrelated user work in `prototype/ui`.

- [ ] **Step 2: Verify old and new prototypes independently**

Run:

```bash
node prototype/ui/contract.test.mjs --screens shell
node prototype/investment-research-prototype/contract.test.mjs
```

Expected: both pass; the old shell contains no `stock-research` route and the new prototype contains no old ResearchCase concepts.

- [ ] **Step 3: Commit cleanup**

```bash
git add prototype/ui prototype/investment-research-prototype
git commit -m "chore: retire rejected stock research prototype"
```
