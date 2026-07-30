# 行业主题证据图谱与研究工作台设计

## 1. 目标与产品边界

本产品是一个以行业主题为入口的投研资料库和研究工作台。它把公告、财报、研报和行业资料保存为可回到原文的材料，并将材料组织为可验证的行业命题，最后穿透到公司、股票、估值与基金持仓。

产品的出口不是基金标签排名，也不是自动投资建议，而是回答：

1. 一个行业命题在当前证据快照下是 `supported`、`contradicted` 还是 `insufficient_evidence`？
2. 这个判断由哪些原文、指标、因果环节和反证组成？
3. 这些关联穿透到哪些产业链公司、股票和基金披露持仓？

第一版允许展示 AI 临时判断，但必须显著标为“未经人工复核”。人工可以确认、修改或驳回 AI 结果；两者均为不可变历史记录。

### 非目标

- 自动买卖、仓位、目标价、收益预测或个性化投资建议。
- 自动判断某条证据链已经成熟或自动触发人工审核。
- 全市场主题覆盖、实时全网爬取、跨行业无限多跳图谱。
- 将基金持仓披露伪装为实时持仓，或将股票映射直接等同于推荐。
- 用一次 LLM 推断自动生成正式因果关系或正式支持/反驳关系。

## 2. 用户与主界面

研究员从一个 `ResearchCase` 进入，例如“AI 算力链”。每个 ResearchCase 包含多条可验证、可反驳、有明确时间范围的 `Thesis`。

默认工作台展示一张一体化关系图，而不是相互隔离的资料列表和基金列表：

```text
DocumentVersion
  -> SourceSpan
  -> SourceStatement
  -> EvidenceLink (supports / contradicts / contextualizes)
  -> Thesis + CausalStep
  -> ThemeRole
  -> Company
  -> Stock + ValuationSnapshot
  <- HoldingDisclosure
  <- Fund
```

视觉上所有节点连续相连；语义上边必须区分，避免把“命题得到支持”误读成“该证券应被买入”。

默认焦点区只显示：一句当前 AI 判断、一个主要阻塞、有限数量的关键正反证据及其穿透路径。完整关系图属于探索模式。点击任意节点或边打开右侧证据抽屉，显示原文、理由、适用时间、范围、审核状态和下游关联。

## 3. 核心领域语言

完整术语以根目录 [CONTEXT.md](../../../CONTEXT.md) 为准。以下是不可混用的关键对象。

| 对象 | 定义 | 不可替代为 |
|---|---|---|
| `ResearchCase` | 持续更新的行业研究档案 | 新闻页、一次性报告 |
| `Thesis` | 可验证、可反驳、带期限的研究命题 | 主题、推荐 |
| `DocumentVersion` | 由内容哈希、来源和获取信息标识的冻结资料版本 | 最新文档 |
| `SourceSpan` | 页码、段落、表格单元格或字符区间等可复现原文位置 | 只有 URL 的引用 |
| `SourceStatement` | 来源明确说出的原子陈述，类型为披露事实、管理层归因、预测或研报观点 | 客观事实、证据 |
| `EvidenceLink` | 解释某个 SourceStatement 为什么支持、反驳或只提供背景的论证关系 | 自动语义相似边 |
| `CausalEdge` | 需独立证据门槛的传导关系 | 公司业绩增长、产业链相邻关系 |
| `EvidenceSnapshot` | 某次 AI 判断可见的冻结材料、陈述和证据关系集合 | 当前数据库状态 |
| `AIAssessment` | 基于 EvidenceSnapshot 的 AI 临时结论 | 正式结论、LLM confidence |
| `ReviewDecision` | 人对 AIAssessment 的确认、修改或驳回记录 | 覆盖式编辑 |
| `HoldingDisclosure` | 带报告期和披露日的基金持仓披露 | 实时持仓 |
| `Expression` | 在估值、暴露、时效和约束后用于表达研究想法的股票或基金 | 推荐、组合 |

## 4. 架构

```text
资料接入与冻结
  -> 证据账本（唯一事实源）
  -> AI 研究引擎
  -> 查询投影与工作台
```

### 4.1 资料接入与冻结

输入包括公告、财报、研报与经批准的行业资料。每次入库生成 DocumentVersion，至少保存：内容哈希、来源 URL、资料类型、发布/可见时间、获取时间、解析器版本和原始文件位置。解析结果保留 SourceSpan 对原文件的可复现定位。

MVP 优先使用 Docling 做 PDF、表格和阅读顺序解析；研报与公告接入通过适配器隔离。来源必须带等级和授权/使用边界，研报观点不得与法定披露混为同等级事实。

