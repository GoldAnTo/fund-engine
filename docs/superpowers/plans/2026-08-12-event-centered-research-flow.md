# 事件主体研究流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将正式事件研究工作台改为可切换事件、可理解当前阶段、可执行唯一下一任务的连续研究界面。

**Architecture:** `CaseFrame` 继续负责读取当前 `EventWorkbench` 和事件列表，但事件选择、阶段推导和任务说明拆成纯展示模型与聚焦组件。所有导航使用已有事件路由和真实 `nextAction`，不新增后端状态或前端伪运行。

**Tech Stack:** React 18、React Router 6、TypeScript、CSS、Vitest、Testing Library。

---

### Task 1: 锁定事件切换和阶段展示合同

**Files:**
- Modify: `frontend/src/tests/ResearchOsPages.test.tsx`
- Create: `frontend/src/domain/eventResearchPresentation.test.ts`

- [ ] 写纯函数失败测试，覆盖生命周期到六阶段的映射、人工/系统责任和下一动作路由。
- [ ] 写页面失败测试，断言“当前事件研究”按钮、事件菜单状态、同工作区切换和六阶段条。
- [ ] 运行 `npm --prefix frontend test -- --run src/domain/eventResearchPresentation.test.ts src/tests/ResearchOsPages.test.tsx`，确认因组件和展示模型不存在而失败。

### Task 2: 实现事件研究展示模型

**Files:**
- Create: `frontend/src/domain/eventResearchPresentation.ts`
- Test: `frontend/src/domain/eventResearchPresentation.test.ts`

- [ ] 定义六阶段常量和 `eventResearchStage(workbench)`。
- [ ] 定义 `eventActionPresentation(workbench, caseId)`，返回责任方、原因、步骤、完成结果、按钮文案和目标路由。
- [ ] 运行领域测试，确认全部映射通过。

### Task 3: 实现正式事件切换器和事件级页头

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/styles/research-os.css`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] 将原生 `select` 替换为“当前事件研究”按钮和事件列表弹层。
- [ ] 每个事件显示真实状态、下一人工动作和更新时间；选择后保留当前工作区后缀。
- [ ] 在事件标题下渲染六阶段进展，将原有五项一级导航标注为“事件工作区”。
- [ ] 加入 Escape、点击选项关闭和窄屏布局。
- [ ] 运行页面测试，确认事件切换与深链接行为通过。

### Task 4: 将结论页下一步改为可执行任务引导

**Files:**
- Modify: `frontend/src/features/case/CasePages.tsx`
- Modify: `frontend/src/styles/research-os.css`
- Test: `frontend/src/tests/ResearchOsPages.test.tsx`

- [ ] 使用展示模型渲染责任方、任务步骤、原因和完成结果。
- [ ] 保留真实路由目标，系统运行或等待状态显示“现在无需操作”。
- [ ] 添加页面测试覆盖证据审核、范围编辑、系统运行和发布后跟踪。

### Task 5: 全量验证

**Files:**
- Verify only

- [ ] 运行 `npm --prefix frontend run typecheck`。
- [ ] 运行 `npm --prefix frontend test -- --run`。
- [ ] 运行 `npm --prefix frontend run build`。
- [ ] 运行 `git diff --check`，并在本地浏览器检查桌面和窄屏事件切换。
