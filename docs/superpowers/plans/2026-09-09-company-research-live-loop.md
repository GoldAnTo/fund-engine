# Company Research Live Loop Implementation Plan

> 使用 subagent-driven-development 分工实现；沿用已批准的设计与隔离工作树。用户已经授权完成整条流程，不重复请求实施许可。

**Goal:** 在同一公司研究工作台完成真实公开资料、真实模型分析、初稿阅读、确定性计算、保存及回放。

**Architecture:** 保留现有严格研究边界与数值引擎，增加每次执行的原文收据、独立不可变初稿及冻结绑定。模型只产生带引用的分析与候选假设。

**Tech Stack:** Python/FastAPI/SQLAlchemy/Pydantic、已有 LLMClient/AIRun、React/TypeScript、pytest/Vitest。

## 1. 原文获取与冻结

Files: 新增 `backend/app/underwriting/services/company_research_live_sources.py` 与 `backend/tests/underwriting/test_company_research_live_sources.py`。

- [x] 先写测试证明当前不存在逐运行真实获取、哈希不符拒绝和精确文本输入收据。
- [x] 实现固定官方来源 allowlist、有界 HTTP、期望 raw SHA-256 验证、HTML/PDF 文本提取、实际输入 excerpts 与完整 source bundle 哈希。
- [x] 原件按内容哈希保存；读取/回放验证文件与 receipt，失败不回退 fixture。
- [x] 定向 pytest 通过，检查网络与时间边界。

## 2. LLM 研究生成

Files: 新增 `backend/app/ai/company_research.py` 与 `backend/tests/underwriting/test_company_research_generator.py`。

- [x] 先写 focus 进入 prompt、严格输出、未知引用/不匹配 quote 拒绝、候选假设不提升权限的失败测试。
- [x] 实现严格输入/输出及 generator；输入原件 excerpts 和数字证据，输出业务分析、经营驱动、候选假设、反证、核验问题与报告章节。
- [x] 所有引用由本地解析；记录 prompt/input/output hashes 和真实 usage；财务模型不接受 LLM 数值结果。
- [x] 用注入 fake client 验证正常与异常。
- [x] 由完整运行验证真实模型：成功 AIRun `a6a1fb71-8071-4674-8f38-29c6d9ea9c47`，两次真实调用合计 65,438 tokens，修正响应通过全部原有约束与引用验证。

## 3. 持久化与 worker 接入

Files: 新增初稿模型、迁移及服务；修改 `company_research_preparation.py`、worker 启动、工作台及发布服务。

- [x] 测试同一 preparation/focus/source bundle 的绑定，以及源阶段失败/过期 claim 不产生可用初稿。
- [x] 在现有 source claim 内执行 HTTP 与 LLM，事务外工作、claim fence 内提交初稿；AIRun 无论成功、失败或过期均保留。
- [x] 初稿在证据未全部审核时独立可读，正式模型仍走现有审核与计算。
- [x] 确认与冻结绑定初稿 ID/hash/source bundle；回放重算验证，不重新联网生成内容。

## 4. API 与七页展示

Files: `company_research_schemas.py`、router、OpenAPI/types、API client、`ResearchWorkbenchPage.tsx`/view 及测试。

- [x] 先写研究初稿待审可读、引用展示、绑定错误拒绝和保存回放测试。
- [x] 新增可选初稿 DTO、严格客户端校验及历史兼容，沿用七页布局展示有依据分析、来源与边界。
- [x] 保存编辑器以真实初稿为初始文本；冻结查看使用精确已保存版本。
- [x] 跑前端相关测试、类型检查和构建。

## 5. 完整验收与交付

- [x] 相关后端、前端回归及独立审查通过。
- [x] 新的独立临时数据库运行真实模型与官方 HTTP；保留现有样例运行作为历史对照。
- [x] 浏览器完成创建、初稿、依据、七项事实确认、复核备忘录、冻结、刷新回放和导出；最终 completed / 100%。
- [x] 交付研究、来源/调用/版本验收记录及只读归档回放，明确未完成的公司适配与估值输入。冻结版本 `091bc569-72d2-49fa-aea0-93043577a5be`，正式判断仍为 not_answerable。

验证命令统一使用后端 `.venv/bin/python -m pytest`，前端 `node scripts/with-project-node.mjs` 启动 Vitest、tsc 和 Vite。离线测试始终使用 test 环境；真实运行独立配置并禁止 mock。

当前证据与剩余真实运行步骤见 `docs/2026-09-09-company-research-live-loop-verification.md`。

## 6. 真实执行发现问题后的收敛

- [x] 官方 IR PDF 独立身份与逐项报表核验；旧 v1 来源继续可回放。
- [x] 安全异常诊断证实原 60 秒配置读取超时，独立运行配置调整到 180 秒；保留失败收据。
- [x] 自动重试清除当前错误状态，历史失败事件保持；完整 worker 176 passed、1 skipped，闭环集成 12 passed。
- [x] 使用确定性原文片段 ID 作为模型引用接口，维持已保存结构及全部原有验证规则；190 个引用覆盖全部片段的非空白文本，93 项生成器回归通过。
- [x] 增加最多一次带校验反馈的模型修正，保留每次请求、响应、失败和用量的真实审计，维持最终严格校验；生成器与运行集成 127 项测试通过，独立代码审查未发现阻断项。
- [x] 修复有服务端自动重试计划时页面停止轮询的问题，以及来源完成阶段的受限状态恢复；工作台/视图 122 项测试、TypeScript 和独立审查通过。
- [x] 修正后完成真实报告复核与冻结导出，更新最终验收记录；原机器稿单独保留，复核稿与页面/冻结/导出逐字节一致。
