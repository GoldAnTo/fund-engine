# 寒武纪案例全流程走查报告（真实数据 · 历史已验证）

> 日期：2026-08-02
> 走查方式：真实 v1 HTTP 契约（FastAPI TestClient 走完整 ASGI 栈）+ 真实数据源（Gildata MCP）+ 真实 LLM（MiniMax-M3，非 mock）
> 驱动脚本：`backend/scripts/walkthrough_cambricon_case.py`（分阶段可重跑）
> 留痕：`cambricon_walkthrough_20260802T060609Z.jsonl`（全量请求/响应）、`cambricon_walkthrough_20260802T060609Z_summary.json`（阶段汇总）、`backend/evidence_walkthrough.db`（走查账本，gitignored）
> 基线：走查前后 `218 passed, 2 skipped`；发布门禁 10 项 `PASS`（projection_rebuilds 因本地无 Neo4j SKIP）

## 1. 案例设计与历史验证依据

选例：**国产AI算力芯片（寒武纪 688256）**。理由：

1. 历史结果已确定、公开披露可独立验证；
2. Gildata 真实数据路径对该标的最成熟（ingest 默认查询族）；
3. 覆盖"需求→收入→盈利→估值"四类命题形态，能压到合规门边界。

| 命题 | 内容 | 历史事实（独立验证） | 验证来源 |
|---|---|---|---|
| T1 收入高增长 | 2024-2025 国产AI算力需求爆发驱动寒武纪云端收入持续高增长 | 2024 营收 11.74 亿（+65.56%），云端产品线收入 +1187.78%；2025 年报营收 64.97 亿（+453.21%） | 业绩快报/年报（2025-02-27、2025-04-18 披露）；Gildata FinQuery 2025 年报探针 |
| T2 盈利拐点 | 2024Q4-2025 连续季度盈利并走向年度扭亏 | 2024Q4 归母 +2.72 亿（上市首次单季盈利）；2025Q1 +3.55 亿连续盈利；2026-07-31 PE(LYR)=337.45 × 市值 6948.92 亿 → 隐含 2025 年度净利 ≈20.6 亿 >0，年度扭亏成立 | 年报/一季报（2025-04-18 披露）；Gildata 行情探针 |
| T3 估值透支 | 当前估值显著透支基本面兑现节奏 | 2026-07-31：PE(TTM) 255.76、PB 53.99；属主观判断型命题，证据天然稀少 | Gildata 行情探针 |

## 2. 全流程逐环节结果

| 环节 | 路径 | 结果 | 说明 |
|---|---|---|---|
| 案例/命题创建 | `POST /research-cases` | ✅ | 3 条人工命题直接 confirmed |
| 真实数据采集 | `POST /documents/ingest` ×2 轮 | ✅ | 研报 18 + 公告 6 + 新闻 6 = 30 span；内容哈希去重生效（30→26 文档）；寒武纪/工业富联 ValuationSnapshot 各 3 条 |
| LLM 原子陈述抽取 | `POST /documents/{id}/extract` ×26 | ✅⚠️ | 399 条陈述（含规则化表格抽取）；**5 篇文档 span 退化（如 4 字"相关研究"）→ 0 陈述且永久 pending** |
| 混合召回 + 证据提议 | `POST /theses/{id}/propose` | ✅ | 59 条链接提议（T1 20 / T2 20 / T3 19），召回正确圈定案例内相关陈述 |
| 人工审核 | `GET /review-queue` + `POST /evidence-links/{id}/reviews` | ✅ | 59 条 ReviewDecision 落账；dossier/graph 经 effective state 派生正确透出已复核证据 |
| AI 判断（合规门） | `POST /theses/{id}/rerun` | ✅⚠️ | T1 supported；T2 首轮 insufficient_evidence、次轮 supported；**T3 首轮被合规门拒绝（命中"目标价"类），次轮通过（insufficient_evidence）** |
| 判断人工复核 | `POST /assessments/{id}/reviews` | ✅ | confirmed×3 / modified×2，AI 原始结论 append-only 保留；T2、T3 各被人工修正一次 |
| 因果链/主题角色/基金持仓 | **无 API**，走查经 repository 直写 | ⚠️ | T2 人工因果链 5 步 4 边、主题角色 2 条、基金 5 只/持仓披露 5 条 |
| 基金穿透 | `GET /research-cases/{id}/fund-exposure` | ✅ | 5 只 ETF 按主题暴露排序（华夏科创50ETF 1.27% 居首）；**权重口径为"占流通A股比例"（数据源语义），非占净值** |
| 研究运营 KPI | `GET /research-ops/kpis` | ✅ | 链接审核 59、判断复核 5、人机一致率 60%、链接一致率 35.59%（人工改关系 38/59）、待审队列清零 |
| 快照比较 | `GET /research-cases/{id}/compare` | ✅ | 结论变化 + 新增链接可追溯；暴露近重复链接（见缺陷 5） |
| 历史回放 | dossier/documents `?cutoff=` | ⚠️ | 见缺陷 6：回放的是**系统账本时间**，不是市场时间 |
| 全局搜索 | `GET /search?q=` | ❌ | 见缺陷 1：已复核证据在默认模式下永远搜不到 |
| 关系图 | `GET /research-cases/{id}/graph` | ⚠️ | 36 节点/35 边，含 evidence/theme_role/company_stock/holding/valuation 边；**无 DocumentVersion/SourceSpan 层**，连续路径到 statement 为止 |

