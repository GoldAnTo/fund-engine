# 证据驱动行业研究产品与前端原型：可借鉴项目核验

> 调研日期：2026-07-31  
> 目标：为 Fund Engine 的资料库、ResearchCase、证据审核、关系图、股票/基金穿透与前端原型提供可执行的产品参考。  
> 来源边界：只采用官方文档、官方仓库、标准与监管数据说明；不以产品宣传、star 数或二手测评作为适配依据。

## 结论先行

当前产品方向是成立的：它不是“带聊天框的金融搜索”，也不是“漂亮的知识图谱”，而是一条持续工作的研究闭环：

```text
资料冻结
  -> 原文定位与原子陈述
  -> AI 提议
  -> 人工审核
  -> 命题判断与因果缺口
  -> 公司/股票/基金披露穿透
  -> 新材料进入后形成新快照和新判断
```

截至本次核验，没有一个外部项目可以整体照搬。最合适的借鉴组合是：

1. 用 **Zotero** 定义“材料—标注—研究笔记—回到原文”的阅读体验。
2. 用 **Docling** 提供页码、字符区间、版面框、表格和文档层级等解析基础。
3. 用 **Argilla** 借鉴“模型建议与人工回答分离”的连续审核交互；用 **OpenRefine** 借鉴多候选实体对齐。
4. 用 **React Flow** 承担 MVP 的有限、可编辑、可键盘操作的焦点关系图；只在确有大规模探索需求时保留 **Cytoscape.js**。
5. 用 **Neo4j/Cypher** 做可重建的路径查询投影；用 **GraphRAG** 做主题发现和候选问题生成，不让自动抽取直接进入正式证据账本。
6. 用 **OpenBB** 借鉴 provider adapter 形态，用 **SEC Form N-PORT** 校准持仓的报告期、披露时间、采集时间与原始申报回链。
7. 用 **W3C PROV-O** 校准溯源词汇，但继续保留自有 `EvidenceLink` 和 `ReviewDecision` 语义。

最高优先级不是再增加一个框架，而是把原型补成三个真正可工作的回路：

- 从一条判断一键定位到冻结原文；
- 从一个 AI 提议完成一次可审计人工决定；
- 从一条行业命题穿透到披露持仓，并明确显示时点和不确定性。

## 评估标准

每个参考项目都按以下问题判断，而不是按功能数量判断：

- 是否让结论回到可复现的原文位置？
- 是否清楚区分机器建议与人的正式决定？
- 是否保留原始值、候选值、版本、时间和责任主体？
- 是否支持围绕当前研究问题聚焦，而不是把所有对象平铺出来？
- 是否能在投影、索引或外部服务失效后从账本重建？
- 是否适合中文财报、公告、研报和滞后的基金持仓披露？

## 优先级总表

| 优先级 | 参考项目 | 主要借鉴 | 建议采用方式 |
|---|---|---|---|
| P0 | Zotero | 原文阅读、标注、笔记与引用回跳 | 借鉴交互，不引入其数据模型 |
| P0 | Docling | 结构化解析、页码/字符/版面 provenance | 解析适配器 + 自有冻结版本 |
| P0 | Argilla | AI suggestion 与 human response 分离、Focus review | 借鉴审核工作台交互，不作正式账本 |
| P0 | OpenRefine | 多候选实体对齐、保留原字符串和人工 judgment | 单独的实体对齐审核类型 |
| P0 | React Flow | 有限焦点图、键盘与屏幕阅读支持、自定义节点 | MVP 主关系画布 |
| P0 | SEC Form N-PORT | 报告期/公开期/批次/原始 filing 回链 | 金融数据 provider contract 样板 |
| P1 | Cytoscape.js | 较大只读图的选择、过滤、布局和路径算法 | 仅用于探索模式；先验证真实规模 |
| P1 | Neo4j/Cypher | 多跳路径、局部子图和最短路径查询 | 可重建查询投影，不作唯一事实源 |
| P1 | Microsoft GraphRAG | 局部/全局/DRIFT 检索、主题与下一问题发现 | 离线实验与候选建议，不发布正式关系 |
| P1 | W3C PROV-O | Entity/Activity/Agent 及 derivation 词汇 | 导出与运行溯源语义参考 |
| P2 | OpenBB | Core/Provider/Toolkit 分层和统一接口 | 借鉴接口或隔离服务，先做许可审查 |

