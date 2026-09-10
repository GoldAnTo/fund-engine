# 可信研报接入与恢复设计

**状态：已确认，尚未实现**
**日期：2026-08-08**

## 1. 决策

保留 `7a675da`、`57042e9`、`439d82d` 中“解析失败后可补资料、不会进入空工作台”的体验目标，但不直接合入其数据写入与自动研究行为。

研报接入被定义为资料收件箱能力，不是正式研究的开始。补充正文、规则或模型抽取的输出，均须经过来源治理、机器校验与人工审核；只有通过研究定义门槛，才可运行补证、监控、股票/基金表达或发布结论。

本规格是[研究操作系统基线](../../design/2026-08-08-research-operating-system-baseline.md)第 4、7 节，以及[抽取信任门禁计划](../plans/2026-08-08-report-extraction-trust-gate.md)、[指标与机制模板设计](2026-08-08-metric-and-mechanism-template-design.md)在“研报无法完整解析、用户补充正文”场景下的具体约束。

## 2. 范围与非目标

### 范围

1. 支持聚源等授权供应商、其他外部研报、网页、用户粘贴和文件上传进入同一资料收件箱。
2. 支持 PDF 解析失败或未产生可研究陈述后的原地恢复。
3. 将恢复材料、候选抽取、人工审核、研究定义和受控运行连接为可解释的状态机。
4. 让用户在每一个状态看到一个唯一主操作、原因和完成后解锁的能力。

### 非目标

1. 不在本次设计中实现全市场研报覆盖、通用聊天问答或自动交易。
2. 不让模型自动确定结果变量、必要条件、阈值或因果结论。
3. 不用“补充正文”修补或改写原 PDF、原网页和已冻结资料。
4. 不将单一指标、研报观点或用户粘贴文本自动升级为正式结论。

## 3. 状态机与用户动作

```text
资料冻结
  ├─ 原文不可读 / 无可用陈述 → needs_supplement
  └─ 可抽取                    → extracting

needs_supplement
  └─ 独立冻结补充内容快照       → extracting

extracting
  └─ 机器校验通过的候选         → candidates_pending_review

candidates_pending_review
  └─ 人工确认发布正式陈述       → research_definition_required

research_definition_required
  └─ OutcomeBinding + 模板 + 反证规则
                                 → researchability_blocked / evidence_ready

evidence_ready
  └─ 立即补证一次或 CaseMonitor → research_run
```

| 状态 | 主操作 | 必须说明 | 解锁条件 |
|---|---|---|---|
| `needs_supplement` | 补充正文并标注页码 | 原件已冻结；为何不可读；补充内容将独立保存 | 有可定位补充内容 |
| `candidates_pending_review` | 审核 N 条关键陈述 | 候选不是事实；逐字引文和机器校验结果 | 至少一条必要陈述被审核发布 |
| `research_definition_required` | 定义结果变量与证伪条件 | 缺失哪个指标、范围、替代解释或验证事件 | `ResearchabilityGate` 通过 |
| `researchability_blocked` | 补齐指定缺口 | 规范化 reason code 与所需证据 | 所有门槛通过 |
| `evidence_ready` | 立即补证一次 | 本次检查范围、允许来源、预算和下一验证事件 | 创建受控 `ResearchRun` |

`ready_to_extract` 不得表示“可以自动研究”；历史接口若暂时保留该字面值，客户端也必须以 `next_action` 和门槛状态为准，不能据此打开正式 Case 工作台。

## 4. 来源与恢复材料

### 4.1 原件永不被补充文本改写

上传 PDF、聚源记录、网页快照和首次粘贴内容各自冻结为独立 `DocumentVersion`。恢复时的用户文本必须创建新的 `pasted_snapshot`（或等价的独立文档版本），以显式关系 `supplements_document_version_id` 关联原件。

补充快照至少记录：

```text
raw_text / content_sha256 / MIME / captured_at / created_by / tenant_id
source_admission_type=pasted_snapshot
supplements_document_version_id / claimed_page_reference
verification_state / retention_policy / source_contract_version
```

`claimed_page_reference` 是用户的页码声明，不能被渲染为 PDF 解析 locator，也不能证明文字确实出自对应页面。

### 4.2 权限继承规则

补充快照继承原资料与补充资料自身约束中的**更严格**权限。它不能因为被关联到已授权原件而得到更宽的 AI 处理、展示、导出、API 使用、保留或跨租户权限。

每个来源必须可关联 `SourceContract`，用于执行供应商/租户、允许展示/检索/AI/导出/API、地域、有效期、保留/删除和下游限制。无合同或核验状态不足时，资料可保留为线索，但不得作为正式证据。

## 5. 抽取、审核和正式陈述

