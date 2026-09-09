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
- [ ] 由完整运行验证真实模型（连接已验证，组合运行待 SEC 联系信息）。

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
- [ ] 新的独立临时数据库运行真实模型与官方 HTTP；保留现有样例运行作为历史对照。
- [ ] 浏览器完成创建、初稿、依据、确认、保存、刷新回放和导出。
- [ ] 交付可打开的研究、来源/调用/版本验收记录，明确未完成的公司适配与估值输入。

验证命令统一使用后端 `.venv/bin/python -m pytest`，前端 `node scripts/with-project-node.mjs` 启动 Vitest、tsc 和 Vite。离线测试始终使用 test 环境；真实运行独立配置并禁止 mock。

当前证据与剩余真实运行步骤见 `docs/2026-09-09-company-research-live-loop-verification.md`。
