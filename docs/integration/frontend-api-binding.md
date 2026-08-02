# 前端 9+2 屏 × v1 API 对接清单

> 目的：前端从 `prototypeFixture` mock 切到真实 v1 API 的逐页映射。
> 状态分三档：✅ 已就绪（端点存在，直接绑）/ 🔧 需适配（端点存在但字段要转换）/ ❌ 无后端（保持 mock 或补端点，见文末缺口清单）。
>
> **切换进度（2026-08-02 全部完成）**：屏 1/2/3/4/5/6/7/8/9/10/11 已切真实 API 并逐屏真实联调通过
> （临时 SQLite 种子库 + uvicorn 直连，含屏2 createCase 与屏6/7 审核写路径 round-trip）。
> 「任务队列 / 活动流 / Provider 查询计划」按缺口清单明确不建，页面已标注「示例 · 非目标范围」。
>
> **配套后端修复（2026-08-02）**：读模型（dossier/graph/knowledge/compare）原先只读
> `EvidenceLink.review_state` 冻结列，而账本全部表 append-only、人工审核结果只追加在
> `evidence_reviews` ——导致人工确认的链接永远进不了已复核视图。现统一经
> `app/queries/effective_state.py` 由最新审核结论派生有效状态（confirmed→reviewed），
> 屏6 审核 → 屏4/5/7 已复核透出 的链路闭合。
>
> 后端契约统一约定：
> - 所有 id 为 UUID 字符串；日期 `YYYY-MM-DD`，日期时间 ISO 8601 带时区
> - 错误统一走 `{schema_version:"v1", error:{code,message,request_id}}` envelope
> - 时间点语义：`cutoff`（datetime）控制账本可见性；`as_of`（date）控制披露可见性
> - 枚举：conclusion ∈ `supported|contradicted|insufficient_evidence`；
>   role ∈ `supports|contradicts|contextualizes`；
>   review outcome ∈ `confirmed|rejected|needs_more_evidence`；
>   relation ∈ `supports|contradicts|contextualizes|evidence_gap`；
>   thesis.review_state ∈ `draft|confirmed`
>
> ### 时间点语义与回放边界（缺陷 6 显性告知）
>
> `cutoff` 与 `as_of` 控制不同轴的可见性，**不**互相替代：
>
> | 参数 | 类型 | 控制的可见性 | 含义 |
> |---|---|---|---|
> | `cutoff` | datetime (UTC) | 账本写入时间 | 只返回 `created_at <= cutoff` 的对象（且 `available_at <= cutoff` 的 evidence） |
> | `as_of` | date | 披露/数据时间 | 只返回数据本身的 `as_of_date <= as_of` 快照（估值、行情、披露） |
>
> **关键告知：`cutoff` 回放 ≠ 市场时间模拟**。
>
> `cutoff=X` 表示「系统时间 X 那一时刻，研究员**已经看见的**账本内容」——案例、文档、链接的可见性按系统写入时间过滤。**不**等于「X 那个市场交易日研究员能看见的市场」：今天采集的 2024 年报在 `cutoff=2024-12-31` 下不可见（`available_at > cutoff`），即使市场在 2024 年已披露。案例在 `cutoff < case.created_at` 时直接 404 也是同一约束。
>
> 这是**诚实的架构选择**——`visible_links` 同时过滤 `available_at` 与 `created_at` 正是为了防后见之明污染；账本不可变约束下无法用 `created_at` 伪造市场时间。生产路径上**做不了真正的「以历史视角模拟研究」**，只能靠预冻结 fixture 走查（见走查报告 `2026-08-02-cambricon-full-pipeline-walkthrough.md` 缺陷 6）。
>
> **前端展示建议**：
>
> - dossier / documents / graph 接受 `cutoff` 时，响应应回显 `cutoff` + 提示文本（「回放下不显示 cutoff 之后采集的文档/已写入账本」）；
> - 列表分页的 `total` 应区分 `cutoff_visible_total` 与 `ledger_total`，避免 cutoff 过滤后被误显为「全量」；
> - `cutoff < case.created_at` 时案例 404 是**预期行为**，不是 bug——前端在新建案例后不要立即用过去 cutoff 查询同一案例。

---

## 1. OverviewScreen（研究总览）— `buildWorkspaceOverview()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 案件头/核心结论 | `GET /api/v1/research-cases/{id}/dossier`（assessment + review） | 🔧 |
| 案件列表 | `GET /api/v1/research-cases`（cursor 分页） | ✅ |
| 全局统计 totals | `GET /api/v1/overview`（现有 totals） | 🔧 |
| 关键变化/研究框架 | dossier evidence + gaps 拼接 | 🔧 |
| 任务队列/活动流 | 无后端 | ❌ 保持 mock，建议明确划出目标范围 |