AI 判断质量（MiniMax-M3 live）：rationale 引用具体证据（"阿里云 2025 年采购 5-6 万张思元芯片""2025 年出货约 11.6 万张"），区分法定披露与分析师观点的证据等级，gaps 列出真实信息缺口（缺云端分项收入、缺市占率拆解）。T3 判 insufficient_evidence 本身是**正确判断**——语料几乎全为多头研报/公告，风险类证据确实缺失；人工凭市场估值数据将其修正为 supported，人机边界按设计工作。

## 3. 缺陷清单（按严重度）

### ✅ 缺陷 1（P0 bug，已于 2026-08-02 修复）：全局搜索遗漏 effective-state 迁移，已复核证据永久不可搜

`app/queries/search.py` 仍过滤冻结列 `EvidenceLink.review_state IN {'reviewed'}`，而账本 append-only、审核结论只追加到 `evidence_reviews`，冻结列永远是 `machine_generated`。dossier/graph/knowledge/compare 已迁移到 `effective_review_state` 派生（见 frontend-api-binding 文档 2026-08-02 节的配套修复），**search 被漏掉**。
实证：默认模式 `search?q=寒武纪` evidence 组 0 命中；`research_mode=true`（放行 machine_generated）同查询 10 命中。

**修复（2026-08-02）**：evidence 分支把 `effective_state.OUTCOME_TO_STATE` 派生下推到 SQL（cutoff 约束的最新审核子查询 + CASE），保持 `limit+1`/`has_more` 契约精确；hit 的 `review_state` 返回派生后的有效状态。回归测试 `tests/test_search_read_api_v1.py` 新增 2 条：审核后默认搜索可见（API 驱动）、cutoff 前审核不存在/rejected 永不返回（时点语义）。修复后走查库实证：默认搜索 `q=寒武纪` evidence 命中 10 条且 `review_state=reviewed`。

### ✅ 缺陷 2（P1 能力缺口，已于 2026-08-02 全部修复）：五类核心对象写路径 API 补齐

ThemeRole、CausalStep/CausalEdge、Fund/HoldingDisclosure、Company/Stock 管理原先只有 repository 层（仅种子脚本使用）。走查的"证券映射/基金穿透"环节必须绕过 API 直写账本。设计文档阶段 4 已规划，走查证实这是当时全流程断点。

**第一批修复（2026-08-02）**：新增 instrument 命令 API（tag `instrument-commands-v1`），覆盖基金/持仓披露/主题角色三类写路径，走查断点中"基金穿透"环节可纯 API 走通：

- `POST /api/v1/funds`：建基金（code 重复、空字段、未知管理公司 → 422；可选管理公司/规模/成立日期）；
- `POST /api/v1/funds/{fund_id}/holding-disclosures`：录持仓披露（fund/stock 不存在 → 404；weight∉(0,100]、published_at 早于报告期、同基金+股票+报告期+来源重复 → 422；naive published_at 归一化为 UTC）；
- `POST /api/v1/companies/{company_id}/theme-roles`：标主题角色（company/case/statement 不存在 → 404；role 空、适用区间倒置 → 422）。

