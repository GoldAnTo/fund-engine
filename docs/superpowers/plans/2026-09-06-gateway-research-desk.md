# Research Desk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 用并列的过程与证据阅读区替代割裂的三标签阅读，保留真实溯源、结果与权限边界。

**Architecture:** 现有 ScopedResearchContent 持有私有读取与选择状态，新的布局组件只负责中央内容与右侧阅读器的结构。原文按需读取，沿用既有身份、run、artifact key 失效行为。已有任务阶段和结果字段只重组，不推断新事实。

**Tech Stack:** React 18、TypeScript、现有 CSS tokens、Vitest/Testing Library、Playwright。

工作目录：`<project-root>`。Node 可执行文件 `<user-home>/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`。

## Task 1: 正式页面整合（单个实现者，避免共享组件并发编辑）

Files: 修改 `frontend/src/workbench/GatewayResearchContent.tsx`、`GatewayAssessmentReview.tsx`、`GatewayExecutionPanel.tsx`、`GatewayConversationPanel.tsx`、`GatewayResearchContent.css`、`GatewayWorkbench.css` 与 `frontend/src/app/GatewayRoutes.tsx`。需要拆分时新增 `GatewayResearchDesk.tsx`，只承载布局/选择视图，不另建网络状态。对应更新 `frontend/src/tests/GatewayResearchContent.test.tsx`、`GatewayAssessmentReview.test.tsx`、`GatewayResponsiveLayout.test.ts`、`GatewayExecutionProgress.test.tsx`。

- [x] 先新增失败行为测试。复用 research reading 测试的 setup、snapshot、research fixtures：

```tsx
it("keeps evidence beside an active task without switching central context", async () => {
  setup(undefined, { ...snapshot, runs: [{ ...snapshot.runs[0]!, status: "running" }] });
  const panel = await screen.findByRole("complementary", { name: "证据与来源" });
  await userEvent.setup().click(within(panel).getByRole("button", { name: "查看原文：来源材料一" }));
  expect(await screen.findByText(detail.quote)).toBeVisible();
  expect(screen.getByRole("heading", { name: "运行进度" })).toBeVisible();
  expect(screen.queryByRole("tab", { name: /证据与来源/ })).not.toBeInTheDocument();
});
```

- [x] 在 frontend 运行 `node node_modules/vitest/vitest.mjs run src/tests/GatewayResearchContent.test.tsx`，确认新测试因缺少并列阅读器失败，不是 fixture 错误。
- [x] 保留 process/result 中央选择，取消 evidence 顶级标签；证据放入同一权限边界下的 `aside aria-label="证据与来源"`。现有 showEvidence 只设置过滤和焦点，不切换中央模式；openEvidence 保存当前触发元素与真实 ID，closeEvidence 回到仍连接的触发元素。
- [x] 顶层 reader 创建 `research-desk` 两列；路由 shell 移除说明右栏，主区域占剩余宽度。桌面两列各自滚动，窄屏变成单列，证据显式跳转/返回。禁止 portal 到共享、未授权外壳。
- [x] 原文在右栏可视顶部展示，列表继续可访问；按 `taskId` 过滤，按评估 `evidenceLinkIds` 提供输入选择。若无 task 关联，明确提示。来源按钮使用已存在的安全 URL 逻辑。
- [x] 增加来自真实字段的运行概览，阶段 strip 提前而非报告末尾；当前 task 状态、更新时间、重试时间与连接状态分开表达；有异常任务展示异常数和历史入口。计数标明重复计数，不解释为唯一材料数量。
- [x] 摘要保留原判断/缺口；共同提示汇总、项目提示渐进展示；判断输入按钮默认可见。原始报告仍在 details 中，不改写内容。角色规划紧凑显示，不映射伪造专业角色进展。
- [x] 测试新增任务选择/判断选择不切换中央、关闭原文返回焦点、筛选清除、没有关联任务、空列表、连接断开。保留原有 401/403/404、旧 scope 响应、冻结匹配测试，只替换已废弃的 evidence tab 导航。
- [x] 运行全部前端单测、类型检查和构建。失败先查真实断言原因，不移除安全测试。由于整片 Gateway 含历史未提交文件，本轮不自动整片提交用户既有改动。

## Task 2: 独立审查与真实验收

Files: 验证脚本保存到 `docs/examples/gateway-research-desk-check.mjs`（如无需持久化可写入专用临时目录），最终记录写回本计划。

- [x] SPEC 审查对照 `docs/superpowers/specs/2026-09-06-gateway-research-desk-design.md`，明确缺失项，修复后再通过。
- [x] SPEC 通过后执行 QUALITY 审查，检查键盘焦点、状态 scope、请求生命周期及 CSS 溢出。
- [x] 真实浏览器只读打开 `http://localhost:5178/research/4001171b-f697-4085-b111-2a49935dee93`。点击任一真实评估输入，确认右侧 quote 与 API 该 ID 返回一致；中央判断仍可见，关闭原文恢复触发焦点。
- [x] API 内容验证沿用原始结论 SHA-256：

```js
assert.equal(createHash('sha256').update(JSON.stringify(content.result)).digest('hex'),
  '63f18ec133286482a9b6fa5ddb83aa78ddf55357547be7dec54f46dcc33a0fe6');
assert.equal(content.total_evidence, 7);
assert.equal(content.assessment_review.items.length, 3);
```

- [x] 桌面 1440px、窄屏 390px 截图并视觉检查。浏览器记录 script errors 为零；失败研究不残留此前 quote。不能为验收重跑研究或在 live DB 执行测试。
- [x] 最终执行 `node node_modules/vitest/vitest.mjs run`、`node node_modules/typescript/bin/tsc --noEmit`、`node node_modules/vite/bin/vite.js build`、`git diff --check`。向用户展示正式本地页面，说明实际验证范围与尚未实现的独立角色调度。

## 最终验证记录（2026-09-06）

- 主代理重新运行前端：8 个测试文件、158 项通过；TypeScript 与 Vite 构建通过；无空白错误。
- SPEC 独立审查先发现窄屏标题展开及质量标签两项遗漏，补充红绿测试后复审通过；随后 QUALITY 独立审查通过。
- 真实浏览器读取既有茅台案例：3 项评估、7 条证据。逐项点击得到与 API 一致的 quote；中央上下文及滚动保持，显式切换过程回到顶部，关闭原文恢复焦点。
- 桌面 1440×1100、窄屏 390×844 验证与截图通过；刷新及失败案例不残留旧证据；无脚本错误、API 错误、浏览器 Bearer 或写请求。
- 原始报告 SHA-256 仍为 `63f18ec133286482a9b6fa5ddb83aa78ddf55357547be7dec54f46dcc33a0fe6`；原消息/4161 条事件 hash 保持 `5ef88a3b60613df37d269264673484666670c2fef00f700a1d19dd0769a258c6`。
- 真实验收脚本与截图：`/tmp/fundclaw-desk-check.weuh4A/`。它读取已有结果，不重跑研究。开发工作继续保留在当前隔离 worktree，未提交整片历史脏改动、未推送。
- 已知限制：历史快照首次读取偶尔超过 15 秒，诊断观察到 snapshot 接口最终返回 200，未出现客户端脚本错误。验收等待调整为 45 秒，不代表产品性能改善；本轮未优化后端快照延迟。
- 固定专业角色仍未接入独立执行。运行中帧更新由已有状态/断连单测验证，本轮真实案例验证的是已结束任务回看，不是新启动研究的端到端执行。
