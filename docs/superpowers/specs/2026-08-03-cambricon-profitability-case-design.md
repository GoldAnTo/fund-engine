# 寒武纪 2025 盈利拐点可复现案例设计

## 1. 目标

在不改变现有产品架构、导航和页面语言的前提下，增加一个能够从空数据库复现、在现有页面完整展示、并能下钻到原始证据的历史研究案例：

> 寒武纪 2025 年是否已经形成可验证的盈利拐点？

案例沿用项目已经存在的链路：

`主题 → ResearchCase → Thesis → DocumentVersion/SourceSpan/SourceStatement → EvidenceLink → EvidenceSnapshot → AIAssessment → ReviewDecision → 结论与关键因素页`

完成后，用户从现有主题/案例入口进入该案例，再进入 `/conclusion/{case_id}`，可以看到当前判断、支持事实、范围限制、反向事实、证伪条件和复现清单。页面数据必须来自真实 HTTP 适配器读取的账本，不使用 `?client=mock`。默认导入不会伪造人工审核：首次展示明确标记为“未经人工复核”，研究员在现有复核流程确认后才形成正式判断。

## 2. 非目标

- 不新增独立演示页或新的一级导航。
- 不重做现有 `ConclusionScreen` 的三栏布局。
- 不增加新的领域实体或数据库迁移。
- 不把案例扩展成投资建议、目标价或基金推荐。
- 不把“盈利为正”写成“需求导致盈利”或“盈利可持续”的因果结论。
- 主命题的可量化覆盖限定为 2025 年四个季度的营业收入、归母、扣非归母净利润、经营现金流净额，不外推到估值、需求或可持续性。
- 不把现场调用聚源作为默认展示的运行前提。
- 不复用现有 `ai_compute` 测试夹具中 `example.test` 来源或未经本次核验的未来数据作为正式案例证据。

## 3. 已核验事实与研究边界

### 3.1 冻结事实

金额单位除特别注明外均为亿元：

| 期间 | 单季度营业收入 | 单季度归母净利润 | 单季度扣非归母净利润 | 单季度经营现金流 |
|---|---:|---:|---:|
| 2024Q4 | 9.8915780017 | 2.7215295265 | -0.0249832631 | — |
| 2025Q1 | 11.1139892680 | 3.5546524104 | 2.7596280395 | -13.9935871285 |
| 2025Q2 | 17.6924454429 | 6.8261732753 | 6.3660404312 | 23.1050903458 |
| 2025Q3 | 17.2678089257 | 5.6656317554 | 5.0632113023 | -9.4045513344 |
| 2025Q4 | 18.8977183502 | 4.5458279456 | 3.5104618038 | -4.6909332530 |

2025 年全年：

- 营业收入 `64.9719619868` 亿元；
- 归母净利润 `20.5922853867` 亿元；
- 扣非归母净利润 `17.6993415768` 亿元；
- 经营现金流净额 `-4.9839813701` 亿元。

聚源 `FinQuery` 返回了 2025Q1、H1、Q1-Q3、FY 累计归母及扣非归母净利润；单季度数由相邻累计值相减得到。官方《2025 年年度报告》第 10 页披露的季度表同时给出单季度营业收入、归母、扣非归母净利润与经营现金流，金额精确到分，与聚源按亿元四舍五入后的归母口径相互独立印证。官方报告来源：

`https://dataclouds.cninfo.com.cn/shgonggao/hsomarket/2026/20260312/05ca784762a7401b9ed371d917e436dc.PDF`

### 3.2 研究判断与审核边界

主命题限定为：

> 寒武纪自 2024Q4 至 2025Q4 连续五个季度单季度归母净利润为正，且 2025 年归母净利润与扣非归母净利润均为正，因此“会计利润口径的盈利拐点已经出现”获得支持。

由冻结数据可重复计算出的研究判断为 `supported`，但 seed 后仍是 `displayed_as_provisional=True` 的草案，不预置 `ReviewDecision`。只有研究员在现有复核流程中确认后，页面才能称为人工正式判断。判断不外推为：

- 国产 AI 算力需求是盈利变化的唯一原因；
- 盈利一定可持续；
- 现金回款质量已经同步改善。

2025 年经营现金流净额为负作为范围警示。需求传导因果与盈利可持续性保留为 `insufficient_evidence` 的证据缺口，不伪装成已经证实的结论。

## 4. 方案选择

### 4.1 采用：真实冻结夹具 + 现有账本装配 + 可选人工刷新

把本次已经核验的聚源响应和官方年报季度表保存为仓库内的冻结来源材料，通过一个案例 seed 使用现有服务写入不可变账本。默认运行完全离线；额外的人工刷新命令读取 `.env` 中的聚源 token，将新返回数据追加为待审核关系。