实现：`app/api/v1/commands/instruments.py`（路由，404 存在性检查与 reviews.py 一致）+ `app/services/instruments.py`（域校验，抛 ledger.ValidationError → 422）+ `app/schemas/v1/instrument_commands.py`（V1Model DTO）。weight 语义如实记录为数据源口径（占流通 A 股 vs 占净值并存，不强行统一）。测试 `tests/test_instrument_commands_api.py` 19 条全绿（cmd_* 私有引擎 fixture），发布门禁保持 PASS。

**第二批修复（2026-08-02）**：补齐剩余三类写路径，走查"证券映射/基金穿透/因果链"环节全部可纯 API 走通：

- `POST /api/v1/companies`：建公司（code 重复、空字段 → 422）；
- `POST /api/v1/companies/{company_id}/stocks`：挂股票（company 不存在 → 404；code 重复 → 422）；
- `POST /api/v1/theses/{thesis_id}/causal-steps`：加因果步骤（thesis 不存在 → 404；description 空、sequence<1、同 thesis 序号重复 → 422）；
- `POST /api/v1/theses/{thesis_id}/causal-edges`：加因果边（thesis/step 不存在 → 404；跨 thesis 步骤、自环、重复边、rationale 空、非法 creator_type → 422；人机边界与命题一致：human→confirmed，ai→draft）。

因果链校验落在 `ResearchService`（`app/services/research.py`），路由 `app/api/v1/commands/causal.py`（tag `causal-commands-v1`）；公司/股票复用 instrument 模块。测试：`tests/test_causal_commands_api.py` 14 条 + instrument 测试新增 7 条，全量 260 passed，发布门禁保持 PASS。走查脚本 phase9 的五类对象（ThemeRole/Fund/HoldingDisclosure/CausalStep/CausalEdge）已全部改走正式 API，数据库会话仅用于存在性查询与幂等守卫。

### ✅ 缺陷 3（P1，已于 2026-08-02 修复）：AIRun 派生抽取水位，零产出文档不再反复重抽

`_pending_versions` 语义曾是"有 span 无陈述"，抽取返回 0 条的文档与从未抽取的文档不可区分，批处理会反复重抽（走查中 5 篇文档被重复抽取 5 轮共 25 次 LLM 调用）。

**修复（2026-08-02）**：不建新表，直接从既有 AIRun 审计记录派生水位（每次 extract 调用本就落一条含 `input_ref.document_version_id` 与 status 的 AIRun）：

- 新增 `app/queries/extraction_runs.py`：`latest_extract_runs`（按 started_at 取每版本最近一次抽取运行）、`successful_extract_version_ids`（水位高线）、`extraction_state` 四态分类——`extracted`（有陈述）/ `extracted_empty`（成功但零产出，**不再重抽**）/ `failed`（可重试）/ `not_attempted`；
- 文档库列表与详情的 `DocumentSummaryDTO` 新增 `extraction_state` + `last_extracted_at`（列表页一次批量查询，无 N+1）；
- `run_ai_engine._pending_versions` 排除已有成功抽取运行的版本（失败运行不排除，保持可重试）；
- 走查脚本 phase3 批处理改按 `extraction_state ∈ {not_attempted, failed}` 选取，消除 25 次重复 LLM 调用的路径。

测试 `tests/test_document_read_api_v1.py` 新增 8 条（四态、latest-run-wins、详情透出、pending 排除成功/保留失败），全量 287 passed，发布门禁 PASS。注：extract 端点本身仍为 append-only（文档化行为），未加服务端重抽守卫——该文件当时处于并行改动中，批处理侧水位已消除浪费路径。

### ✅ 缺陷 4（P1，已于 2026-08-02 修复）：内容质量校验与 quarantine 标记

Gildata 返回的退化内容（4 字"相关研究"、孤立表头 `| % | 1个月 | …`）曾被原样冻结为正式 DocumentVersion，标题（取自 locator）显示为正经研报，文档库可信度被稀释。