## 2. NewResearchScreen（新建研究）— `buildNewResearchView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 提交（确认命题并继续） | `POST /api/v1/research-cases`，body 见 `CreateCaseRequest` | ✅ |
| 初始命题字段 | ThesisInput：`statement/title/observation_start·end/support_condition/falsification_condition/next_verification_event/creator_type`；AI 草案自动落 `review_state=draft` | ✅ |
| 已有资产汇总（NewResearchAssetSummary） | 可由 `GET /api/v1/documents` + dossier 拼 | 🔧 |
| 研究计划预览（Provider 查询计划） | 无后端 | ❌ mock |

## 3. ResearchPlanScreen（研究计划）— `buildResearchPlanView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 证据缺口（PlanGap） | `GET /api/v1/research-cases/{id}/gaps` | ✅ |
| 已有资料与数据（PlanAsset） | `GET /api/v1/documents`（冻结版本列表） | 🔧 |
| 待审核结果（PlanPendingResult） | `GET /api/v1/review-queue?case_id=` | 🔧 |
| Provider 查询计划（PlanProviderQuery） | 无后端（需新实体，勿拍脑袋建表） | ❌ mock |
| Provider 运行记录（PlanProviderRun） | `GET /api/v1/provider-runs?kind=&limit=` | ✅ |

## 4. CaseWorkbenchScreen（行业案例）— `buildCaseWorkbenchView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 当前判断/正式结论 + 审核状态 | dossier（assessment.provisional + latest review） | ✅ |
| Thesis 行（支持/反证计数） | dossier evidence records（按 role 分组） | ✅ |
| 反证（CaseWorkbenchRebuttal） | dossier 中 `role=contradicts` 的记录 | ✅ |
| 因素行/来源行 | dossier statements + spans | 🔧 |
| AI rerun 按钮 | `POST /api/v1/theses/{id}/rerun` | ✅ |

## 5. RelationshipCanvasScreen（五层画布）— `buildRelationshipGraphView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 证据→命题→因果链→公司层 | `GET /api/v1/research-cases/{id}/graph?depth=&limit=` | ✅ |
| 基金层（持仓披露节点） | `GET /api/v1/research-cases/{id}/fund-exposure?as_of=` | 🔧 叠加到 graph 结果 |
| 时点切换 | graph 的 `cutoff` 参数 | ✅ |

## 6. ReviewWorkbenchScreen（审核工作区）— `REVIEW_QUEUE`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 待办队列（冻结原文 vs AI 提议） | `GET /api/v1/review-queue?case_id=&limit=` | ✅ |
| 四要素提交（确认写入） | `POST /api/v1/evidence-links/{id}/reviews`，`outcome=confirmed` + `relation`（必填，不许 `evidence_gap`）+ `factor_role/scope_boundary/reason/reviewer` | ✅ |
| 驳回 / 要求补充证据 | 同上，`outcome=rejected` / `needs_more_evidence`（relation 可空或 `evidence_gap`） | ✅ |
| 评估级审核 | `POST /api/v1/assessments/{id}/reviews` | ✅ |
| 提交后出队 | 已审链接自动从队列消失（append-only，无需刷新 hack） | ✅ |

## 7. LibraryScreen（资料与知识）— `buildLibraryView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 不可变来源层（DocumentVersion→SourceSpan） | `GET /api/v1/documents` + `GET /api/v1/documents/{id}`（含 citations） | ✅ |
| 已复核知识层（SourceStatement→EvidenceLink） | `GET /api/v1/knowledge?case_id=&review_state=` | ✅ |
| AI 待审核提议（隔离区） | `GET /api/v1/review-queue` | 🔧 |

## 8. DataCenterScreen（数据中心）— `buildDataCenterView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 指标目录（DataCatalogItem） | `GET /api/v1/metrics/catalog?stock_id=&metric_name=` | ✅ |
| 时点序列（DataSeriesPoint） | `GET /api/v1/metrics/series?stock_id=&metric_name=` | ✅ |
| 修订对照（DataRevisionComparison） | series 多点对比即可（旧值 vs 新值） | 🔧 |
| Provider 运行记录 | `GET /api/v1/provider-runs?kind=&limit=` | ✅ |

