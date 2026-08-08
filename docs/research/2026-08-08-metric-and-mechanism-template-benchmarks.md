# Fund Engine 指标与机制模板：可借鉴项目调研

> 调研日期：2026-08-08  
> 问题：怎样把研报中的命题稳定地转为固定结果变量、机制节点、替代解释和反证条件，并让这些结果可版本化、可回放评测？  
> 资料范围：项目官方文档、源码仓库、项目维护方页面与原始论文/共享任务说明。下文的“可借鉴”是产品/工程模式，不代表把任何项目直接作为生产依赖。

## 结论先行

没有一个开源项目能自动且可靠地回答“这条投研命题的**必要**关键条件是什么”。这是一个需要行业机制、研究口径、可用数据和人工责任共同确定的研究设计问题；模型只能从文本中提出候选。正确的借鉴组合是：

```text
Eidos / FinCausal           -> 从原文提出有方向的因果片段与时间、量化候选
MetricRegistry              -> 将候选映射到受控指标、口径、频率和可验证窗口
MechanismTemplate           -> 用行业模板补齐不可跳过的传导节点与替代路径
INDRA                       -> 让“标准化命题 + 原文证据 + 支持关系 + 哈希”成为可追溯对象
DoWhy                       -> 将因果图假设、混杂因素和反驳测试显式化
FinQA / TAT-QA              -> 用“证据单元格/原句 + 可执行算式”核验数值和期间
CLadder + 私有金标集        -> 把机制逻辑、反证和抽取正确性做成回归评测
冻结的 ResearchCaseVersion  -> 保存每次提议、人工选择、运行配置和结论，支持回放
```

其中，**DoWhy / EconML / CausalML 不是“研报因果证明器”**。它们仅在已经冻结了处理变量、结果变量、混杂假设、样本口径与足够数据后，帮助估计或反驳某个统计因果问题；一篇研报、几条新闻或股价窗口都不满足这个条件。

## 要解决的四个不同问题

| 问题 | Fund Engine 应交付的对象 | 不能交给模型决定的部分 |
|---|---|---|
| 从命题固定结果变量 | `OutcomeBinding(metric_id, entity_scope, direction, horizon, baseline)` | 结果变量是否真正衡量这条命题；合并口径能否代替业务线口径。 |
| 找机制必要节点 | `MechanismTemplateVersion` 的节点与边；每条边有需要的证据类型 | 哪些节点是该研究目的下的“必要条件”，哪些只是背景或替代解释。 |
| 写支持/反证条件 | `VerificationRule`：支持阈值、反证阈值、允许来源、窗口与下一验证事件 | 阈值能否从披露/基线获得；没有阈值时是否应判为不可研究。 |
| 可回放评测 | 输入文档哈希、模板/词典版本、模型/提示词、候选、审核决定、金标结果 | 人工裁决与来源许可；不能用模型一致性替代真值。 |

## 借鉴项目对照

