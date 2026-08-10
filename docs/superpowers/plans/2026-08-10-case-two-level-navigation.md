# Case 两层导航 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将单个 ResearchCase 的八个平铺页面入口改为五个研究阶段和上下文二级导航，同时保留全部现有深链接。

**Architecture:** `CaseFrame` 是唯一的 Case 导航宿主。它根据当前 pathname 解析一级阶段与可选二级页面，渲染稳定的一级阶段导航和仅在相关阶段出现的二级导航。窄屏不再横向滚动一级阶段，而是保留当前阶段并通过原生 `details` 的“更多研究内容”入口暴露其他阶段。

**Tech Stack:** React 18、React Router、TypeScript、CSS、Vitest、Testing Library、Playwright。

---

## 文件结构

- `frontend/src/features/case/CasePages.tsx`：定义一级阶段、二级页映射、路径解析和 CaseFrame 导航渲染。
- `frontend/src/styles/research-os.css`：定义一级、二级、窄屏“更多研究内容”导航的布局和焦点状态。
- `frontend/src/styles/research-os-overrides.css`：移除与新导航冲突的窄屏横向滚动覆盖规则。
- `frontend/src/tests/ResearchOsPages.test.tsx`：验证导航层级、激活状态、直接深链接和 375px 视口可见性。
- `frontend/e2e/research-os.spec.ts`：验证真实浏览器中的一级/二级导航和旧 URL 可达性。

### Task 1: 以页面测试锁定两层导航合同

**Files:**
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: 写入一级阶段和二级导航的失败测试**

在现有“keeps every Case workbench tab discoverable on narrow screens”测试旁替换为以下三个独立断言：

```tsx
const stages = await screen.findByLabelText("Case 研究阶段");
expect(stages).toHaveTextContent("研究结论");
expect(stages).toHaveTextContent("证据工作台");
expect(stages).toHaveTextContent("市场与表达");
expect(stages).toHaveTextContent("监测与运行");
expect(stages).toHaveTextContent("关系与图谱");
expect(stages).not.toHaveTextContent("原文资料");

expect(await screen.findByLabelText("证据工作台页面")).toHaveTextContent("命题与证据");
expect(screen.getByRole("link", { name: "证据工作台" })).toHaveAttribute("aria-current", "page");
expect(screen.getByRole("link", { name: "原文资料" })).toHaveAttribute("aria-current", "page");

expect(await screen.findByRole("group", { name: "更多研究内容" })).toBeVisible();
```

渲染 `/events/event-tsm`、`/events/event-tsm/documents` 和 375px 宽度下的 `/events/event-tsm/market`，确保结论页不展示二级导航，原文深链接显示两个正确的选中状态，窄屏仍可发现其他阶段。

- [ ] **Step 2: 运行测试验证 RED**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: FAIL，因为当前 `CaseFrame` 仍输出 `aria-label="Case 页面，可横向滚动查看全部入口"` 和八个平铺入口。

- [ ] **Step 3: 提交仅测试合同**

```bash
git add frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "test: define case two-level navigation"
```

### Task 2: 用路径分组渲染一级和二级导航

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] **Step 1: 定义阶段和页面映射**

替换平铺 `tabs` 常量为显式配置：

```tsx
const caseSections = [
  { id: "conclusion", label: "研究结论", to: "", pages: [] },
  { id: "evidence", label: "证据工作台", to: "evidence", pages: [
    ["evidence", "命题与证据"], ["documents", "原文资料"], ["review", "证据审核"],
  ] },
  { id: "market", label: "市场与表达", to: "market", pages: [] },
  { id: "monitor", label: "监测与运行", to: "monitor", pages: [] },
  { id: "relations", label: "关系与图谱", to: "wiki", pages: [
    ["wiki", "Wiki 图谱"], ["relations", "关联研究"],
  ] },
] as const;
```

实现 `caseSectionForPath(pathname, caseId)`，将 `history` 归入 `conclusion`，`scope` 和 `protocol` 归入 `evidence`，`stocks/*` 与 `funds/*` 归入 `market`，`monitor/config` 归入 `monitor`。对二级页返回当前 suffix，其他深链接只选择一级阶段。

- [ ] **Step 2: 以最小 CaseFrame 变更替换平铺导航**

在 `CaseFrame` 调用 `useLocation()` 并渲染：

