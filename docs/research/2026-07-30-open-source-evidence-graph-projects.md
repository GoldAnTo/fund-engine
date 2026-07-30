# 行业主题证据图谱：可借鉴的开源项目调研

> 调研日期：2026-07-30  
> 范围：以行业主题为入口，将公告、研报、财报等材料组织成可审计的事实、主张、关系与解释，并穿透到股票和基金。  
> 来源边界：只采用项目官方 GitHub 仓库、仓库内文档和项目官方文档。活跃度为调研当日 GitHub 页面可见的发布/更新信号；不把 star 数当作技术适配性的证明。

## 结论先行

没有一个开源项目能够直接交付这套产品。可行方式是把项目拆成五层，选择性复用：

1. **文档解析**：MVP 首选 Docling；若以后需要大量异构数据源连接器，再评估 Unstructured。两者先二选一，不要同时引入。
2. **受约束的知识构建与检索**：借鉴 KAG 的 schema-constrained extraction、图与原文块互索引；用 Microsoft GraphRAG 做主题发现、局部/全局检索实验，但不把其自动抽取结果当成已审核事实。
3. **原型加速**：Neo4j LLM Graph Builder 适合验证“文档 → 实体/关系 → 图探索”，不宜直接成为生产证据账本。
4. **审计底座**：OpenLineage 只记录“哪次任务用什么输入生成什么输出”的处理血缘；业务证据仍需项目自己实现不可变的 `DocumentVersion / SourceSpan / ExtractedFact / Claim / EvidenceLink / ReviewDecision`。
5. **呈现与证券映射**：Cytoscape.js 做可交互图谱视图；AKShare/OpenBB 只作为股票、基金、行情和基础资料的候选适配器。持仓时点、证券主数据、基金披露周期和数据授权仍必须由本项目治理。

因此，最值得实施的组合是：

```text
Docling
  -> 自有不可变原文与 SourceSpan
  -> 受 schema 约束的抽取/实体对齐/人工审核
  -> 自有证据账本（事实、主张、支持、反证、缺口、时点）
  -> Neo4j 投影视图 + GraphRAG/KAG 检索实验
  -> 股票/基金持仓适配器（AKShare / OpenBB / 已有数据源）
  -> Cytoscape.js 证据穿透视图

OpenLineage 横向记录每次解析、抽取、审核发布和图谱投影的运行血缘
```

## 选择矩阵

