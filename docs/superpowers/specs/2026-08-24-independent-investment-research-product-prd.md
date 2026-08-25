# 独立投资研究产品 PRD

日期：2026-08-24

状态：核心领域口径已修订，待用户书面审阅

首个完整案例：宁德时代（CATL，300750.SZ）

复用验证案例：Alphabet（GOOGL / GOOG）

## 1. 产品摘要

本产品是一套面向专业个人投资者的本地化公司投资研究工作台。用户从公司、证券或行业对象开始，系统自动建立研究议程，采集并冻结公开资料，帮助用户把行业机制、公司经营、财务预测、估值和反证连接起来，最终形成一份有来源、有时间边界、可更新、可回放的投资判断。

产品不是新闻终端、自动研报生成器、聊天机器人、股票评分器或交易系统。它要解决的核心问题是：

> 在指定研究期限、资料截止日、价格时点和要求回报下，现有事实、假设与未知允许投资者形成怎样的暂定判断；哪些条件支持它，哪些证据会推翻它，未来新增信息为什么改变或没有改变判断。

第一版不要求证据完备到机构投委会标准。非关键缺口被明确记录为 `ResearchGap`、关键数字可追溯、反证和验证条件可见时，系统可以输出低或中等置信度的暂定方向、价值区间和预期回报区间。若关键经营基线、财务闭合或估值可识别性失败，系统只能输出 `insufficient_evidence`，不得用低置信度方向替代失败关闭。它不得把缺失数据伪装成事实，也不得自动生成仓位或交易指令。

## 2. 背景与问题

个人投资者研究一家公司的过程通常分散在公告、年报、行业数据、券商预期、表格、估值模型和个人笔记中，存在五个系统性问题：

1. 公司、证券和行业身份经常混用，研究结论与具体股权类别、币种和价格时点脱节。
2. 公开事实、公司指引、市场一致预期、价格隐含假设和个人预测被放在同一张表里，来源和性质不清。
3. 行业叙事没有经过因果和财务传导，无法说明它如何改变公司的收入、利润、自由现金流和价值。
4. 新公告或新价格到来后，旧模型被直接覆盖，无法还原“当时为什么这样判断”。
5. AI 擅长检索、提取和草拟，但如果缺少确认与冻结边界，容易把推断、缺失或当前数据写成历史事实。

现有代码已经建立了不可变来源、历史基准、证据档案、候选行业证据、版本回放和差异读取等可信底座，但尚未形成完整的公司研究、预测、估值和投资判断主流程。本 PRD 统一目标产品口径，并明确现有能力与目标交付之间的差距。

## 3. 产品目标与非目标

### 3.1 产品目标

1. 用户能从公司、证券或行业搜索开始，无需先写一个“研究问题”。
2. 系统根据研究对象与研究授权自动生成研究议程，用户只需补充可选关注点。
3. 每条进入正式模型的数据都保留来源或人工假设记录、定位、期间、单位、可得时间、数值性质、来源角色、情景、知识状态和确认状态。
4. 行业变量能够通过明确机制映射到公司分部、财务预测和估值。
5. 估值同时展示我方经营情景形成的 ValueRange、当前价格隐含假设和关键敏感性，而非单点目标价。
6. 在证据不完全时，仍可生成诚实的暂定判断，并显式展示置信度、缺口、反证和验证条件。
7. 每次正式研究发布都绑定资料截止日和独立价格快照；后续行情或资料不得改写旧版本。
8. 用户可以创建后继版本、查看变化原因，并导出与冻结版本一致的 Web、Markdown 和 PDF 研究备忘录。
9. 第一版在本机通过 Docker 一键运行，无需登录，数据可备份和恢复。
10. 产品内核支持 A 股、H 股和美股；交付先完成 CATL 纵向闭环，同时在领域契约中预留跨市场边界，再用 Alphabet 验证多证券、币种和会计口径复用。

### 3.2 非目标

- 移动端适配；
- 多用户、角色权限、投委会和独立复核流程；
- 实盘持仓、组合优化、自动仓位、经纪商接入或下单；
- 全市场选股器、排行榜、热门主题、新闻信息流或 AI 综合评分；
- 无人值守地发布最终投资结论；
- AI 自动关闭 `ResearchGap` 或把人工假设改写成来源已报告事实；
- 单点目标价、确定性买卖评级或精确但未经校准的情景概率；
- 公有云多租户部署；
- 可多人协作编辑的 Word 工作流。

## 4. 用户与核心使用场景

### 4.1 主要用户

第一版仅服务一类核心用户：在自己电脑上持续研究公司的专业个人投资者。

用户愿意阅读公告、核对证据和修改模型，但不应被机构级权限、任务编排或多人审批流程阻挡。系统中的“确认”代表本机用户的自我复核，不代表经身份认证的独立审阅。

### 4.2 核心场景

#### 场景 A：首次研究 CATL

用户搜索宁德时代并确认公司与 300750.SZ 证券，建立 InvestmentMandate、ResearchScope 和 RevisionBoundary。系统生成研究议程；用户启动资料采集并确认关键 SourceStatement、ModelInput 和 ResearchGap，建立动力电池行业状态、公司分部和现金流模型，形成 Base / Bull / Bear 预测，通过 DCF、反向 DCF 和合理倍数交叉检查得到价值区间，最后显式发布暂定判断与研究备忘录。

#### 场景 B：CATL 新公告后的更新

用户点击检查更新、由已启用的本机计划任务发现新公告，或用户手工导入资料后，系统形成 `UpdateCandidate`。用户查看新增事实、机制、预测、估值和不确定性变化，接受的内容先进入 WorkspaceDraft；用户显式发布后才创建后继版本。旧版本的资料、价格和结论保持不变。

#### 场景 C：Alphabet 复用验证

用户用同一产品流程研究 Alphabet。系统复用对象、证据、版本、预测、估值、判断和备忘录内核，只新增适合广告、Cloud、AI 资本开支和不同股权类别的行业机制与数据适配器。

## 5. 产品原则