## 9. VersionsScreen（版本比较）— `buildVersionsView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 双栏对比（正式结论/文档/已审关系/因素角色/缺口变化） | `GET /api/v1/research-cases/{id}/compare?base=&compare=` | ✅ |
| 快照列表（VersionRecordRow） | `GET /api/v1/research-cases/{id}/snapshots` | ✅ |
| AI RERUN 区 | `POST /api/v1/theses/{id}/rerun` 后重新 compare | ✅ |

## 10/11. ThemeIndexScreen / ThemeWorkbenchScreen（主题入口/工作台）

| 数据块 | 来源 | 状态 |
|---|---|---|
| 主题列表（ThemeStatus） | `GET /api/v1/research-cases`（title/industry_topic 映射主题） | 🔧 |
| 主题假设（ThemeHypothesis） | dossier thesis + 新增强字段（观察期/支持/反证条件已在 v1 DTO 之外，需 dossier 透出，见 G4） | 🔧 |
| 正反证据（ThemeClaim） | dossier evidence records | ✅ |
| 关联股票（ThemeStock，带估值） | fund-exposure positions（含 pe_ttm/pb） | ✅ |
| 命中基金（ThemeFund） | fund-exposure funds（theme_exposure 排序） | ✅ |
| 基金反穿 | `GET /api/v1/funds/{id}/composition` | ✅ |

## 12. CompanyListPage（公司研究 · 列表）— `listCompanies()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 公司列表（代码/名称/类型/股票数/角色数/最新披露期） | `GET /api/v1/companies?q=&cursor=` | ✅ |
| 公司行点击进入档案 | 路由 `/companies/:companyId` | ✅ |
| 右侧检查器预览（角色/命题摘要） | `getCompanyDossier(id)` 增量调用 | ✅ |
| 过滤（按代码/名称） | 前端二次过滤（mock 阶段足够，真实后端用 `q`） | ✅ |

## 13. CompanyDossierPage（公司档案）— `getCompanyDossier()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 身份 + 股票 | dossier `company` + `stocks[]` | ✅ |
| 跨案例主题角色（适用期间 + 来源 statement/span 回链） | dossier `theme_roles[]` | ✅ |
| 关联命题及判断（AI 草案与人工复核分离承载） | dossier `related_theses[]`（`ai_assessment` + `review` 双段） | ✅ |
| 估值快照（每股票每指标按 `as_of_date ≤ cutoff` 取最新） | dossier `valuations[]` | ✅ |
| 基金披露持仓（报告期 + 披露日 + 采集日 + source 口径） | dossier `fund_holders[]` | ✅ |
| 时点回放 | `?cutoff=` query；`basis.is_historical` 标记历史视图 | ✅ |
| 主题角色过期隐藏 | 前端按 `applicable_to` 过滤（与案例 dossier 同源） | ✅ |

## 14. TopicListPage（横切主题 · 列表）— `listThemes()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 主题标签列表（标签/案例数/公司数/命题数） | `GET /api/v1/themes` | ✅ |
| 顶部固定提示「聚合投影、非主题级结论」 | UI 静态文案 | ✅ |
| 行点击进入主题视图 | 路由 `/topics/:tag` | ✅ |
| 标签过滤 | 前端二次过滤（标签量级小） | ✅ |

## 15. TopicViewPage（横切主题 · 视图）— `getThemeView()`

| 数据块 | 来源 | 状态 |
|---|---|---|
| 参与案例与命题有效状态（每案例 supported/contradicted/insufficient/ai_pending 计数） | 视图 `cases[]`（从案例层 dossier 派生，**不合成主题级结论**） | ✅ |
| 公司 × 主题角色表（公司→案例→角色→适用期间→来源 statement） | 视图 `company_roles[]` | ✅ |
| 基金披露持仓构成（按主题内已映射股票聚合） | 视图 `fund_exposure[]`（与案例 fund-exposure 同口径） | ✅ |
| derivedFrom 引用列表 | 视图 `derived_from.{case_ids, thesis_ids, theme_role_ids, disclosure_ids}` | ✅ |
| 时点回放 | `?cutoff=` query；`basis.is_historical` 标记 | ✅ |
| 空主题（无标签案例） | 服务端返回 `cases=[]` + 完整空 derivedFrom；前端显示空态 | ✅ |

**写路径（命令 API）**：
- 主题标签：`PATCH /api/v1/research-cases/{case_id}/theme-tags`（受控词汇，不在表 → 422；diff 当前有效标签 → 追加 add/remove 事件）。
- 公司/股票/估值：`POST /api/v1/companies` / `POST /api/v1/companies/{id}/stocks` / `POST /api/v1/stocks/{id}/valuation-snapshots`（沿用既有 `instrument-commands-v1` 模式）。

