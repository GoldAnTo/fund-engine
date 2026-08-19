# Anthropic Financial Services：对 Fund Engine 的可借鉴模式核验

> 调研日期：2026-08-10  
> 对象：[`anthropics/financial-services`](https://github.com/anthropics/financial-services/tree/38652224c10610fa52eee2acee3ac712dcff01f2)（提交 `38652224c10610fa52eee2acee3ac712dcff01f2`）  
> 方法：只读该仓库的一手源码与 Fund Engine 当前 README / 设计文档；没有修改产品代码。  
> 结论边界：这是 Claude 插件与 Managed Agent 的**参考模板库**，不是带不可变研究证据账本的金融研究产品。

## 结论先行

Fund Engine 不应照搬它的“多个命名 Agent 产出研究/模型文档”的产品形态；本项目已经在关键的可审计性上更强：结论可回到冻结原文、AI 与人审记录分离、`available_at` 支持历史回放，并且研究运行不直接发布结论。[Fund Engine README](../../README.md) · [全局运行透明度设计](../superpowers/specs/2026-08-09-global-research-run-transparency.md)

最值得吸收的是四个工程模式：**一套领域技能、多种运行表面；连接器集中配置；不可信材料隔离、独立复核、最小写权限；把配置/运行/交接变成可验证的契约。** 应把它们嵌入现有 `ResearchRun → 候选 → 人审 → 追加发布` 链条，而不能绕开账本直接生成或发布研究判断。

## 一、该项目实际采用的结构

```text
垂直插件（技能源、命令、MCP）
        ├─ 同步为每个命名 Agent 的自包含技能副本
        └─ Agent 系统提示词
                 ├─ Cowork / Claude Code 插件
                 └─ Managed Agent：编排器 + 深度一层的叶子 worker + 外部事件编排
```

- 以 `financial-analysis` 为共享核心，再按投行、卖方研究、PE、财富、基金运营、KYC 等垂直领域组织技能；命名 Agent 是端到端工作流包装。仓库明确将技能源放在 vertical plugin，再同步进 Agent bundle，并用 `check.py` 防止副本漂移。[仓库结构说明](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/CLAUDE.md) · [同步/校验实现](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/scripts/check.py)
- 同一系统提示词和技能可作为 Cowork 插件，也可被 `agent.yaml` 引用并部署为 Managed Agent；后者采用编排器调用深度仅一层的叶子 worker，跨 Agent 由外部工作流引擎接收 `handoff_request` 后重新投递，而不是 Agent 彼此任意调用。[README](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/README.md) · [Managed Agent 约定](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/managed-agent-cookbooks/README.md) · [交接参考循环](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/scripts/orchestrate.py)
- 数据连接器被集中在核心 `.mcp.json`，工作流插件复用它；文档列出外部数据/文档供应商，并提示访问可能要求订阅或 API key。[连接器清单](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/README.md#MCP-integrations)

## 二、最有价值的治理与安全模式

以 GL 对账模板为代表，该项目将风险路径拆成：`reader（读不可信对手方文件） → critic（仅用受信任 GL/子账复核） → resolver（仅接收已核验集合并写报告）`。编排器本身无写权限；reader 无 MCP、shell、写权限；resolver 不读取外部文件；最终账务调整仍留给人工审批。[安全分层说明](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/managed-agent-cookbooks/gl-reconciler/README.md) · [权限配置](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/managed-agent-cookbooks/gl-reconciler/agent.yaml) · [reader 输出模式](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/managed-agent-cookbooks/gl-reconciler/subagents/reader.yaml)

这与 Fund Engine 的边界高度一致：机器产物是待审材料，而不是结论、交易、账务或审批；但它额外提供了一个可复用的提示注入防线——**不可信原文绝不与写工具、企业连接器同处一个执行身份**。外部 Agent 交接也采用目标白名单和 payload JSON Schema 校验。[交接白名单与 Schema](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/scripts/orchestrate.py)

## 三、建议落到 Fund Engine 的方式

| 优先级 | 建议 | 如何保持 Fund Engine 的证据语义 |
|---|---|---|
| P0 | 建立版本化“研究技能注册表” | 为 `报告提取`、`事件研究`、`指标复算`、`实体对齐`定义输入范围、允许来源、所需定位、候选输出类型、排除条件、复核动作与版本；技能只创建 `Proposal` / `SourceStatement` 候选，不能创建正式结论。用同一份定义驱动人工入口和后台 `ResearchRun`。 |
| P0 | 采用 reader / critic / publisher 三段式隔离 | PDF/OCR/网页抽取 worker 仅能读取原始文件并输出受限结构化候选；critic 只从冻结 `DocumentVersion/SourceSpan` 和许可 provider 快照核验；publisher 只能在人工 `ReviewDecision` 后追加正式记录。对中文原文不要照搬 ASCII 字符白名单，应传递不可变 `SourceSpan` ID、hash 和 locator，再由服务端取原文。 |
| P0 | 做“连接器契约注册表”，不是仅一份 MCP URL | 每个 provider 记录授权主体/租户范围、可用数据域、是否只读、请求与响应版本、采集时间、原始响应 hash、`available_at` 规则、失效/限流策略。研究运行只引用已冻结 `provider_record`；这正好补强现有的许可来源与回放边界。 |
| P1 | 将 `ResearchRun` 解释为可回放的工作流编排 | 参照其 steering event，把触发原因、技能版本、输入 scope、connector snapshot、worker 阶段、拒绝原因、候选数量、下一人工动作做成不可变 run event；当前设计已有 `CaseMonitorVersion` 与运行事件，重点是把技能/连接器版本补进解释面，而非另建 Agent 平台。 |
| P1 | 把模板校验扩展为真实发布门禁 | 除路径/引用/副本漂移外，加入 MCP JSON/YAML 解析、权限矩阵检查、输出 schema 实际调用测试、提示注入回归集、来源/页码/`available_at` 金标集，以及“无人工决定不得发布”端到端测试。 |
| P2 | 仅在跨 Case 的正式协作需要出现后采用事件交接 | 使用类型化的 `ResearchRun` 事件和 allowlist 来启动新任务；不要从模型文本中正则解析交接指令。跨工作流的输入应只带 case/snapshot/proposal ID，而非原文或供应商响应。 |

## 四、不能照搬，以及本次核验发现的实现缺口

1. **研究语义不足。** 例如其 `thesis-tracker` 把“加仓/减仓/退出”和 conviction 作为普通输出步骤；它有“可证伪、追踪反证”的好原则，但没有冻结原文、`available_at`、证据关系、人审追加记录的强制数据模型。[Thesis Tracker](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/plugins/vertical-plugins/equity-research/skills/thesis-tracker/SKILL.md) Fund Engine 应保留命题、支持/反证条件与证据账本，禁止让技能直接导出交易动作。
2. **Schema 防线在源码中尚未闭环。** 文档称 deploy harness 会在 reader 与编排器之间执行 schema 校验；但当前 `validate.py` 只是独立 CLI，`deploy-managed-agent.sh` 在发起 API 请求前删除 `output_schema`，脚本内没有调用该校验器。故这应被视为模板设计意图，而不是已由本仓库证明的运行时保证。[校验器](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/scripts/validate.py) · [部署脚本](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/scripts/deploy-managed-agent.sh)
3. **配置验证有盲区。** 在该固定提交上，`financial-analysis/.mcp.json` 的 `egnyte` 项后缺逗号、末尾括号也不配对，`python -m json.tool` 解析失败；而 `scripts/check.py` 对 YAML、plugin/marketplace/steering JSON 做校验，却未把 `.mcp.json` 纳入 JSON glob，仍报告通过。因此 Fund Engine 若采用连接器注册表，必须把全部可加载配置纳入发布门禁，且不得把 provider URL 配置视为已验证连接。[有问题的 MCP 配置](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/plugins/vertical-plugins/financial-analysis/.mcp.json) · [check.py 的 JSON 范围](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/scripts/check.py)
4. **文本型交接仍有注入面。** 其交接脚本自己也说明：模型可能回显不可信材料中的 `handoff_request`；allowlist 与 schema 是缓解而非根除，生产实现应使用模型无法伪造的 typed tool/SSE event。Fund Engine 应直接由服务端从已审核状态转换产生下游 run，而非解析模型输出文本。[脚本中的威胁模型](https://github.com/anthropics/financial-services/blob/38652224c10610fa52eee2acee3ac712dcff01f2/scripts/orchestrate.py)

## 推荐顺序

先做 P0 的技能/连接器契约和不可信资料隔离，并把它们接到既有审核与发布门禁；随后补 P1 的运行解释和测试矩阵。多 Agent、跨系统 handoff 属于 P2：在真实人工闭环与 provider 快照稳定之前，引入它只会放大运行状态与审计复杂度。
