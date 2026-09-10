# Fund Engine Research Context

Fund Engine is an industry-research evidence library that turns source material into auditable thesis assessments, then links those assessments to companies, stocks, funds, and dated holdings disclosures.

## Research

**ResearchCase**:
A persistent research dossier organized around one industry topic. It contains multiple versioned theses, their evidence, assessments, and review history.
_Avoid_: News page, topic feed, one-off report

**Thesis**:
A testable, falsifiable, and time-bounded proposition within a ResearchCase.
_Avoid_: Theme, conclusion, recommendation

**AIAssessment**:
An immutable provisional AI judgment about a Thesis based on a frozen evidence snapshot. Its result is `supported`, `contradicted`, or `insufficient_evidence`, and it remains visibly unreviewed until a human decision exists.
_Avoid_: Final conclusion, confidence score

**ReviewDecision**:
An immutable human decision that confirms, modifies, or rejects a Proposal or an AIAssessment and records the reason without replacing the machine output.
_Avoid_: Edit, approval flag, mutable review state

**Proposal**:
An immutable machine or human suggestion to create a SourceStatement, EvidenceLink, CausalEdge, EntityAlignment, or AIAssessment. A Proposal is not a formal research relationship until a ReviewDecision publishes a reviewed version.
_Avoid_: Evidence, approved relation, automatic fact

**HistoricalBasis**:
The explicit cutoff and ledger/projection watermarks used to answer what information was visible at a point in time across evidence, graph, search, valuation, and holding disclosures.
_Avoid_: Latest state, search snapshot

## Sources and Evidence

**DocumentVersion**:
An immutable version of a source document identified by its content hash, publication time, and acquisition metadata.
_Avoid_: Document, latest file

**SourceSpan**:
An exact, reproducible location inside a DocumentVersion, such as a page region, paragraph, table cell, or character range.
_Avoid_: Citation URL, excerpt without location

**SourceStatement**:
One atomic statement explicitly made by a source, typed as a disclosed fact, management attribution, forecast, or research opinion. It records what the source says, not whether the statement is objectively true.
_Avoid_: Fact, evidence, Claim

**EvidenceLink**:
A versioned argument that explains why a SourceStatement supports, contradicts, or contextualizes a Thesis for a defined time and scope.
_Avoid_: Automatic SUPPORTS edge, semantic similarity

**EvidenceSnapshot**:
The frozen set of DocumentVersions, SourceStatements, and EvidenceLinks visible to one AIAssessment at its cutoff time.
_Avoid_: Current database state

**CausalEdge**:
A proposed transmission relationship between two domain factors with its own evidence requirements. A positive company result or a source attribution does not by itself establish a CausalEdge.
_Avoid_: Correlation, supply-chain adjacency

## Investment Expression

**ThemeRole**:
A company's explicit role in an industry theme or causal chain, including its scope, applicable period, and supporting source.
_Avoid_: Theme membership tag

**HoldingDisclosure**:
A fund's disclosed position in a stock, preserving both the holding report period and the publication date.
_Avoid_: Current holding, real-time position

**Expression**:
A stock or fund used to express exposure to a supported research idea after considering valuation, exposure, freshness, and constraints. It is not a recommendation by itself.
_Avoid_: Pick, recommendation, portfolio

## Implementation Status (2026-08-02)

Four hardening rounds landed on top of the MVP (commits `5963ffb`, `631b9c2`, `95cf64f`, `c472e36`):

**Hybrid recall (P0).** `RecallService` now fuses two legs with RRF: BM25 over coarse tokens plus a local, deterministic char-n-gram TF-IDF dense leg (recovers sub-word matches the whole-CJK-run tokenizer makes BM25-invisible); the lexical signal is fused as a third leg. `mode="bm25"` remains as the evaluation baseline. `backend/scripts/eval_recall_ab.py` replays the frozen AI-compute slice against the human-curated gold links: overall recall@20 0.7333 → 1.0000 (4 gold statements recovered, 0 lost), with a hybrid-below-baseline regression guard. A real embedding backend can replace `tfidf_rank` behind the same contract.

**Second gold case + real PDF path (P1).** New frozen case 锂电储能链 (`seed_storage_chain_case.py`): 6 fixtures — including the first real binary PDF (`06_sungrow_annual_summary.pdf`) — 15 statements, 15 links, 3 theses with human reviews, fund penetration, human causal chain. `app/services/pdf_text.py` parses PDF text layers into reproducible spans (CJK soft-wrap rejoining, table-block line preservation, fail-closed on text-less PDFs; documents stamped `parser_version=pypdf-v1`). The dataset manifest is now v2 (per-case hash sets attributed by `source_url` prefix); the release gate runs 10 checks including `pdf_fixture_parse_gold`.