1. **对象先于问题**：用户先确认公司、行业和证券。研究议程由系统生成，“用户关注点”只是可选补充，不要求用户先提出专业问题。
2. **研究数据按正交维度表达**：数值性质、来源角色、情景、知识状态和审阅状态分别保存；`Actual`、`Consensus`、`House`、`Base`、`Assumption` 和 `Unknown` 不再作为同一个枚举的互斥值。
3. **资料截止与市场时点分离**：`HistoricalBasis` 冻结研究资料，`PriceSnapshot`、`FXSnapshot` 和 `CapitalStructureSnapshot` 冻结市场与每股价值桥接所需状态。市场时点可以早于、等于或晚于资料截止，但不得把市场时点之后的新资料引入旧基准。
4. **先解释经营，再讨论估值**：行业叙事必须经过公司分部、财务和现金流传导后才能影响价值判断。
5. **范围优于伪精确**：展示价值区间、隐含假设组合和敏感性，不选择一个精确数字冒充确定答案。
6. **反证与判断并列**：最强反证、关键未知和下一验证条件不是附录，而是投资判断的一部分。
7. **暂定判断不等于正式事实**：非关键证据不足时允许形成低或中等置信度的暂定判断，但必须保留缺口和失效条件；关键门控失败时只能输出 insufficient_evidence。
8. **AI 只提议，不静默发布**：AI 可以检索、提取、归纳、建议机制和草拟文字；用户确认后才能进入正式版本。
9. **草稿可编辑，发布不可变**：工作区草稿允许自动保存和反复修改；每次正式发布原子冻结一张完整依赖图。正式来源、陈述、机制、预测、估值、判断和备忘录只追加，不覆盖历史。
10. **投资研究不等于交易执行**：产品可以表达暂定看多、中性、谨慎或证据不足，不输出仓位和订单。

## 6. 统一领域语言

| 术语 | 定义 | 不应称为 |
|---|---|---|
| `ResearchObject` | 被持续研究的 Industry、Company 或 Security | 搜索关键词、股票代码 |
| `ResearchProject` | 围绕一个主要公司及其行业、证券建立的长期研究档案 | 一次运行、一次报告 |
| `InvestmentMandate` | 相对稳定的投资期限、基准币种、最低要求回报、永久损失容忍和比较基准 | 资料截止、价格时点、交易指令 |
| `ResearchScope` | 当前项目的主要公司、目标证券、关联行业、覆盖分部和可选 UserFocus | 投资授权、一次运行 |
| `RevisionBoundary` | 一次研究修订使用的资料截止、价格、汇率、资本结构和模型定义引用 | InvestmentMandate、当前数据库状态 |
| `ResearchAgenda` | 系统根据对象、授权和当前缺口生成的待研究主题与验证项 | 用户必须填写的问题 |
| `UserFocus` | 用户可选填写的特殊关注点或担忧 | 正式事实、必要输入 |
| `HistoricalBasis` | 一个版本在资料截止前可使用的来源清单、内容哈希、指标定义和解析版本 | 价格快照、当前数据库状态 |
| `PriceSnapshot` | 某只证券在独立时点的冻结价格、币种、来源和哈希 | 实时价格、历史基准 |
| `FXSnapshot` | 估值或回报换算使用的冻结汇率、时点、来源和哈希 | 实时汇率、隐式换算 |
| `CapitalStructureSnapshot` | 某时点的现金、债务、少数股东权益、稀释股数及其他 EV 到每股价值桥接项 | Security 静态属性 |
| `SourceReference` | 尚未成功获取并冻结的来源指针 | 正式来源、搜索结果即证据 |
| `RetrievalArtifact` | 一次获取返回的不可变原始字节和获取信封 | 解析后文档、临时缓存 |
| `DocumentVersion` | 由内容哈希、发布时间和获取元数据标识的不可变文档版本 | 可被覆盖的 SourceArtifact |
| `SourceSpan` | DocumentVersion 内可重现的页、段、表格单元格或字符定位 | 只有 URL 的引用 |
| `SourceStatement` | 来源明确表达的原子陈述，记录“来源说了什么” | 客观真理、Assumption、Unknown |
| `ModelInput` | 进入模型的规范化输入；引用 SourceStatement、派生公式或人工假设记录 | 无谱系数字、混合类型单元格 |
| `ResearchGap` | 对判断有影响的缺失、冲突、陈旧、单一来源、未验证或不可识别项 | EvidenceClaim、零值、普通待办 |
| `ResearchDebt` | 一个版本中尚未解决的 ResearchGap 集合及其对判断的约束 | 来源数量、普通待办列表 |
| `Mechanism` | 有方向、时滞、范围、量级、替代解释和证伪条件的传导关系 | 相关性、行业叙事 |
| `ForecastSetVersion` | 一组按数值性质、来源角色、情景、知识状态和审阅状态正交分类且财务闭合的预测 | 一列混合估计 |
| `ValueRange` | 由若干内部一致情景形成的无概率权重价值包络 | 概率分布、目标价 |
| `ValueDistribution` | 仅在参数分布经过校准并被冻结时使用的概率价值分布 | 未加权 Bull / Base / Bear 区间 |
| `ValuationRunVersion` | 绑定预测、市场快照、方法和假设的 ValueRange 或合格 ValueDistribution 计算 | 目标价 |
| `ResearchAssessmentVersion` | 对投资方向、价值区间、回报区间、置信度、反证和验证条件的不可变判断 | 自动交易建议 |
| `UpdateCandidate` | 尚待用户确认的新资料、解释或模型变更候选 | 已发布更新 |
| `ChangeSet` | 两个正式版本之间按事实、机制、预测、价值、价格、不确定性和判断分类的变化 | 文本 diff |
| `WorkspaceDraft` | 可自动保存、可反复编辑、尚未发布的工作区状态 | 正式研究版本 |
| `ResearchRevision` | 一次发布事务冻结的 HistoricalBasis、模型、市场快照、判断和备忘录引用集合 | 子对象的“当前最新版”拼装 |

## 7. 端到端用户流程

```text
搜索并确认 ResearchObject
→ 建立 InvestmentMandate 与 ResearchScope
→ 建立本次 RevisionBoundary
→ 系统生成 ResearchAgenda
→ 自动采集并冻结来源
→ 用户确认 SourceStatement 与 ModelInput
→ 建立行业状态与机制
→ 映射公司分部和现金流
→ 建立 House 情景预测
→ 运行估值与反向估值
→ 通过可回答性与发布门控
→ 形成暂定投资判断或 insufficient_evidence
→ 原子冻结 ResearchRevision 并导出备忘录
→ 新资料形成 UpdateCandidate
→ 用户接受候选进入 WorkspaceDraft
→ 显式发布后创建后继版本与 ChangeSet
```

### 7.1 新建研究的最少输入

首次建立项目必填：

- 已确认的主要公司和目标证券；
- 研究期限，默认 3–5 年；
- 基准币种；
- 最低要求回报；若使用比较基准，必须同时声明相对基准的最低超额回报；
- 可接受的永久损失边界；
- 比较基准或替代机会集合。

每次正式修订必填：

- 精确到时区的资料截止时间，默认用户启动研究时的当前时间；
- 目标证券的独立价格快照；
- 估值涉及跨币种时使用的 FXSnapshot；
- EV 到每股价值桥接使用的 CapitalStructureSnapshot；
- 使用的指标定义和模型版本。

可选：

- `UserFocus`，例如“海外扩张是否改善长期利润率”。