| 项目 / 一手资料 | 最值得借鉴的模块 | 对“固定结果变量 / 必要条件 / 反证”的实际帮助 | 不能解决的边界 | 状态与许可核验 |
|---|---|---|---|---|
| [Eidos](https://github.com/clulab/eidos) | 规则化机器阅读：将自由文本提取为实体、增减/量化修饰和有方向的 `cause → effect` 事件；可导出 JSON-LD。项目示例还明确保留触发词、cause、effect 与量化方向。 | 用作 `MechanismEdgeCandidate` 的设计参照：每个候选必须保存触发词、两端原文 span、方向、量化和时间修饰，不能只存一段 LLM 摘要。 | 不懂中国证券研报术语，也不能区分“作者认为会导致”与已发生的可验证机制；更不能判定某边是必要条件。其规则/本体需重写，且技术栈为 Scala。 | 公开项目；仓库标为 Apache-2.0，但 README 同时提示曾有 GPL 依赖问题，采用前必须做依赖级许可证扫描，不能只看仓库标签。 |
| [INDRA](https://indra.readthedocs.io/en/latest/) | `Statement + Evidence` 的知识组装：把不同来源的机制陈述标准化、去重并附带证据；Statement 有 `supports/supported_by`，并支持浅/全 hash 及 JSON 序列化。 | 借鉴为本项目的对象边界：`MechanismEdge` 不等于结论；它应拥有标准化主体/客体/方向、证据、来源、hash 和版本。支持/反驳应是与证据关联的关系，而不是覆盖原命题。 | 生物医学本体与语义，不能直接复用为金融实体字典；它也不证明一条边在经济系统中成立。 | 公开、持续维护的研究框架；采用前以源码树内实际 LICENSE 和依赖清单复核（不要仅按文档推定）。 |
| [DoWhy](https://www.pywhy.org/dowhy/) | `model → identify → estimate → refute` 把因果图假设、识别条件、估计和反驳分开；图反驳会检查图所蕴含的条件独立关系，估计反驳包括 placebo、dummy outcome、随机共同原因、子样本与敏感性分析。 | 借鉴**协议**而非直接依赖：每个机制模板都显式写 `treatment / outcome / mediator / confounder / alternative path`；反证卡应写“若该边错，哪类负对照、替代解释或后续披露会出现”。 | DoWhy 要求先有明确的因果图和可用数据；它不会从研报抽出图，也不会证明图为真。价格相关性、单公司财报或管理层说法不足以自动得到因果估计。 | 开源、PyWhy 维护，MIT；官方文档/仓库可见当前安装与测试路径。 |
| [EconML](https://www.microsoft.com/en-us/research/project/econml/) | 明确将 `T`（处理）与 `Y`（结果）及 `X/W`（特征/控制）拆开，估计异质性处理效应并给出推断/置信区间。 | 借鉴 `OutcomeBinding` 的硬字段：任何进入统计检验的命题必须明确 `T、Y、X/W、实体样本、时间窗、估计对象`；缺一个就不能声称做了因果估计。 | 不能提取研报命题、机制或必要节点。官方说明还明确：观察数据的因果解释需要“无未观测混杂”或有效工具变量等假设，投研场景通常难以自动满足。 | Microsoft Research / PyWhy 开源；[仓库](https://github.com/py-why/EconML) 为 MIT。适合未来的离线、受限研究试验，而不是 P0 主链。 |
| [CausalML](https://github.com/uber/causalml) / [CausalNLP](https://github.com/amaiya/causalnlp) | CausalML 提供 uplift/CATE 的标准接口；CausalNLP 的 `Autocoder` 把原始文本变成主题、情绪等可进入分析的变量，并带敏感性分析与“key driver”提示。 | 借鉴“文本是候选特征而非结论”：将文本提议先变成可审计特征，和固定结果变量分离；把 key-driver 仅标为**待验证线索**。 | predictive key driver、相关性或 CATE 输出都不等于机制必经节点。尤其 CausalNLP 的 driver 分析是提示，不可自动写入 `KeyFactor` 或正式结论。 | CausalML：公开、Apache-2.0，仓库称稳定但有实验 API；CausalNLP：Apache-2.0，PyPI 标为 alpha，最新公开版 0.8.1（2025-02），只适合参考/PoC。 |
| [FinCausal Shared Task](https://www.lllf.uam.es/wordpress/wp-content/uploads/FINCAUSAL-24.pdf) | 金融文本的因果元素抽取评测。2025 任务用年报原始段落，问题抽象、答案必须从上下文逐字抽取，并以 exact match / semantic answer similarity 评估；其错误分析包含“把目的关系误作因果”和无关上下文。 | 直接借鉴候选质量门槛：因果端点必须回到连续原文；评测不能只有语义相似度，必须有 span/边界精确率和“目的 vs 因果”混淆矩阵。 | 它抽取的是段落中声称的 cause/effect，不验证现实中的经济因果，更不提供行业模板或中国研报金标。 | 共享任务/数据集，不是可直接嵌入的生产库；许可、数据重分发和中英适用性需按具体版本复核。 |
| [FinQA](https://finqasite.github.io/) | 金融报告上的“支持事实 + 可执行推理程序”金标；官网说明包含 2.8k 报告、8k 专家标注问答及完整推理程序/支持事实。 | 借鉴 `MetricEvidence` 评测格式：每个数值判断必须列出表格单元格/文本 span、单位、期间、计算程序和预期结果；模型只在同口径输入上运行。 | QA/算术正确不等于选对研究结果变量，更不等于因果机制正确。英文公开年报也不能替代已授权的中文研报金标。 | 数据 CC BY 4.0、代码 MIT（分别复核）；适合借鉴标注与 evaluator，不应混入受限材料。 |
| [TAT-QA](https://github.com/NExTplusplus/tat-qa) | 文本与表格混合证据；其 TagOp 思路先定位相关单元格/文本 span，再用固定算子做符号推理。 | 借鉴“双证据核验”：结果变量的数值、同比/环比、分子分母、单位与期间必须同时保存文本/表格定位和可复算算式；避免 LLM 口算。 | 仍是问答基准，不会选定公司研究的最终指标，也不处理来源权威性、时点或机制反证。 | 数据 CC BY 4.0、代码 MIT；2024 发布测试集真值，适合作回归设计参照。 |
| [CLadder](https://github.com/causalNLP/cladder) | 因果推理 LLM 评测：每样本保留自然语言前提、问题、答案、推理、查询类型、因果阶梯和底层图标识；项目提供可运行的生成/评测代码与测试。 | 借鉴私有 `MechanismTemplate` 金标的字段和对抗测试：换因果方向、删中间节点、加入混杂因素、替换结果变量、目的关系伪装因果，预期系统应拒绝或转人工。 | 合成/通用因果题不能证明金融抽取质量；不要报告 CLadder 分数来替代真实研报审核通过率。 | 公开、MIT；基准和生成器可作为 evaluator 思路，不是领域真值。 |
| [DVC experiments](https://dvc.org/blog/ml-experiment-versioning/) | 将代码、参数、输入数据版本、指标和产物组成可恢复的实验状态，并可比较实验差异。 | 借鉴 `Extraction/Mechanism Run` 留痕清单：输入 document hash、解析器版本、词典/模板版本、模型/提示词、候选集合、验证规则版本、金标集版本与指标。 | DVC 是 ML 实验管理工具，不是研究结论账本；本项目仍需用不可变业务版本、人工审核和来源保留策略保证审计性。 | 公开开源工具；只借鉴运行清单与差异比较，不建议在 P0 引入另一套数据主存储。 |

## 对本项目最有用的“组合逻辑”

### 1. 用机制模板反推结果变量，而不是让抽取器猜

以“海外云厂商 CapEx 将改善中国光模块公司的利润”为例，模板不是一段自然语言，而是一份被版本冻结的有向图：

```text
客户实际 CapEx
  -> 投向相关网络/产品架构
  -> 目标公司订单或份额
  -> 交付/出货
  -> 相关业务收入
  -> ASP、成本、毛利率
  -> 利润结果变量
```

- 目标是“利润改善”时，`ASP/成本/毛利率` 不能被跳过；目标是“出货增长”时，它们是结果路径外的替代解释，而不是必要节点。
- 每个节点须在 `MetricRegistry` 中绑定指标定义、公司/业务线范围、频率、允许来源与最晚验证事件。
- 文本抽取器只做两件事：提出“原文中哪个 phrase 可能对应哪个节点”和提供原文定位。模板缺口不能由模型补写为事实。

这相当于采用 Eidos 的“带方向事件候选”加 INDRA 的“标准 statement/evidence”，但把节点字典、口径和必要性判断保留在 Fund Engine 的行业模板中。

### 2. “必要条件”是研究协议字段，不是模型标签

对每一条 `MechanismEdge`，研究员在模板版本中至少选择一种角色：

| 角色 | 含义 | 例子 |
|---|---|---|
| `required_for_outcome` | 在**已固定的结果变量和研究范围**内缺失即不能称机制成立 | 要称“利润改善”，毛利率/成本机制必须被观察。 |
| `required_for_attribution` | 要称“客户 CapEx 传导”而非“公司自己变好”，必须有的中间桥梁 | CapEx 投向相关产品，且订单/份额有证据。 |
| `alternative_explanation` | 可解释相同结果、但不走主路径 | 收入增长来自非目标业务；库存出清而非客户 CapEx。 |
| `scope_guard` | 防止把总量/行业指标替换为业务线结果 | 合并收入不能代替数据中心产品收入。 |

这借鉴 DoWhy 的“先把假设显式化，再尝试反驳”，但并不把图算法输出误称为必要条件。必要性随**结果变量、范围与可被允许的结论措辞**变化，必须由模板作者批准并版本化。

### 3. 反证不是“风险提示”，而是可执行规则

每一条机制边至少有一条 `VerificationRule`：

```text
edge_id + expected direction + metric definition + baseline
+ support condition + contradiction condition
+ allowed source roles + available-at / observed-period window
+ next verification event + rule_version
```

例：

- 支持：客户 CapEx 达到冻结基线，且 A 公司**相关业务**订单/交付和收入在两个财报期内改善。
- 反驳：CapEx 达到基线但订单未改善；或订单增长而 ASP/毛利率恶化，使“利润改善”不成立。
- 证据不足：只有券商转述，或只有公司总收入，或可验证窗口未到。

FinCausal 的 span 精确评测、FinQA/TAT-QA 的证据与可计算程序，正好防止系统用抽象摘要和错误同比口径“证明”这些规则。

## 推荐的数据与评测契约

以下是**借鉴后应自建**的对象，不是直接复制任何上列仓库：

```text
MetricRegistryVersion
  metric_id / display_name / canonical definition / entity scope
  unit / frequency / allowed source roles / period semantics

MechanismTemplateVersion
  industry / template_key / node & edge schema / required-role / alternative paths
  reviewer / effective_at / supersedes_version

OutcomeBindingVersion
  thesis_id / result_metric_id / direction / baseline / horizon / scope
  template_version / approved_by

MechanismEdgeCandidate
  from/to node / polarity / trigger_quote / span locator / extractor_run
  proposed_by / confidence (non-decisive) / status

VerificationRuleVersion
  support & contradiction predicate / permitted source roles / time window
  next event / reviewer / reason

EvaluationRun
  frozen document ids+hashes / parser / prompt / model / registry+template versions
  gold-set version / metrics / failure cases / reviewer decisions
```

最低回归指标应是：原文连续引用与 offset/hash 通过率（机器校验必须为 100%）、指标/单位/期间/主体/范围精确率、因果与目的关系混淆率、必要节点漏检率、替代解释覆盖率、反证规则可执行率、人工通过/退回率以及同一冻结输入的版本漂移。

## 采用顺序

1. **先做 `MetricRegistry + MechanismTemplate + ResearchabilityGate`**：只覆盖一个行业和 10–20 个真实 Case；没有固定 `OutcomeBinding`、模板版本、至少一条反证规则的材料，只能作为线索。
2. **再接抽取候选**：按 Eidos/FinCausal 的 provenance 结构输出候选；只写入待审表，不能直接改 `SourceStatement`、命题或结论。
3. **再补数值评测**：按 FinQA/TAT-QA 模式建授权、脱敏的中文金标集和 evaluator；公开英文数据仅作为外部冒烟测试。
4. **最后才试验统计因果模块**：只有某类机制拥有冻结的 T/Y/样本、可解释的混杂假设、足够时点数据及研究负责人批准时，才把 DoWhy/EconML 作为一个独立的 `CausalStudyRun`。其输出仍是附带假设和稳健性结果的证据，不会自动升级 Case 结论。

## 不建议的误用

- 用 Eidos/LLM 抽到“因为/导致”就认定经济传导成立。
- 让 CausalNLP 的 key-driver 或相关性筛选自动产生关键因素。
- 把 DoWhy/EconML 的显著性或置信区间当作对研报观点的证明，忽略未观测混杂、样本选择和时点泄漏。
- 用 FinQA/TAT-QA 的公开分数代替授权中文研报的真实审核质量。
- 让新模板、词典或模型运行覆盖已发布命题；必须产生新版本、跑回归、经人工决定后才替换当前视图。

## 参考资料（均为一手来源）

- [Eidos 源码与抽取/JSON-LD 文档](https://github.com/clulab/eidos)。
- [INDRA 文档](https://indra.readthedocs.io/en/latest/)；[Statement、Evidence 与 hash API](https://indra.readthedocs.io/en/latest/modules/statements.html)。
- [DoWhy 用户指南](https://www.pywhy.org/dowhy/main/user_guide/index.html)；[图反驳](https://www.pywhy.org/dowhy/main/user_guide/refuting_causal_estimates/refuting_effect_estimates/graph_refutation.html)。
- [Microsoft Research EconML 项目页](https://www.microsoft.com/en-us/research/project/econml/)；[EconML 源码](https://github.com/py-why/EconML)。
- [CausalML 源码](https://github.com/uber/causalml)；[CausalNLP 官方文档](https://amaiya.github.io/causalnlp/)与[源码](https://github.com/amaiya/causalnlp)。
- [FinCausal 2025 共享任务说明](https://www.lllf.uam.es/wordpress/wp-content/uploads/FINCAUSAL-24.pdf)。
- [FinQA 官网与数据许可](https://finqasite.github.io/)；[TAT-QA 源码、数据与许可](https://github.com/NExTplusplus/tat-qa)。
- [CLadder 源码与数据结构](https://github.com/causalNLP/cladder)。
- [DVC 实验版本化说明](https://dvc.org/blog/ml-experiment-versioning/)。
