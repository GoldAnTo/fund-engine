# Fund Engine 后端参考项目与借鉴边界

> 调研日期：2026-07-31
>
> 基线：当前 `backend/`、`docs/design/backend-design.md`、三张 `prototype/设计原型*.png` 与前端 `ResearchClient` 契约。
>
> 来源边界：只使用官方仓库、官方文档和正式规范。本文是对 [2026-07-30 调研](./2026-07-30-open-source-evidence-graph-projects.md) 的后端补充，不重复 GraphRAG、KAG、Neo4j LLM Graph Builder、Unstructured、OpenLineage、AKShare、OpenBB 的已有结论。

## 结论先行

当前系统不缺另一套“文档转知识图谱”框架，缺的是把原型变成可靠产品 API 的应用后端：

1. **原文定位契约**：把现在自由形态的 `SourceSpan.locator` 收敛为可验证的页码、坐标、字符区间、逐字引用与上下文锚点。
2. **统一提案与审核队列**：`statement / evidence_link / causal_edge / entity_alignment` 都先成为 AI proposal，人工决定另写一条不可变 decision；不能直接改 proposal。
3. **可恢复长任务**：上传、解析、抽取、对齐、提案、评估和投影不能继续只靠同步脚本或一条最终 `AIRun`。
4. **可靠投影与活动流**：同一数据库事务里同时追加领域事实和 outbox event，再让图、搜索、任务变化和活动流分别消费；不能在请求里双写 PostgreSQL 与 Neo4j/OpenSearch。
5. **两种“时间点”分开**：业务回放使用 `available_at/published_at <= cutoff`；搜索引擎 PIT 只保证一次分页期间结果不漂移，不能替代业务 cutoff。
6. **证券身份与披露适配**：外部代码先映射到内部 instrument identity；持仓必须保存报告期、披露日、采集日、原始 filing/document version 和字段口径。

建议的采用顺序：

```text
现在（P0）
  W3C Web Annotation locator 形态
  Docling / Docling Graph provenance 输出
  统一 Proposal + ReviewDecision 领域模型
  append-only domain_events + cursor API
  provider adapter + identifier mapping + disclosure provenance

数据量或任务复杂度触发后（P1）
  Debezium Outbox -> Neo4j / OpenSearch / activity consumers
  Temporal durable workflow，或 LangGraph 仅承接 AI 图中的 interrupt/checkpoint
  OpenSearch 全文/语义检索 + PIT/search_after

只作架构校验（不迁移）（P2）
  PROV-O 术语映射
  XTDB 双时间查询语义
```

## 原型能力与当前后端缺口

| 原型/前端要求 | 当前已有 | 后端仍缺 |
|---|---|---|
| 研究总览：任务队列、证据变化、活动流 | `AIRun` 只记录最终 success/failed；账本实体有 `created_at` | `jobs` 当前状态、步骤进度、重试/取消、`domain_events`、按 cursor 的活动 API、按 case 聚合的 overview API |
| 案例档案：命题版本、当前判断、竞争解释、研究日志 | `ResearchCase / Thesis / EvidenceSnapshot / AIAssessment / ReviewDecision` | case/thesis 显式版本链、列表与详情 API、变更日志、竞争解释/缺口的稳定结构化契约 |
| 原文检查器：页内定位、引用记录、解析失败 | `DocumentVersion / SourceSpan(locator JSON, verbatim_text)` | 原始二进制对象存储引用、locator schema、解析任务/错误阶段、定位校验、文档版本列表和引用反查 API |
| 审核队列：四类待审对象、确认/修改/驳回/跳过 | `EvidenceLink.review_state` 与 assessment 级 `ReviewDecision` | 通用 `Proposal`/`ReviewDecision`，待审分页/认领/并发控制，statement/causal/entity alignment 审核；当前没有写 API |
| 全局搜索：案例/命题/证据/公司/股票/基金 | 无 | 搜索索引文档、权限与 cutoff 过滤、稳定分页、按类型分组、回源 ledger ID |
| 连续关系画布与历史截点 | ledger 组装 graph；Neo4j 可重建；workbench 有 cutoff | 图投影批次/水位、增量 outbox 消费、查询 API、所有节点/边统一的 valid/available interval；搜索和图的 cutoff 一致性 |
| 基金/公司库与披露持仓 | 公司、股票、基金、估值、持仓、主题角色；Gildata 仅研报/公告/行情 | canonical instrument identity、别名/映射审核、基金/份额类别、持仓披露文档与行级 provenance、provider capability/freshness/error contract |