不要求用户填写：

- “研究问题”；
- 报告目录、模型类型、指标清单、新闻数量；
- 目标价、仓位或交易计划；
- 数据源技术配置。

### 7.2 系统生成的 ResearchAgenda

议程至少覆盖：

- 公司如何赚钱以及哪些分部创造利润和现金；
- 行业需求、供给、产能、利用率、价格、成本和竞争位置；
- 2–4 个最重要的经营变量及其反方解释；
- 历史实际、公司指引、市场一致预期、价格隐含和我方假设的差异；
- 对收入、利润、自由现金流和价值的传导；
- 当前最大未知、最强反证和下一验证事件。

议程随证据和版本更新。自动生成的议程必须保留规则模板或 AI 模型、提示模板、输入摘要和输出哈希；每个正式版本保留当时的冻结议程。议程是研究计划，不是事实，也不因生成成功而表示研究完成。

## 8. 前端产品需求

### 8.1 产品壳

- 独立于现有 Event Research / Fund Engine 页面壳；
- 桌面优先，不承担移动端验收；
- 中文界面，来源原标题、专业缩写和必要英文术语可保留；
- 左侧模块导航，中间为结构化研究工作区，右侧为可收起的 AI 助手；
- 不显示旧产品的运行控制台、事件流、热门主题、研究评分或模拟持仓。

### 8.2 一级页面

#### 研究首页

- 搜索公司、证券或行业；
- 展示最近研究、待确认更新、存在关键缺口的项目；
- 支持继续旧研究或创建新研究；
- 不默认显示行情榜、新闻流、推荐或 AI 摘要。

#### 新建研究

- 确认 Company 与 Security 身份关系；
- 设置 InvestmentMandate、ResearchScope 和本次 RevisionBoundary；
- 预览系统将生成的研究议程；
- 点击后立即进入工作台，不承诺“自动完成研究”。

#### 公司研究工作台

固定模块：

1. 概览；
2. 来源与证据；
3. 行业；
4. 公司模型；
5. 预测与情景；
6. 估值；
7. 判断与反证；
8. 版本与变化；
9. 研究备忘录。

### 8.3 关键页面行为

#### 概览

首屏按以下顺序展示：

```text
可回答性 | 暂定方向（如有） | 置信度（如有） | 最大反证 | 当前价格要求
→ 关键经营变量及证据状态
→ 公司如何赚钱与行业传导
→ 预测、价值区间与预期回报
→ 最大未知与下一验证事件
```

不得用来源数量、模型完成度或 AI 评分代替投资判断。

#### 来源与证据

- 原文、来源身份、页码或段落定位、发布时间、首次可得时间、抓取时间和哈希可展开；
- 机器提取项先作为候选，用户可接受、编辑或拒绝；
- 原始机器提取值永不被人工编辑覆盖；人工修改必须另存规范化值、修改类型、修改原因和操作者；改变来源原意的内容必须改记为人工假设，不能继续作为 SourceStatement；
- 冲突证据并列，不用“综合可信度分数”隐藏分歧；
- 数值性质、来源角色、情景、知识状态和审阅状态使用文字与视觉双重区分；
- ResearchGap 独立展示其影响的判断、严重度、解决办法和下一复核时间，不以零值或 Evidence 代替；
- 无权限、无法获取和无法解析是明确错误状态，不自动换成无来源内容。

#### 行业

- 展示需求、名义与有效产能、利用率、库存、价格、成本曲线、利润池、份额和准入约束；
- 每个正式机制显示驱动、目标、方向、时滞、范围、量级、证据、替代解释、证伪条件和状态；
- 不满足正式条件时可保留候选并创建 ResearchGap，不强制编译行业状态。

#### 公司模型

- 按分部展示量、价、收入、单位成本、毛利、费用、税、资本开支、折旧、营运资金和自由现金流；
- 显示行业变量如何映射到公司财务；
- 公司报表与分部桥接必须闭合，残差明确标为派生值；
- 显示资产负债表和资本配置约束。

#### 预测与情景

- 页面至少按以下正交维度筛选或对照：
  - `value_nature`：actual / forecast / derived / assumption；
  - `provenance`：company_guidance / consensus / house / market_implied / third_party；
  - `scenario_key`：base / bull / bear / null；
  - `epistemic_status`：observed / estimated / uncertain / unknown；
  - `review_status`：candidate / self_reviewed / adopted / challenged / superseded；
- 前端可为易读性组合显示“Actual”“Consensus”“House Base”等标签，但组合标签不能替代底层正交字段；
- Base / Bull / Bear 必须由不同机制和参数驱动，不允许机械同比例上浮下调；
- 每个预测显示来源、理由、区间、传导机制、敏感性和证伪条件；
- 财务报表和现金流在每个情景下内部一致。

#### 估值

- 主方法为情景 DCF / FCFF；
- 反向 DCF 展示当前价格可由哪些经营假设组合支持；
- PE、PB、EV 等只在经济上合理时作为交叉检查，并说明适用性；
- FCFF 使用与资本结构和币种一致的 WACC；InvestmentMandate 的最低要求回报是股权判断门槛，不直接替代 WACC；
- DCF 先得到估值时点的企业价值，再通过冻结的 CapitalStructureSnapshot 和 SecurityRightsVersion 桥接到目标证券稀释后每股价值；
- “价值区间”默认表示由一组内部一致情景产生的无概率权重包络。只有存在经过校准且被版本冻结的参数分布时，才称为概率分布；
- 展示当前价值区间、3–5 年年化股东回报区间、关键敏感性和不可识别区域；年化回报必须明确包含的股息、回购、稀释、退出价值和 FX 假设；
- 不默认输出单点目标价。

#### 判断与反证

判断方向：

- `provisional_bullish`；
- `provisional_neutral`；
- `provisional_cautious`；

判断同时保存四个正交维度：

- `answerability`：answerable / partially_answerable / not_answerable；
- `direction`：provisional_bullish / provisional_neutral / provisional_cautious / null；
- `confidence`：low / medium / high / null；
- `publication_status`：draft / user_frozen / superseded；其中 draft 只存在于 WorkspaceDraft，正式 ResearchAssessmentVersion 只能是 user_frozen 或 superseded。

第一版不使用 `confirmed_view`。`user_frozen` 只表示本机用户主动冻结了该版本，不表示独立复核或客观正确。

方向门槛：

- `provisional_bullish`：可回答，且主要回报区间相对 InvestmentMandate 的要求回报具有正向余量；
- `provisional_neutral`：可回答，但主要回报区间围绕要求回报或不同合理情景跨越门槛；
- `provisional_cautious`：可回答，且主要回报区间低于要求回报，或永久损失路径超过 Mandate 边界；
- `not_answerable`：`direction=null`、`confidence=null`，页面显示 `insufficient_evidence`，不得用低置信度方向代替；
- `partially_answerable` 仅在缺口不会破坏关键经营基线、财务闭合和价值可识别性时允许方向，且置信度最高为 medium。