## 一、研究资料库与引用体验：Zotero

### 官方事实

Zotero 的 PDF 阅读器允许把标注加入笔记；进入笔记的标注会带回到 PDF 页面的链接和 citation。用户可以从单篇材料或多篇材料的标注生成笔记，并从笔记中的标注重新打开原文上下文。Zotero 将自身标注保存在数据库中，以避免协作同步时反复改写整个 PDF，同时支持导出带嵌入标注的 PDF。[Zotero PDF Reader and Note Editor](https://www.zotero.org/support/pdf_reader) · [Annotations in Database](https://www.zotero.org/support/kb/annotations_in_database)

### 可借鉴能力

- 资料库不应只是一张文档表。打开材料后，默认工作区应同时显示“文档结构/标注列表、原文、研究引用或检查器”。
- `SourceSpan` 应像 Zotero annotation 一样成为可点击对象；用户在判断、证据卡、因果边和研究日志中点击引用，都应回到同一个冻结版本的页码与高亮区域。
- “原文摘录”和“研究者写的解释”必须视觉分开。摘录保留引用，解释保存作者、时间和适用范围。
- 允许从多条 SourceSpan 组装研究笔记，但组合笔记不能抹掉每条片段各自的来源。

### 不可照搬的边界

- Zotero 管理的是文献、附件、标注与引用，不负责判断某条材料是支持、反驳还是背景，也没有本项目的 `ResearchCase / Thesis / EvidenceLink / ReviewDecision`。
- Zotero 标注可编辑；本项目正式进入证据链的冻结片段与审核决定应追加版本，不能用普通笔记的覆盖式编辑替代审计历史。
- 页码回跳只是可定位，不等于来源可信、范围匹配或因果成立。

### 对原型的具体改法

在资料页右侧检查器增加固定的“引用身份条”：

```text
DocumentVersion 标题与版本
页码 / 段落 / 表格单元格 / bbox
原始文件 hash + 解析器版本
published_at / acquired_at
引用到：命题 2 · 因果边 1 · 研究日志 3
[在原文中显示] [复制可审计引用]
```

“在原文中显示”必须打开同一冻结版本，而不是跳转到可能已更新的网页。

## 二、文档解析与 SourceSpan：Docling

### 官方事实

Docling 的统一 `DoclingDocument` 可表达文本、表格、图片、文档层级、页眉页脚、版面框和 provenance。其 `ProvenanceItem` 包含 `page_no`、`bbox` 和 `charspan`，适合作为从解析对象回到原文的轻量指针。[DoclingDocument 概念](https://docling-project.github.io/docling/concepts/docling_document/) · [DoclingDocument API](https://docling-project.github.io/docling/reference/docling_document/)

### 可借鉴能力

- 将 Docling 输出映射为稳定的 `SourceLocatorV1`，至少保留页码、字符区间、版面框、文档元素类型和解析器版本。
- 对表格不要只存一段展平文本；保存表格对象、单元格坐标、表头关系和原始页位置，以便复核指标口径。
- 解析任务应产生“成功、部分成功、失败阶段、警告、耗时、解析器/模型版本”而不是单一质量分数。
- 在中文扫描财报、双栏研报、跨页表格上建立 30–50 条人工金标 SourceSpan，验收定位回跳、阅读顺序和表格单元格，而不是只测是否能抽出文本。

### 不可照搬的边界

- Docling 解决结构和定位，不解决一个陈述是披露事实、管理层归因、预测还是研报观点。
- 解析器输出不能直接成为正式证据；仍需 `SourceStatement`、范围/期间标准化与人工审核。
- 同一文件在不同解析器版本下可能产生不同结构，因此 `SourceSpan` 不能只依赖易漂移的数组索引。

### 对原型的具体改法

资料库目前的“解析质量”应改成可解释的解析状态：

- 原文完整性：有无缺页或乱码；
- 定位可复现：页码/bbox/charspan 是否齐全；
- 表格可用性：表头与单元格是否保留；
- OCR 状态：是否启用、语言、模型；
- 影响范围：哪些引用因解析失败不可验证。

用户看到的是具体问题和重试动作，而不是 `82 分` 之类无法解释的评分。

## 三、AI 建议、人审与实体对齐：Argilla + OpenRefine

### 3.1 Argilla：审核工作台交互

Argilla 把模型输出称为 Suggestion，把人的提交称为 Response；模型建议可以预填，用户可以接受或修改后提交。其 Focus view 按记录线性处理，提交后自动进入下一条，并提供 Pending、Draft、Submitted、Discarded 等队列与键盘快捷键。[Argilla Suggestions](https://docs.argilla.io/latest/reference/argilla/records/suggestions/) · [Argilla annotation workflow](https://docs.argilla.io/latest/how_to_guides/annotate/)

#### 可借鉴

- AI 结果与人工结果在对象和视觉上都分开，而不是把 AI 文案直接变成人工结论。
- 审核页面采用“单条聚焦 + 原文始终可见 + 表单决定 + 自动下一条”，符合当前原型的三栏方向。
- 支持保存草稿、跳过和回看，但高风险证据关系不做一键批量批准。
- 审核表单按 proposal kind 改变：Statement、EvidenceLink、CausalEdge、EntityAlignment 不能共用一套只有“同意/拒绝”的表单。

#### 不可照搬

- Argilla 的目标是标注数据集，记录和 response 可以更新；本项目正式审核历史必须 append-only。
- suggestion score 只能用于排序或筛选，不能成为证据强度，也不能自动触发“可审核”或自动通过。
- Pending/Submitted 只是任务状态，不等于命题已获得支持或研究已完成。

### 3.2 OpenRefine：实体对齐工作台

OpenRefine 的 reconciliation 会根据名称、类型和其他属性返回候选实体；官方文档明确把它描述为半自动过程，需要人判断、审核和批准。界面保留原始字符串，同时保存匹配实体；对不明确匹配展示多个候选、分数和预览信息。[OpenRefine Reconciling](https://openrefine.org/docs/manual/reconciling) · [Reconciliation API](https://openrefine.org/docs/technical-reference/reconciliation-api)

#### 可借鉴

- 公司简称、证券代码、基金份额类别的对齐应成为独立 `EntityAlignmentProposal`，不能夹在 EvidenceLink 审核里。
- 候选卡并排展示强标识符、市场、公司全名、存续状态、份额类别、数据源和为什么匹配。
- 永远保留原始字符串和所有候选；人工决定只是追加 judgment，不回写或覆盖原文。
- 支持“仅此条”和“对相同原始字符串应用”两种作用域，但批量应用前必须显示影响数量与可撤回版本。

#### 不可照搬

- 不接受“最高分即真值”。OpenRefine 自身也强调人的判断，本项目还需处理股票代码复用、跨市场代码、基金 A/C 份额和公司更名。
- OpenRefine 的通用 HTTP API 官方说明并非稳定版本化接口，不应直接把它作为本项目长期服务契约。[OpenRefine API](https://openrefine.org/docs/technical-reference/openrefine-api)

### 对原型的具体改法

审核队列顶部增加 proposal kind 切换：

```text
原子陈述 | 证据关系 | 因果关系 | 实体对齐
```

右侧决定表单至少要求：决定、理由、适用范围、适用期间；数值证据还要求指标定义、单位、币种和合并范围。AI 建议以琥珀色独立区块显示，人工决定以另一个对象提交，禁止在同一字段内静默覆盖。

## 四、前端关系图：React Flow 与 Cytoscape.js 的明确分工

### 官方事实

React Flow 原生支持节点和边的 Tab 聚焦、Enter/Space 选择、Escape 清除、节点键盘移动、自动平移到焦点，以及可定制 ARIA 标签。其布局文档也明确说明：Dagre 对带外部连接的 sub-flow 存在限制，大图持续运行力导布局会带来性能成本。[React Flow Accessibility](https://reactflow.dev/learn/advanced-use/accessibility) · [React Flow Layouting](https://reactflow.dev/learn/layouting/layouting)

Cytoscape.js 提供图元素选择器、事件、框选、平移缩放、布局扩展和图算法；官方性能指南指出性能会随元素数量下降，复杂样式、边、多重边和高像素比尤其昂贵。[Cytoscape.js 文档](https://js.cytoscape.org/)

### 推荐分工

**MVP 只保留 React Flow 作为用户主关系画布。** 原因不是它能承载无限大图，而是 MVP 的正确任务是“围绕当前 Thesis 显示一条有限、可读、可操作的证据到基金路径”。React Flow 的 DOM 节点与内建键盘能力更适合当前可访问性目标。

只有满足以下触发条件后，才把 Cytoscape.js 保留为独立“全图探索”模式：

- 真实案例稳定超过 200 个可见节点；
- 研究员确实需要框选、邻域扩展、路径算法或动态图布局；
- 焦点图与结构化列表已经不能完成任务；
- 有性能基准证明 Cytoscape 方案优于 React Flow 的服务端切片方案。

### 当前原型需要立即修正

当前代码同时保留 Cytoscape 版 `EvidenceGraph` 与 React Flow 版 `RelationshipFlow`，容易让样式、选中语义、无障碍和测试分裂。更重要的是，`RelationshipFlow` 目前按输入顺序截取前 60 个节点，再过滤边；这不能保证保留当前命题到证据、公司和基金的完整路径。

建议把切片算法改为服务端或领域层的“焦点子图”：

```text
当前 Thesis
  + 直接相连的 CausalStep
  + 每一步最高优先级的支持/反证/缺口
  + 选中路径上的 Company/Stock/HoldingDisclosure/Fund
  + 用户手动展开的一跳邻域
```

每次响应返回 `omitted_node_count`、`omitted_edge_count`、`projection_watermark` 和“继续展开”游标。绝不能把“前 60 条”伪装成“完整图”。

### 原型交互建议

- 默认是阅读模式，节点位置由系统确定，不能拖动造成研究语义错觉；编辑关系进入明确的 Edit mode。
- 节点只显示对象名、关键状态和一行摘要；详情固定进入右侧检查器。
- 支持/反驳不能只靠颜色：边上显示文字、线型和箭头；冲突时两类边同时保留。
- 键盘焦点顺序按“证据 → 命题 → 因果环节 → 公司/股票 → 基金”排列，而不是 DOM 生成顺序。
- 始终提供等价的结构化“路径列表”，屏幕阅读器和不擅长图操作的研究员可以完成相同下钻。

## 五、知识图谱与研究发现：Neo4j + Microsoft GraphRAG

### 5.1 Neo4j/Cypher：路径查询投影

Cypher 的核心是用声明式 pattern 匹配节点、关系和路径，并支持固定/可变长度路径、最短路径和路径约束。[Neo4j Cypher Patterns](https://neo4j.com/docs/cypher-manual/current/patterns/)

#### 可借鉴

- 用它回答“某条命题通过哪些已审核关系穿透到哪家公司和哪些基金披露”这类路径问题。
- 默认查询应从当前 Thesis 或选中 EvidenceLink 出发，返回有限路径，不从全图随机布局开始。
- Neo4j 投影记录 ledger version / cutoff / projection watermark，页面显示投影是否落后。

#### 不可照搬

- 图中存在一条边不代表证据可信或关系正式成立。业务真相仍来自不可变 ledger 和 ReviewDecision。
- Cypher 的可变长度遍历如果不限制关系类型、方向、深度和 cutoff，会生成语义错误或不可解释的路径。
- Neo4j 不能替代原文件、SourceSpan、权限或审核记录。

### 5.2 GraphRAG：研究发现而非正式裁决

GraphRAG 提供 Local、Global、DRIFT 和 Basic Search。Local Search 把 AI 抽取的图与原文 text units 结合；Global Search 基于社区报告回答跨数据集主题问题；DRIFT 从局部问题引入社区信息并生成后续问题。标准索引会用 LLM 抽取实体、关系和可选 claim；官方同时说明索引成本较高，领域语料需要适配 prompt，并要求领域专家核验生成结果和追踪 provenance。[GraphRAG Query Engine](https://microsoft.github.io/graphrag/query/overview/) · [Indexing Methods](https://microsoft.github.io/graphrag/index/methods/) · [Responsible AI FAQ](https://github.com/microsoft/graphrag/blob/main/RAI_TRANSPARENCY.md)

#### 可借鉴

- Global Search 用于总览页生成“这个主题有哪些争议簇、哪些子问题近期变化最大”的候选导航。
- Local Search 用于从公司、指标或因果步骤召回相关 text units，帮助研究员找材料。
- Question Generation/DRIFT 用于提出“下一条应该验证什么”，输出进入任务建议，不进入正式命题结论。
- 在固定小语料上比较关键词、向量和 GraphRAG 的 SourceSpan 召回，而不是默认图检索一定更好。

#### 不可照搬

- AI 抽取的 entity、relationship、claim 和 community report 都是检索索引产物，不是 reviewed evidence。
- 社区摘要会压缩来源差异，不适合替代正反证据逐条对照。
- GraphRAG 官方明确需要人类领域分析；因此不能用其回答直接写入 `supported/contradicted`。
- 首版 6–10 份材料规模很小，GraphRAG 可能增加成本而没有召回收益，应放到 P1 对照实验。

## 六、金融数据接口与持仓时间语义：OpenBB + SEC Form N-PORT

### 6.1 OpenBB：借鉴 provider adapter

OpenBB Platform 将 Core、Provider extensions 与 Toolkits 分开；Core 提供统一访问和 FastAPI 基础，各 provider 以扩展包接入并通过标准接口被资产类别功能调用。[OpenBB Developer Guide](https://docs.openbb.co/platform/developer_guide) · [Provider architecture](https://docs.openbb.co/odp/python/developer/architecture_overview)

#### 可借鉴

- 自有 `FinancialDataProvider` 不返回供应商原始字典，而返回领域标准模型和 `provider_record_id`。
- capability 要显式声明：支持的市场、数据类型、最早日期、频率、是否点时、是否含修订历史、授权边界。
- provider 失败必须带可分类错误：无权限、限流、字段漂移、标的不存在、能力未暴露、日期不可用。
- 每次响应保留 provider、schema version、request parameters、acquired_at 和 raw payload hash。

#### 不可照搬

- OpenBB 主仓库是 AGPLv3；嵌入闭源或网络服务前必须单独做许可评估。[OpenBB repository and license](https://github.com/OpenBB-finance/OpenBB)
- OpenBB 官方也提示数据未必准确；统一接口并不会自动提供数据授权、点时一致性或中国基金持仓真值。
- 不引入整个平台来替代已有聚源等数据源；优先借鉴 adapter contract。

### 6.2 SEC Form N-PORT：持仓披露契约样板

SEC 的 Form N-PORT 页面说明：相关基金报告月度组合信息，公开数据集按季度更新，只包含已公开内容；数据集可能含申报或抽取错误，不能替代原始 filing。[SEC Form N-PORT Data Sets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets)

这个官方数据集最重要的启发不是接入美国数据，而是校准“持仓”绝不是一个 `fund_id + stock_id + weight`：

```text
fund_external_id
instrument_external_id
position / market_value / weight / currency
report_period
filed_at / published_at
acquired_at
filing_id / source_url
raw_document_sha256
provider / provider_schema_version
```

### 对原型的具体改法

- 所有基金节点都显示“披露持仓，截至 YYYY-MM-DD”，禁止简称为“当前持仓”。
- 检查器把报告期、披露日、采集日分三行显示；主题暴露也显示所用披露批次。
- 用户点击持仓边时能回到原始披露 DocumentVersion 或明确说明当前 provider 无原文能力。
- 同一基金不同份额、合并基金和不同币种必须先经过实体对齐，不能仅按名称聚合。

## 七、溯源标准：W3C PROV-O

W3C PROV-O 以 `Entity / Activity / Agent` 为起点，使用 `wasGeneratedBy`、`used`、`wasDerivedFrom`、`wasAssociatedWith` 等关系表达数据如何产生以及谁对此负责；它还提供 `wasQuotedFrom`、`wasRevisionOf` 和 `hadPrimarySource` 等派生关系。[W3C PROV-O](https://www.w3.org/TR/prov-o/)

### 可借鉴能力

- `DocumentVersion / SourceSpan / AIAssessment` 可视为不同 Entity。
- 解析、抽取、评估、审核发布和图投影是不同 Activity。
- parser、model、reviewer 和数据 provider 是不同 Agent。
- 导出 citation manifest 或审计包时，可映射到通用 provenance 词汇，减少自造含混字段。

### 不可照搬的边界

- PROV-O 描述来源和生成过程，不表达“这条陈述为什么支持或反驳这个命题”。
- `wasDerivedFrom` 不能替代 `EvidenceLink`，`Agent` 也不能替代审核状态机和权限模型。
- MVP 不需要先部署 RDF/SPARQL；先让自有字段语义与 PROV 对齐即可。

## 对现有设计与原型的集中修订建议

### P0：下一轮原型必须补齐

1. **把首页从信息摘要改成研究调度。** 每个 ResearchCase 卡必须明确显示：当前判断、最大反证或缺口、下一验证事件、待审核数、最后一次新证据。避免把“文档数、节点数”当进度。
2. **把原文回跳做成第一等交互。** 任何证据、判断、因果边都必须在一次点击内看到冻结摘录，再一次点击进入原页高亮。
3. **审核对象分型。** Statement、EvidenceLink、CausalEdge、EntityAlignment 使用不同表单和必填校验，不再共用模糊的“确认/修改/驳回”。
4. **主画布统一为 React Flow。** Cytoscape 旧图先收敛为内部实验或后续探索模式，避免双实现继续分叉。
5. **按语义路径切片，不按数组前 N 条切片。** 当前焦点路径必须完整，省略内容要可见、可展开、可计数。
6. **把时点放到对象标题旁。** 历史 cutoff、证据 available_at、数据 observed period、基金 report period 不应只藏在检查器底部。
7. **去掉可能被误读的置信度标签。** 模型 score 只放在“AI 提议”元数据中；研究页面展示的是来源等级、定位完整性、范围匹配、时效和审核状态。

### P1：闭环后再增强

1. 增加“下一问题建议”，使用规则或 GraphRAG 候选生成，但由研究员决定是否创建任务。
2. 增加实体对齐专用队列，支持候选预览和作用域明确的批量 judgment。
3. 增加投影 watermark、重建状态和索引延迟提示，避免把旧投影当最新账本。
4. 在真实规模超过阈值后评估 Cytoscape 探索模式，先有性能和可用性基准，再引入第二套图库。
5. 输出可携带的 citation manifest / provenance bundle，包含快照、引用、模型/规则版本和人工决定。

### 暂不做

- 不把聊天作为首页或研究主流程。
- 不因 GraphRAG、Neo4j 或大模型生成了一条关系就发布为正式证据。
- 不把图谱节点数、材料数、suggestion score 或模型置信度做成研究成熟度。
- 不做全市场实时图；首版只验证一个 ResearchCase 的完整闭环。
- 不把披露持仓、主题暴露或命题得到支持解释为买入建议。

## 建议用一轮可测试原型验证

下一轮不要继续扩页面数量，而应选一条真实 AI 算力链 Thesis 完成以下 12–15 分钟任务：

1. 从首页发现“有一条新反证待处理”。
2. 进入案例，看见当前判断、反证和缺口，而不是先看大图。
3. 点击反证，在右侧看到冻结摘录、范围、期间和审核状态。
4. 打开原文并定位到具体页/表格单元格。
5. 返回审核队列，看到 AI suggestion 与空白 human response。
6. 修改证据角色或范围，写理由并提交。
7. 自动进入下一条，不丢失筛选和 cutoff。
8. 回到 ResearchCase，看见新人工决定形成新版本，旧 AI 判断仍可查看。
9. 打开关系模式，只显示与当前 Thesis 有关的完整路径。
10. 点击基金持仓边，看见报告期、披露日、采集日和原始来源。

验收时记录：完成时间、回跳失败数、用户误把 AI 当人工的次数、用户误把持仓当实时的次数、找不到反证/缺口的次数。只要这些关键误解仍出现，就不应先扩展公司库、基金库或更多主题。

## 最终采用建议

本项目不需要寻找一个“开源 Bloomberg”。应把外部项目当作可拆卸的设计证据：Zotero 证明引用回跳怎样自然，Argilla 证明 AI 建议和人类提交怎样分离，OpenRefine 证明实体匹配为何需要候选与 judgment，Docling 提供原文定位，React Flow 提供焦点图交互，Neo4j/GraphRAG 提供路径查询和研究发现，OpenBB/SEC 提供金融数据接口与披露时点样板，PROV-O 提供通用溯源语言。

真正不可外包的核心仍然是：`ResearchCase` 的可证伪问题、不可变证据账本、`EvidenceLink` 的论证语义、人工审核边界、双时间快照，以及从每个最终判断回到冻结原文的能力。
