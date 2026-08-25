# CATL 2024 中国动力电池 IndustryState：证据放行决定（研究记录，不是投资结论）

**固定研究截止日：** `2025-05-15T15:59:59Z`

**问题：** 截止日前公开可定位的资料，是否足以把中国动力/储能电池行业写成一个数值化、可重放的 `IndustryState`？
**结论：不可以。** 现有公开资料足以形成来源明确、范围受限的候选证据包；不足以填充正式状态，更不能生成“实际有效产能”“实际利用率”或“产能到价格/利润”的数值因果结论。

这是一份**放行决定**，不改写现有 CATL evidence-only fixture、机制、研究版本或应用代码。任何被采纳的发现都必须经过人工审阅，并以新的不可变版本写入。

## 先给决策

| 项目 | 截止日前最强公开证据 | 可作为 | 不可作为 |
| --- | --- | --- | --- |
| 中国名义电芯产能 | IEA 图表：2024 年中国已安装锂离子**电芯 nameplate** manufacturing capacity 为 **2.8 TWh/年**。 | 带范围的候选观察：`China / lithium-ion cell / nameplate / 2024`。 | `effective_capacity_gwh`，或精确可交付产能。 |
| 中国 EV+储能生产 | IEA 报告 p.149 的中国 2024 `production` 柱约 **0.9 TWh/年**；图注限定 EV battery 与 stationary storage，排除 EV 与电池囤货。 | `chart_approximation`，保留“约数”、图页和范围。 | 精确生产数据列、装车量、公司销量或行业利用率。 |
| 85% | 同图把既有产能按 85% 使用称为**假设的** maximum average utilisation rate。 | `assumption_bound` 的情景参数候选。 | 2024 中国实际利用率或实际有效产能。 |
| 中国锂电制造/装机 | 工信部：全国锂离子电池总产量 **1,170 GWh**；消费/储能/动力为 **84/260/826 GWh**；含新能源汽车和新型储能的装机量超过 **645 GWh**。 | 中国广义锂电制造与部署的 `official_aggregate` 观察。 | IEA 电芯产能的可比分子，或“行业有效利用率”。 |
| 行业价格与材料成本 | 工信部：2024 年电池级碳酸锂/微粉级氢氧化锂均价约 **9.0/8.7 万元/吨**；IEA：全球锂电池包价下降 20%、中国近 30%。 | 原料价格和电池包价格压力的背景证据。 | 中国电芯 ASP、单位现金成本、CATL 利润弹性或价格机制校准。 |
| CATL 公司事实 | CATL 年报：电池系统产能 676 GWh、产量 516 GWh、报告利用率 76.33%、销量 475 GWh；动力电池系统收入/成本为 CNY 253.041337bn / 192.461282bn。 | 公司层面的 `reported`/可审查派生事实。 | 中国行业事实，或由行业总量反推的 CATL 份额、ASP、利润。 |

**严禁的拼接：** `0.9 / 2.8`、`1,170 / 2,800`、`826 / 2,800`、`645 / 2,800`，以及 CATL 的 `516 / 676`，都不能被命名为“中国 2024 实际行业利用率”。每个比率混合了不同的地理、产品、统计对象、库存边界或来源方法；其中 0.9 还是图读近似值，85% 明确是情景假设。

## 已核验的一手/官方来源