每份判断必须显示：

- 价值区间与预期回报区间；
- 置信度：low / medium / high；
- 判断依据和关键假设；
- 最强反证与替代解释；
- ResearchGap 和 ResearchDebt；
- 下一验证条件与判断有效期。

#### 版本与变化

- 默认展示当前选定版本，不用最新资料替换历史内容；
- 工作区草稿可自动保存；草稿、后台重计算成功和正式发布是三个不同状态；
- 支持查看版本时间线、父版本、资料截止和价格快照；
- `ChangeSet` 分类展示事实、机制、预测、价值、价格、不确定性和判断变化；
- 新资料先显示为待确认候选。接受候选只更新 WorkspaceDraft，不自动发布；用户显式发布时才原子创建后继 ResearchRevision；
- 仅新增价格或汇率时创建 MarketMark；只有用户选择冻结新的估值或判断时，才创建新的 ValuationRunVersion、ResearchAssessmentVersion 或 ResearchRevision，避免行情刷新制造无意义版本。

#### 研究备忘录

- 由已发布冻结版本生成 Web、Markdown 和 PDF；
- 内容至少包括对象与授权、判断摘要、公司与行业机制、预测、估值、反证、未知、来源和版本信息；
- 导出内容必须与页面选择的版本一致；
- 不在导出时调用最新数据或重新生成结论。

### 8.4 AI 助手边界

AI 可以：

- 建议检索路径并发起受控采集；
- 从冻结来源中提取候选陈述和表格；
- 建议指标定义、机制、替代解释和证伪条件；
- 解释模型不平衡、冲突和敏感性；
- 草拟研究备忘录文字。

AI 不可以：

- 在用户未确认时把候选写成正式证据；
- 静默修改历史版本；
- 补造缺失数字或来源；
- 把数值性质、来源角色、情景、知识状态和审阅状态压成一个不可追溯标签；
- 把行业相关性直接写成公司因果；
- 生成仓位、下单或确定性买卖指令。

## 9. 后端领域与服务需求

### 9.0 正交数据分类契约

任何进入模型或判断的数字必须分别保存以下维度，不允许用单一 `type` 字段混合表达：

| 维度 | 允许值 | 回答的问题 |
|---|---|---|
| `value_nature` | actual / forecast / derived / assumption | 这个值是实际、预测、派生还是人工假设？ |
| `provenance` | company_guidance / consensus / house / market_implied / third_party / source_reported | 谁提供或通过什么方式得到？ |
| `scenario_key` | base / bull / bear / null | 属于哪个情景？ |
| `epistemic_status` | observed / estimated / uncertain / unknown | 当前知道到什么程度？ |
| `review_status` | candidate / self_reviewed / adopted / challenged / superseded | 处于什么审阅生命周期？ |

约束：

- `unknown` 通过 ResearchGap 表达，不作为有值的 SourceStatement 或 ModelInput；
- 人工假设可以没有外部来源，但必须有作者、理由、创建时间、适用范围和证伪条件；
- 派生值必须保存公式、输入版本和计算器版本；
- 前端组合标签必须能无损还原上述字段。

### 9.1 一级对象与版本

#### ResearchObject

- `Industry`、`Company` 和 `Security` 为独立身份；
- Company 可关联多个 Security；
- Security 保存交易所、代码、股权类别、交易币种、经济权利和公司行动身份；
- 资本结构是时间变化的公司级状态，由 CapitalStructureSnapshot 保存；
- 代码、名称、上市地、股权类别和公司关系必须支持有效期，防止更名、换代码、重新上市或代码复用造成身份串联；
- 任何估值和价格含义必须绑定具体 Security。

#### ResearchProject

- 长期存在，不等于一次运行；
- 绑定主要 Company，可关联 Industry 和一个或多个 Security；
- 包含多个按序号和父子关系连接的正式修订版本。

从 Industry 搜索进入时，第一版先进入行业浏览和公司选择流程；只有用户确认主要 Company 与目标 Security 后才创建 ResearchProject。第一版不创建独立 IndustryProject。

#### InvestmentMandate

至少保存并版本化：

- 投资期限；
- 基准币种；
- 最低年化要求回报；
- 使用比较基准时的基准身份和最低超额回报；
- 可接受的永久损失边界；
- 替代机会或比较集合；
- 生效时间、失效时间、父版本和内容哈希。

InvestmentMandate 不保存资料截止或价格时点。Mandate 改变时，新 ResearchRevision 必须明确引用新版本；旧判断继续引用旧 Mandate。

#### ResearchScope

至少保存主要 Company、目标 Security、关联 Industry、覆盖分部、UserFocus 和明确排除项。第一版一个 ResearchProject 只有一个主要 Company，但允许为多个经济权利相同或不同的 Security 运行独立估值。

#### RevisionBoundary

一次发布至少引用：

- HistoricalBasis；
- 一个或多个 PriceSnapshot；
- 必需的 FXSnapshot；
- CapitalStructureSnapshot；
- SecurityRightsVersion；
- 指标定义、会计口径和模型版本；
- InvestmentMandateVersion；
- 父 ResearchRevision。

#### HistoricalBasis

至少冻结：

- 资料截止时间；
- 来源清单及其哈希；
- 指标定义和模型版本；
- 创建时间和版本内容哈希。

HistoricalBasis 的准入条件为 `available_at <= cutoff_at`，其中 cutoff_at 必须为带时区时间戳。发布时间早于截止但实际首次可得时间晚于截止的资料不得进入。HistoricalBasis 不限制 PriceSnapshot 的先后顺序；市场时点晚于资料截止不等于允许使用晚到资料。

#### 来源与陈述链

后端保留以下分层，不以单个 SourceArtifact 抹平：

- SourceReference：发现但未成功冻结的来源指针；
- RetrievalArtifact：一次获取返回的原始字节、HTTP/提供方信封、获取时间和内容哈希；
- DocumentVersion：不可变文档版本、发布时间、首次可得时间、解析版本和权限策略；
- SourceSpan：页、段、表格单元格或字符级定位；
- SourceStatement：来源明确表达的原子陈述及其来源角色；
- ModelInput：经过单位、期间、地域和口径规范化后进入模型的值；
- ResearchGap：缺失、冲突、陈旧、单一来源、未验证、权限不足或不可识别项。

每层至少保存父引用、内容哈希和状态。来源政策必须分别声明 search / retrieve / process / retain / display / export 权限；若 retain 不允许，正式发布必须失败关闭或只使用允许持久化的替代来源，不能声称可离线完整回放。