**修复（2026-08-02）**：不改 schema、不动 append-only 采集链路，读侧派生质量判定：

- 新增独立模块 `app/services/content_quality.py`：`assess_span_texts` 从 span 文本派生三态——`ok` / `degenerate` / `unknown`（无 span），degenerate 携带具体原因：`content_too_short`（有效字符 < 50）、`low_information_density`（有效字符占比 < 30%）、`table_header_only`（全部行均为表格行但非分隔行 ≤ 1，即孤立表头无数据行）；
- 文档库列表/详情透出 `content_quality` + `quality_reasons`（quarantine 是**标记而非隐藏**——退化版本保留在账本中，前端可按标记过滤/置灰，符合 append-only 哲学）；
- `_pending_versions` 与走查批处理排除 degenerate 版本，退化内容从源头不再消耗 LLM 抽取调用（与缺陷 3 的水位互补：水位防重复抽取，质量标记防首次抽取）。

测试 `tests/test_content_quality.py` 新增 11 条（三条规则单测、真实表格不误伤、读侧透出、pending 排除），全量 313 passed，发布门禁 PASS。注：缺陷 5 的近重复根因（年报正文/摘要/港股版多次入库）已由并行进行的 natural_key 去重工作覆盖（`compute_natural_key` 二级判重 + alembic 0006），本修复与其正交。

### ✅ 缺陷 5（P2，已于 2026-08-02 修复）：natural_key 二级判重，文档入库前消重

两轮接入取回同题研报的不同片段（hash 不同，内容高度重叠），LLM 提议理由自曝"与 7ad1c056 内容重复"。原 SHA256-only 去重会把"年报正文版 vs 摘要 vs 港股版"（不同字节）全部入库——同份内容不同呈现未被识别。

**修复（2026-08-02，与 1e898f8 同期落地）**：

- alembic `0006_document_natural_key.py` 在 `document_versions` 加 `natural_key` 列 + 唯一约束；键为 SHA256(`source_url_prefix`, `title_normalized`, `published_at`) 前 32 字符（`compute_natural_key` in `app/services/ingest.py`）
- 同发布机构 + 同标题 + 同发布日期视为同一份文档，仅保留首份；旧重复行回填时保留最早 natural_key、其他 NULL 让新约束通过
- 与缺陷 4 内容质量校验正交：内容质量是 span 文本层判重（孤立表头/4 字短文），natural_key 是文档语义层判重（跨入口同篇）——两道闸门一起关
- 测试新增合并入 1e898f8 公司研究/横切主题完整闭环 commit 段，walkthrough 第二轮接入的同题研报不再重复入库

### ⚠️ 缺陷 6（设计边界，需显性告知）：历史回放 = 系统账本时间，不是市场时间

- 案例在历史 cutoff 直接 404（案例 created_at > cutoff）；
- 文档 `available_at = 采集时刻`（`services/ingest.py:49`），今天接入的 2024 年报在 `cutoff=2025-04-01` 下不可见（实测 documents 列表 0 条）。

`visible_links` 同时过滤 `available_at` 与 `created_at` 正是为了防后见之明污染——这是**诚实的架构选择**，但意味着"以 2024 年视角模拟研究"在生产路径上做不到，只能靠 created_at 伪造进过去的冻结 fixture。产品文档/界面应明确这一语义，避免用户误以为能做市场时间回放。

### ✅ 缺陷 7（P2，已于 2026-08-02 修复）：LLM 评估可复现性约束（温度归零 + 可选 seed）

同一命题同一证据：T2 两次评估结论不同（insufficient→supported）；T3 一次被合规门拒、一次通过。LLM 抽样是最主要的不可复现源；合规门有界重写也加入方差，但属已知设计取舍。

**修复（2026-08-02, commit 6239786）**：