**Compliance rewrite loop (P2).** The three-action compliance contract is live: REFUSE-category hits refuse immediately and never reach the rewrite stage; REWRITE-category hits (target price / return promise) get exactly one LLM rewrite attempt (`rewrite-v1` prompt), the result is re-evaluated through the same gate, and any residual hit refuses the whole run. Repaired assessments record `rewritten_for_compliance` on the AIRun. 422 still signals a refused rerun to the frontend.

**Research-ops KPIs (P3).** `GET /api/v1/research-ops/kpis?case_id=&as_of=` derives management metrics from the ledger only: review throughput (with pending queue via effective review state), human-AI agreement (assessment- and link-level; null when no data), and judgment latency (evidence→assessment, assessment→first-review, in days). Supports point-in-time replay via `as_of`.

**Third gold case (P4).** New frozen case 半导体设备国产化 (`seed_semiconductor_case.py`): 5 text fixtures (order announcement, annual/quarterly excerpts, broker research, industry tracker), 23 spans, 18 statements, 18 links, 3 theses with human reviews, fund penetration, human causal chain. It deliberately covers assessment shapes the first two cases lack: T2 is "demand proven but margin repair unproven" (insufficient_evidence), and T3 is a policy-constraint falsification (contradicted: litho localization <5% + export-control delivery disruption vs. sector valuation support). The dataset manifest now lists three cases; the release gate seeds all three and stays green.

**Quality posture:** 218 backend tests (+24 across the four rounds), release gate 10 checks green via `docs/evaluation/reproduce.sh`, frontend contract regenerated after the KPI endpoint.

**Verification stack + CI (2026-08-02, commits `2b07cda`–`9992940`).** Both tiers are now enforced by GitHub Actions on every push/PR touching the relevant tree:

- `backend-ci` (`.github/workflows/backend.yml`): pytest on sqlite (218 passed; `pg_only`/`neo4j_only` auto-skip without env vars) plus the 10-check release gate as its own job (`projection_rebuilds` skips without `NEO4J_URL`, never fails the gate).
- `frontend-ci` (`.github/workflows/frontend.yml`): `tsc --noEmit` + 62 vitest tests, and a Playwright job running 32 e2e specs against the dev server in mock mode (deterministic; bundled Chromium on ubuntu, `PW_BROWSER_CHANNEL=chrome` exists only as a macOS 12 local fallback).
- The e2e suite was rewritten for the PrototypeShell (theme-first) app shell — the 13 legacy specs asserted a retired UI. New coverage: shell navigation/search, theme → workbench flow, case/relationship/library/review/versions screens, data-center research-ops section, legacy-route cutoff banners, and a review-decision **write loop** (queue shrinks, audit link reaches snapshot versions) that runs only under `?client=mock`, a main.tsx test hook guaranteeing zero API calls to a live backend. All other specs are read-only and mode-agnostic (mock or live backend).
- Mock fidelity fix: `MockResearchAdapter.search` now filters by query and returns an honest empty state, matching live-backend semantics (caught by the mock-mode e2e run).

**公司研究 / 主题研究（横切主题）落地（2026-08-02，spec `2026-08-02-theme-company-research.md`）**。两个原本是 `NotImplementedPage` 的导航入口接上真实读写闭环：

**后端（Plan A + Plan B）**。新增 `app/api/v1/companies.py` + `app/api/v1/themes.py` 读路由与 `app/api/v1/commands/themes.py` 主题标签命令；公司/股票/估值写路径（`companies` / `companies/{id}/stocks` / `stocks/{id}/valuation-snapshots`）与主题标签受控命令（`PATCH /research-cases/{case_id}/theme-tags`）沿用既有命令侧 `InstrumentService` + `ResearchService` 域校验模式，标签受控词汇为代码内 `frozenset`。主题身份采用 append-only 事件表 `case_theme_tag_events`（PG 迁移 0007 + 同步 trigger，SQLite 测试走 `Base.metadata.create_all`），由事件折叠派生有效标签——账本「只追加、可审计」原则一致，标签变更天然留痕。读模型全部走 `HistoricalBasis` cutoff：`GET /companies` 支持 q + 游标分页；`GET /companies/{id}` 组装五段（identity / theme_roles / related_theses / valuations / fund_holders），每条 ThemeRole 携带 statement/span 回链，关联命题分离承载 AI 草案（`aiProvisional`）与人工复核（`reviewOutcome`）；`GET /themes` 聚合标签维度， `GET /themes/{tag}` 拼接 `cases` / `company_roles` / `fund_exposure` 三大段与 `derived_from` 引用列表（case_ids / thesis_ids / theme_role_ids / disclosure_ids），保证每个数字可展开还原。新增测试 39 条（`test_company_read_api_v1.py` 23 + `test_theme_read_api_v1.py` 14 + `test_theme_tags_command_api.py` 7 + instrument +15），全量 314 passed，发布门禁 9 PASS / 1 SKIP（无 Neo4j 仍保持）不变。