#### SourceStatement、ModelInput 与 ResearchGap

SourceStatement 至少保存：

- 来源与定位；
- `effective_at` 和 `available_at`；
- 原始陈述文本或原始数值；
- source_reported / company_guidance / third_party 等来源角色；
- candidate / self_reviewed / adopted / challenged / superseded 状态；
- 冲突、父项和替代解释。

ModelInput 至少保存指标身份、规范化值、单位、期间、地域、会计口径、正交分类字段，以及以下谱系之一：

- SourceStatement 引用和规范化差异；
- 派生公式、输入版本和计算器版本；
- 人工假设作者、理由、适用范围和证伪条件。

ResearchGap 至少保存类型、严重度、影响的模型或判断、是否阻塞发布、解决动作、责任方、下一复核时间和关闭原因。

### 9.2 行业与机制

`IndustryModelVersion` 至少覆盖：

- 需求和终端结构；
- 名义产能、有效产能、利用率和库存；
- 价格、成本曲线和边际供给；
- 利润池、份额、客户结构和进入壁垒；
- 认证、技术、监管和资本周期。

每个 `MechanismVersion` 至少保存：

- driver 与 target；
- 方向、时滞、生效范围和量级区间；
- 来源证据和适用指标定义；
- 替代解释、证伪条件和失效条件；
- candidate / self_reviewed / formal / challenged / superseded 状态；
- 对公司财务科目的映射。

### 9.3 公司经营模型

`CompanyModelVersion` 按分部保存：

- 销量、ASP、收入、单位成本和毛利；
- 费用、经营利润、税；
- 资本开支、折旧、营运资金和自由现金流；
- 资产负债表约束和资本配置；
- 行业变量到公司变量和财务科目的映射；
- 来源、公式、口径和对账差异。

### 9.4 预测与情景

`ForecastSetVersion` 必须：

- 使用 9.0 的正交分类契约，不把 Actual、Guidance、Consensus、Implied、House、Estimate 和 Scenario 保存为同一个枚举；
- 包含定义一致的 Base、Bull 和 Bear；
- 对每项预测保存期间、来源、理由、区间、机制、置信度、敏感性和证伪条件；
- 执行收入、利润、现金流、资产负债表和股本的一致性检查；
- 不使用未经校准的精确情景概率。

### 9.5 价格与估值

`PriceSnapshot` 至少保存：

- Security、币种、价格、价格类型；
- 市场时间、首次可得时间、来源和内容哈希；
- 公司行动和复权口径。

`FXSnapshot` 至少保存货币对、汇率、市场时间、首次可得时间、来源、报价方向和内容哈希。

`CapitalStructureSnapshot` 至少保存现金、债务、少数股东权益、投资资产、养老金或其他必要调整、基本和稀释股数、潜在稀释工具、报告期间、市场时点、来源和哈希。

`SecurityRightsVersion` 至少保存目标 Security 的经济权利、投票权、转换或 ADR 比例、股息权利和有效期。

`ValuationRunVersion` 至少保存：

- ForecastSetVersion 与 PriceSnapshot 引用；
- DCF / FCFF 参数、估值时点、终值方法和 WACC；
- CapitalStructureSnapshot、SecurityRightsVersion 及必要的 FXSnapshot 引用；
- 企业价值到目标 Security 稀释后每股价值的完整桥接；
- 反向 DCF 的多组可行隐含假设；
- 有经济依据的 PE / PB / EV 交叉检查；
- 无概率权重的价值区间，或有校准依据且明确标记的概率分布；
- 3–5 年年化股东回报区间及股息、回购、稀释、退出价值和 FX 假设；
- 与 InvestmentMandate 要求回报和比较基准的差额；
- 无法识别或解空间过宽的区域；
- 方法、币种、期间、来源和哈希。

### 9.6 投资判断

`ResearchAssessmentVersion` 至少保存：

- answerability、direction、confidence 和 publication_status 四个正交状态；
- 价值区间和预期回报区间；
- confidence 取 low / medium / high / null；
- 判断依据、关键假设和适用期限；
- 最强反证、替代解释和 ResearchGap；
- 下一验证条件；
- 对 SourceStatement、ModelInput、ResearchGap、Mechanism、Forecast、Valuation 和全部市场快照的冻结引用。

方向性暂定判断的发布条件：

1. 所有 ModelInput 均满足正交分类契约；
2. 核心数值有来源或明确的人工假设记录；
3. 公司预测内部闭合；
4. 估值可识别并能给出有定义的价值和年化回报区间；
5. 最强反证和下一验证条件非空；
6. 关键缺口不会被重新标记为已报告事实；
7. 不存在阻塞方向发布的 ResearchGap；
8. 结论只表达研究判断，不产生交易和仓位动作。

以下任一条件成立时，answerability 必须为 not_answerable，direction 和 confidence 必须为空：

- 关键经营基线缺失或严重冲突；
- 核心机制无法从相关性提升为可使用的经济或会计关系；
- 收入、利润、资本投入、现金流或股本无法闭合；
- 估值解空间过宽，无法形成有定义的价值区间；
- 核心来源无权限、无法重现或晚于截止；
- RevisionBoundary 完整性验证失败。

此时可以发布 `insufficient_evidence` 评估，内容只包括阻塞原因、受影响判断、需要补齐的资料、解决动作和下一复核时间，不包含方向性结论。

### 9.7 不可变更新

正式发布对象只追加新版本：

- RetrievalArtifact / DocumentVersion / SourceStatement；
- ModelInput / ResearchGap；
- MechanismVersion；
- IndustryModelVersion；
- CompanyModelVersion；
- ForecastSetVersion；
- ValuationRunVersion；
- ResearchAssessmentVersion；
- ResearchMemoVersion。

WorkspaceDraft 可编辑和自动保存，但不得作为正式研究结果导出。新资料先创建 `UpdateCandidate`；用户接受、编辑或拒绝候选只改变草稿或追加审阅记录，不自动发布。

用户显式发布时，RevisionService 必须在一个事务边界内：

1. 验证 RevisionBoundary 和全部父引用；
2. 运行分类、时间、权限、财务闭合和可回答性门控；
3. 冻结使用的完整依赖图和 canonical manifest；
4. 创建 ResearchRevision 及内容哈希；
5. 生成相对父版本的 ChangeSet；
6. 成功后才使新版本对正式读取可见。

任一步失败都不得留下部分发布版本。禁止覆盖旧记录或让“当前关系”“最新行情”或未引用草稿替代冻结父图。

## 10. 后端服务边界