### 4.2 证据账本

证据账本是唯一事实源，使用具备事务、审计与版本控制能力的关系型持久层。每个写入均追加版本，不执行覆盖式更新。至少保存：

- DocumentVersion、SourceSpan、SourceStatement。
- ResearchCase、Thesis、CausalStep、CausalEdge。
- EvidenceLink 与其理由、角色、时间、范围、生成者、审核状态。
- Company、Stock、Fund、ThemeRole、ValuationSnapshot、HoldingDisclosure。
- EvidenceSnapshot、AIAssessment、ReviewDecision。
- 解析、抽取、检索、投影的运行记录、模型/提示词/规则版本和失败原因。

双时间要求：涉及事实时保存 `observed_period` 或 `as_of_date`，以及 `published_at`/`available_at`。历史回放只能使用截止日当时已公开且已进入快照的材料。HoldingDisclosure 同时保存持仓报告期、披露日和采集日。

### 4.3 AI 研究引擎

引擎只读证据账本，输出机器生成版本，不能直接修改已发布记录。

步骤：

1. 固定 cutoff，建立 EvidenceSnapshot。
2. 按 Thesis、CausalStep、公司和指标检索 SourceSpan。
3. 提议 SourceStatement、实体对齐、EvidenceLink 和潜在 CausalEdge，全部标记为机器生成或待复核。
4. 列出支持、反驳、背景和缺口，并生成 AIAssessment。
5. 输出 `supported`、`contradicted` 或 `insufficient_evidence`，同时输出理由、反证、范围限制和未覆盖问题。

禁止使用 LLM 自报 confidence 直接确定证据强度。证据展示需要拆分来源等级、原文定位完整性、抽取/审核状态、时间适用性、范围匹配和证据角色。

### 4.4 查询投影与前端

Neo4j、向量索引、关键词索引和前端图谱均为证据账本的幂等投影。删除任一投影后，应能从账本无损重建。Neo4j 用于多跳关系与局部子图查询，不存放唯一事实。

前端使用 Cytoscape.js 或同类图库。绿色边表示支持/反驳关系，黄色边表示产业与证券映射，当前 Thesis 使用独立焦点样式。完整图按切片加载，避免一次渲染全图。

## 5. 关系与判断规则

### 5.1 EvidenceLink

每条 EvidenceLink 必须指向一个 SourceStatement 和一个 Thesis 或 CausalStep，且保存：

- `role`: `supports`、`contradicts`、`contextualizes`。
- `reason`: 该陈述为何与命题相关。
- `scope`: 公司、业务线、地理范围、指标口径。
- `applicable_period` 与 `available_at`。
- `creator_type`: `ai` 或 `human`，以及规则/模型版本。
- `review_state`: `machine_generated`、`reviewed`、`rejected`。

Document 不可直接连到 Thesis。SourceStatement 不可因“来自财报”自动成为支持证据。研报观点能成为 EvidenceLink 的输入，但必须标注为观点来源。

### 5.2 因果边

`CausalEdge` 独立于 SourceStatement。例如“云厂商 CapEx 增加 → GPU/服务器采购增加 → 某公司收入增长”至少分为多个可检验环节。管理层归因或公司业绩改善只可证明其自身陈述/结果，不能自动证明上游传导。

每个 CausalEdge 显示支持、反证与缺口；AI 可提议边，但正式边需要独立审核记录。

### 5.3 AI 与人工结论

AI 结论默认直接可见，页面显著标示“AI 临时判断，未经人工复核”。系统永不判断“证据已成熟”或自动提交审核。研究员在需要时新增 ReviewDecision：

- `confirmed`：认可 AI 判断。
- `modified`：保留 AI 判断，写入人工结论和理由。
- `rejected`：拒绝 AI 判断并记录原因。

新资料触发新的 EvidenceSnapshot 和 AIAssessment，不修改既有快照或既有判断。

## 6. 证券与基金穿透

主题到基金的穿透不是推荐链，而是可解释的暴露链：

```text
Thesis -> ThemeRole -> Company -> Stock -> HoldingDisclosure -> Fund
```

ThemeRole 必须说明公司在主题中扮演什么角色、适用范围及来源。Stock 关联 ValuationSnapshot，保存估值日期、计算口径和数据源。Fund 关联 HoldingDisclosure，展示权重、报告期、披露日、采集日和来源；主题暴露是将已映射股票的披露权重聚合得到的派生值。

页面必须说明：披露持仓存在滞后；主题暴露不等于实时持仓；研究命题被支持不等于该股票估值合适，更不等于基金推荐。

## 7. 首个 MVP

研究对象：一个 AI 算力链 ResearchCase。