**前端（Plan A + Plan B）**。新增 `CompanyListPage` / `CompanyDossierPage` / `TopicListPage` / `TopicViewPage`（均位于 `src/pages/prototype/`，替换两条 `NotImplementedPage` 路由）——页面只依赖 `ResearchClient.listCompanies / getCompanyDossier / listThemes / getThemeView`，领域类型 `CompanyListItem / CompanyDossierView / TopicListItem / TopicView`（`Topic` 前缀避免与案例中心「主题」混淆）已在 `domain/prototypeTypes.ts` 落定，`HttpResearchAdapter` 完成 v1 DTO 映射，`MockResearchAdapter` 提供典型/空/历史回放场景。AI 草案与人工复核在卡片上分两行 `StatusBadge` 渲染（`ai` 琥珀 + `reviewed` 深绿，颜色+文字双编码）。`PaperCard` 透传 `data-*` HTML 属性，便于 e2e 锚定。新增 vitest 15 条（`HttpResearchAdapter` 双向映射 + `MockResearchAdapter` 边界），Playwright e2e 7 条（companies-topics.spec.ts 覆盖列表/档案/历史回放），tsc 0 错误，vitest 77 passed（62 → 77），e2e 44 测试 43 passed / 1 skipped（macOS 12 + `PW_BROWSER_CHANNEL=chrome` 路径）。`docs/integration/frontend-api-binding.md` 的屏 12/13（公司）与屏 14/15（横切主题）补齐，前端契约 `openapi.json` + `src/contracts/v1.ts` 重新生成。

**P2 缺陷 9 修复（2026-08-02）— 图谱加原文层**。走查报告里 P2 缺陷 9「关系图缺原文层」落地：

- 后端 `app/queries/graph.py` 在 statement 节点添加时链向上游：缓存以 (`span_id`, `document_id`) 维度添加 `document` + `span` 节点，附 `contains`（document→span）和 `derived`（span→statement）边。`document` 节点按 `available_at ≤ cutoff` 过滤（防后见之明，与 design 10 一致），不满足时整条 document→span→statement 链路全部消失。`DocumentVersion` 没有 `title` 字段，节点标签用 `source_url` 末段 + `published_at` + `parser_version` 拼接作为「冻结口径」提示。
- 前端 `VALID_NODE_KINDS` 加 `document` / `span`，`VALID_EDGE_KINDS` 加 `contains` / `derived`，`LAYER_OF` 把两者归到 `evidence` 列（5 列布局不变），`EDGE_LABEL` 加「原文 / 衍生」标签。修复上一轮未提交代码的 `VersionsScreen` 重复 `SnapshotTimeline` 函数定义（line 19 + 173 双重实现，保留 SVG 版本）。
- 新增测试：后端 `tests/test_graph_read_api_v1.py` +3（document/span 节点 + contains/derived 边正确性 + cutoff 之后整链路消失 + 边 ID 唯一性）；前端 `src/tests/HttpResearchAdapter.test.ts` +1（白名单接受 document/span/contains/derived）；e2e `case-relationship-library.spec.ts` +1（mock 模式 evidence 列渲染 document 卡片）。
- 验证：pytest 336 passed（314 → 336）；vitest 78 passed（77 → 78）；Playwright 45 passed（43 → 45）；OpenAPI 契约 + `src/contracts/v1.ts` 重新生成。走查报告 9 项 P2 缺陷中缺陷 9 已标绿，剩缺陷 5/7/8。

**后端硬化收尾（2026-08-02，commit 待 push）— 走查 P2 缺陷 8 复核 + 实体写规范化 + 两阶段主题标签 + audit_log + 时点一致性**。Spec 7 验收落地之外剩下的几条「项目规范与流程控制」要求一次性补齐：

- **走查 P2 缺陷 8 复核**。走查报告记录的 "估值 as_of 偏小一档" 在 49ed5cc `fix: 估值快照 as_of 取上一个工作日而非 ingest 当日` 已修，本轮检查 `_previous_business_day()` 实现 + `fetch_quote` 返回 `trade_date` 字段链路一致。`InstrumentService._today_utc()` 提供给 service 层做时点校验使用，保留 ingest 当日 UTC 基准。

