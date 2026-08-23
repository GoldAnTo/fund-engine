# 公司／行业研究档案：只读版本、证据与变化设计

## 目的

把已经存在的不可变 Underwriting 研究版本，变成投资研究员能实际使用的档案入口：从公司或行业找到研究，看到当时能知道什么、不能知道什么，以及新版本到底改变了哪条证据或哪项机制判断。

它解决的是“研究是否沉淀、是否可复用、变化是否可见”，而不是预测股价或代替投资决策。

## 非目标与硬边界

- 不显示实时价格、PE/PB、DCF、目标价、收益率、仓位、加仓、止损或买卖建议。
- 不在页面加载时抓取新闻、刷新来源、调用当前有效账本，或用新资料补全旧版本。
- 不把 `candidate` 机制、图表约值、`assumption_bound` 或 Unknown gap 显示成已确认事实。
- 不把当前 CATL evidence-only 基线升级为 IndustryState、有效产能、行业利用率、盈利预测或估值。
- 事件研究工作台与 Underwriting 档案并行存在；本设计不把两套对象模型强行合并。

## 用户与使用问题

| 用户 | 要回答的问题 | 不能接受的答案 |
| --- | --- | --- |
| 个人投资者 | 这份研究凭什么成立；现在缺什么；更新后真正变了什么？ | 新闻摘要、未经证实的行业比率、行动暗示 |
| 投资经理 | 团队是否重复研究；新资料是否改变原来事实、假设或可回答性？ | 只有一段 AI 总结、没有时间和来源定位 |
| 研究员 | 下一次应补什么证据，旧结论是否能精确回放？ | “最新数据”覆盖掉原有版本 |

## 信息架构

新增独立路由，不混入事件研究 case：

```text
/underwriting/research
/underwriting/research/:objectId/:versionKind
```

第一个页面是“研究档案目录”，支持按 `canonical_name`、`external_key` 和对象种类（industry/company/security）检索。每行只显示：对象、研究类别、最新序号、资料截止日、研究状态、版本数；绝不显示金融价格或行动字段。

第二个页面分为三个稳定区域：

1. **研究身份与边界**：对象／证券关联、研究类别、所选版本、资料截止日、来源清单哈希、内容哈希。若该版本无法验证，明确显示“谱系损坏，不能读取”，不降级为最新版本。
2. **版本与变化**：左侧按 sequence 排列的历史时间线；选中一个非首版时，中部调用已存在的祖先 diff，仅展示 `added`、`removed`、`replaced` 的 typed artifact。变更按 evidence → mechanism → industry model → answerability 排序，每一项保留来源定位、单位、期间、可得时间和状态。
3. **研究边界**：右侧展示 answerability、blockers、research-debt 和 Unknown evidence gaps。`wait_for_validation` 只解释为“研究尚需验证”，不能写成持仓动作。候选机制清晰显示为 candidate，正式机制才可显示 formal。

当用户选择 CATL 当前 evidence-only 版本，档案必须直观显示：已有公司财务／销量／来源事实；缺少行业名义／有效产能与可校准价格链；所以结论是 `not_answerable`，而不是“看多／看空”。

## 后端读模型

现有三个版本 API 保持原样：

```text
GET /api/underwriting/v1/objects/{object_id}/research-versions/{version_kind}
GET /api/underwriting/v1/research-versions/{revision_id}
GET /api/underwriting/v1/research-versions/{from_revision_id}/diff/{to_revision_id}
```

新增一个仅用于发现的、只读的目录 API：

```text
GET /api/underwriting/v1/research-archives?query=&kind=&limit=&cursor=
```

返回按 `(canonical_name, external_key, object_id, version_kind)` 稳定排序的档案摘要。每个摘要包含对象身份、最新 revision 的 ID/sequence/cutoff、版本数，以及 `readable | unreadable` 谱系状态。

- `readable`：用 `ResearchRevisionDiffService.effective_revision()` 已验证；摘要只来自该冻结版本。
- `unreadable`：目录仍显示对象与 `version_kind`，但不输出任何未验证的 parent、cutoff、来源或模型结论；提供受控的 `lineage_unreadable` 状态，以便研究员发现坏记录。
- 查询只在对象名称／外部键上执行，不能搜索原始 payload 或来源内容。
- 全部 GET 路径包裹在 `Session.no_autoflush`，不 commit、不 update、不调用 fixture／网络／当前 effective ledger。

## 前端边界与数据流

新增一个独立 `underwritingResearchApi`，默认 base URL 为 `/api/underwriting/v1`。不能复用默认 `/api/v1` 的事件研究客户端，也不能让 mock 数据静默替代真实档案。

```text
目录 API ──> 档案列表 ──> 版本历史 API ──> 已选 revision
                                      └──> 相邻祖先 diff API
```

深链接须携带 `objectId` 与 `versionKind`。页面首次加载选择 history 的末版；切换版本只读该版本的历史 descriptor。查看变化时只允许 `previous sequence → selected sequence` 的严格祖先配对。API 404、422、网络失败和 `unreadable` 必须是不同的可理解状态。

## 错误、状态与可访问性

- Loading：保留区域骨架和明确“读取冻结研究版本”。
- 404：不存在的档案／版本；不给出猜测替代项。
- 422：谱系或历史证据不可信；展示服务端 envelope message、request id（若有）和“不能用最新数据替代”的说明。
- Empty：没有已发布档案；不是“研究完成”。
- 键盘可操作版本选择；变更列表具有 group/状态的文本标签，不只使用颜色；hash、locator 可以复制。

## 验收与测试

1. 目录可以发现 CATL 公司档案，且只显示其持久化的 research family。
2. 选择任意历史版本后，显示的 cutoff、hash 和 source locator 与后端 revision response 完全一致。
3. 新增 successor 后，旧版档案 DOM／API 数据不发生变化；diff 只含两个冻结父集合的 typed 差异。
4. `not_answerable`、Unknown gap、candidate mechanism 和 `wait_for_validation` 在界面上保留原始状态与来源边界。
5. 页面与 contracts 中不存在 price、PE、PB、DCF、target、buy、sell、stop、position、return、valuation、recommend 或 action 字段。
6. 目录、详情和失败状态在移动端不横向溢出；路由、键盘选择、空态、422 与无写入副作用均有测试。
7. API／前端的 generated OpenAPI 与 TypeScript 契约稳定；不覆盖现有用户未提交的生成文件改动。

## 后续但不属于本次

IEA 2024 中国电芯候选证据（2.8 TWh 名义产能、约 0.9 TWh 图表产出、85% 假设上限）需要一个独立的人工审阅与版本发布设计。它只有在准确保留 `source_reported`、`chart_approximation` 和 `assumption_bound` 区别后，才可进入新的候选基线；仍不能填充实际有效产能／利用率，或启动价格和估值链条。