优点：可重复、可测试、无外部调用额度依赖，且页面走真实数据库和真实 API。代价是新增两份小型冻结材料和两个案例脚本。

### 4.2 未采用：每次启动都现场调用聚源

现场数据新鲜，但会受 token、资源包、调用限额、供应商响应变化影响，无法保证演示与测试复现，也会混淆“已审核快照”和“最新抓取”。

### 4.3 未采用：只在前端 fixture/mock 中增加案例

改动最小，但绕过不可变账本、审核状态、时点查询和真实 HTTP 适配器，不能证明项目流程已经跑通。

## 5. 数据装配设计

### 5.1 文件

新增：

- `backend/app/fixtures/cambricon_profitability_case/juyuan_finquery_2026-08-03.json`
- `backend/app/fixtures/cambricon_profitability_case/cninfo_2025_annual_report_page_10.txt`
- `backend/app/scripts/seed_cambricon_profitability_case.py`
- `backend/app/scripts/refresh_cambricon_profitability_case.py`
- `backend/tests/test_seed_cambricon_profitability_case.py`
- `backend/tests/test_refresh_cambricon_profitability_case.py`
- `backend/tests/test_cambricon_profitability_case_e2e.py`

只有在现有页面无法正确呈现账本数据时，才允许对已有查询或适配器做最小修复，并必须先以失败测试证明缺口。设计预期不修改 `ConclusionScreen.tsx`、路由或 OpenAPI DTO。

### 5.2 冻结材料

聚源 JSON 保存：

- provider、tool、query；
- fetched_at；
- 原始累计期间和精确人民币数值；
- 响应主体；
- 由测试重新计算的 SHA-256。

年报文本保存第 10 页季度主要财务数据表的必要逐字片段、页码、公告日期、官方 URL和精确人民币数值。仓库只保存完成本案例所需的最小节选，不复制整份 PDF。

### 5.3 账本映射

Seed 仅调用现有 `DocumentService`、`ResearchService`、`AssessmentService`、`ThemeService` 和 repositories：

1. 两份冻结材料分别写为 `DocumentVersion`，保留 content hash、source URL、published/available/acquired time、标题和 parser version。
2. 每个可引用数据区块写为 `SourceSpan`；聚源使用明确的查询/结果定位信息，年报使用 `page=10` 和表格行定位。
3. 精确事实写为 `SourceStatement(kind="disclosed_fact")`，不把推导值冒充披露原文。
4. 单季度推导同时记录公式，例如 `2025Q2 = 2025H1 - 2025Q1`；官方季度表作为独立交叉验证来源。
5. `EvidenceLink` 分为：
   - `supports`：连续五季度归母为正、全年归母和扣非归母为正；
   - `contextualizes`：全年经营现金流为负、结论仅适用于会计利润口径；
   - 不把现金流负值错误标成对狭义盈利命题的直接反驳。
6. Seed 创建的 link 保持 `review_state="machine_generated"`，不伪造 `EvidenceReview`。研究员通过已有复核 API 确认后，系统追加真实的 `EvidenceReview`，范围与理由不可为空。
7. Seed 在刷新前冻结一份固定的 `EvidenceSnapshot` 并创建 `AIAssessment(supported)`，保留 `displayed_as_provisional=True`；seed 不预置 `ReviewDecision`。研究员先复核该 snapshot 中的证据关系，再通过已有评估复核 API 追加 `ReviewDecision`。AI 记录和人工记录互不覆盖；后续刷新数据不会倒灌进这份 snapshot。
8. 使用已存在的受控主题标签 `算力国产化`，把案例接到当前主题读取路径。
9. 如现有主题详情要求公司关系，则只加入寒武纪公司、`688256.SH` 股票和一条有来源的 `ThemeRole`；不为了展示补造基金持仓或估值数据。

### 5.4 重复执行

Seed 以案例标题和冻结材料 hash 做前置检查：

- 空库运行时创建完整案例并打印 `case_id` 与页面 URL；
- 完整案例已经存在时返回同一个 `case_id`，不追加重复记录；
- 同名但关键材料或链路不完整时明确失败，不尝试 UPDATE/DELETE 修补不可变记录。

这样既尊重 append-only 约束，也避免“幂等”名义下静默掩盖半成品账本。

## 6. 人工刷新设计

刷新是运维命令，不增加页面按钮或新产品流程：

```bash
cd backend
.venv/bin/python -m app.scripts.refresh_cambricon_profitability_case --case-id <uuid>
```

行为：

1. 从现有环境加载聚源 token；token 缺失或供应商失败时非零退出，不影响冻结案例。
2. 使用固定、可审计的 `FinQuery` 查询集拉取累计归母与扣非归母净利润。
3. 原始响应按新 hash 追加为 `DocumentVersion`；相同响应由内容寻址去重。
4. 新事实只追加为 `SourceStatement` 和 `EvidenceLink(review_state="machine_generated")`。
5. 不创建新的正式 snapshot、assessment 或 review，不改变现有结论。
6. 输出新增、重复、待审核数量以及复核中心入口。