- **实体写路径 status code 规范化**。重复 code（Company / Stock / Fund / HoldingDisclosure / ValuationSnapshot）从 422 升为 409 Conflict：domain 层 `app.models.ledger.ConflictError` 与 HTTP 层 `app.errors.ConflictError` 分离；`translate_validation` 统一把两个 domain error 翻译成对应 HTTP envelope；`main.py` 加 `conflict_error_handler` 注入 `409 conflict` envelope（结构与 `validation_failed` / `not_found` / `upstream_unavailable` 一致：都带 `request_id`）。这条对前端契约的实质变化是：**422 现在只表示「请求体本身畸形」，409 表示「请求合法但与账本已有记录冲突」**，区分让客户端可以分别给出「修 body」和「fetch 已有 record」的两种处理路径。422 的语义边界由 `as_of_date > 今天`、`report_period > 今天`、`metric_value 非有限` 等「无法合法构造」守住，409 守住「well-formed 但撞已有」。

- **PATCH theme-tags 两阶段**。`case_theme_tag_events` 加 `proposed_by` / `status` / `proposal_id` 三列（PG 迁移 0008 + 同步 trigger），`effective_tags()` 只折叠 `status='confirmed'` 事件——pending 事件留在账本里作为「AI 提议的证据」但不影响有效标签集。流程：
  - AI 调 `PATCH /research-cases/{id}/theme-tags {tags, proposed_by: "ai"}`：服务生成 `proposal_id` UUID，所有 diff 事件以 `proposed_by="ai", status="pending", proposal_id=...` 入账，**有效集不变**，响应体回传 `proposal_id`。
  - 人工调同一接口 `{tags, proposed_by: "human"}`：服务计算当前 pending 提案的「假如确认后的有效集」，若与本次 desired **完全相等**则把 pending 事件对应的 confirmed 双胞胎 append 到账本（账本不可变，不能直接 UPDATE 原始事件），响应回 `promoted_proposal_id`；否则按差异 append `proposed_by="human", status="confirmed"` 事件（直接落账）。
  - 这意味着：human PATCH 与 AI 提议**desired 一致**才会「确认」；不一致就是「我自己的主张」，pending 提案保留待办。匹配规则是集合精确相等，不是 token-level diff——避免「悄悄 auto-promote 不同集合」破坏两阶段语义。
  - 旧 default 行为（`proposed_by="human"`, append `status="confirmed"` 事件）保留：所有 11 条 theme_tags 测试更新，新加 5 条（human 走 default / ai 走 pending / 端到端 promote / human 不同集合不 promote / 无提案 human 直接写）+ 1 条校验非法 `proposed_by="robot"` 仍 422。

- **audit_log 表 + 写路径接入**。新建 `audit_logs` 表（PG 迁移 0008，append-only）：`id / actor / action / entity_type / entity_id / payload(json) / result(success|failed) / error_message / request_id / created_at`。命令侧 `audit_command(db, request, *, action, entity_type, payload, fn, args, kwargs)` 装饰 `translate_validation` 的 service 调用：成功 → `result="success"` + 从返回值 `.id` 抓 `entity_id`；失败（捕获全部异常包括 422/404/409）→ `result="failed"` + `error_message` + `entity_id=None`（写一半尚未拿到），审计行不阻塞原 HTTP 响应。actor 从 `X-Actor` 请求头读（生产环境接 auth；现默认 `human:anonymous`）。`audit_log` 失败本身用 best-effort 兜底：审计写入失败时 `db.rollback()` 静默吞掉，**绝不掩盖原响应错误**——监控可以靠「成功响应但缺 audit row」发现审计缺漏。所有 7 条命令端点（companies / stocks / funds / holding-disclosures / valuation-snapshots / theme-roles / theme-tags）已接入。`audit_logs` 同样进 `IMMUTABLE_TABLES` 走 PG 触发器，防御绕过 ORM 的 DELETE/UPDATE。

- **时点一致性 services 层校验**。`InstrumentService` 加 `_today_utc()` 工具：
  - `add_valuation_snapshot` 拒绝 `as_of_date > today`（422，畸形请求，因为未来行情无法诚实获取；这条先于 uniqueness check 跑，所以「新鲜的未来日期」也是 422 不是 409）。
  - `add_holding_disclosure` 拒绝 `report_period > today`（422，未来季报未公布）。
  - `add_valuation_snapshot` 的 existing 重复仍然 409（同一股票同一 metric 同一 as_of 同一 source 已有）。
  切到历史 cutoff 时不允许写入未来数据这条已经由「今天」守住，case 级 evidence_cutoff 的更细粒度隔离在读路径 `cutoff` 过滤上由 `HistoricalBasis` 实现。

- **测试与契约**。后端 +24（已有 336 → 347 = 347 passed / 2 skipped）；新加 6 条 409 envelope + audit_log + 时点 + 422 vs 409 区分测试，5 条两阶段主题标签测试。OpenAPI 契约与 `src/contracts/v1.ts` 需要随 409 envelope + 主题标签响应字段（`proposal_id` / `promoted_proposal_id`）重新生成，文档已就位。
