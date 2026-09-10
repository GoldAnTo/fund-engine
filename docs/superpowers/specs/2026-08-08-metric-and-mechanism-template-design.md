# 固定结果变量与机制模板设计

> **状态：已确认设计，尚未实现。**
>
> 本规格补充 [Research Operating System 基线蓝图](../../design/2026-08-08-research-operating-system-baseline.md) 的研报主张与关键因素部分。它定义如何让不同研报进入同一套可复核研究协议，而不是让模型每次临时决定结果变量与关键条件。

## 1. 目标与非目标

目标是使一条研报命题在进入正式验证前，拥有固定的结果变量、范围、时间窗、行业机制、替代解释和反证规则。系统应允许不同产业使用不同机制，但同一机制类别内的研究必须可比较、可回放和可被推翻。

本规格不实现统计因果估计、自动交易、目标价或收益预测。也不允许模型或因果库自动宣布“必要条件”或“传导成立”。

## 2. 已选方案：版本化的行业协议

选择“**受控指标词典 + 版本化行业机制模板 + 可研究性门槛**”。

- 不选择“每份研报由 LLM 自由生成结果变量与因素”：输出不可稳定复用，无法做回放或质量门禁。
- 不选择“一张全行业通用大图”：会把行业背景、替代解释和真正必经环节混为一谈。
- 每个 `ResearchCase` 选择一个明确版本的 `MechanismTemplateVersion`；后续模板升级不改写已冻结 Case，而是建立新的版本与重新复核。

## 3. 核心对象

### 3.1 `MetricRegistryVersion`

指标词典是受控字典，不是从研报文本即时生成的标签。每项至少有：

```text
metric_id / display_name / canonical_definition / entity_scope
unit / frequency / period_semantics / allowed_source_roles
role_eligibility(outcome, driver, mediator, context) / effective_at / version
```

例如“合并营业收入”和“数据中心业务收入”是不同指标；前者不可自动代替后者。`CapEx` 可以是“客户 CapEx 是否兑现”命题的结果变量，也可以是“供应商是否受益”命题的驱动变量；角色由本次 `OutcomeBindingVersion` 固定，而不是由指标名称决定。

### 3.2 `OutcomeBindingVersion`

任何正式 `ReportClaim` 都要绑定一个结果变量。绑定必须包含：

```text
report_claim_id / result_metric_id / target_entity_scope / direction
baseline / horizon / template_version / approved_by / approved_at / reason
```

`baseline` 只能来自已冻结的公司指引、原始披露、同口径历史值、可授权一致预期或研报明确写出的预测基线；不能由模型编造。缺少指标、范围、方向、基线或时间窗的材料是“待定义线索”，不得产生正式验证结论。

### 3.3 `MechanismTemplateVersion`

模板由行业负责人维护，至少定义节点、边和各节点角色：

| 角色 | 含义 |
|---|---|
| `required_for_outcome` | 在本次固定的结果变量与范围内缺失时，不能称结果机制成立。 |
| `required_for_attribution` | 若要声明特定上游驱动传导，而非“公司结果变好”，必须存在的中间桥梁。 |
| `alternative_explanation` | 能解释相同结果但不走主路径的竞争解释。 |
| `scope_guard` | 防止用行业/总量/不同业务线指标替代命题指定结果变量。 |

“必要”只针对已经固定的结果变量、研究范围和允许的结论措辞；它不是对现实世界的绝对断言。

### 3.4 `VerificationRuleVersion`

每条关键边都有至少一条可执行规则：

```text
mechanism_edge_id / expected_direction / metric_definition / baseline
support_predicate / contradiction_predicate / allowed_source_roles
observed_period_window / available_at_window / next_verification_event
reviewer / reason / version
```

只有“风险提示”而没有可观测反面结果的规则不合格。没有可靠阈值时，规则状态为“尚不可判定”，不是自动支持或反驳。

## 4. 可研究性门槛 `ResearchabilityGate`

系统在任何 Case 建立正式结论前强制检查：

1. 存在已审核的 `OutcomeBindingVersion`；
2. 已选择有效的 `MechanismTemplateVersion`；
3. 有至少一个 `required_for_outcome` 或 `required_for_attribution` 节点及可验证来源；
4. 有至少一个 `alternative_explanation` 或明确的 `contradiction_predicate`；
5. 每个主指标都有实体/业务线范围、单位、期间与下一验证事件；
6. 业务线层面的主结论至少需要两个独立主指标。仅一个主指标时，状态固定为 `single_metric_monitoring`，正式判断只能是 `insufficient_evidence` 或 `not_due`；
7. 所有输入都有冻结原文或授权数据来源及可得时间。

