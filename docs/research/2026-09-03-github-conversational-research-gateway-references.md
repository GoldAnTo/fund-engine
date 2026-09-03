# 对话式投研 Gateway：GitHub 借鉴清单

日期：2026-09-03

## 结论

Fund Engine 应自建 tenant/case/证据账本控制层，选择性借鉴开源项目的
运行时、事件协议和可观测性模式。不要把任何通用 agent 框架的 session、memory、
tool 权限或 trace 当作本系统的授权或研究事实来源。

推荐顺序：

1. **OpenAI Agents SDK Python**：固定角色、受限 tools、guardrails、handoff 和
   tracing 的 Python 实现参考。
2. **AG-UI + CopilotKit**：网页端的实时角色事件、暂停/恢复、决策卡和 typed UI
   事件协议参考。
3. **LangGraph**：durable task graph、检查点、可恢复执行与人工中断的实现参考；
   仅在现有 worker/事件账本无法承担编排时才引入运行时依赖。
4. **OpenClaw**：Gateway、skill/tool 分层和隔离 session 的参考；不作为核心
   多租户授权边界或默认执行器。
5. **Langfuse**：开发/运维侧 LLM tracing 与 prompt/version 观测参考；用户可见的
   研究进度仍由 Fund Engine 追加式领域事件驱动。
6. **OpenBB**：金融资料 provider extension / 标准化 adapter 的参考；绝不替代来源
   许可、冻结、locator、available-at 或准入门禁。

## 逐项评估

| 项目 | 可借鉴的具体能力 | 在 Fund Engine 的落点 | 不应照搬 | 许可证 / 适配性 |
| --- | --- | --- | --- | --- |
| [OpenAI Agents SDK Python](https://github.com/openai/openai-agents-python) | `Agent` 的 instructions/tools/output/guardrails；manager-as-tools 模式；handoff；trace/span 层次与自定义 exporter | `AgentRuntime` 的原生 adapter；四个固定角色；每个领域命令加 Pydantic 输入/输出验证；`conversation_id`/`run_id` 作为 trace group/correlation | SDK session、默认 tracing 和 agent handoff 不能决定 tenant、case 权限或账本写入；模型不能自主选择任意 tool | MIT；Python，最贴近 FastAPI 后端。官方 [orchestration](https://github.com/openai/openai-agents-python/blob/main/docs/agents.md) 与 [tracing](https://github.com/openai/openai-agents-python/blob/main/docs/tracing.md) 值得优先阅读。 |
| [AG-UI](https://github.com/ag-ui-protocol/ag-ui) | 面向 Agent ↔ 用户的事件协议；run/tool/activity 生命周期；snapshot + delta；暂停、恢复、取消与 steering | Gateway 的 SSE 事件 schema 的借鉴来源；角色卡和 evidence/blocker 卡应消费领域安全子集，而不是直接转发 token 或 reasoning | 不让前端接受通用 `Raw` 事件或自由生成的 UI tree；鉴权、事件排序、数据脱敏由后端承担 | MIT；Python/TypeScript 皆有，适合前后端解耦。见 [事件模型](https://github.com/ag-ui-protocol/ag-ui/blob/main/docs/concepts/events.mdx)。 |
| [CopilotKit](https://github.com/CopilotKit/CopilotKit) | React agent chat、tool/状态事件渲染、human-in-the-loop、headless UI 组织方式 | 可借鉴对话 timeline、可恢复决策卡、role activity components；优先借 UX/协议思路，谨慎引包 | 不把 agent shared state 作为 Case 或证据真相；不让模型任意生成可执行前端组件 | MIT；React/TS 匹配现有前端。 |
| [LangGraph](https://github.com/langchain-ai/langgraph) | 任务图并行、checkpoint、interrupt/resume、节点级持久化和流式 task updates | 作为四角色 DAG 的参考；若后续替换/补强 worker 时，可将 `RoleRun` checkpoint 映射到数据库事件 | 不与现有不可变 ResearchRun、AcquisitionJob、Evidence ledger 建立第二套权威状态；不要把“thread memory”用于历史回放 | MIT；Python 可用。其 [checkpoint 文档](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/checkpointers.mdx) 对崩溃恢复很有价值。 |
| [OpenClaw](https://github.com/openclaw/openclaw) | Gateway/channel adapter、skill/tool/plugin 分层、隔离子代理、后台任务与流式交付 | 二期 `OpenClawRuntimeAdapter` 的能力模型和 sandbox profile；技能 manifest 的目录/版本/准入管理 | 默认共享会话、全局 memory、host exec、聊天中安装 plugin、将 session 误作授权边界 | 源码与部署细节需在接入前逐项复核许可证；当前建议仅借鉴模式。官方 [agent/session 边界](https://github.com/openclaw/openclaw/blob/main/docs/concepts/agent.md) 是关键阅读。 |
| [Langfuse](https://github.com/langfuse/langfuse) | 自托管 LLM trace、prompt version、成本/延迟、evaluation dataset、OpenTelemetry 集成 | 仅开发和运维侧：将 `run_id`、role、model manifest、tool 调用类型做脱敏 tracing；生产 trace 可以辅助诊断而非驱动研究判断 | 原始提示词、用户材料、证据正文默认不出库；用户 UI 不直接展示内部 chain-of-thought 或 trace | OSS 可自托管；需单独评估部署数据、遥测关闭及许可证。见 [observability 文档](https://github.com/langfuse/langfuse-docs/blob/main/content/docs/observability/overview.mdx)。 |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | 多 provider 的 extension、标准参数和 REST router 组织 | 对市场/公开财务数据 adapter 的能力注册、健康度和 source fallback | 外部 provider 返回值只是待验证资料；不能跳过本项目 source policy、原件冻结和时间边界 | MIT；Python，金融领域相关。见 [Platform README](https://github.com/OpenBB-finance/OpenBB/blob/develop/openbb_platform/README.md)。 |

## 不建议作为一期依赖的项目

- [Microsoft AutoGen](https://github.com/microsoft/autogen)：仓库已标注 maintenance
  mode，并建议新项目改用 Microsoft Agent Framework；可读其 event-driven/agent team
  设计，但不宜新增依赖。
- [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)：MIT、Python
  且功能很全（workflow/checkpoint/streaming/HITL），可作为 LangGraph 的替代评估项；
  但一期不应同时引入两个大型编排运行时。
- 通用 Deep Research、任意 web-browser agent 和 agent marketplace：其自由浏览、
  动态 skill 安装和不可控记忆模型与 Fund Engine 的来源授权、历史回放和审阅边界冲突。

## 建议的技术组合

一期：自建 `ResearchGateway` + PostgreSQL 追加事件 + 现有 worker，参考 Agents SDK
的角色/tools/guardrails，采用 AG-UI 风格的 SSE 事件契约，前端自己实现窄的 React
角色卡和证据卡。

二期：若现有 worker 无法承载角色 DAG，再评估 **仅一种** durable workflow runtime
（LangGraph 或 Microsoft Agent Framework）；公告监控成熟后再接 OpenClaw runtime
adapter。Langfuse 可以作为脱敏、可关闭的开发观测旁路，而非用户研究记录。

## 采用前门槛

每个第三方依赖/adapter 在引入前必须完成：许可证与版本锁定、安全审查、网络/数据
出境审查、tenant/case scope 验证、敏感字段脱敏、失败/重试幂等测试，以及“不能绕过
冻结证据和人工审核”的端到端验收。