| 来源与可得时间 | 精确定位与可安全陈述 | 范围与保留边界 |
| --- | --- | --- |
| [IEA, *Global EV Outlook 2025* 报告页](https://www.iea.org/reports/global-ev-outlook-2025)；官方页面标注 **Published 14 May 2025**、[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；早于截止日。 | [官方 chart 页面](https://www.iea.org/data-and-statistics/charts/share-of-nameplate-manufacturing-capacity-by-region-and-location-of-battery-producers-headquarters-2024-2030)标注 **Last updated 8 Apr 2025**。报告 PDF p.148 的图题为 *Installed lithium-ion battery cell nameplate manufacturing capacity …, 2024*，中国图上总量为 **2.8 TWh**。 | 对象是锂离子**电芯**的名义制造产能，地理是工厂所在地中国；不是良率、认证后可售产能或已观测利用率。图表底层资料来自 Benchmark Mineral Intelligence 与 BloombergNEF；可保留 IEA 的公开结论、URL、页码、抓取时间和哈希，不能把商业厂级数据当成本项目可再分发资产。 |
| 同一 IEA 报告，PDF p.149；报告于 2025-05-14 发布，早于截止日。 | 图题为 *Historical production and announced expansion of battery manufacturing maximum output …*。中国 `2024 production` 柱约 **0.9 TWh/年**。图注明确 production 为 EV 电池与电池储能，排除 EV 与电池囤货。 | 图没有公开可下载的精确数列或柱顶标签；只能登记为视觉读数/约值。它不是中国车辆装车、销售或所有锂离子电池产量。 |
| 同一 IEA 报告，PDF p.149。 | 图注把 increased utilisation 描述为既有产能按 **85%** 使用而得到的缺口，并将 85% 说明为**假设的最大平均利用率**。 | 这是 IEA 情景方法的边界，不是它对 2024 中国实际利用率的观察。不得把 `2.8 × 85%` 写为实际 effective capacity。 |
| [工信部电子信息司《2024 年全国锂离子电池行业运行情况》](https://www.miit.gov.cn/jgsj/dzs/gzdt/art/2025/art_dc20a4d4b3a74dcf91ac1b212f87bbfc.html)，页面发布时间 **2025-02-27 15:06**，早于截止日。 | 第 5 行：全国锂电总产量 **1,170 GWh**；第 7 行：消费/储能/动力为 **84/260/826 GWh**，装机量（新能源汽车、新型储能）超过 **645 GWh**；第 11 行：碳酸锂/氢氧化锂均价 **9.0/8.7 万元/吨**。 | 政府官方汇总，但“总产量”含消费类且“装机”跨新能源汽车/新型储能。页面未见开放再利用条款，建议 `reference_only`：仅保留 URL、时间、定位和派生观察。 |
| IEA 电池章节，[在线第 68–90 行](https://www.iea.org/reports/global-ev-outlook-2025/electric-vehicle-batteries)及[第 102–125 行](https://www.iea.org/reports/global-ev-outlook-2025/electric-vehicle-batteries)。 | IEA 报告 2024 能源部门电池需求约 1 TWh、EV 电池需求逾 950 GWh；同时报告锂离子**电池包**价格全球下降 20%、中国近 30%。 | 需求是基于售车和平均电池容量的估计，排除库存；电池包价格是 EV 与储能销量加权平均，且底层含 Bloomberg/BNEF 数据。可做范围明确的背景，不可替代制造产出、cell ASP 或现金成本。 |
| [CATL 2024 年报，CNINFO PDF](https://static.cninfo.com.cn/finalpage/2025-03-15/1222806982.PDF)，披露日 **2025-03-15**，早于截止日。 | p.18–19：动力电池系统收入 **CNY 253.041337bn**、成本 **CNY 192.461282bn**；p.20：电池系统产能 **676 GWh**、产量 **516 GWh**、报告利用率 **76.33%**、销量 **475 GWh**、库存 **106 GWh**。 | 发行人自身合并“电池系统”口径，不是中国或全球行业口径。`收入 / 销量` 是公司混合产品收入实现代理，不是公开合同 ASP，也不是行业价格序列。现有源治理为 `derived_only`。 |

### 时点与字节留存的额外警告

IEA 报告页和 chart 页的发布/更新时间足以证明**公开结论**在截止日前已发布；但本轮取得的当前 CDN PDF 响应显示的 `Last-Modified` 晚于截止日。因而，不能仅用今天下载的 PDF 字节哈希证明“字节完全相同的文件”在截止日已存在。若把 IEA 候选放入不可变证据清单，必须由人工保存：报告/图表 URL、页面显示的发布日期或更新时间、抓取时间、当前字节哈希、页码/图题，以及“publication-time identity verified”的审阅结论。未做该审阅前，它只能是待验证候选。

## 证据不能覆盖的正式状态输入

当前 `IndustryState` 编译器要求正式机制，且在 [`industry_state.py`](../../backend/app/underwriting/services/industry_state.py) 中对全部基础输入 fail-closed：`ev_sales_millions`、`average_battery_kwh`、`storage_demand_gwh`、`nominal_capacity_gwh`、`commissioned_share`、`certified_share`、`yield_rate`、`shipments_gwh`、`production_gwh`、`cell_asp_cny_per_kwh`、`unit_cash_cost_cny_per_kwh`。它之后才计算：

```text
effective_capacity = nominal × commissioned × certified × yield
utilization        = shipments / effective_capacity
unit_margin        = cell_asp − unit_cash_cost
```

截至 cutoff 的以上资料最多为名义产能、近似生产、广义生产/装机和方向性价格资料。它们没有给出同一范围、同一期间、可复算的：

- 已投产比例、认证可供比例和良率；
- 出货量（而非装机或制造产量）；
- 电芯 ASP 和单位现金成本；
- 与供给/利用率变化对应的滞后、反例和可证伪的价格传导；
- 已提升为 `formal` 的独立机制审阅记录。

因此即使将 IEA 2.8 TWh 或约 0.9 TWh 录入新的候选版本，正式 `IndustryState` 仍会（且应该）保持 `not_answerable` / `wait_for_validation`。

## 允许的下一步与必须的人审决定

### 可以推进：一个不提升状态的候选证据版本

人工审阅者可批准一个独立版本，其中每一项均保留性质与范围：

| 候选字段 | 值/状态 | 必须带上的标签 |
| --- | --- | --- |
| `industry.nameplate_capacity` | `2.8 TWh/year`, `source_reported` | `China; lithium-ion cell; IEA 2024 installed; nameplate; chart page/p.148` |
| `industry.production` | `approximately 0.9 TWh/year`, `chart_approximation` | `China; EV + stationary storage; inventory excluded; p.149; chart-reading method` |
| `industry.maximum_average_utilisation` | `85%`, `assumption_bound` | `IEA scenario maximum-average assumption; not actual utilisation` |
| `china.lithium_ion_output` | `1,170 GWh`, `official_aggregate` | `China; all lithium-ion batteries; MIIT aggregation` |
| `china.lithium_material_price` | `9.0/8.7 万元/吨`, `official_aggregate` | `2024 annual average; material not cell cost` |

候选版本不得填 `effective_capacity_gwh`、`utilization`、`cell_asp_cny_per_kwh` 或 `unit_cash_cost_cny_per_kwh`，也不得把这些观察值提升为 formal mechanism。

### 人类必须明确批准的决定/权限

1. **对象边界：** 是研究“中国 EV+储能锂离子电芯”，还是“中国所有锂离子电池”？两者不得同用一个分子/分母。若不选择，继续保持 Unknown。
2. **证据身份与时点：** 核验 IEA 发布页/图表页所指的版本、图表读数和当前/归档字节；批准 source manifest 的来源授权、可得时点、哈希和留存政策。
3. **图表转录方法：** 批准约 0.9 TWh 的读图程序、允许误差和 `chart_approximation` 状态；禁止把它静默转换成精确 GWh 或利用率百分比。
4. **有效产能方法：** 只有在取得同范围的 commissioned、certified、yield（或发行人/授权供应商给出的等价定义）后，才可批准 `effective_capacity` 的方法；不能以 85% 假设代替。
5. **价格传导证明：** 批准可留存、可重算的电芯价格/成本数据，及“容量/利用率 → 价格 → 毛利”的范围、滞后、反例、falsifier。原料均价和 pack-price 同比不足。
6. **独立机制审阅：** 两名独立审阅者确认因果方向、替代解释和 falsifier 后，才可把机制从 candidate 提升；状态编译不应自行越过此门槛。

## 最终放行矩阵

| 决策 | 结果 |
| --- | --- |
| 保存官方/一手候选证据及其范围 | **可以**，前提是按上述时点、授权和图表约值规则人工审阅。 |
| 新建 evidence-only / candidate 研究版本 | **可以**，不得覆盖历史版本。 |
| 数值化行业 `effective_capacity` 或实际 `utilization` | **不可以**。 |
| 将“产能 → 利用率 → 价格”改为正式机制 | **不可以**。 |
| 将行业背景转成 CATL 收入、利润、估值、买卖或仓位判断 | **不可以**；本研究不涉及这些结论。 |

**推荐状态：** 维持现有 `not_answerable` 与 `wait_for_validation`。当前最有价值的进展是可追溯地记录“已知、约值、假设、未知”的界线；不是用表面完整的数字填满一个无法识别的行业模型。
