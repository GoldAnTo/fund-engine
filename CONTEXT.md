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
