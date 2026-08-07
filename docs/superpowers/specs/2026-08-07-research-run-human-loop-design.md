# 自动研究人工闭环与后台运行设计

## 目标

让研究员能在真实后端中完成“启动自动研究 → 审核并发布证据提案 → 审核 AI 判断 → 查看正式结论”的单案例闭环；运行由可恢复的数据库 worker 执行，不阻塞 HTTP 请求。

## 运行模型

`POST /research-cases/{case_id}/runs` 只创建 `ResearchRun` 和初始 `ResearchTask`，返回 `queued`。独立 worker 用已有的 `jobs` 操作表领取一个 queued run，在任务边界检查取消请求，并持续写入 `ResearchRun`、`ResearchTask` 和 `JobEvent`。worker 重启后可继续领取 queued/running 但未终结的运行。

每次运行只处理当前案例关联的文档版本；研究进展按该案例内 thesis 的正式 `EvidenceLink` 数量统计。待审核 Proposal 不计为已确认的证据，但会作为独立 pending 产物在运行详情和审核工作区中展示。

## 人工闭环

运行详情页允许选择案例、启动运行、取消 queued/running run，并把 pending proposal 和 assessment review task 链接到审核中心。审核中心合并两类队列：

1. 已发布 EvidenceLink 的关系复核，沿用现有四要素决定。
2. `Proposal` 复核，展示提案、版本和所属命题；确认、修改或驳回调用 proposal decision API。确认/修改由 `ProposalPublisher` 发布正式实体，并关闭对应任务。

评估仍在案例工作台审核。审核成功后刷新运行详情、案例工作台和结论页面，结论只读取人工确认的 review decision。

## 开发与验证

前端默认连接真实 API；mock 必须显式使用 `?client=mock` 或 `npm run dev:mock`。`npm run dev:live` 需要 `VITE_RESEARCH_API_URL`。仓库声明 Node >=20，Playwright 与 Vite 都由同一 Node 版本执行。

删除未被运行时路由使用的旧页面测试，替换为 prototype 页面测试。新增一个不使用 mock 的端到端测试：临时 SQLite 后端种子化、前端连该 API，驱动研究员完成 run 的启动、proposal 发布和 assessment 复核，并断言结论回流。

## 非目标

不引入第三方消息队列、分布式锁或实时 SSE。首版用单进程 worker CLI 与轮询；多 worker 扩展保留给以后。