Investoday 当前账户返回“无可用资源包”，因此不作为本案例的成功依赖；保留在运行说明的已知限制中，不用虚构 fallback 数据。

## 7. 现有页面中的展示路径

不修改页面结构。验收路径为：

1. `/themes` 中出现由现有读模型生成的相关主题；
2. 进入主题后能够看到“寒武纪 2025 盈利拐点”案例和“AI 待复核”状态；完成现有复核动作后变为正式支持状态；
3. 进入现有案例工作台，能够看到命题、证据关系、审核状态和证伪条件；
4. 点击现有“结论与关键因素”进入 `/conclusion/{case_id}`；
5. 页面显示：
   - 复核前显示「AI 临时标记」+「AI 草案（临时标记）：…」+ reviewer=— 的等价表述（即「支持草案 · 未经人工复核」的语义），复核后显示正式支持结论、人工复核标签和真实人工复核元数据；具体字面沿用现有 ConclusionScreen 渲染，不为该案例新增文案或按钮；
   - 连续五季度盈利的精确事实；
   - 聚源与年报两条独立来源；
   - 经营现金流为负的范围警示；
   - “需求因果、可持续性证据不足”的明确边界；
   - snapshot、document hash、页码/定位和复现信息；
6. 证据原文可通过现有下钻入口检查。

## 8. 测试与验收

### 8.1 单元/数据测试

- 冻结材料 hash 稳定，精确值未被浮点截断；金额计算使用 `Decimal`。
- 2025Q2/Q3/Q4 单季度值等于累计值之差。
- 四个季度相加与年报全年营业收入、归母、扣非、经营现金流完全一致（即 §3.1 表与本节一致；seed 在账本内补一条 2025_revenue SourceStatement 与对应 supports 链接）。
- 2024Q4 至 2025Q4 五个单季度归母净利润均大于零。
- 两份来源都可回溯到非空 `SourceSpan.verbatim_text` 和 locator。
- Seed 与刷新产生的 link 初始都没有人工 review；seed 的 snapshot 成员集合固定，刷新项不能倒灌并改变它。
- Seed 第二次执行不增加账本行数。

### 8.2 API 集成测试

在临时 SQLite 数据库运行 seed 后，通过真实 FastAPI `TestClient` 验证：

- `/api/v1/research-cases` 可找到案例；
- `/api/v1/themes` 与主题详情能追踪到案例；
- `/api/v1/research-cases/{id}/dossier` 暴露正式与待审核边界；
- `/api/v1/research-cases/{id}/conclusion` 在复核前返回 `supported` 草案、`ai_provisional=true`、空 reviewer、证据引用、范围警示和 reproduction manifest；
- 通过现有证据与评估复核 API 后，同一结论接口返回真实 reviewer 和正式支持状态，原草案仍保留；
- 早于证据 `available_at` 的 cutoff 看不到未来证据；
- 人工刷新后，默认冻结结论的 snapshot、判断与审核状态保持不变，研究模式/复核队列能看到待审核项。

### 8.3 页面验收

- 启动真实后端与前端，不使用 `?client=mock`；
- 按现有导航进入案例和结论页；
- 页面无控制台错误、无空白核心区、无 mock 标识；
- 页面关键数字与冻结材料一致；
- 截图记录完整结论页和至少一次证据下钻。

### 8.4 验证命令

```bash
cd backend
.venv/bin/pytest tests/test_seed_cambricon_profitability_case.py \
  tests/test_refresh_cambricon_profitability_case.py \
  tests/test_cambricon_profitability_case_e2e.py \
  tests/test_conclusion_read_api_v1.py \
  tests/test_time_travel.py \
  tests/test_release_gate.py -q

cd ../frontend
npm test -- --run src/tests/ConclusionScreen.test.tsx src/tests/HttpResearchAdapter.test.ts
npm run build
```

最后再执行全量 backend 与 frontend 测试；外部聚源刷新属于可选 live 验收，不能替代离线验收。

## 9. 完成定义

只有同时满足以下条件才算完成：

- 一条命令可从空数据库生成该真实案例；
- 再次执行不会重复写入；
- 现有 API 和页面读取的都是账本数据；
- AI 草案、人工复核前后状态、范围警示与证据缺口边界清楚，且没有预置虚构的人工审核；
- 每个页面展示事实可定位到冻结材料；
- 外部刷新失败不破坏默认展示，刷新成功也不自动改变正式结论；
- 聚焦测试、构建、全量测试和真实浏览器验收均通过。