**Spec 与 plan 文档**：`docs/superpowers/specs/2026-08-02-theme-company-research.md` / `docs/superpowers/plans/2026-08-02-company-research-plan-a.md` / `docs/superpowers/plans/2026-08-02-theme-research-plan-b.md`。

---

## 缺口清单（已全部补齐 ✅，2026-08-01）

| # | 缺口 | 影响的页 | 端点（已上线） |
|---|---|---|---|
| G1 | AIRun/Provider 运行记录只读 | 3、8 | `GET /api/v1/provider-runs?kind=&limit=` |
| G2 | 案件快照列表 | 9 | `GET /api/v1/research-cases/{id}/snapshots` |
| G3 | 已复核知识层（statements 按 review 状态） | 7 | `GET /api/v1/knowledge?case_id=&review_state=`（含每条 link 的最新人工审核） |
| G4 | dossier 不透出 Thesis 新增强字段 | 10/11 | dossier `theses[]` 已透出 title/观察期/支持反证条件/验证事件/creator_type/review_state |

**明确不建**（与文档1「别陷数据迷宫」一致）：任务队列、活动流、图表库。
前端这些块保持 mock 并标注「非目标范围」即可。

---

## 命令端点全景（2026-08-01 补齐接入/抽取/提案后）

「接入 → 抽取 → 提案 → 评估」四步现全部可界面触发：

| 步骤 | 端点 | 说明 |
|---|---|---|
| 接入 | `POST /api/v1/documents/ingest` | 201；body 全可选（case_id/research_queries/announcement_query/quote_query/quote_stock_code，缺省用 AI 算力链默认查询）；幂等（文档按内容哈希去重、估值按股票+日期+指标+源去重）；404 case_id 不存在；503 GILDATA_TOKEN 未配置或上游不可用（`upstream_unavailable`，真实数据源不静默 mock） |
| 抽取 | `POST /api/v1/documents/{document_version_id}/extract` | 201；append-only，重复调用会重复插 statements；404 版本不存在 |
| 提案 | `POST /api/v1/theses/{thesis_id}/propose` | 201；产出全部进审核队列（machine_generated），不自动确认；404 thesis 不存在 |
| 评估 | `POST /api/v1/theses/{thesis_id}/rerun` | 201；冻结新快照 + 追加临时评估；**422 `validation_failed` = 合规拒绝**（AI 文本命中投资建议用语，拒绝文本不入账，但失败 AIRun 已留作审计，前端应展示为"被合规拦截"状态而非报错） |

**读端点补充**（2026-08-02）：

| 端点 | 说明 |
|---|---|
| `GET /api/v1/research-ops/kpis?case_id=&as_of=` | 研究效能 KPI：审核吞吐（含有效状态待审队列）、人机一致率（评估级+链路级，无数据返回 null）、判断时滞（证据→评估、评估→复核，天数均值/峰值）。`as_of` 支持时点回放。尚未绑定页面，适合挂数据中心或投研管理视图 |

**合规拒绝的透出设计**（2026-08-01；2026-08-02 补重写回路）：
- rerun 被合规门拒绝时：快照不落库（合规先于持久化，账本不可变约束下无法删半成品快照），失败的 AIRun 保留为审计痕迹。
- 2026-08-02 起合规门为有界三段：REFUSE 类（买卖建议/荐股/仓位/个性化投顾）命中立即拒绝；REWRITE 类（目标价/收益预测）命中先给模型一次修复机会，修复文本重过合规门，残留违规才拒绝。修复成功的 rerun 返回 201，评估文本为清理后版本，AIRun 的 `output_summary` 带 `rewritten_for_compliance` 标记（provider-runs 可见）。前端语义不变：422 仍只表示"被合规拦截"。
- dossier 新增 `assess_failure` 字段（`{model_version, error, failed_at}`）：仅当失败比最新成功评估**更新**时透出；后续 rerun 成功后自动隐藏。完整运行历史走 `GET /api/v1/provider-runs?kind=assess&status=`。
- 引擎脚本按 thesis 容错：单个 thesis 被拒不中断整跑。

引擎脚本 `run_ai_engine` 的抽取过滤已从 parser_version 字面量改为「有 span 且无 statements」的待抽取语义，seed 场景幂等（重复跑不会重复抽取）。

## 切换顺序建议

1. 先切 6（审核工作区）——写侧闭环，价值最高，且端到端流程测试已锁定契约
2. 再切 9+8+5（版本比较/数据中心/画布）——纯读，契约稳定
3. 然后 4+10/11（案例/主题工作台）——依赖 dossier 的 G4 小补
4. 最后 1+2+3（总览/新建/计划）——含 mock 混合区，边切边标