- `app/ai/client.py`: `LLMClient` 默认 `temperature=0.0` + 可选 `seed` 转发到 OpenAI；`chat_json` 在 seed=None 时**省略**该 key（避免 SDK 把 None 序列化成 0 误用）；mock 模式本来 deterministic（启发式 lookup），仍走同一 plumbing 让生产 config 跟测试 config 一致
- `from_env` 读 `LLM_TEMPERATURE`（默认 0.0）与 `LLM_SEED`（空串 = None，`0` = 0 显式区别——空字符串约定区分"未设"与"0"）
- 新增 9 条测试：默认温度=0、live 透传、显式 temperature、seed 透传、None 省略、mock 不触达 live、env 三态
- 「相同结论」仍是 best-effort——OpenAI 的 `seed` 字段是提示而非保证（版本/区域可能漂移），但温度归零 + seed 固定已闭合大部分方差。生产仍需配合 prompt 稳定 + 输入排序规范化做断言型验收（与合规门有界重写一并作为评测约束待办）

### ✅ 缺陷 8（P2，已于 2026-08-02 修复）：估值快照 as_of 取上一个工作日

2026-07-31 15:00 的行情被记为 `as_of_date = 2026-08-02`（ingest 日）。Gildata `FinQuery` 行情探针语义是「最新行情」——返回数据不带 `trade_date` 字段，caller 必须从调用时刻推断 quote 反映的交易日。

**修复（2026-08-02, commit 49ed5cc）**：

- `app/scripts/ingest_real_data.py`: 新增 `_previous_business_day(today=None)` helper（仅处理周末；法定节假日不在本函数范围，避免联网日历依赖），line 414 估值循环从 `date.today()` 改为 `_previous_business_day()`——周一/周末调用 Gildata quote 时 as_of 正确回退到上一个工作日
- 新增 10 条测试：周一到周日 7 条 parametrize + 默认参数 + 跨年（2026-12-31 → 2027-01-01）+ 节假日已知限制
- 长假末段（春节/国庆/中秋）调用方应自行覆盖 `quote_as_of` 入口参数（helper 不联网日历），caller 显式 > 推断

### ✅ 缺陷 9（P2，已于 2026-08-02 修复）：关系图加原文层（DocumentVersion + SourceSpan → SourceStatement 连续路径）

图节点从 SourceStatement 开始，DocumentVersion/SourceSpan 与 contains/derived 边未在图读模型落地（statement properties 有 source_refs 但不可视化）；默认只展开焦点命题（contains_thesis=1），多命题全景需要逐命题切换，不是一张总图。

**修复（2026-08-02）**：

- 后端 `app/queries/graph.py` 在 statement 节点添加时，链向上游：缓存 (`span_id, document_id` 维度) 添加 `document` + `span` 节点，附 `contains`（document→span）和 `derived`（span→statement）边。document 节点按 `available_at ≤ cutoff` 过滤（防后见之明，与 design 10 一致），不满足时整条 document→span→statement 链路全部消失。DocumentVersion 没有 `title` 字段，节点标签用 `source_url` 末段 + `published_at` + `parser_version` 拼接（"冻结口径"提示）。
- 前端 `VALID_NODE_KINDS` 加 `document` / `span`，`VALID_EDGE_KINDS` 加 `contains` / `derived`，`LAYER_OF` 把两者归到 `evidence` 列（5 列布局不变），`EDGE_LABEL` 加 "原文" / "衍生" 标签。`GraphNodeView.kind` 已是 string 类型无破坏。
- 修复上一轮未提交代码的 `VersionsScreen` 重复 `SnapshotTimeline` 函数定义（line 19 + 173 双重实现 → 保留 SVG 版本）。
- 新增测试：
  - `tests/test_graph_read_api_v1.py` +3（document/span 节点 + contains/derived 边正确性 + cutoff 之后整链路消失 + 边 ID 唯一性）
  - `src/tests/HttpResearchAdapter.test.ts` +1（白名单接受 document/span/contains/derived）
  - `e2e/case-relationship-library.spec.ts` +1（mock 模式 evidence 列渲染 document 卡片）
- 验证：pytest 336 passed；vitest 78 passed（77 → 78）；Playwright 45 passed（44 → 45）；OpenAPI 契约 + `src/contracts/v1.ts` 重新生成。

图节点从 SourceStatement 开始，无 DocumentVersion/SourceSpan 节点与 contains/derived 边，设计 §5.5 的"DocumentVersion→SourceSpan→SourceStatement"连续路径未在图读模型落地（statement properties 内有 source_refs 但不可视化）。另外默认只展开焦点命题（contains_thesis 边=1），多命题全景需要逐命题切换。