| 项目 | 在本项目中的候选角色 | 许可 | 2026-07-30 活跃度信号 | 建议 |
|---|---|---|---|---|
| [Microsoft GraphRAG](https://github.com/microsoft/graphrag) | 主题子图发现、局部/全局问答、社区摘要 | MIT | GitHub 显示 v3.1.0 于 2026-05-28 发布 | 试验性采用，不做事实库 |
| [OpenSPG KAG](https://github.com/OpenSPG/KAG) | 领域 schema、图文互索引、多跳推理 | Apache-2.0 | 最新发布 v0.8.0 为 2025-06-27；截至调研日页面仍有 issue/PR 活动，但发行节奏明显慢于其他候选 | 借鉴设计，第二阶段 POC |
| [Neo4j LLM Graph Builder](https://github.com/neo4j-labs/llm-graph-builder) | 文档入图、schema 原型、图探索 UI | Apache-2.0 | GitHub Releases 显示 v0.8.5 于 2026-02-11 发布 | 用于原型，不作核心账本 |
| [Docling](https://github.com/docling-project/docling) | PDF/财报/表格/XBRL 解析与定位 | MIT（模型需分别核验许可） | GitHub Releases 显示 v2.115.0 于 2026-07-23 发布 | MVP 首选 |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | 异构文档分区、连接器、chunking | Apache-2.0 | GitHub 显示 0.23.1 于 2026-06-11 发布 | Docling 的备选/后续补充 |
| [OpenLineage](https://github.com/OpenLineage/OpenLineage) | 解析/抽取/发布任务的数据血缘 | Apache-2.0 | 官方规范站当前版本选择器显示 1.52.0，GitHub 仓库明确标为 active development | 后续接入，不能替代证据溯源 |
| [Cytoscape.js](https://github.com/cytoscape/cytoscape.js) | 浏览器端图谱探索与路径高亮 | MIT；GitHub 同时提示仓库中有 Unknown licenses，扩展需逐项检查 | GitHub 显示 v3.33.4 于 2026-05-19 发布 | 前端首选候选 |
| [OpenBB](https://github.com/OpenBB-finance/OpenBB) | 多提供商金融数据统一接口 | AGPL-3.0-only | GitHub 组织页显示仓库于 2026-05-27 更新；Releases 页面已有 v4.7.0 | 只做隔离适配器，先过许可审查 |
| [AKShare](https://github.com/akfamily/akshare) | 中国股票/基金/指数等公开数据适配 | MIT | GitHub 显示 v1.18.64 于 2026-05-27 发布 | 中国市场 POC 候选，不作唯一真源 |
| [Verifiable Company Research Agent](https://github.com/Yaoniguan-Money/Verifiable-Company-Research-Agent) | 证据优先研究工作流、带 citation 的报告、冻结评测包 | MIT | 2026 年公开 MVP；仓库当前仅 10 次提交、无 issue/PR，不能将活跃度视为生产保证 | 优先借鉴工作流和评测交付方式，不直接作为业务内核 |

## 项目详评

### 1. Microsoft GraphRAG

官方定位是从非结构化文本抽取结构化数据，并以图结构增强 RAG；查询引擎提供 Local Search、Global Search、DRIFT Search、Basic Search 和 Question Generation。仓库同时明确提醒：代码是方法演示，并非受官方支持的 Microsoft 产品；索引成本可能很高，开箱即用的 prompt 也未必适合自有数据。

可复用：

- Local Search 的“实体关系 + 原文 text units”联合检索思路，适合从某家公司或某条产业链边下钻到材料片段。
- Global Search/社区摘要适合生成“AI 算力链有哪些子主题、主要争议是什么”的导航层。
- 索引输出和不同查询方法可作为召回质量基线，用于与纯向量检索比较。

不能直接解决：

- 自动抽取的 entity/relationship/community report 不是已核验事实，不能直接成为投资证据。
- 没有本项目需要的披露时点、观察期、审核状态、证据角色、支持/反证/缺口和冻结版本语义。
- 不提供基金持仓时点穿透、证券主数据映射、估值口径或数据授权治理。

官方链接：[仓库](https://github.com/microsoft/graphrag) · [查询引擎概览](https://github.com/microsoft/graphrag/blob/main/docs/query/overview.md) · [Responsible AI Transparency FAQ](https://github.com/microsoft/graphrag/blob/main/RAI_TRANSPARENCY.md)

### 2. OpenSPG KAG

KAG 面向专业领域知识库，强调 schema-constrained knowledge construction、知识与原文 chunk 互索引、概念语义对齐，以及 logical form-guided 的混合检索和多跳推理。v0.8.0 还提供 Outline、Summary、KnowledgeUnit、AtomicQuery、Chunk、Table 等可配置索引，并暴露 recall 与 reasoning/Q&A API。

可复用：

- 用固定领域 schema 限制 LLM 抽取，明显比开放式三元组更适合 `Company / Stock / Fund / Document / Fact / Claim / EvidenceLink`。
- “KnowledgeUnit ↔ Chunk/Document”互索引非常接近本项目“图上结论必须回到原文”的需求。
- 分层检索与多跳 planner 可用于“主题 → 产业链角色 → 公司 → 股票 → 基金”的解释型查询。

不能直接解决：

- 通用 factual QA 的准确率不等于金融证据被正确审核；项目自己的 release-gate 仍不可缺少。
- 不能判断管理层归因、公司结果、产业链因果传导这三类证据是否被越级使用。
- 当前发行活跃度较弱，引入前应先用一小套中文公告/财报做可重复 POC，并评估部署和二次开发成本。

官方链接：[仓库](https://github.com/OpenSPG/KAG) · [Releases（含 v0.8.0）](https://github.com/OpenSPG/KAG/releases) · [OpenSPG 引擎](https://github.com/OpenSPG/openspg)

### 3. Neo4j LLM Graph Builder

该应用支持从 PDF、DOC、TXT、网页和对象存储等来源抽取实体与关系到 Neo4j；允许预定义/自定义 schema，提供图可视化、对话检索和答案来源元数据。它依赖 Neo4j 5.23+ 与 APOC。

可复用：

- 很适合在 1～2 周内演示“上传财报 → schema 约束抽取 → 图谱浏览 → 查看 chunk 来源”。
- 可借鉴其抽取任务状态、schema 编辑、来源级图谱过滤和 chunk 展示交互。
- 可验证 Neo4j 图查询和向量检索能否支撑产品预期，再决定是否长期使用 Neo4j。

不能直接解决：

- 它默认解决“把文档变成图”，不解决“哪些事实经谁审核、在哪个时点可见、是否支持特定主张”。
- LLM 抽取和回答的来源元数据不等于不可篡改证据版本，也没有本项目的正反证据裁决流程。
- 生产使用仍需单独实现幂等导入、版本冻结、失败重跑、身份合并审计和权限边界。

官方链接：[仓库](https://github.com/neo4j-labs/llm-graph-builder) · [Releases](https://github.com/neo4j-labs/llm-graph-builder/releases)

### 4. Docling

Docling 支持 PDF、DOCX、PPTX、XLSX、HTML、图像、邮件等格式，提供版面、阅读顺序、表格、公式、OCR 解析和统一的 `DoclingDocument` 表示；官方 README 已列出 XBRL 财务报告支持，并支持 lossless JSON 与本地/隔离环境运行。

可复用：

- 财报 PDF 的页、段、表格与阅读顺序解析；输出应保留页码、bounding box、原始文件哈希和解析器版本。
- XBRL/表格解析可减少“财务指标先转纯文本再让 LLM 猜表格关系”的错误。
- 本地执行适合受限材料；统一文档对象便于后续建立稳定 `SourceSpan`。

不能直接解决：

- 它只负责解析，不负责金融实体对齐、指标口径、事实审核、证据角色和主张状态。
- 中文扫描件、双栏研报、跨页表格必须用自有金标集验收，不能从官方功能列表推断质量达标。
- 使用 VLM/OCR 模型时要分别检查模型许可、资源消耗和数据外发风险。

官方链接：[仓库](https://github.com/docling-project/docling) · [官方文档](https://docling-project.github.io/docling/) · [`DoclingDocument` 概念](https://docling-project.github.io/docling/concepts/docling_document/) · [Releases](https://github.com/docling-project/docling/releases)

### 5. Unstructured

Unstructured 是面向 LLM 的开源文档预处理工具，提供多格式 partition、元素化输出、chunking 以及数据源连接器。其开源库与商业 API/平台并存，评估时需明确哪些能力在 Apache-2.0 仓库内、哪些是托管产品能力。

可复用：

- 按文档类型自动 partition 和按元素切分，适合邮件、HTML、Word、PDF 等材料统一接入。
- 连接器和批处理设计适合材料来源扩展后借鉴。
- 可作为 Docling 的基准对照：在同一中文金融材料金标集上比较段落完整性、表格准确率、定位稳定性与耗时。

不能直接解决：

- 结构化元素不是金融事实或证据；仍需实体/指标对齐、审核和冻结版本。
- 开源库、Transform MCP、托管 Pipeline 的能力与收费边界不同，不能把官网平台特性默认算作可自托管能力。
- 若 MVP 已采用 Docling，同时保留两套主解析链会扩大口径差异与运维面。

官方链接：[仓库](https://github.com/Unstructured-IO/unstructured) · [开源文档](https://docs.unstructured.io/open-source/introduction/overview) · [Partitioning](https://docs.unstructured.io/open-source/core-functionality/partitioning) · [Releases](https://github.com/Unstructured-IO/unstructured/releases)

### 6. OpenLineage

OpenLineage 是运行时 job、run、dataset 血缘元数据的开放标准，使用可扩展 facets 描述处理事件，并提供 Spark、Airflow、dbt、Flink 等集成。它适合回答“哪次运行由哪些输入生成了哪些输出”。

可复用：

- 为 `parse_document`、`extract_facts`、`review_publish`、`project_to_graph` 记录 run id、代码/模型/提示词版本、输入文档版本与输出数据集版本。
- 用 custom facets 增加模型名称、prompt hash、parser version、as-of cutoff 和审核批次等运行元数据。
- 将重跑、失败、回填与批次依赖从业务图谱中分离出来。

不能直接解决：

- 数据血缘不是论证血缘。它不能表示“这条原文如何支持/反驳这个主张”，也不能判断证据是否充分。
- 标准的 dataset/job/run 模型不能替代 `SourceSpan`、`Fact`、`Claim`、`EvidenceLink`、`ReviewDecision`。
- 对很小的 MVP 可能过重；可以先在自有 run 表中保留兼容字段，进入多管道生产后再发 OpenLineage events。

官方链接：[仓库](https://github.com/OpenLineage/OpenLineage) · [官方规范](https://openlineage.io/docs/spec/) · [核心模型](https://openlineage.io/docs/spec/object-model/) · [自定义 Facets](https://openlineage.io/docs/spec/facets/custom-facets/)

### 7. Cytoscape.js

Cytoscape.js 是浏览器端图论分析与网络可视化库，提供节点/边样式、事件、选择器、布局和图算法，并有丰富扩展生态。

可复用：

- 展示 `Theme → Claim → Fact → SourceSpan` 与 `Theme → Company/Stock ← Fund` 两类路径。
- 按证据角色、审核状态、支持/反证/缺口、时点和数据新鲜度编码颜色与线型。
- 点击节点后打开证据抽屉，展示原文定位、发布时间、观察期、审核记录，而不是把全部内容塞在图中。

不能直接解决：

- 它只负责呈现和客户端图分析，不提供图数据库、文档检索、权限、证据审核或版本治理。
- 大图会快速失去可读性；产品默认视图应是一条当前结论和有限的关键路径，完整图仅作为探索模式。
- 每个布局/交互扩展都需单独核验许可与维护状态；GitHub 对主仓库也提示存在 Unknown licenses。

官方链接：[仓库](https://github.com/cytoscape/cytoscape.js) · [官方文档](https://js.cytoscape.org/) · [扩展列表](https://js.cytoscape.org/#extensions)

### 8. OpenBB

OpenBB Open Data Platform 提供多数据提供商的统一 Python/FastAPI 接口，定位是把公共、授权和自有金融数据接入分析、AI copilot 与研究看板。主仓库采用 AGPL-3.0-only。

可复用：

- 借鉴 provider extension + 标准化 query/result model，把行情、公司基本面、宏观和其他金融数据隔离在适配层。
- FastAPI 服务和扩展机制可作为自有 `MarketDataProvider` 接口设计参考。
- 对海外证券或多个供应商的字段对齐，能减少项目自己重复造连接层。

不能直接解决：

- 不提供本项目的行业主题证据模型、公告/研报审核、因果边或基金持仓证据链。
- 数据可用性、准确性和授权仍由具体 provider 决定；官方也明确数据不保证准确。
- AGPL 对网络服务分发有重要约束，不能在未做法律/架构评估前把代码直接嵌入闭源产品。可优先参考接口形态，或作为隔离服务验证。

官方链接：[仓库](https://github.com/OpenBB-finance/OpenBB) · [官方文档](https://docs.openbb.co/platform) · [Releases](https://github.com/OpenBB-finance/OpenBB/releases) · [License](https://github.com/OpenBB-finance/OpenBB/blob/develop/LICENSE)

### 9. AKShare

AKShare 是面向中国及全球多类金融数据的 Python 接口库，覆盖股票、基金、指数、债券、期货、宏观等主题。官方声明数据仅供学术研究/参考，接口可能因不可控因素被移除。

可复用：

- 快速构建 A 股、基金列表、基金持仓披露、行情和部分基本面的 POC 适配器。
- 用于主题 → 股票 → 基金的样例闭环和数据字段探索。
- MIT 许可便于代码级评估，但数据内容仍受原始站点条款约束。

不能直接解决：

- 抓取接口并不是稳定、授权、可审计的数据产品；上游网页变化可能导致字段或接口失效。
- 不能把“当前抓到的基金持仓”当作实时持仓；必须记录报告期、公告日、抓取日和来源 URL。
- 不提供实体主数据、复权/口径治理、证据审核或可重复快照；不应作为唯一真源。

官方链接：[仓库](https://github.com/akfamily/akshare) · [官方文档](https://akshare.akfamily.xyz/) · [基金数据目录](https://akshare.akfamily.xyz/data/fund/fund.html) · [Releases](https://github.com/akfamily/akshare/releases)

### 10. Verifiable Company Research Agent

该项目是一个企业公开信息研究的 MVP/reference implementation。其默认流程是 `收集来源 → 切分 evidence chunks → 检索 → 抽取 facts → verification → 带 citations 的报告 → compliance check`，并将工作流的节点结果与关键分支写入 audit trail。它提供可替换的 LLM、搜索、向量、重排与 workflow 接口；实现了 Dense + BM25 + RRF + reranker 的混合检索，也提供冻结数据集、失败样本和一键离线复现实验。

可复用：

- 将来源收集、证据块、事实抽取、验证、报告生成和合规校验拆成显式工作流节点；本项目可将这些节点映射为 `DocumentVersion → SourceSpan → SourceStatement → EvidenceLink → AIAssessment`。
- `source_id/chunk_id/title/url/retrieved_at` 的 citation grounding、工作流审计记录、provider 接口与“真实 provider 缺 key 直接失败”的边界。
- 冻结金标、原始输出、失败样本、一键复现的评测交付方式。尤其要保留 no-answer 误命中等失败，而不是只报告高分。
- 财报表格解析、单位归一化、指标字典、混合检索与本地 reranker 的工程实现可作为 POC 素材。

不能直接复用：

- 它围绕单次企业研究任务/报告建模，没有本项目需要的持续 `ResearchCase`、可证伪 `Thesis`、产业因果环节、股票/基金持仓穿透和研究版本语义。
- README 明确部分 LLM 抽取事实在 `confidence=1.0` 时直接标为 verified。LLM 自报 confidence 不能作为正式证据强度，本项目仍应要求可回到冻结 SourceSpan，并让 SourceStatement/EvidenceLink 保持机器生成或人工复核状态。
- 其离线指标来自合成财报式片段；项目也明确说明不能外推到真实年报搜索、OCR 文档或线上 LLM 抽取质量。应借鉴评测方法，而不是接受分数作为真实可用性的证明。
- 项目自称 MVP/reference implementation，当前仓库规模和社区信号不足以支持整套 fork 后长期依赖；优先按模块阅读或重写，而不是让业务模型依赖其表结构。

结论：这是目前最接近本项目“证据优先研究管线”的参考实现之一，建议列为 **P0 借鉴项目**。借鉴其 `workflow audit + grounded citation + reproducible evaluation`，但以本项目的不可变证据账本和 `ResearchCase/Thesis/EvidenceLink` 模型替换其 task/report 中心模型。

官方链接：[仓库](https://github.com/Yaoniguan-Money/Verifiable-Company-Research-Agent) · [许可证](https://github.com/Yaoniguan-Money/Verifiable-Company-Research-Agent/blob/main/LICENSE) · [冻结证据与评测包](https://github.com/Yaoniguan-Money/Verifiable-Company-Research-Agent/tree/main/evidence)

## 对当前《设计文档》的具体影响

现有设计的大方向——主题入口、文档证据、产业链关系、股票/基金穿透、wiki 式可视化——是成立的，但开源调研显示应收紧以下五点：

1. **不要把 `Claim.confidence` 当证据强度。** LLM 的自报置信度不可校准。应把质量拆成来源级别、原文定位完整性、抽取验证状态、时间适用性、范围匹配和支持/反证角色。
2. **`SUPPORTS/CONTRADICTS` 不能只由一次 LLM 判断直接入正式图。** 先作为待审核建议；正式边应带 reviewer、rule/model version、decision reason、created_at，且冻结版本不可原地改写。
3. **区分事实、主张和因果边。** `Document -> Fact` 表示原文可复核事实；`Claim -> EvidenceLink -> Fact` 表示某事实被用来支持或反驳某主张；`CausalEdge` 需要单独的更高证据门槛。不能因为财报结果变好就自动证明上游主题传导成立。
4. **把双时间写入模型。** 至少保存 `observed_period/as_of_date` 与 `published_at/available_at`；历史回看只允许使用当时已经公开并审核可见的材料。基金持仓必须同时保存报告期和披露日。
5. **图数据库应是查询投影，不宜是唯一事实源。** 原文版本、哈希、SourceSpan、审核决策和冻结研究版本应先进入可事务审计的持久层，再幂等投影到 Neo4j；这样重建图谱不会丢失证据边界。

建议把当前“借鉴 FinGPT/TrustRAG/GraphRAG Agent/TradingAgents”的宽泛表述替换为本报告中的官方项目和明确复用边界。当前设计文档中部分引用是论文页、PyPI 或二手文章，并不能证明相应代码可直接复用、许可可接受或仍在维护。

## 建议的最小验证，不是先搭完整平台

先做一个 2 周左右的 **AI 算力链证据切片**，用于决定架构，而不是证明投资结论：

- 固定 3 家公司、2 只披露持仓的基金、6～10 份材料；公告/财报优先，研报作为观点来源单独标识。
- 用 Docling 解析，人工建立 30～50 条金标 `SourceSpan + Fact`；比较表格数值、页码定位、跨页段落和中文 OCR。
- 定义最小 schema：`Theme, ResearchCase, DocumentVersion, SourceSpan, Fact, Claim, EvidenceLink, Company, Stock, Fund, HoldingDisclosure`。
- 所有自动抽取先进入 pending；人工审核后才发布到正式证据图。
- Neo4j LLM Graph Builder 仅做交互原型；同时用自有 schema 投影一份 Neo4j 图，比较可控性。
- 只用 GraphRAG 的 Local Search 做一次检索实验；只有当它在固定问题集上显著改善“找到正确原文片段”的召回，才继续投入。不要以生成答案是否流畅作为验收。
- 用 Cytoscape.js 做一页：默认显示“一句当前结论 / 一个主要阻塞 / 关键支持与反证 / 穿透股票与基金”；完整关系图放在展开层。

验收门槛应是：任意结论都能点回冻结的原文位置；同一历史截点可重复；自动抽取不会越过审核门；主题到基金的每个权重都能回到具体持仓披露；删除 Neo4j 后可从审计底座无损重建。满足这些门槛后，再决定是否引入 KAG、OpenLineage 或更完整的金融数据平台。

## 一手资料清单

- Microsoft GraphRAG：[GitHub](https://github.com/microsoft/graphrag) · [Query Overview](https://github.com/microsoft/graphrag/blob/main/docs/query/overview.md) · [Releases](https://github.com/microsoft/graphrag/releases)
- OpenSPG KAG：[GitHub](https://github.com/OpenSPG/KAG) · [Releases](https://github.com/OpenSPG/KAG/releases)
- Neo4j LLM Graph Builder：[GitHub](https://github.com/neo4j-labs/llm-graph-builder) · [Releases](https://github.com/neo4j-labs/llm-graph-builder/releases)
- Docling：[GitHub](https://github.com/docling-project/docling) · [Docs](https://docling-project.github.io/docling/) · [Releases](https://github.com/docling-project/docling/releases)
- Unstructured：[GitHub](https://github.com/Unstructured-IO/unstructured) · [Open Source Docs](https://docs.unstructured.io/open-source/introduction/overview) · [Releases](https://github.com/Unstructured-IO/unstructured/releases)
- OpenLineage：[GitHub](https://github.com/OpenLineage/OpenLineage) · [Specification](https://openlineage.io/docs/spec/) · [Releases](https://github.com/OpenLineage/OpenLineage/releases)
- Cytoscape.js：[GitHub](https://github.com/cytoscape/cytoscape.js) · [Docs](https://js.cytoscape.org/) · [Releases](https://github.com/cytoscape/cytoscape.js/releases)
- OpenBB：[GitHub](https://github.com/OpenBB-finance/OpenBB) · [Docs](https://docs.openbb.co/platform) · [Releases](https://github.com/OpenBB-finance/OpenBB/releases)
- AKShare：[GitHub](https://github.com/akfamily/akshare) · [Docs](https://akshare.akfamily.xyz/) · [Releases](https://github.com/akfamily/akshare/releases)
