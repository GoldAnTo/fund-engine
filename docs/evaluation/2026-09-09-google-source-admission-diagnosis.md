# Google 正式案例 0 准入只读诊断

观察时点：2026-09-09 10:05 左右（Asia/Shanghai，约等于 2026-09-09 02:05 UTC）。本报告基于当时对 localhost:5180 安全只读接口、服务日志和本地数据库只读查询的动态快照；采集任务仍在运行，计数会随 worker 继续处理而变化。

## 对象

- conversation_id：`82c9c4bf-a8e6-415e-be64-e8baca3f9798`
- run_spec：`62bd7e46-e3e5-4007-abf4-49dbc1efed9c`
- native_run_id：`35dd5739-7225-4439-ad82-9dccf1e5808f`
- 约束：未修改、未重试、未取消该研究；未发起新的模型调用；未打印凭证、原始模型推理或完整环境变量。

## 动态快照

当时 run 仍处于 `waiting_for_sources` / `retrieve`，worker 在线，`next_action` 为等待执行继续推进。15 个采集任务的状态快照为：

| 状态 | 数量 |
| --- | ---: |
| partial | 9 |
| running | 2 |
| queued | 4 |

累计观测到的采集/处理计数约为：

| 指标 | 数量 |
| --- | ---: |
| discovered | 379 |
| fetched | 379 |
| frozen | 332 |
| admitted | 0 |
| exceptions | 862 |

这些数字不是终态，只表示观察时刻的快照。

## 已确认结论

1. 0 准入的主因是 evidence admission 的语义门，而不是当前 MiniMax JSON fence 问题。

   trace 和数据库异常聚合显示，最大类问题是 `evidence_not_admitted` / `automatic_admission_quarantined`，约 673 条，全部卡在 `semantic` gate。主要 reason code 为：`semantic_subject_mismatch` 约 565 条、`semantic_subject_not_grounded` 约 50 条、`semantic_subject_missing` 约 40 条。服务日志中没有看到当前 run 以 `malformed_response` 作为主因继续失败；AI extraction 有大量 success，少量历史 generic failed。

2. Google 海外主体被当前采集策略路由到了国内/default 来源组合。

   15 个 acquisition job 的 enabled/planned adapters 均为 `gildata`、`sse`、`szse`。SSE/SZSE search 均出现非重试性 `ValueError`，没有形成可用 source reference；实际 fetched/frozen 的材料基本来自 Gildata，来源类型主要是 licensed provider research report 和 announcement。未观察到面向 Google/Alphabet 的美国官方披露或海外公司公告来源。

3. Gildata 返回中混入大量无关中文主体，导致 admission 严格拒绝。

   异常样本和 candidate subject 分布显示，除少量 Google/Alphabet 相关条目外，结果中包含中文基金、港股/中股公告、其他公司主体以及 subject 缺失条目。`search_item_rejected` 中存在 `variant_conflict`，`incompatible_source_contract` 也多发生在无关公告/研究材料上。这说明检索层对“谷歌 + 财务/资本开支/自由现金流”等中文关键词的泛化过宽，admission 层随后按语义门正确地拒绝了大量无关证据。

4. Google/Alphabet 别名与 grounding 是导致相关材料也无法准入的确定性问题。

   当前 frozen scope 中 `entity_names` 为 `['谷歌']`，`security_codes` 为空。自动准入语义门要求候选 subject 与 snapshot entity 规范化匹配，并要求 subject 在 quote/text 中可落地。实际候选中出现 `Alphabet`、`Alphabet Inc.`、`Google TPU8i` 等形式时，会与 `谷歌` 发生 subject mismatch；候选 subject 为 `谷歌` 的材料，如果 quote/text 未直接包含“谷歌”，则会触发 `semantic_subject_not_grounded`。这解释了为什么即便有少量 Google/Alphabet 标题相关研究报告，仍可能 admitted 为 0。

5. 新 MiniMax JSON fence 协议修复已覆盖后续模型请求，但不会 retroactively 修复这个已运行案例中的历史失败。

   运行中的 API 容器代码已包含更强的 JSON transport contract，用于要求模型返回裸 JSON、禁止 Markdown/code fence。该修复只影响之后发起的模型请求；这个正式 run 已经持久化的 extraction failed / admission quarantine 不会自动被修复。当前案例的主要剩余风险仍是来源覆盖、路由和实体别名/准入匹配。

## 建议优先级

P0：不要自动重试、取消或改写当前用户案例。让现有 run 自然完成；若终态仍是 0 admitted，应明确展示“当前来源与准入策略下未获得可用证据”，避免把 partial/frozen 误表达为可用研究证据。

P1：修复海外上市公司来源路由。对 Google/Alphabet 这类海外主体，应路由到支持海外上市公司的官方披露/公司 IR/SEC 或可授权海外数据源；如果当前环境没有这些来源，应在采集规划阶段给出清晰的 unsupported source coverage / geography coverage reason，避免继续走 SSE/SZSE。

P1：增加主体别名冻结或 admission alias 支持。至少应把 `谷歌`、`Google`、`Alphabet`、`Alphabet Inc.`、`GOOGL`、`GOOG` 作为同一研究主体的授权别名参与 retrieval 和 semantic admission，但仍要求 quote/text 能真实落地到其中一个授权别名，保持 hallucination 防线。

P2：收紧 Gildata 检索与预过滤。对海外公司问题，若临时仍允许 Gildata，应在搜索结果进入 fetch/freeze 前按 issuer/subject/title/source metadata 做更强过滤，减少中文基金、无关公告和概念词命中的噪声。

P2：将 admission 失败原因聚合回前端/报告。把 `semantic_subject_mismatch`、`semantic_subject_not_grounded`、`source_policy_blocked` 等 reason 汇总成用户可理解的失败诊断，便于区分“没有材料”“材料来源不支持”“有材料但主体不匹配”。

## 建议验收条件

1. 对同类 Google/Alphabet 正式研究，采集规划不再把海外主体默认只分配给 `gildata/sse/szse`；若没有海外来源能力，run 应早期返回明确 source coverage failure，而不是长时间 partial 且 0 admitted。

2. 给定 frozen entity `谷歌` 与来源文本中的 `Alphabet` / `Alphabet Inc.` / `Google`，admission 能在授权别名内通过 subject matching；给定无关中文基金、港股/中股公告、其他公司主体，仍应被 semantic gate 拒绝。

3. 一次受控回归样例中，Google 财务问题至少能 admitted 到一条主体匹配、来源合同允许、quote/text 可落地的证据；如果缺少合规来源，则应返回可解释的 no usable evidence / unsupported source coverage 状态。

4. MiniMax fence 修复的回归保持不变：客户端仍严格拒绝 fenced/malformed JSON，但正常请求侧 prompt/transport contract 要求裸 JSON；不得通过 stripping fences、repair JSON 或接受 malformed output 来绕过 strict parser。

5. 前端/运行摘要能展示动态采集计数和 admission reason 聚合，避免用户把 discovered/frozen 误解为 admitted evidence。