| 服务 | 职责 |
|---|---|
| `ResearchProjectService` | 搜索确认、项目、InvestmentMandate、ResearchScope、议程和对象关系 |
| `SourceAcquisitionService` | 受控检索、抓取、权限检查、原始来源冻结 |
| `DocumentParsingService` | 文档解析、页码与表格定位、候选陈述生成 |
| `EvidenceAdjudicationService` | SourceStatement 候选接受、规范化修正、拒绝、冲突、ModelInput 和 ResearchGap |
| `IndustryModelService` | 行业状态、机制、情景和正式化门控 |
| `CompanyModelService` | 分部模型、财务桥接、对账和行业暴露 |
| `ForecastService` | Actual / Consensus / Implied / House / Scenario 管理和一致性检查 |
| `MarketSnapshotService` | Price、FX、CapitalStructure 和 SecurityRights 冻结与公司行动处理 |
| `ValuationService` | DCF、反向 DCF、交叉检查、EV-to-equity 桥接、价值与年化回报区间 |
| `AssessmentService` | Answerability、暂定方向、置信度、反证、ResearchGap、有效期和发布门控 |
| `RevisionService` | WorkspaceDraft、RevisionBoundary、原子发布、后继关系、canonical manifest 和历史回放 |
| `ChangeSetService` | 两版本之间的语义变化分类和影响解释 |
| `MemoExportService` | 从选定冻结版本生成 Web、Markdown 和 PDF |

采集、解析、AI 提取和重计算使用异步 worker。用户确认、草稿保存、版本发布和导出请求通过同步 API 执行。后台任务结束只能表示该任务成功或失败，不得表示“研究已经完成”。

所有写请求和异步任务必须支持幂等键。异步任务至少保存 queued / running / partial_success / succeeded / failed / cancelled 状态、进度、尝试次数、输入边界、产物引用和结构化失败原因；重试不得重复采纳来源或重复创建正式对象。

## 11. API 能力范围

第一版 API 至少覆盖：

- 搜索 Industry / Company / Security，并解析公司与证券关系；
- 创建和读取 ResearchProject、InvestmentMandate、ResearchScope、ResearchAgenda；
- 创建和读取 WorkspaceDraft 与 RevisionBoundary；
- 创建和读取 HistoricalBasis、PriceSnapshot、FXSnapshot、CapitalStructureSnapshot 与 SecurityRightsVersion；
- 发起来源采集、查看任务状态、读取来源和定位；
- 接受、规范化修正、拒绝 SourceStatement 候选，管理 ModelInput 和 ResearchGap；
- 读取和更新 IndustryModel、Mechanism、CompanyModel；
- 创建和读取 ForecastSet 与情景；
- 运行和读取 ValuationRun、Reverse DCF 和交叉检查；
- 创建、预览和发布暂定 ResearchAssessment；
- 预览发布门控并原子创建 ResearchRevision，读取历史、canonical manifest、冻结边界和 ChangeSet；
- 创建和读取不自动发布研究修订的 MarketMark；
- 生成和下载 Markdown / PDF 备忘录；
- 读取外部任务失败原因并安全重试。

所有读取历史版本的 API 必须只从该版本的冻结父图重放，不得查询当前 fixture、最新关系、最新行情或同一项目下未引用的数据。

## 12. 错误与失败关闭

系统至少区分：

- 来源不可访问；
- 来源权利或保留策略不允许；
- 文档解析失败；
- 证据冲突；
- 数据晚于资料截止；
- 指标定义或单位不兼容；
- 行业或公司模型不闭合；
- 关键证据不足；
- 缺少有效价格快照；
- 缺少有效汇率、资本结构或证券权利快照；
- 估值不可识别；
- 历史版本损坏或无法验证；
- 外部数据或 AI 服务不可用。

外部服务失败不得污染正式版本。可重试错误保留任务、幂等键和来源上下文；不可恢复错误显示原因和人工处理建议。历史版本完整性验证失败时，页面必须失败关闭，不得以当前或最新研究代替。发布事务失败时，任何部分产物只能保留在草稿或失败任务上下文中，不得成为正式 ResearchRevision。

## 13. 数据来源与调用策略

### 13.1 数据优先级

1. 交易所、公司公告、监管机构和政府部门的原始公开资料；
2. 权威行业组织和国际机构报告；
3. 有明确授权的商业行情、财务和一致预期适配器；
4. 可追溯的二级资料，仅用于补充或识别待核验线索；
5. 人工假设，必须显式标记，不得伪装成来源数据。

### 13.2 本地与网络边界

- 正式项目、来源清单、原始文件、模型和版本数据保存在本机；
- 第一版不在应用完全关闭时进行后台监控；更新来自用户点击“检查更新”、用户手工导入，或用户明确启用且应用运行中的本机定时任务；
- 外部数据和 AI 只在用户发起任务或用户显式启用的本机计划任务中调用；
- 每次外部调用记录提供方、模型及版本、请求目的、时间、提示模板版本、输入来源哈希集合、参数、工具与解析器版本、原始输出哈希、后处理版本和使用边界；
- 发送给外部 AI 的内容必须受 SourcePolicy 约束；不允许外发的来源只能由本地解析器处理；
- 不要求常驻云服务；
- 无网络时已冻结版本仍可查看、比较和导出。

## 14. 一键本地运行

第一版通过 Docker Compose 提供：

- 前端；
- FastAPI 后端；
- 研究 worker；
- 采集与解析 worker；
- 本地 PostgreSQL；
- 本地文件存储。

必须提供：

- 初始化；
- 启动、状态和停止；
- 数据库迁移；
- 备份与恢复；
- 任务日志和失败诊断。

默认仅监听 localhost。第一版不提供登录，但本地无登录不等于来源权利、审计和版本完整性可以省略。必须防止任意 URL 抓取造成 SSRF、限制本地文件导入范围和文件大小、校验文档类型，并将外部服务密钥放在不进入备份和导出的秘密存储中。

## 15. 第一版交付范围

### 15.1 CATL 完整闭环

第一版必须完成：

1. 搜索并确认宁德时代公司与 300750.SZ；
2. 建立默认 3–5 年授权、资料截止和独立价格快照；
3. 自动生成研究议程；
4. 由用户启动自动采集并冻结公司公告、年报和关键行业资料；
5. 用户确认关键 SourceStatement、ModelInput 和 ResearchGap；
6. 建立动力电池行业状态与候选/正式机制；
7. 建立公司分部、经营和自由现金流桥接；
8. 建立 Base / Bull / Bear House 情景；
9. 运行 DCF / FCFF、反向 DCF 和合理倍数交叉检查；
10. 形成带置信度、反证、未知和验证条件的暂定判断；
11. 冻结发布并导出 Web、Markdown 和 PDF；
12. 使用第二个资料截止创建后继版本并展示 ChangeSet；
13. 使用不改变资料截止的第二个价格快照创建 MarketMark，并证明旧 ResearchRevision 不被改写。