```tsx
<nav className="ros-case-primary-tabs" aria-label="Case 研究阶段">
  {caseSections.map((section) => (
    <Link aria-current={activeSection.id === section.id ? "page" : undefined}
      className={activeSection.id === section.id ? "active" : ""}
      to={`/events/${caseId}${section.to ? `/${section.to}` : ""}`}>
      {section.label}{section.id === "evidence" && data.progress.pending > 0 ? ` 待审核 ${data.progress.pending}` : ""}
    </Link>
  ))}
</nav>
{activeSection.pages.length > 0 && <nav aria-label={`${activeSection.label}页面`} className="ros-case-secondary-tabs">...</nav>}
```

二级链接以 `currentSuffix === suffix` 设置 `aria-current="page"`，不要让二级页面改变一级链接目标。保留 Case 标题、Case 切换器和已有内容渲染顺序。

- [ ] **Step 3: 运行页面测试验证 GREEN**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx`

Expected: PASS，且 Case 页面测试继续覆盖原文、审核、市场、监测和关联页面。

- [ ] **Step 4: 提交导航结构**

```bash
git add frontend/src/features/case/CasePages.tsx frontend/src/tests/ResearchOsPages.test.tsx
git commit -m "feat: group case navigation by research stage"
```

### Task 3: 让窄屏可发现全部研究阶段

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/styles/research-os.css`
- Modify: `frontend/src/styles/research-os-overrides.css`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`
- Test: `frontend/e2e/research-os.spec.ts`

- [ ] **Step 1: 写入窄屏“更多研究内容”的失败测试**

在页面测试中设置 `window.innerWidth = 375`，派发 resize，并验证：

```tsx
const more = await screen.findByRole("group", { name: "更多研究内容" });
expect(more).toHaveTextContent("市场与表达");
expect(more).toHaveTextContent("关系与图谱");
```

在 Playwright 新增测试，访问 `/events/event-tsm/documents?client=mock`：

```ts
await page.setViewportSize({ width: 375, height: 800 });
await expect(page.getByLabel("Case 研究阶段")).toContainText("证据工作台");
await page.getByText("更多研究内容").click();
await expect(page.getByRole("link", { name: "市场与表达" })).toBeVisible();
await expect(page.getByLabel("证据工作台页面").getByRole("link", { name: "原文资料" })).toHaveAttribute("aria-current", "page");
```

- [ ] **Step 2: 运行测试验证 RED**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx && npm --prefix frontend run e2e -- --grep "case navigation"`

Expected: FAIL，因为当前没有 `更多研究内容` 组，一级标签依赖横向滚动。

- [ ] **Step 3: 添加语义化的更多阶段入口和响应式样式**

在一级导航后添加：

```tsx
<details className="ros-case-nav-more">
  <summary>更多研究内容</summary>
  <div role="group" aria-label="更多研究内容">
    {caseSections.filter((section) => section.id !== activeSection.id).map(...) }
  </div>
</details>
```

桌面端隐藏 `.ros-case-nav-more`。`@media (max-width: 640px)` 下隐藏 `.ros-case-primary-tabs a:not(.active)`，显示 `.ros-case-nav-more`；设置 summary 与链接使用现有边框、焦点和触控高度。删除 `.ros-case-tabs` 的窄屏横向滚动规则，只保留骨架屏专用 `.ros-case-tabs`。

- [ ] **Step 4: 运行窄屏和全部路由测试验证 GREEN**

Run: `npm --prefix frontend test -- --run src/tests/ResearchOsPages.test.tsx src/tests/routes.test.tsx && npm --prefix frontend run e2e -- --grep "case navigation"`

Expected: PASS，375px 下可展开全部阶段，全部现有深链接继续显示明确的 live-data 错误或正常 mock 页面。

- [ ] **Step 5: 浏览器实查和提交**

在 `http://127.0.0.1:5173/events/<caseId>/documents` 检查桌面和 375px：一级阶段不得横向滚动，原文页面必须同时显示“证据工作台”和“原文资料”选中态。关闭检查标签后提交：

```bash
git add frontend/src/features/case/CasePages.tsx frontend/src/styles/research-os.css frontend/src/styles/research-os-overrides.css frontend/src/tests/ResearchOsPages.test.tsx frontend/e2e/research-os.spec.ts
git commit -m "feat: make case navigation responsive"
```

### Task 4: 全量回归

**Files:**
- Verify only

- [ ] **Step 1: 运行类型与全前端验证**

Run: `npm --prefix frontend run typecheck && npm --prefix frontend test -- --run && npm --prefix frontend run e2e && npm --prefix frontend run build && git diff --check`

Expected: typecheck、183+ 前端测试、35+ 端到端测试和生产构建全部通过，工作树除用户未跟踪资料外无改动。

- [ ] **Step 2: 更新计划状态**

将本计划任务标为完成，并在总计划中把“重构 Case 导航与路由呈现”和“验证桌面与窄屏导航、直接链接和核心研究流程”标记为完成。
