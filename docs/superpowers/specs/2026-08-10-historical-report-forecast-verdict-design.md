# 历史研报预测验证与基金穿透单案例设计

## 目标

完成一个可重复、可审计的历史研报验证闭环，而不是生成投资建议：

```text
研报预测 → 冻结预测与基线 → 后续公司公告指标 → 自动判据候选
→ 人工 verdict → 股票与基金披露影响
```

首个案例使用火星人（`300894.SZ`）的公开历史材料，验证系统既能呈现预测兑现，也能诚实地呈现预测未兑现。

## 冻结案例与来源

| 角色 | 冻结材料 | 口径 |
| --- | --- | --- |
| 预测源 | [开源证券《2022Q4 毛利率改善，看好新兴渠道全面加速拓展》](https://pdf.dfcfw.com/pdf/H3_AP202304251585791096_1.pdf)，2023-04-25，第 1 页，SHA-256 `8e5d8d...eaef2` | 预测 2023 年归母净利润 `455,000,000 CNY`；并将全年毛利率列为 `47.0%`。该预测是券商观点，绝不升级为公司披露事实。 |
| 基线 | 同一研报财务摘要中的 2022 年归母净利润 `315,000,000 CNY`，可回到同一页表格 | 仅作为同比改善预测的公开基线；预测目标仍是绝对值 4.55 亿元。 |
| 实际值 | [火星人 2023 年年度报告](https://static.cninfo.com.cn/finalpage/2024-04-22/1219704396.PDF)，2024-04-22，第 123 页，SHA-256 `3d83bb...920d3` | 经审计的 2023 年归母净利润 `247,245,713.03 CNY`。 |
| 基金披露 | [广发中证 1000 ETF（`560010`）2024 年中期报告](https://www.sse.com.cn/disclosure/fund/announcement/c/new/2024-08-30/560010_20240830_0EVT.pdf)，第 100 页，SHA-256 `98eb18...40031` | 2024-06-30 历史持有 `300894.SZ` 349,600 股、4,481,872.00 CNY、基金净值 0.04%；不是实时持仓、基金收益归因或交易建议。 |

验证窗口固定为 `2023-01-01` 至 `2023-12-31`；实际公告的 `available_at` 固定为 2024 年年报披露时间。对该个案，数值实际值低于预测值且低于研报基线，因此机器结论必须是 `contradicted`，不能因毛利率等其他指标改善而把利润预测判为支持。

## 设计选择

不使用当前 `ClaimVerification` 直接写入人工结论的路径。它能记录审核后的支持/反驳，却不能表示“机器依据一个已冻结的数值规则得出候选、随后由人审发布”。

新增一个狭窄的、版本化的预测评估子域：

1. `ForecastTargetVersion` 固化研报中一个可比较的量化预测：关联 `ReportClaim`、指标定义、公司/股票范围、目标值、单位、预测期、比较运算符与容差、预测原文来源与审核人。它不从文本猜测数值。
2. `ActualMetricObservation` 固化后续公告的同口径实际值：关联冻结 `SourceStatement`，并保存数值、单位、观察期和 `available_at`。
3. `ForecastEvaluationCandidate` 由纯函数生成，只引用一个已审核 target 和一个同口径 observation；它产出 `supported / contradicted / insufficient_evidence / not_due`，附比较输入、规则版本和理由。该记录是 `machine_generated`，不可直接进入股票/基金表达。
4. `ForecastVerdict` 是人工对候选的追加决定：`confirmed / modified / rejected`，保留理由、审核人和时间；不覆盖候选和原始材料。

首版只支持绝对金额的 `at_least` / `at_most` / `within_tolerance` 比较。火星人使用 `within_tolerance`：预测 `455,000,000 CNY`，相对容差 10%，实际 `247,245,713.03 CNY`，所以判为 `contradicted`。不把“预测有方向”误写成“预测准确”。

## 数据流与边界

```text
DocumentVersion + SourceSpan
  → 已审核 SourceStatement (forecast / disclosed_fact)
  → 已审核 ReportClaim
  → ForecastTargetVersion
  → ActualMetricObservation
  → ForecastEvaluationCandidate (machine_generated)
  → ForecastVerdict (human-reviewed)
  → reviewed MarketInstrumentBinding / FundamentalImpact
  → HoldingDisclosure 的股票与基金读模型
```

- 所有来源必须属于该 `ResearchCase`，且 `SourceContract` 允许展示和处理。
- 评估器不调用 LLM、不读取实时数据、不产生投资评级、收益预测或交易动作。
- 实际观察值必须与 target 的指标、单位、实体范围、观察期相匹配，并且 `available_at` 不晚于查询 cutoff；否则返回 `insufficient_evidence` 或 `not_due`。
- 基金层只能表述“在指定披露时点持有受此经审核研究因素影响的股票”；若持仓已过期或覆盖不完整，沿用现有 `stale_disclosure` / `coverage_incomplete`，不返回精确当前暴露。
- 预测 verdict 为 `contradicted` 时仍允许下钻查看股票与基金持仓，但必须展示“预测未兑现”；绝不推导“基金将下跌”或任何买卖结论。

## API 与读模型

增加受控写入与读接口：

```text
POST /research-cases/{case_id}/forecast-targets
POST /research-cases/{case_id}/actual-metric-observations
POST /forecast-targets/{target_id}/evaluate
POST /forecast-evaluations/{candidate_id}/verdicts
GET  /research-cases/{case_id}/forecast-verdicts?cutoff=
```

写入均要求来源 ID、审核人和理由。`evaluate` 只创建候选；`verdicts` 是唯一使候选在读模型中成为正式 verdict 的路径。现有 `GET /research-cases/{case_id}/market-expression` 扩展为按 factor 返回已确认 forecast verdict，以便同一页面下钻到已审核股票绑定和带时点基金披露。

## 单案例端到端验收

测试必须从冻结的真实、短摘录材料开始，不调用外网，并断言：

1. 研报预测是 `forecast` / `research_opinion`，且页码、quote hash 和发布时点可回跳。
2. target、2022 基线、2023 实际值和观察窗口不可变；错单位、错实体、错观察期和未来 `available_at` 均不能被评估。
3. 纯评估函数以 4.55 亿元和 2.47 亿元输出 `contradicted`，并保存精确输入与规则版本。
4. 未经人工 verdict 时，市场表达和基金下钻不显示正式评估结论；确认后才显示 `contradicted`、人工理由和双方来源。
5. 已审核股票绑定可下钻至 300894.SZ，基金结果保留报告期、披露日、采集日、来源、许可和新鲜度；过期披露不冒充当前精确暴露。
6. 假设将实际值改为容差内时，候选变为 `supported`；对同一冻结 target 的历史候选和人工 verdict 绝不被覆盖。
7. 后端 API 集成测试、真实 HTTP 读写流程和前端 HTTP 适配器测试均通过；不使用 `?client=mock` 证明写入闭环。

## 非范围

- 不做批量研报评分、研报机构排名或预测准确率聚合。
- 不自动抓取、转载或训练于未授权研报；首例仅冻结必要短摘录、原始 URL、hash 与定位。
- 不计算基金回报、交易信号、目标价正确率或因果投资收益。
- 不改变现有用户工作树中的 `event_impact` 实验分支内容。