未通过门槛的条目可保留为线索或研究计划，但不能进入 `EvidenceLink`、正式图谱、股票/基金表达或发布结论。

## 5. 从研报到机制验证的受控流程

```text
已审核 SourceStatement
  → AI 提议 ReportClaim / MechanismEdgeCandidate（均有原文定位）
  → 模板匹配与 MetricRegistry 映射
  → 研究员确认 OutcomeBinding / 范围 / 版本
  → ResearchabilityGate
  → KeyFactor 与 VerificationRuleVersion
  → 独立后续资料验证
  → supported / contradicted / insufficient_evidence / not_due
```

AI 可以提议“原文中的哪一段可能对应模板节点”或“哪项指标可能是结果变量”，但不能创建正式 `OutcomeBindingVersion`、决定节点角色、选择阈值或越过门槛。模板的缺口必须显示为研究缺口，不能由模型补写为事实。

## 6. 首个模板：海外 AI CapEx 到中国硬件公司

首批只实现一个狭窄模板：`overseas_ai_capex_to_china_hardware/v1`。

```text
客户实际 CapEx
  → 投向相关网络/产品架构
  → 目标公司订单或份额
  → 交付/出货
  → 相关业务收入
  → ASP、成本、毛利率
  → 利润结果变量
```

- 目标为“出货增长”时，订单与交付是主路径；ASP/毛利率属于利润解释与替代路径。
- 目标为“利润改善”时，ASP、成本和毛利率是 `required_for_outcome`，不得跳过。
- 若客户 CapEx 兑现但订单未落到目标公司，属于主路径反证；若收入增长来自非目标业务，属于 `scope_guard` 失败；若订单增长但 ASP/毛利率恶化，反驳“利润改善”。
- 只有行业增长或公司合并收入增长时，不得使用“客户 CapEx 传导成立”的措辞。

## 7. 版本、评测与方法借鉴

每次运行记录 document hash、解析器/模型/提示词、指标词典版本、模板版本、规则版本、候选、人工决定和金标结果。新版本不会覆盖旧 Case。

借鉴 [DoWhy](https://www.pywhy.org/dowhy/) 的“先显式建模、再尝试反驳”协议；借鉴 [INDRA](https://indra.readthedocs.io/en/latest/) 与 [Eidos](https://github.com/clulab/eidos) 的“标准化机制陈述 + 原文证据”对象边界；借鉴 [FinCausal](https://www.lllf.uam.es/wordpress/wp-content/uploads/FINCAUSAL-24.pdf) 的因果端点定位与因果/目的混淆评测；借鉴 [FinQA](https://finqasite.github.io/)、[TAT-QA](https://github.com/NExTplusplus/tat-qa) 的数值、单位、期间和可复算证据；借鉴 [CLadder](https://github.com/causalNLP/cladder) 的删中间节点、交换方向、替换结果变量和加入替代解释的对抗回归。

DoWhy、EconML、CausalML 或 CausalNLP 的结果只可在已有冻结处理变量、结果变量、样本、混杂假设和足够结构化数据时，作为独立 `CausalStudyRun` 的补充证据；不得替代上述研究协议或自动升级正式结论。

## 8. 验收标准

1. 同一类机制的两个 Case 能比较结果变量、范围、主节点和反证，而不依赖模型措辞；
2. 任何正式结论可回放至采用的指标/模板/规则版本和原始证据；
3. 删除必要节点、替换为范围不匹配指标、缺少反证规则或没有基线时，门槛拒绝其进入正式链路；
4. 模板更新只产生新版本与再审核，不改写既有结论；
5. 系统明确区分公司结果支持、公司直接归因与已审计传导成立。

## 9. 实施分解

后续实现应分为三个独立计划：

1. `MetricRegistry + OutcomeBinding + ResearchabilityGate` 数据模型、服务和审计测试；
2. `MechanismTemplate + VerificationRule`、首个 AI CapEx 模板和 Case 审核 API；
3. 模板选择/结果变量确认/关键因素反证工作台，以及私有中文金标回放。

任何实现前先单独审阅每个计划；当前用户已要求暂不开始代码。