## 高杠杆参考项目与准确接缝

### 1. W3C PROV-O：统一“谁用什么生成了什么”的术语，不引入 RDF 账本

PROV-O 用 `Entity / Activity / Agent` 及 `used / wasGeneratedBy / wasDerivedFrom / wasAttributedTo` 表示可追溯链；`wasRevisionOf` 表示新实体是旧实体的修订，`Bundle` 是一组具名 provenance 描述，且 bundle 内容改变就形成不同 bundle。[PROV-O 的起点模型](https://www.w3.org/TR/prov-o/#starting-points) · [修订关系](https://www.w3.org/TR/prov-o/#wasRevisionOf) · [Bundle](https://www.w3.org/TR/prov-o/#Bundle)

**借鉴**：把内部审计字段固定成一套可导出的映射：

| Fund Engine | PROV-O 语义 |
|---|---|
| `DocumentVersion / SourceSpan / SourceStatement / EvidenceSnapshot / AIAssessment` | Entity |
| `ParseRun / ExtractionRun / ProposalRun / ProjectionRun` | Activity |
| 人、模型、解析器、provider | Agent / SoftwareAgent |
| `supersedes_id` | `wasRevisionOf` |
| 冻结 citation manifest | Bundle |

**不要照搬**：不为兼容规范而把 PostgreSQL 账本改成 RDF store；PROV-O 也不表达本项目 `supports / contradicts / contextualizes` 的论证语义。

**模块接缝**：新增 `backend/app/provenance/mapping.py` 只做导出映射；`models/ledger.py` 中的 run 记录补 `agent_ref / input_entity_ids / output_entity_ids / code_version`，citation manifest 保存为内容寻址的不可变实体。不要把这些字段塞进 Neo4j 后再反向当真相。

### 2. W3C Web Annotation + Docling provenance：把 `locator JSON` 变成稳定契约

W3C Web Annotation 的 `TextQuoteSelector` 同时保存 `exact / prefix / suffix`，`TextPositionSelector` 保存 Unicode 字符流的 `[start, end)`；规范明确指出位置选择器在内容变化后很脆弱，因此要和资源状态/版本一起使用。[Text Quote 与 Text Position Selector](https://www.w3.org/TR/annotation-model/#text-quote-selector) · [Fragment Selector](https://www.w3.org/TR/annotation-model/#fragment-selector)

Docling 的 `ProvenanceItem` 已提供 `page_no / bbox / charspan`，用于从抽取元素回到文档；Docling Graph 进一步把 chunk、page、doc item ref、几何位置、text hash 和节点 lineage 输出成独立 `provenance.json`，并区分 document/chunk/span 定位精度。[Docling `ProvenanceItem`](https://docling-project.github.io/docling/reference/docling_document/#docling_core.types.doc.document.ProvenanceItem) · [Docling Graph provenance ledger](https://docling-project.github.io/docling-graph/fundamentals/graph-management/provenance/)

**借鉴**：定义版本化 `SourceLocatorV1`，至少包含：

```json
{
  "schema": "source-locator/v1",
  "document_sha256": "...",
  "page": 4,
  "bbox": {"l": 90, "t": 280, "r": 506, "b": 306, "origin": "top-left"},
  "text_position": {"start": 412, "end": 795},
  "text_quote": {"exact": "...", "prefix": "...", "suffix": "..."},
  "parser_item_ref": "#/texts/57",
  "parser_version": "..."
}
```

`document_sha256 + page/bbox + quote + offsets` 必须组合使用；只存页码或只存 chunk id 都不足以让右侧检查器可靠定位。解析完成后跑 locator round-trip：按 locator 重取文本，哈希或规范化文本必须与 `verbatim_text` 对上。

**不要照搬**：Docling Graph 自动抽出的 graph node 仍是机器结果，不能越过 Proposal/Review；`provenance.json` 也不能替代业务 EvidenceLink 与 ReviewDecision。

**模块接缝**：

- `app/services/ingest.py`：接收原始 blob ref，调用 parser adapter，生成 DocumentVersion + SourceSpan。
- 新增 `app/documents/locators.py`：Pydantic schema、坐标归一化、round-trip validator。
- 新增 `app/datasources/docling.py`：只负责把 `DoclingDocument.prov` 映射为 `SourceLocatorV1`。
- `GET /api/documents/{version_id}/spans/{span_id}/source`：返回短期 blob URL、locator 和高亮信息；文档权限与 span 权限同源校验。

### 3. LangGraph：只借鉴 AI 阶段的 checkpoint/interrupt，不把业务审核状态藏在 agent state

LangGraph persistence 在每步保存 checkpoint，支持故障恢复和时间旅行；interrupt 会持久化当前状态并等待外部输入，恢复时必须使用同一 thread ID。官方同时提醒：interrupt 所在节点恢复时会从节点开头重跑，因此 interrupt 前的副作用必须幂等。[Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) · [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

**借鉴**：若 AI 链发展为多步并带人工暂停，可将 `parse -> extract -> align -> propose -> assess` 做成 checkpointed graph；每个节点只产出候选 artifact ID，人工审核通过正式 API 写 PostgreSQL `ReviewDecision`，随后用 decision ID 恢复 workflow。

**不要照搬**：LangGraph checkpoint 不是审计账本，thread state 不是正式 ReviewDecision，也不能用 checkpoint “时间旅行”替代 `published_at/available_at <= cutoff` 的历史事实规则。

**模块接缝**：`app/ai/workflow.py` 只编排现有 `extraction.py / proposal.py / assessment_gen.py`；`thread_id = job_id`；所有节点以 `document_version_id / thesis_version_id / proposal_ids` 传参，副作用采用 idempotency key。`app/services/reviews.py` 与 API 不依赖 LangGraph，保证未来可替换编排器。

### 4. Temporal：长任务和人工等待的生产级参考

Temporal Workflow Execution 是可恢复的 durable execution，失败后依据 append-only Event History 从最后记录处继续；Workflow 可用 Query 读状态、Signal 接受异步写、Update 接受可校验并可等待结果的同步写。[Workflow durability](https://docs.temporal.io/workflow-execution) · [Event History](https://docs.temporal.io/workflow-execution/event) · [Signals, Queries, Updates](https://docs.temporal.io/encyclopedia/workflow-message-passing)

**借鉴**：当单文档管道跨分钟、需要重试、取消、等待人工决定或 provider 限流时，用一个 `DocumentIngestionWorkflow` 管理阶段；Activity 承担不确定 I/O，Workflow 只做确定性编排。任务 API 返回 `job_id`，前端轮询/SSE 读取自有 read model，不直接暴露 Temporal history。

**不要照搬**：Temporal Event History 是执行恢复日志，不是用户可见活动流，也不是证据事实；小规模 MVP 不必立即部署 Temporal。先实现稳定 job contract 与幂等 activity，达到触发条件再换执行器。

**触发条件**：出现任一项再引入：同步请求经常超过 30 秒；需要人工暂停后跨进程恢复；同一任务含三种以上外部 provider 重试；已有脚本无法安全取消/重跑。

**模块接缝**：

- 新增 `app/jobs/contracts.py`：`JobStatus = queued/running/waiting_for_review/succeeded/failed/cancelled`、step、progress、error_code、attempt。
- 新增 `app/workflows/ingestion.py` 与 `app/workflows/activities.py`；现有 `run_ai_engine.py` 退为 worker/CLI 入口。
- `POST /api/documents:ingest -> 202 {job_id}`、`GET /api/jobs/{id}`、`POST /api/jobs/{id}:cancel`、`GET /api/jobs/{id}/events?after=`。

### 5. Debezium Outbox + CloudEvents：可靠驱动图投影、搜索和活动流

Debezium 的 Outbox Event Router 通过捕获同库 outbox 表避免“内部状态已提交、外部事件未发出”的不一致；官方基础列包括唯一 event id、aggregate type/id、event type 和 payload，aggregate id 也用于保持分区内顺序。[Debezium Outbox Event Router](https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html)

CloudEvents 规定事件至少有 `id / source / specversion / type`；`source + id` 可用于重复检测，`subject / time / dataschema` 可支持路由和 schema 演进。[CloudEvents 规范](https://github.com/cloudevents/spec/blob/main/cloudevents/spec.md)

**借鉴**：每次账本事务同时 INSERT 一条 append-only `domain_events`：

```text
id, aggregate_type, aggregate_id, event_type, occurred_at,
actor_id, causation_id, correlation_id, schema_version, payload
```

由不同 consumer 维护各自 checkpoint，增量更新 Neo4j、搜索索引、workspace evidence changes 和 activity feed。重复事件按 event id 幂等，错序按 aggregate sequence 检测；全量 rebuild 仍从 ledger 开始，不能只靠 event bus。

**不要照搬**：MVP 不需要 Kafka + Debezium 全套基础设施。先落 `domain_events` 和数据库 cursor poller；当多个独立 consumer 或吞吐达到瓶颈时，再把投递换成 Debezium。CloudEvents 是传输 envelope，不是领域事件模型。

**模块接缝**：新增 `app/models/events.py`、`app/repositories/events.py`、`app/services/activity.py`、`app/projections/checkpoints.py`。Repository 的每个业务 INSERT 与 event INSERT 共用同一 session/transaction；`projection.py` 增加 `apply(event)` 和 checkpoint，保留 `rebuild_all()` 作为校验与灾备。

### 6. ActivityStreams 2.0：只借鉴活动条目的 actor/verb/object/target 与分页

ActivityStreams 把活动建模为 actor 对 object 执行动作并可带 target；`OrderedCollection / OrderedCollectionPage` 提供有序集合与分页链接。[Activity 模型与示例](https://www.w3.org/TR/activitystreams-core/#activities) · [Collection paging](https://www.w3.org/TR/activitystreams-core/#collection)

**借鉴**：用户可见 `ActivityEvent` 固定为 `actor / verb / object_ref / target_ref / occurred_at / summary`，并使用 opaque cursor，而不是从十几张业务表临时 UNION 出不稳定文案。活动由 `domain_events` 派生，原始 event 保持机器可读，summary 由模板生成。

**不要照搬**：无需输出完整 JSON-LD，也不要把研究活动开放成社交协议；“任务状态”与“活动记录”仍是不同 read model。

**模块接缝**：`GET /api/research-cases/{id}/activity?after=&limit=` 和 `GET /api/activity?after=&actor=&type=`；`app/services/activity.py` 负责授权、过滤和模板化，cursor 至少含 `(occurred_at, event_id)` 以稳定翻页。

### 7. XTDB：校验双时间语义，不建议迁移 PostgreSQL

XTDB 是开源 immutable SQL database，区分系统时间（记录何时进入数据库）和有效时间（事实何时在业务世界成立），支持跨时间查询；官方概念页明确把 valid time 用于乱序更新、回填和领域建模。[XTDB 仓库](https://github.com/xtdb/xtdb) · [Time in XTDB](https://docs.xtdb.com/about/time-in-xtdb.html) · [Key concepts](https://docs.xtdb.com/concepts/key-concepts.html)

**借鉴**：用它检查当前模型是否把下列时间混为一谈：

- `observed_period / report_period / applicable_from,to`：valid time；
- `published_at / available_at`：当时外部世界/系统可见；
- `acquired_at / created_at`：system record time；
- `cutoff`：查询 basis。

业务 API 的 `cutoff` 必须同时约束证据、主题角色、持仓、估值和投影版本，不应只过滤 EvidenceLink。

**不要照搬**：现有 PostgreSQL append-only 模型、触发器和测试已经成立；为获得语法糖迁移数据库会扩大风险。参考 XTDB 的查询矩阵，为 PG 写 adversarial tests 即可。

**模块接缝**：新增 `app/time/basis.py`，统一 `HistoricalBasis(cutoff, observed_on?, system_recorded_before?)`；repositories 不再各自解释 cutoff。为 `projection_batch` 保存 `ledger_high_watermark / cutoff / built_at / schema_version`，API 返回 basis metadata。

### 8. OpenSearch PIT：解决分页一致性，不解决历史事实

OpenSearch Point in Time 把一次搜索绑定到固定索引状态，可与 `search_after` 配合做稳定深分页；官方明确普通 `search_after` 在并发索引/删除时可能不一致。[OpenSearch PIT](https://docs.opensearch.org/latest/search-plugins/searching-data/point-in-time/)

**借鉴**：全局搜索索引每条记录必须携带 ledger ID、object type、case IDs、`available_from / available_to`、review state、source version hash 与权限字段。请求先用业务 `cutoff` 过滤，再用 PIT + search_after 保证用户翻页期间不漂移。

**不要照搬**：PIT 是短期搜索快照，不能回答“2024-05-31 当时知道什么”；搜索索引也不能成为证据真相源。首版数据量不大时可先用 PostgreSQL FTS/trigram，同一 `SearchService` 接口后换 OpenSearch。

**模块接缝**：新增 `app/search/service.py`、`app/search/postgres.py`，后续增加 `opensearch.py`；`GET /api/search?q=&cutoff=&types=&cursor=` 返回分组 hit 和 ledger deep link。索引 consumer 只消费 reviewed/authorized 版本，机器 proposal 仅在审核工作台的受限搜索中可见。

### 9. OpenFIGI：把外部代码映射当作“候选对齐”，不是静默主数据合并

OpenFIGI 官方 API 可把第三方 identifier 映射为 FIGI，mapping job 支持交易所、市场、币种等过滤；同一输入可能返回多个结果或 no-match warning，因此它天然要求消歧，而非简单字典替换。[OpenFIGI API 文档](https://www.openfigi.com/api/documentation)

**借鉴**：`InstrumentProvider.resolve_identifiers()` 返回 0..N 个候选及证据，不直接写入 Stock/Fund。每个 mapping 保存 provider、request、response hash、retrieved_at、有效期和 mapping status；歧义进入 `entity_alignment` review queue。

**不要照搬**：FIGI 不能覆盖所有中国公募基金份额与内部产品 ID；ticker 也不是稳定主键。内部 `instrument_id` 仍由本项目生成，FIGI/ISIN/CUSIP/交易所代码/基金代码只是带来源和有效期的 alias。

**模块接缝**：新增 `app/instruments/identity.py`、`app/datasources/openfigi.py`；扩展模型 `instrument_identifiers` 与 proposal kind `entity_alignment`；Gildata/AKShare/OpenBB provider 都先经过同一 identity resolver。

### 10. SEC Form N-PORT：基金持仓披露适配器的官方数据契约样板

SEC 的 Form N-PORT 数据集来自公开结构化申报，基金按月报告组合并按季度公开；SEC 页面同时强调数据集不是原始 filing 的替代品，可能存在申报或抽取错误，且更新为季度批次。[SEC Form N-PORT Data Sets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets) · [EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)

**借鉴**：它很好地说明“持仓值”和“何时公开可见”必须分开。一个 production `HoldingDisclosureAdapter` 应输出：

```text
provider, fund_external_id, instrument_external_id, position,
weight, currency, report_period, filed_at/published_at, acquired_at,
filing_id, source_url, raw_document_sha256, provider_schema_version
```

每一行必须能回到 filing/DocumentVersion；批量数据只是检索与初筛，最终用户点击时仍能回到官方原始披露。中国数据源也应遵守同一 adapter contract。

**不要照搬**：N-PORT 只覆盖美国监管口径，不能作为中国基金真源；月度报告也不等于月度实时公开持仓。它的价值是验证字段、时点和原始申报回链。

**模块接缝**：新增 `app/datasources/base.py` 的 `HoldingDisclosureProvider` 协议与 `Capability` 描述；`app/services/holdings_ingest.py` 做身份解析、报告期/披露日校验、原始文档冻结和行级 provenance；当前 `repositories/instruments.py.add_holding_disclosure()` 增加 `document_version_id / source_span_id / provider_record_id / currency / position`。

## 建议形成的后端模块边界

```text
backend/app/
  api/
    overview.py          # 总览聚合
    cases.py             # case/dossier/graph
    documents.py         # 列表、版本、span、ingest
    reviews.py           # queue + decision
    jobs.py              # 状态、事件、取消
    search.py            # grouped search
    activity.py          # cursor feed
    instruments.py       # company/stock/fund/disclosure reads
  documents/
    locators.py          # SourceLocatorV1 + round-trip
  review/
    proposals.py         # 四类统一 proposal
    decisions.py         # append-only human decisions
  jobs/
    contracts.py         # 与执行器无关
  workflows/             # 达到触发条件后接 Temporal/LangGraph
  events/
    outbox.py            # domain event envelope
    consumers.py         # projection/search/activity
  search/
    service.py
    postgres.py          # P0
    opensearch.py        # P1
  instruments/
    identity.py
  datasources/
    base.py
    docling.py
    openfigi.py
    gildata/             # 现有适配器继续使用
```

最重要的依赖方向是：

```text
API -> application service -> append-only ledger + domain_event（同一事务）
                                  |
                                  +-> projection consumers -> Neo4j / Search / Activity

Parser / LLM / financial providers -> adapters -> Proposal or frozen source
                                                   |
                                                   +-> human ReviewDecision -> formal ledger relation
```

Neo4j、OpenSearch、Temporal/LangGraph、Docling 都是可替换基础设施；领域对象、审核决策、原文版本与 citation manifest 不依赖它们。

## 最小接口清单（用于验证这些借鉴是否真正落地）

| 方法 | 路径 | 关键语义 |
|---|---|---|
| GET | `/api/overview?cutoff=` | case 摘要、任务、变化、活动，全部返回 basis |
| GET | `/api/research-cases` | 列表、更新时间、当前 thesis version |
| GET | `/api/research-cases/{id}/dossier?thesis_id=&cutoff=` | 当前判断、因果链、正反证、缺口、审核历史 |
| GET | `/api/research-cases/{id}/graph?cutoff=&focus=&cursor=` | 连续证据到基金图，带 projection watermark |
| GET | `/api/documents?q=&cutoff=&cursor=` | 冻结版本、解析状态、引用计数 |
| POST | `/api/documents:ingest` | 202 + idempotency key + job id |
| GET | `/api/documents/{version_id}` | 文档元数据、版本链、解析结果 |
| GET | `/api/documents/{version_id}/spans/{span_id}/source` | 可复现定位和授权后的原文访问 |
| GET | `/api/reviews?kind=&case_id=&cursor=` | 通用待审 proposal 队列 |
| POST | `/api/reviews/{proposal_id}/decisions` | append confirmed/modified/rejected；要求 expected version |
| GET | `/api/jobs/{id}` | 状态、step、progress、错误码、重试信息 |
| GET | `/api/jobs/{id}/events?after=` | 可恢复进度事件 |
| GET | `/api/activity?case_id=&after=` | actor/verb/object/target 活动流 |
| GET | `/api/search?q=&cutoff=&types=&cursor=` | 分组搜索；cutoff 与分页快照分离 |
| GET | `/api/funds/{id}/holdings?as_of=` | 披露持仓，不伪装实时；每行回链来源 |
| GET | `/api/instruments/{id}/identifiers` | 内部 ID、外部 alias、mapping 状态和来源 |

## 不应借鉴或暂缓引入

- **不要再引入第二个业务真相库**：XTDB、Neo4j、Docling Graph 的内部存储均不替换 PostgreSQL ledger。
- **不要把 workflow history 当业务审计**：Temporal/LangGraph 只负责运行恢复；正式审核和证据版本独立保存。
- **不要直接把 LLM graph 写成 reviewed graph**：所有机器关系先形成 Proposal；尤其 causal edge 需要独立高门槛。
- **不要先部署 Kafka/OpenSearch/Temporal 再补契约**：先固定 event、job、search、locator、provider 接口；数据量或可靠性触发后再换基础设施。
- **不要把外部 identifier mapping 当事实**：多候选、无匹配、代码复用和份额类别都必须显式处理。
- **不要混淆搜索 PIT 与历史 cutoff**：前者保证一次浏览会话一致，后者决定当时可见的证据集合。

## 参考落地优先级

1. **先补 `SourceLocatorV1 + Proposal/Decision + domain_events`**。这三项决定资料库、审核队列、原文定位、活动流是否可信，也是后续 workflow/search/projection 的共同地基。
2. **再补前端已经声明的六类 API**：overview、dossier、graph、documents、reviews、search；所有读接口统一接受并返回 HistoricalBasis。
3. **把现有同步 AI 管道包进 job contract**，先用数据库 worker；达到触发条件后再接 Temporal/LangGraph。
4. **把 Neo4j 增量投影与搜索索引改为 outbox consumer**；始终保留 ledger 全量 rebuild 检查。
5. **最后扩金融 provider**：先完成 identity mapping、披露 provenance 和 capability probe，再增加数据源数量。

验收不看“接入了多少框架”，只看五条闭环：任意判断可回到冻结原文；AI proposal 不越过人工审核；历史 cutoff 可重复；图/搜索可从 ledger 重建；每条基金持仓可回到特定报告期与原始披露。