### 15.2 Alphabet 复用验证

CATL 闭环完成后，使用 Alphabet 验证：

- Company 与 GOOGL / GOOG 多证券关系；
- 美股公告、财务和价格适配；
- 广告、Cloud 和 AI 资本开支机制；
- 同一页面、版本、证据、预测、估值和判断内核的复用；
- 不为 Alphabet 复制一套产品流程。

## 16. 分阶段交付

### 阶段 A：产品内核与入口

- 独立产品壳；
- 对象搜索与身份确认；
- ResearchProject、InvestmentMandate、ResearchScope、HistoricalBasis 和市场快照；
- WorkspaceDraft、RevisionBoundary、ResearchRevision 原子发布、canonical manifest 和最小历史回放；
- 自动 ResearchAgenda；
- Docker 一键运行、备份和恢复。

### 阶段 B：证据与 CATL 行业

- 中国公告、年报和行业资料采集；
- 来源权限、双时间、哈希和定位；
- SourceStatement、ModelInput、ResearchGap 候选与自我复核；
- 行业变量、机制、ResearchGap 和替代解释；
- 可用于后续建模的暂定行业状态。

### 阶段 C：公司、预测、估值与判断

- CATL 分部和自由现金流模型；
- Base / Bull / Bear House 情景；
- DCF / FCFF、反向 DCF 和交叉估值；
- 暂定判断、置信度、反证、未知和验证条件。

### 阶段 D：更新、变化与备忘录

- UpdateCandidate；
- UpdateCandidate 到 WorkspaceDraft 的接受、部分接受和拒绝流程；
- 语义 ChangeSet、第二基准和价格-only MarketMark；
- 完整历史回放；
- Web、Markdown 和 PDF 备忘录。

### 阶段 E：Alphabet 复用

- 美股数据适配；
- 多股权类别；
- Alphabet 专属行业机制；
- 完整复用回归和跨市场验证。

阶段 A 必须先用最小 Alphabet 身份 fixture 验证多 Security、币种和 SecurityRightsVersion 契约；阶段 E 才接入完整 Alphabet 数据和研究机制，避免到最后才发现核心模型只能处理 A 股单证券。

## 17. 验收标准

### 17.1 前端验收

- 产品入口和导航独立于旧 Event Research 壳；
- 用户不填写研究问题也能完成新建研究；
- 中文桌面工作台覆盖九个模块；
- 任一 ModelInput 都能分别展示 value_nature、provenance、scenario_key、epistemic_status 和 review_status；组合标签不能丢失底层维度；
- 任一正式数字可展开到来源、定位、期间、单位和可得时间；
- 行业变量可导航到公司分部、预测和估值影响；
- 估值展示 ValueRange 或合格 ValueDistribution、敏感性和隐含假设，不以单点目标价为主；
- 判断页面同时显示反证、未知和下一验证；
- 更新不会静默覆盖历史，旧版本不会读取当前数据；
- 导出备忘录与选中的冻结版本一致；
- 候选或 AI 输出未确认时不能伪装成正式事实；
- 页面不提供仓位、下单或确定性买卖指令。

### 17.2 后端验收

- Industry、Company 和 Security 身份独立且关系明确；
- HistoricalBasis 读取只受资料截止和冻结来源约束；ResearchRevision、估值和判断读取受完整 RevisionBoundary 约束；
- 晚于截止的信息不能进入历史版本；
- 来源、证据、派生值、机制、预测、估值和判断均有完整谱系；
- 发布只创建后继版本，不更新旧版本；
- 公司报表、分部和自由现金流桥接可对账；
- Base / Bull / Bear 情景内部一致且由不同机制驱动；
- 反向 DCF 支持多组隐含假设并能表达不可识别；
- answerability、direction、confidence 和 publication_status 状态正交，not_answerable 时 direction 与 confidence 必须为空；
- 同一 ResearchRevision 在重启、恢复和后续数据加入后，其 canonical manifest 哈希、结构化语义 JSON、关键数字和父引用保持一致；
- 外部数据和 AI 失败可安全重试且不污染正式版本；
- 应用层和数据库层共同保护不可变记录。

### 17.3 产品级完成定义

第一版完成不是“有一个 CATL 页面”，而是用户能够：

```text
搜索 CATL
→ 建立研究授权和时间边界
→ 核对公司与行业证据
→ 理解关键机制和财务传导
→ 建立可审计的 House 情景
→ 看见当前价格要求哪些假设成立
→ 形成诚实的暂定投资判断
→ 冻结并导出
→ 在新资料到来后创建后继版本并解释变化
```

随后 Alphabet 必须在不复制产品内核的前提下走通同一流程。Web、Markdown 与 PDF 允许容器格式字节不同，但其内容清单、版本身份、关键数字、引用和判断必须来自同一个 canonical manifest。

### 17.4 可执行黄金验收

第一版至少固定以下自动化 fixture：

1. `CATL-T1`：第一资料截止、第一价格快照和可发布的暂定方向；
2. `CATL-T2`：第二资料截止，至少包含一条新事实、一条受挑战机制、一项预测变化和一项判断变化；
3. `CATL-PRICE-ONLY`：资料基准不变、价格变化，只产生 MarketMark 或用户显式冻结的新估值，不改写 T1；
4. `CATL-NOT-ANSWERABLE`：关键经营基线或估值不可识别，必须输出 direction=null、confidence=null；
5. `ALPHABET-IDENTITY`：Company 同时关联 GOOGL 与 GOOG，价格、权利和每股价值绑定具体 Security。

自动验收至少断言：

- 所有正式 ModelInput 具有完整正交分类和谱系；
- `available_at > cutoff_at` 的数据准入数为 0；
- 收入、利润、现金流和 EV-to-equity 桥接在声明精度内闭合；金额误差不超过显示最小单位，比例误差不超过 1 个基点；
- 发布失败产生的正式 ResearchRevision 数为 0；
- 重试同一幂等任务不增加重复正式对象；
- CATL-T1 在导入 T2 和新价格后 canonical manifest 哈希不变；
- Web、Markdown 和 PDF 的版本 ID、关键数字、来源引用集合和判断完全一致。

## 18. 当前实现基线与差距

### 18.1 已有可复用能力

- Industry / Company / Security 与 HistoricalBasis 的不可变研究内核；
- 来源清单、MetricObservation、双时间、哈希和防未来信息机制；
- CATL 2024 冻结证据基线、显式 Unknown 和候选机制；
- evidence-only 研究版本、版本档案、历史详情、祖先差异和冻结边界 API；
- CATL evidence-only 页面与候选行业证据展示；
- 候选 dossier、双记录角色审阅、不可变发布和防越权正式化门控；
- OpenAPI / TypeScript 契约和历史回放测试。