固定切片：

- 6–10 份冻结材料，公告/财报优先，研报作为单独标识的观点来源。
- 3 条中层 Thesis，例如 CapEx、硬件采购传导、公司兑现与估值。
- 3 家公司、3–5 只股票、2 只披露持仓的基金。
- 30–50 条人工金标 SourceSpan/SourceStatement，用于解析、抽取与检索评测。

必须演示：

1. 主题页显示一条 AI 临时判断、一条主要阻塞和正反证据。
2. 点击任一 EvidenceLink 可打开 SourceStatement、SourceSpan 与冻结原文。
3. 从任一证据可连续下钻到公司、股票、估值点和基金持仓披露。
4. 人工复核不覆盖 AI 原结果。
5. 在固定历史 cutoff 重放同一快照，结果引用集合保持一致。
6. 清空 Neo4j 投影后可从证据账本重建页面查询所需关系。

## 8. 测试与验收

### 单元测试

- DocumentVersion 内容哈希去重与新版本追加。
- SourceSpan 在页/段/表格位置上的可复现定位。
- SourceStatement 类型、指标、单位和期间标准化。
- EvidenceLink 的角色、范围、双时间与不可变历史。
- AI Assessment 与 ReviewDecision 版本隔离。
- HoldingDisclosure 对报告期、披露日、权重和主题暴露的聚合。

### 对抗测试

- 同一公司简称、股票代码、不同市场代码的实体歧义。
- 同一指标不同单位、币种、期间和合并范围。
- 一份财报的公司整体收入被错误用于证明一个业务线的命题。
- 来源在 cutoff 后发布，历史回放不得可见。
- 支持证据和反证证据都存在时，AI 不得只展示支持方。
- AI 生成的边、人工确认的边和人工拒绝的边不得混淆。
- 基金后续调仓不得回写历史披露持仓。
- 删除查询投影后，由账本重建的节点和边应一致。

### 评测集

借鉴 Verifiable Company Research Agent 的冻结评测包方式，保存金标、输入、模型/规则版本、原始输出、失败样本和一键复现命令。评估至少覆盖：正确 SourceSpan 召回、无答案时的拒答、表格事实抽取、引用忠实度、范围/时间错误和人工复核差异。

不得用合成样本高分证明真实线上材料已可用；真实材料 POC 与离线回归结果必须分开报告。

## 9. 可借鉴开源项目与边界

- Docling：PDF/表格/XBRL 解析与 SourceSpan；不处理投研证据语义。
- Verifiable Company Research Agent：证据优先工作流、citation grounding、审计轨迹、冻结评测；不采用其 task/report 中心领域模型，也不采用 LLM confidence 直通 verified。
- Neo4j LLM Graph Builder：用于快速原型和图探索；不作为唯一事实库。
- Microsoft GraphRAG、KAG、LightRAG：用于检索和图文召回实验；自动提取不能越过审核门。
- OpenRefine：早期公司/股票/基金实体清洗与人工对齐。
- Ragas：检索与引用评测的辅助工具；不评价投资结论真伪。
- Cytoscape.js：关系图与路径高亮；不承担审计、检索或版本治理。
- AKShare/OpenBB：证券和基金数据适配候选，必须单独验证授权、时点与数据口径。

完整调研见 [开源项目调研](../../research/2026-07-30-open-source-evidence-graph-projects.md)。

## 10. 实施顺序

1. 建立证据账本 migration、不可变写入、双时间字段及审计运行记录。
2. 接入 DocumentVersion/SourceSpan 解析，建立金标材料与可回原文页面。
3. 建立 ResearchCase/Thesis/CausalStep/EvidenceLink/AIAssessment/ReviewDecision。
4. 接入公司、股票、估值、基金与 HoldingDisclosure 的最小主数据和时点穿透。
5. 建立 Neo4j/检索投影与一体化关系图。
6. 接入 AI 提议与临时判断，并建立固定评测与人工复核流程。
7. 以 AI 算力链切片完成端到端验收后，才扩展其他主题、来源和自动化。

## 11. 硬性发布门

产品不得因“图谱可见”或“测试通过”宣称真实运行。首个版本只有在以下全部满足时才可宣称完成 AI 算力链闭环：

- 真实冻结材料与真实持仓披露均已成功导入。
- 任一页面结论可回到特定 DocumentVersion 的 SourceSpan。
- 每条主题暴露可回到具体 HoldingDisclosure。
- 历史 cutoff 回放没有使用未来可见的资料。
- AI 与人工结论的边界可在 UI 和导出数据中同时辨认。
- 关键对抗测试、金标评测、构建与端到端验证均有可复现输出。