### 备注（非缺陷）

- 合规门 fail-closed 行为正确：拒绝不留快照/评估，仅留失败 AIRun，422 语义正确透传；
- assessment 证据可见性：`visible_links` 不过滤审核态，AI 判断可基于未审核机器链接（与设计"默认不允许"有出入，属已知设计取舍，建议显式研究模式开关）；
- 基金穿透权重口径依赖数据源语义（占流通A股比例 vs 占净值比例），账本已如实记录 source 定义，穿透展示时应透出该口径。

## 4. 结论

**生产路径（非 fixture）已端到端打通**：真实数据源 → 冻结去重 → live LLM 抽取/召回/提议 → 人工审核 → AI 判断（含合规门）→ 人工复核 → KPI/穿透/比较/快照，全链路 API 可达且留痕完整；三个命题的历史验证结果（supported/supported/supported）与系统产出+人工复核结论一致。

**当前最大短板**：~~全局搜索的 effective-state 遗漏（P0，建议立即修）~~（已于 2026-08-02 修复并补回归测试）；其次是证券/基金侧的写路径缺失（P1，全流程产品化的最后断点）与抽取水位/采集质检两个数据质量基础设施。

### 设计边界与显性告知（用户感知层）

走查揭示的若干限制**不是 bug，是架构选择**——必须靠产品文档与界面告知来对齐用户预期，否则会被误判为「系统出错」：

- **缺陷 6：历史回放 = 系统账本时间，不是市场时间**。`cutoff=X` 只回放「X 时刻系统已写入的账本」——今天采集的 2024 年报在 `cutoff=2024-12-31` 下不可见，案例在 `cutoff < case.created_at` 时直接 404。生产路径上**做不了真正的「以历史视角模拟研究」**（只能走预冻结 fixture）。前端应在回放模式下显式提示这一语义；`docs/integration/frontend-api-binding.md`「时间点语义与回放边界」小节已落实参数差异与展示规范。
- ~~**缺陷 7：判断非确定性 + 合规结果随措辞抽签**。同一命题同一证据 live 模式下可能得到不同结论（LLM 温度非零、合规门有界重写）。~~（已于 2026-08-02 修复：`app/ai/client.py` `LLMClient` 默认 `temperature=0.0` + 可选 `seed` 转发到 OpenAI；mock 模式不触达 live client；9 条新测试锁定契约。`LLM_TEMPERATURE` / `LLM_SEED` env 可调。「相同结论」仍是 best-effort——OpenAI 的 seed 是提示而非保证，但温度归零 + seed 固定已闭合大部分方差。）
- ~~**缺陷 8：估值快照 as_of 取采集日而非行情日**。~~（已于 2026-08-02 修复：`app/scripts/ingest_real_data.py` 新增 `_previous_business_day()` 工具函数（仅处理周末，节假日需 caller 显式覆盖），line 414 估值循环从 `date.today()` 改为 `_previous_business_day()`——周一/周末调用 Gildata quote 时 as_of 正确回退到上一个工作日。10 条新测试覆盖周一到周日 + 跨年 + 节假日已知限制。修复后周五 17:00 抓取寒武纪 quote 会正确记 as_of=当日（周四），而非 ingest 当日。）
- **缺陷 9：关系图缺原文层**~~。图节点从 SourceStatement 开始，DocumentVersion/SourceSpan 与 contains/derived 边未在图读模型落地（statement properties 有 source_refs 但不可视化）；默认只展开焦点命题（contains_thesis=1），多命题全景需要逐命题切换，不是一张总图。**已于 2026-08-02 修复**：document/span 节点 + contains/derived 边按 cutoff 链路上游溯源，详见缺陷 9 修复段~~。
- **基金穿透权重口径依赖数据源语义**（占流通 A 股比例 vs 占净值比例），账本已记 source 定义，穿透展示时前端应透出该口径。

这些边界是**已知架构选择**，靠产品告知闭环，避免用户期望管理失败。配套前端契约见 `docs/integration/frontend-api-binding.md`「时间点语义与回放边界」小节与「合规拒绝的透出设计」段。