```text
冻结 DocumentVersion / 补充快照
  → 页/段/表/单元格/阅读顺序
  → AtomicClaimCandidate
  → 连续 quote + offset/hash + 数值/单位/期间/主体/权威性校验
  → 人工审核
  → reviewed SourceStatement
  → ReportClaim / MechanismEdgeCandidate 提议
```

`AtomicClaimCandidate` 至少保留逐字 quote、起止 offset、quote hash、文档 hash、主体、谓词、对象/数值、单位、期间、范围、断言者、陈述性质、来源权威性、生成器版本和全部校验结果。

规则、LLM 或 OCR 均只能创建候选和观测记录。只有人工确认的候选能发布 `SourceStatement`；拒绝或需修改的候选永远不发布。券商研报及用户补充内容对公告的转述默认是二手 `reported_claim`，不是 `disclosed_fact`。

## 6. 从正式陈述到可研究 Case

审核后的 `SourceStatement` 可以让系统提议 `ReportClaim` 与 `MechanismEdgeCandidate`，但仍须研究员完成以下确认：

1. `OutcomeBindingVersion`：固定结果指标、实体/业务范围、方向、基线和时间窗；
2. `MechanismTemplateVersion`：标明必要结果节点、归因桥梁、替代解释和范围保护；
3. `VerificationRuleVersion`：支持、反驳、允许来源、观察/可得时间和下一验证事件；
4. `ResearchabilityGate`：验证全部输入有冻结或授权来源；业务线主结论至少两个独立主指标。

门槛未通过时，Case 只能是研究计划或待定义线索，不可创建正式市场影响任务、自动监控、股票/基金表达或发布结论。

## 7. 前端信息架构

### 7.1 资料接入后去哪里

资料接入成功后，路由至跨 Case 的“下一步操作”卡，而不是空白 Case 概览。卡片必须显示：资料标题、当前状态、阻塞原因、唯一主操作和完成后解锁内容。

用户仍可查看 Case 概览和原文，但它们是次要入口。

### 7.2 恢复操作必须显式分叉

恢复 UI 必须提供三个不同动作：

1. **继续补充原 Case**：保留恢复目标，并创建独立补充快照；
2. **放弃恢复并新建资料**：清除 `recoveryTarget`、恢复 query 参数和表单关联；
3. **取消**：不改变已冻结资料，不创建新 Case。

不得仅隐藏恢复提示而保留提交目标。恢复路由状态应由一个严格校验的 `RecoveryRouteState` 编码/解码，拒绝未知输入类型和未知恢复原因。

## 8. 对现有分支提交的改造边界

以下内容可保留：

1. 解析失败不进入空工作台；
2. 恢复目标可跨刷新保存；
3. scope 的 `changed_by`、`change_summary`、`created_at` 审计字段；
4. 后端对 Case/Document 归属和并发写入的校验。

以下内容必须替换或后移：

1. `supplement_text()` 不能向原 `DocumentVersion` 追加 `SourceSpan`；
2. `ReportClaimExtractor` 不能直接写 `SourceStatement`；
3. 恢复、文本创建或 PDF 创建不能因抽取结果直接创建正式 scope 或调度市场影响；
4. 前端“创建并开始自动研究”文案改为与当前 `next_action` 对应的可解释动作；
5. 恢复状态清除必须同时清理内存目标与 URL。

## 9. 错误与审计要求

1. 解析/OCR 失败：保留原件、失败代码和可恢复操作；不得丢失原件或伪造文本。
2. quote、offset、hash、期间、单位、实体或权威性校验失败：保留失败诊断和候选观测，不发布正式陈述。
3. 权限不足：记录拒绝原因，不向 AI、导出或下游任务泄露内容。
4. 门槛不完整：返回稳定的 `ResearchabilityGate` reason codes，禁止用模糊“已开始研究”代替。
5. 每一次人工审核、恢复关联、结果变量批准、模板选择与运行触发都必须带操作者、时间、理由和版本。

## 10. 验收标准

1. 对同一 PDF 的补充文本有独立 hash 和独立来源准入记录；删除或变更原 PDF 不会改变补充快照，反之亦然。
2. 补充快照的有效权限等于原件与补充资料权限的交集，不能提升权限。
3. 无论规则、LLM 还是 OCR 产出，未审核候选都不能创建 `SourceStatement`、正式 scope、市场影响任务或 `ResearchRun`。
4. UI 在 `needs_supplement`、待审核、待定义、被阻断和可补证状态都显示唯一主操作、原因和解锁效果。
5. 用户选择“新建资料”后，提交不会调用旧 Case 的补充接口；刷新恢复链接仍能继续原 Case。
6. 缺少结果指标、替代解释、验证规则或第二独立主指标时，`ResearchabilityGate` 必须阻断正式结论。
7. 回归测试覆盖：来源权限继承、补充来源独立性、quote/offset/hash、候选审核发布、恢复路由分叉、门槛阻断和并发幂等。