### 18.2 尚未完成

- 独立的目标产品首页、新建研究和九模块公司工作台；
- 不以“研究问题”为必填项的 InvestmentMandate / ResearchScope / ResearchAgenda 流程；
- CATL 可正式或暂定使用的行业状态与机制；
- 公司分部、财务桥接和自由现金流模型；
- Consensus / Implied / House 分层预测与完整情景；
- 独立 Price / FX / CapitalStructure / SecurityRights 快照、DCF、反向 DCF 和估值交叉检查；
- 暂定投资判断、置信度、反证、有效期和验证条件；
- UpdateCandidate 到完整研究后继版本的产品闭环；
- 与冻结版本一致的 Markdown / PDF 备忘录；
- Docker 一键部署、备份和恢复的目标产品组合；
- Alphabet 复用验证。

### 18.3 必须显式迁移的现有模型

- 当前 HistoricalBasis 同时保存 `price_as_of`，目标模型必须迁移为 HistoricalBasis 与市场快照独立引用；迁移前后的历史版本哈希验证策略必须明确；
- 当前 InvestmentMandate 已包含期限、币种、要求回报、永久损失边界和比较集合；目标模型保留这些含义，不把资料截止和价格时点并入 Mandate；
- 当前 SourceReference / RetrievalArtifact / DocumentVersion / SourceSpan / SourceStatement / EvidenceLink 分层继续作为可信来源内核，PRD 不以统一 SourceArtifact 替换；
- 当前 AnswerabilityState / UnderwritingVersion / EligibleAction 与目标 ResearchAssessment 的映射必须通过一次性迁移或兼容读取层完成；第一版 UI 不展示 EligibleAction，但旧历史记录不得被重写或误称为新判断；
- 新对象 ResearchAgenda、WorkspaceDraft、RevisionBoundary、MarketMark、ForecastSetVersion、ValuationRunVersion 和 ResearchAssessmentVersion 均为新增能力，不得用旧研究档案页或旧动作矩阵冒充完成。

### 18.4 需要调整的既有口径

- 现有 `not_answerable / wait_for_validation` 继续表达严格知识边界，但不能作为第一版唯一输出。只有 partially_answerable 或 answerable 才可能产生方向；not_answerable 继续失败关闭，不得用 low confidence 绕过。
- 现有双角色候选审阅是可信底座实验能力。目标产品第一版是本地单用户自我复核，不以独立审阅或认证身份作为发布前提。
- 现有研究档案页是历史证据查看器，不等于目标公司的完整研究工作台。
- 旧设计中的必填“研究问题”由 `InvestmentMandate + ResearchScope + ResearchAgenda + 可选 UserFocus` 取代。
- 旧设计中的无持仓动作矩阵不作为第一版 UI 输出；第一版输出研究判断，不输出进入资格、仓位或交易动作。

## 19. 关键产品指标

第一版不以报告字数、来源数量或 AI 生成量作为成功指标。每个指标必须记录定义、分母、采样窗口和目标值：

| 指标 | 定义 | 第一版目标 |
|---|---|---|
| CATL 闭环通过率 | 17.4 中 CATL fixture 通过数 / CATL fixture 总数 | 100% |
| 正式输入谱系覆盖率 | 有 SourceStatement、派生公式或人工假设记录的正式 ModelInput / 全部正式 ModelInput | 100% |
| 历史完整回放率 | canonical manifest、父引用和关键数值验证通过的 ResearchRevision / 被测试 ResearchRevision | 100% |
| 变化可归因率 | 能归入事实、机制、预测、价值、价格、不确定性或判断之一的 ChangeSet 项 / 全部 ChangeSet 项 | 100%，允许额外标记 unexplained 但不得漏项 |
| 更新确认耗时 | 用户打开 UpdateCandidate 到接受、拒绝或延期的中位时长 | 仅观测，不设虚假目标；首版建立基线 |
| 备忘录一致率 | Web、Markdown、PDF 中版本、关键数值、引用和判断全部一致的导出次数 / 全部导出次数 | 100% |
| 跨市场内核复用率 | Alphabet 流程调用既有领域服务而未复制实现的核心能力数 / 预定义核心能力总数 | 100%；允许新增 provider adapter、会计映射和机制模板 |
| 正式版本零污染率 | 外部服务失败后未新增或改写正式 ResearchRevision 的失败任务 / 全部外部失败任务 | 100% |

## 20. 风险与约束

1. **公开行业数据不足**：CATL 的行业有效产能、实际利用率和价格序列可能无法在截止日前完整获得。产品必须支持显式 ResearchGap、范围和人工假设，不能为演示编造数据。
2. **一致预期授权**：Consensus 可能需要商业数据源。没有授权时必须缺失或由用户手工录入并标明来源。
3. **模型复杂度失控**：第一版只实现影响 CATL 判断的关键变量和财务桥接，不追求通用电子表格平台。
4. **暂定判断被误读**：页面和导出必须突出 `provisional`、置信度、缺口和失效条件，避免被理解为确定推荐。
5. **跨市场差异**：Alphabet 验证重点是身份、币种、公告、价格和多证券复用，不要求第一版同时覆盖所有美股特殊规则。
6. **AI 来源漂移**：正式版本只引用冻结内容，任何重新抓取或模型升级都必须产生新候选或新版本。
7. **估值口径漂移**：WACC、股权要求回报、退出价值、FX、稀释和公司行动若未冻结，会造成无法解释的价值变化。所有估值必须引用完整 RevisionBoundary。
8. **本地服务攻击面**：无登录的 localhost 服务仍可能受到恶意网页、SSRF、危险文件和泄露密钥影响。第一版必须限制来源域、出站协议、文件导入和秘密存储。

## 21. 发布决策

本 PRD 冻结以下产品决策：

- 架构支持 A 股、H 股和美股；交付顺序为 CATL → Alphabet；
- 目标用户为本地单机使用的专业个人投资者；
- 第一版无登录、无多人审批、仅自我复核；
- 产品从研究对象和授权开始，不要求用户输入研究问题；
- HistoricalBasis 与 Price / FX / CapitalStructure / SecurityRights 市场快照独立冻结，并由 RevisionBoundary 组合；
- 主界面是结构化工作台，AI 为嵌入式助手；
- 第一版允许边界清晰的暂定投资判断；not_answerable 不产生方向和置信度，第一版不使用 confirmed_view；
- 估值采用情景 DCF / FCFF、反向 DCF和有经济依据的倍数交叉检查；
- 不自动交易、不输出仓位、不以单点目标价作为核心结论；
- 交付形式包含可回放页面、Markdown 和 PDF；
- 本地 Docker 一键运行并支持备份恢复。
