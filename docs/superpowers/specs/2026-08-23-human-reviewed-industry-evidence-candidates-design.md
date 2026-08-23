# 人工审阅的行业候选证据包设计

**状态：** 已确认的设计；尚未实现。

## 目标与边界

系统需要让研究者把截至固定历史截止日可定位、但不足以放行数值化行业状态的资料保存为可审计候选。首个使用场景是 CATL 2024 中国动力/储能锂离子电芯研究：IEA 的 2.8 TWh 名义产能、约 0.9 TWh 图读产出、85% 情景假设和工信部汇总数据都可进入候选审阅，但不能被自动拼接成实际有效产能、利用率、价格、利润、估值或持仓结论。

本设计只新增候选证据的人工审阅、不可变发布与只读展示。它不改变现有 CATL evidence-only 基线，不自动抓取网页，不赋予任何候选 `formal` 状态，也不生成 `IndustryState`、情景、EarningsEngine、预测或投资动作。

## 方案选择

采用独立的“候选证据包 + 双独立审阅”模型，而不是复用 `MechanismPack` 生命周期。事实/图表转录/假设的治理与因果机制的治理不同：前者审查来源、授权、时间、口径和转录方法，后者审查因果方向、替代解释和 falsifier。二者只能在后续人工明确操作中建立引用，不能互相自动升级。

仅靠 Markdown 记录不满足不可变哈希、历史重放、审阅责任和修订差异的要求，故不采用。

## 领域对象

### CandidateEvidenceDossierVersion

新增 append-only `uw_evidence_candidate_dossier_versions`。每一版绑定一个 `UnderwritingResearchObject`、一个 `HistoricalBasis` 和一个经验证的 `SourceManifest`，且包含：

- `dossier_key`、连续 `version`、`supersedes_id`、`created_at`；
- 由人工选择的单一 `scope_statement`，明确地理、产品、统计对象、期间和排除项；
- `purpose`，只能是 `evidence_candidate`；
- 规范化候选条目、拒绝的计算以及明确的 Unknown 项；
- 规范化内容哈希和来源清单哈希。

候选条目必须使用下列互斥状态之一：`source_reported`、`official_aggregate`、`chart_approximation`、`assumption_bound`、`unknown`。每项都保留 metric key、值/单位（Unknown 无数值）、观察期间、可得时间、来源 ID、精确 locator、范围/排除项、方法说明和反禁止拼接说明。`chart_approximation` 必须带转录方法与误差界；`assumption_bound` 必须带情景用途和“不是观察”的声明；`unknown` 必须带缺口原因，不能用零或空字符串代替。

候选条目不是 `MetricObservation`，不进入 `FrozenObservationSet`，不能被机制编译器、IndustryState 或 EarningsEngine 消费。

### CandidateEvidenceReviewVersion

新增 append-only `uw_evidence_candidate_review_versions`。一条审阅精确绑定已哈希的 dossier 版和候选条目哈希，记录不可变的审阅身份、角色、决定、理由、审阅时间和内容哈希。

每个 dossier 要达到 `reviewed_candidate`，必须有且只有两个有效批准：

1. `provenance` 审阅：来源身份、授权/留存、locator、可得时间和 byte/version 警告；
2. `methodology` 审阅：范围、口径、图表转录误差、假设/Unknown 标签，以及禁止拼接结论。

两条批准的 canonical reviewer identity 必须不同。任一 rejection 或 request-changes 会阻止发布；修订 dossier 后必须重新审阅，旧审阅不能迁移。这里的“独立”是以不同、可审计的受认证身份和不同职责实现；若未来接入组织权限，身份认证可在边界层加强，但不会改写既有审阅记录。

## 写入与放行

`CandidateEvidenceService` 只有以下显式写入命令：创建 dossier、追加 dossier successor、提交审阅、发布 reviewed candidate。所有写入由调用方事务包裹；服务不得自行 commit。

发布前必须验证：

1. dossier、manifest 和所有源均为认证冻结对象，且每个可得时间不晚于 basis cutoff；
2. 每项来源和 locator 精确属于 manifest，候选 hash 与 review 绑定 hash 一致；
3. 两位不同身份、不同角色的审阅均为 approve，且没有后续 rejection/request-changes；
4. scope 与对象一致，Unknown 不被当作数值，且没有受禁的利用率/有效产能/价格/估值字段或语义；
5. 产生新的 `UnderwritingResearchVersion`，kind 为 `industry_evidence_candidate`，其冻结父图包含 dossier、两条 review、manifest 和 `Answerability(not_answerable)`。

该发布仍只能记录 evidence candidate 与研究债务。`Answerability` 不得变为 answerable；不写入 formal mechanism、IndustryState、scenario、exposure、earnings、forecast 或任何建议字段。

## 历史读取与接口

研究修订解析器将候选 dossier/review 作为已知、可验证的 immutable parent descriptor；其内容、身份、created_at 和选择的来源/条目 hash 都必须被父图 seal 绑定。历史读取不得按 dossier key 查当前版本、不得加载 fixture、不得从未选中的 review 或对象关系补数据。

新增 GET-only 候选证据读模型和 DTO。它必须只返回 selected revision 已冻结的 dossier/review；没有被选为父项的候选、审阅或后续 successor 一律不可见。档案 UI 显示“候选、已审阅但未正式化”，明确列出范围、方法、Unknown 和两位审阅角色；不显示 action、价格、估值或推荐。

## 失败关闭规则

- 任何未知字段、重复条目/审阅角色、非 canonical UUID/hash/UTC 时间、来源/locator 不匹配、未来可得时间、篡改条目或审阅负载都返回验证错误；
- 一个 reviewer 兼任两种角色、两个角色使用同一 identity、审阅跨 dossier hash 复用、或审阅顺序错误均拒绝；
- 候选数值被用作 `MetricObservation`、候选机制被编译、或候选包试图发布 formal/IndustryState/earnings parent 时拒绝；
- 旧版 dossier 在新 revision/review 出现后仍按其冻结父图重放；任何 parent 注入、删除或等内容不同身份替换均使读取失败关闭。

## 验收与测试

测试以 red-green 方式覆盖：

1. 合法 source-reported、chart-approximation、assumption-bound、Unknown dossier 的 canonical hash 和不可变 successor；
2. 缺 locator、错误 source、晚于 cutoff、范围混合、精确化图表近似、Unknown 数值化、或禁止利用率拼接的拒绝；
3. 双角色、双 identity 审阅；同一 identity、单审、拒绝/改稿、旧审阅复用和内容篡改的拒绝；
4. 发布只生成 evidence-candidate revision 与 `not_answerable`，并证明没有 formal mechanism/state/scenario/earnings/forecast；
5. 两个独立数据库的语义 hash 一致、后续 dossier/review 不改变旧版本、父图/来源/审阅篡改均 fail closed；
6. GET-only API/OpenAPI/TypeScript、404/422 envelope、严格 DTO、无 action/valuation/recommendation 字段；
7. 前端只呈现 selected frozen candidate 和审阅边界，不读取 current 资料或调用 economic-model endpoint。

## 非目标

不会在该阶段把 IEA/工信部候选自动写入当前 CATL fixture；该动作需要独立的人类资料权限、对象边界和 source-manifest 决定。也不会把候选 2.8 TWh、约 0.9 TWh 或 85% 做除法、命名为实际利用率，或以任何方式输出投资结论。
