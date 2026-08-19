# 本轮任务完成概览

## 已完成

- 补完主题工作台反方研究任务闭环。
- 发起任务后保存后端返回的任务 ID，后续完成操作更新真实任务。
- 同步修正旧版 dossier 入口的反方任务 ID 处理。
- 增加反方任务区样式与 Mock fixture 数据。
- 清理前端源码中的旧 `legacy` 路由引用。
- 将依赖 fixture 的核心页面测试改为显式使用 `MockResearchAdapter`。
- 将根入口从主题列表改为研究总览，增加明确的“开始一项研究”和“继续当前研究”动作。
- 新建研究成功后，按 `caseId` 进入对应研究计划或案例工作台，不再丢失上下文。
- 研究计划增加“打开研究工作台”和“查看证据图谱”下一步动作。
- 案例工作台增加返回研究计划入口，总览任务队列可直接进入当前案例。
- 将研究总览升级为首屏研究驾驶舱：展示五阶段研究轨道、当前阶段、下一步主按钮和快捷动作条。
- 当前阶段使用明显高亮，已完成阶段显示完成态，窄屏自动改为纵向流程轨道。

## 研究主路径

`研究总览 → 开始研究 → 新建研究 → 研究计划 / 接入数据 → 案例工作台 → 证据图谱 / 审核中心 → 结论`

## 验证结果

- `npm run typecheck` 通过。
- `npm run build` 通过。
- 核心页面测试 3 个文件、21 个断言全部通过：
  - `ResearchCaseDossierPage.test.tsx`
  - `ResearchWorkbenchPage.test.tsx`
  - `RelationshipCanvasPage.test.tsx`

## 自动研究后端引擎

- 新增 `ResearchRun` / `ResearchTask` operational 模型与 `0011_auto_research` 迁移。
- 新增自动研究 repository/service：自动创建 support、contradict、result、alternative 任务，复用现有抽取、证据提议和 AI 临时评估能力。
- 新增启动与查询接口：`POST /api/v1/research-cases/{case_id}/runs`、`GET /api/v1/research-runs/{run_id}`。
- 实现预算、最大轮次、无新增证据停止规则；运行最终停在 `waiting_for_review`，AI 产物不越过人工复核闸门。
- 已打通 `waiting_for_review` handoff：本 run 产生的 pending `Proposal` 与 provisional `AIAssessment` 会幂等创建首页 `TaskItem`（`review_proposal` / `review_assessment`），运行详情返回 `pending_proposals` 与 `review_tasks`。
- 证据继续走 `/review-proposals/{id}/decisions`，临时判断继续走 `/assessments/{id}/reviews`；不直接写正式结论。
- 已补自动研究运行列表与取消：`GET /api/v1/research-cases/{case_id}/runs`、`POST /api/v1/research-runs/{run_id}/cancel`。
- 人工审核完成后自动关闭对应首页任务：`TaskRepository.close_review_task` 幂等将 `open/in_progress` 设为 `done`（已 done 或不存在不报错）。
- 已补自动研究运行列表、取消和运行摘要事件接口：取消支持 queued/running/waiting_for_review，cancelled 幂等，其他终态返回 409。
- 前端已接入自动研究入口、运行列表和详情页，展示进度、证据、缺口、待审核 proposal/assessment；HTTP/Mock adapter 均已对齐。
- 自动研究专项测试 15 项通过；后端全量测试 `578 passed, 2 skipped`。
- 修复自动研究加载错误：前端改用真实案例列表中的 `case_id`，不再请求不存在的硬编码 ID；404 接口错误改为明确提示“接口不存在，请重启后端服务”，不再伪装成网络不可用。
- 修复 HTTP dossier adapter 对缺失 `changes` 字段的兼容，避免页面被无关响应字段拖垮。
- 前端自动研究与 HTTP adapter 定点测试 21 项通过，`npm run typecheck` 与 `npm run build` 通过；仅有既存 chunk size/dynamic import 警告。
- 运行环境修复：重启后端并对齐 PostgreSQL Alembic 版本（实际已有 0010 表，版本记录从 0009 安全 stamp 到 0010），执行 0011 创建 `research_runs/research_tasks`；真实运行列表接口已返回 200。
- 详细实现审计见 [auto-research-engine-implementation.md](/Users/xiongjiali/code/fund-engine/outputs/auto-research-engine-implementation.md)。

## 备注

工作区仍包含本轮之前已存在的后端、前端和评估报告改动；本轮未回滚或覆盖这些改动。
