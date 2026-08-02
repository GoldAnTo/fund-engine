# 半导体设备国产化 评估金标集（Gold Set）

第三个冻结垂直切片，与 `ai-compute-gold-set.md`、`storage-chain-gold-set.md` 并列，
用于"半导体设备国产化" ResearchCase 的可审计评估。文本样例沿用手工 marker 约定；
本切片刻意覆盖前两个案例没有的评估形态——**T3 为"关键环节受政策约束而证伪"的
contradicted 判断**，且 T2 呈现"需求成立但盈利未兑现"的 insufficient_evidence 结构。
所有材料为模拟数据，不构成任何投资依据。可由
`backend/app/scripts/seed_semiconductor_case.py` 离线重放。

## 冻结源材料

`.txt` 文件经 `[PAGE n][PARA m]` 标记解析（本切片无 PDF 原件，PDF 解析路径由
storage-chain 切片覆盖）。

| 文件 | 类型 | 发布日 (published_at) | content_sha256 |
|---|---|---|---|
| `01_naura_order_announcement.txt` | 公告（重大合同） | 2026-03-12 | `6864756290ef23c9e82648477038626f6d1d8e81d7e517951e0e05e80a0b4bbb` |
| `02_amec_annual_report.txt` | 年报节选 | 2026-03-28 | `edcb16aadf5cf63b990847d39bb6d8312f60f5a0fcd3debd783f20efd9762648` |
| `03_htsc_equipment_research.txt` | 券商研报 | 2026-06-10 | `8e7afbddc0dd098c95fd0e6676fecac1feb32ac8ba5cb0226e794026a1d3efff` |
| `04_changchuan_quarterly_report.txt` | 季报节选 | 2026-04-25 | `4e67c81e997b450b5a25123224411c839ecf5a3ed00169dbc19bf536c2b13ca3` |
| `05_semi_capex_tracker.txt` | 行业数据跟踪 | 2026-05-20 | `feeacd78a9bf8de73eff925a59d09ba9d4fbefe32da57ec1b51014dc24600b4b` |

- DocumentVersion 数量：5
- SourceSpan 总数：23
- SourceStatement 数量：18；EvidenceLink 数量：18
- 涉及公司：北方华创、中微公司、长川科技

## 评估 cutoff 日期

- EvidenceSnapshot cutoff：`2026-12-31T23:59:59+00:00`
- 该 cutoff 晚于所有材料的 `available_at`，保证每条 AIAssessment 可沿
  `snapshot → evidence_link → statement → span` 完整回溯（由门禁检查 3 强制）。

## 三条 Thesis

| Key | 命题 | AIAssessment 结论 |
|---|---|---|
| T1 | 2026年国内晶圆厂扩产将驱动国产半导体设备订单持续高增长 | supported |
| T2 | 先进封装扩产将显著修复国产测试设备环节盈利 | insufficient_evidence |
| T3 | 光刻环节受限不改国产半导体设备板块整体估值支撑 | contradicted |

## 结论结构与证据形态

- **T1（supported）**：7 条支持证据，覆盖下游资本开支（SEMI 预测）、招标份额
  （国产中标占比 41%）、龙头订单（北方华创 +65%）与收入兑现（中微 +44%）四类
  独立来源，多源交叉验证。
- **T2（insufficient_evidence）**：2 条支持（先进封装扩产、测试设备收入 +38%）
  对 3 条反驳（毛利率 -2.1pct、管理层确认价格竞争、研报认为弹性待验证）——
  需求侧成立但盈利侧证据不足，是"证据缺口"形态的典型样本。
- **T3（contradicted）**：2 条支持（研报成长逻辑、研发高投入）对 4 条反驳
  （光刻装机 <5%、出口管制影响交付、研报估值预警、订单确认节奏延后）——
  关键环节受政策约束导致整体假设被证伪，是前两个案例未覆盖的评估形态。

## 人工复核

每条 AIAssessment 均携带一条 `seed-human-reviewer` 的 ReviewDecision
（三条均为 confirmed），原始 AI 结论保留不变（门禁检查 6、7 强制）。

## 基金穿透

- 华夏国证半导体芯片ETF联接（012854）：北方华创 9.8%、中微公司 8.4%（2026Q1）
- 国联安半导体设备混合（018565）：北方华创 7.1%、长川科技 4.6%（2026Q1）
- 保留一条 2025H1 陈旧披露（国联安半导体设备混合 → 北方华创 5.5%），
  供时点回放验证（门禁检查 5 使用 cutoff 2026-04-01）。
