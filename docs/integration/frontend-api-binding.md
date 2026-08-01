# 前端 9+2 屏 × v1 API 对接清单

> 目的：前端从 `prototypeFixture` mock 切到真实 v1 API 的逐页映射。
> 状态分三档：✅ 已就绪（端点存在，直接绑）/ 🔧 需适配（端点存在但字段要转换）/ ❌ 无后端（保持 mock 或补端点，见文末缺口清单）。
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

## 命令端点全景（2026-08-01 补齐抽取/提案后）

「接入 → 抽取 → 提案 → 评估」四步现全部可界面触发：

| 步骤 | 端点 | 说明 |
|---|---|---|
| 接入 | （gildata 接入仍为脚本/服务层，未包 API） | 见缺口说明 |
| 抽取 | `POST /api/v1/documents/{document_version_id}/extract` | 201；append-only，重复调用会重复插 statements；404 版本不存在 |
| 提案 | `POST /api/v1/theses/{thesis_id}/propose` | 201；产出全部进审核队列（machine_generated），不自动确认；404 thesis 不存在 |
| 评估 | `POST /api/v1/theses/{thesis_id}/rerun` | 201；冻结新快照 + 追加临时评估 |

引擎脚本 `run_ai_engine` 的抽取过滤已从 parser_version 字面量改为「有 span 且无 statements」的待抽取语义，seed 场景幂等（重复跑不会重复抽取）。

## 切换顺序建议

1. 先切 6（审核工作区）——写侧闭环，价值最高，且端到端流程测试已锁定契约
2. 再切 9+8+5（版本比较/数据中心/画布）——纯读，契约稳定
3. 然后 4+10/11（案例/主题工作台）——依赖 dossier 的 G4 小补
4. 最后 1+2+3（总览/新建/计划）——含 mock 混合区，边切边标
