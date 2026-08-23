# CATL 2024 中国动力电池 IndustryState：正式输入逐项审计（研究记录，不是投资结论）

**固定研究截止日：** `2025-05-15T15:59:59Z`

**审计对象：** 为 CATL 2024 研究尝试编译一个范围明确为“中国、2024 年、动力/储能锂离子电芯”的数值 `IndustryState`。
**结论：不允许 formalization；维持 `not_answerable` / `wait_for_validation`。**

本文复核既有的 [行业基线缺口评估](catl-industry-baseline-gap-assessment.md)、[放行决定](catl-2024-china-power-battery-industry-state-formalization-decision.md) 和 [权威来源复核](catl-2024-industry-baseline-authority-recheck.md)。它只记录研究证据和编译门槛，**不**写入 fixture、机制、研究版本或产品代码，也不作价格、利润、估值、交易或仓位结论。

## 审计口径

当前编译器在 [`industry_state.py`](../../backend/app/underwriting/services/industry_state.py) 对下列 11 个输入逐项 fail-closed；任何一个缺失都会阻止编译：

```text
battery_demand      = ev_sales_millions × average_battery_kwh + storage_demand_gwh
effective_capacity  = nominal_capacity_gwh × commissioned_share × certified_share × yield_rate
utilization         = shipments_gwh / effective_capacity
unit_margin         = cell_asp_cny_per_kwh − unit_cash_cost_cny_per_kwh
```

本审计中的“可用”不是“有一个相近数字”：它要求该数字是**截至 cutoff 可认证身份/时间、可重放的字节或受审查版本、同一研究对象范围、确切字段定义与单位、且不依赖未批准的估算或隐含拼接**的正式输入。只要其中一项不成立，即为正式编译的 `No`。这比“可作为候选证据”严格得多。

`独立` 指该证据并非由准备被解释的 CATL 自身披露所替代的行业总量，也不只是商业数据的二手转述；它不是对来源权威性的排序。

## 经过字节和时点审计的权威来源

| 来源 | 身份、可得性与字节记录 | 精确定位、范围与可安全保留的值 | 留存/权限边界 |
| --- | --- | --- | --- |
| [IEA, *Global EV Outlook 2025* 报告页](https://www.iea.org/reports/global-ev-outlook-2025)，及其在 cutoff 前的[静态 PDF](https://iea.blob.core.windows.net/assets/0aa4762f-c1cb-4495-987a-25945d6de5e8/GlobalEVOutlook2025.pdf) | IEA 报告页标注 `Published 14 May 2025`、`CC BY 4.0`，早于 cutoff。审计重取静态 PDF：`3,807,622` bytes，SHA-256 `12a76f53d9a72755b107d812f4b71d4adcf8dc09ccf4a8f38920a3f6a9267bc2`；与既有权威来源复核记录一致。主报告下载链接现可指向标为 **Revised version, July 2025** 的不同文件，不能用那个后修订文件倒证 cutoff 内容。 | p.10：2024 中国 electric-car sales 为 **over 11 million**。p.148：中国 2024 installed lithium-ion **cell nameplate** manufacturing capacity 图上为 **2.8 TWh/year**。p.149：中国 2024 `production` 柱仅可读为 **约 0.9 TWh/year**；图注限于 EV battery + stationary storage，排除 EV/battery stockpiling。p.149 的 85% 是 assumed maximum average utilisation rate。页 134–145 还明确 EV demand 是 sales × volume-weighted average battery size，且图表分析含 EV Volumes；battery-pack price 是 EV/储能销量加权 pack price。 | 可冻结 URL、页码、上述哈希、抓取时间和受限派生观察。IEA 报告本身 CC BY 4.0；但 p.148–149 底层含 Benchmark Mineral Intelligence、BloombergNEF、EV Volumes，不能把厂级或商业底层数据当成获授权的项目资产。建议 `derived_only` / `hash_locator_and_derived_observations`。 |
| [工业和信息化部电子信息司《2024年全国锂离子电池行业运行情况》](https://www.miit.gov.cn/jgsj/dzs/gzdt/art/2025/art_dc20a4d4b3a74dcf91ac1b212f87bbfc.html) | 官方页面显示发布时间 `2025-02-27 15:06`，早于 cutoff。以普通浏览器 UA 在本次审计重取响应：`8,542` bytes，SHA-256 `952d9010ae944e223c005a3211aec2dd6f081eb99ca12e0e9cc93d398eddc814`。该政府页面对无 UA 的请求会返回 403，故哈希只能识别本次取到的响应，不能证明历史页面字节恒定。 | 正文第 5–11 行：全国锂离子电池总产量 **1,170 GWh**；消费/储能/动力分别 **84/260/826 GWh**；新能源汽车和新型储能合计装机“超过 **645 GWh**”；电池级碳酸锂/微粉级氢氧化锂年均价约 **9.0/8.7 万元/吨**。范围包括消费电池，且装机跨两种终端。 | 官方汇总但未见开放再利用许可；只应 `reference_only` 保存 URL、时间、定位、哈希和派生事实。它不是 IEA 的电芯名义产能同口径分子，也不是电芯 ASP/现金成本。 |
| [CATL 2024 年报，CNINFO 公告 PDF](https://static.cninfo.com.cn/finalpage/2025-03-15/1222806982.PDF) | 发行人法定披露，发布日期 `2025-03-15`，早于 cutoff。审计重取：`2,070,073` bytes，SHA-256 `b4f1713d7b821eb076c102711d177fe942ccc2bc8dd171ae5d7a95799a65b0ad`。 | p.18–19：动力电池系统收入/成本 **CNY 253.041337bn / 192.461282bn**。p.20：合并“电池系统”产能/产量/利用率/销量/库存 **676 / 516 GWh / 76.33% / 475 GWh / 106 GWh**。这是发行人合并产品口径，不是中国行业或明确的动力/储能电芯行业口径。 | 依既有来源治理仅 `derived_only`；可保留哈希、页码和派生公司事实。禁止把公司产量、销量、利用率、收入或成本代替行业输入，或把收入/销量称为 cell ASP。 |
| [国家统计局《2024年四季度全国规模以上工业产能利用率为76.2%》](https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202501/t20250117_1958324.html) | 国家统计局，成文日期 `2025-01-17`，早于 cutoff。作为术语交叉核验；本审计未以它充当电芯数据源。 | 第 65–85 行将产能利用率定义为实际产出/生产能力，二者均按**价值量**；年度“电气机械和器材制造业”为 **75.1%**。没有电芯 GWh 行业表。 | 可作为定义与拒绝错误映射的权威依据；不能把 75.1% 变成中国电芯 `commissioned_share`、`yield_rate` 或 GWh utilization。 |
| [国家能源局 2024 年新型储能新闻发布会](https://www.nea.gov.cn/20250123/3e5379eb74b94d94bcdaf556268ad466/c.html) | 国家能源局，发布时间 `2025-01-23`，早于 cutoff。 | 第 21 行：截至 2024 年底新型储能已建成投运累计 **73.76 GW / 168 GWh**，并报 2024 年等效利用小时约 1,000。 | 这是电力系统投运**存量**，且并非只含锂离子电芯；不是年度储能电池需求、制造产量或出货。它反而确认不能把 168 GWh 填进 `storage_demand_gwh`。 |

本轮没有发现可在 cutoff 前读取、归属可验证、并能保存版本/时间的中国汽车动力电池产业创新联盟原始公告来替代上述来源。IEA 当前页面链接的[联盟微信 URL](https://mp.weixin.qq.com/s/W7181pLemWq1eThd4o2vew)在本次审计中不可读取，故无法核验账号、正文、历史版本、来源字节或 `available_at`。现有的政府保存页或同期新闻转载最多说明该数据被转述，不能反向认证联盟原件或其原始可得时点；本审计不把转载数值写进正式输入矩阵。

## 11 个正式输入的决定矩阵

| 编译器输入 | 截止日前最接近的权威证据 | 身份/时点已认证？ | 独立于 CATL 公司口径？ | 正式可用？ | 决定理由 |
| --- | --- | :---: | :---: | :---: | --- |
| `ev_sales_millions` | IEA p.10：中国 2024 electric-car sales **over 11m**。 | Yes | Yes | **No** | 有可审计的中国电动乘用车量级，但该字段参与“动力/储能电芯”总需求，仍缺与 `average_battery_kwh`、储能和目标车辆模式一致的精确基线；`over 11m` 不是可重算的精确数。 |
| `average_battery_kwh` | IEA 在线电池章节定义 EV demand 为销量 × volume-weighted average battery size。 | Yes（定义） | Yes | **No** | 公开结论没有给出可冻结的中国、2024、目标车辆组合的精确平均 kWh；其图表底层依赖 EV Volumes。不能用总需求/销量倒推而静默改变库存、车型和范围。 |
| `storage_demand_gwh` | IEA：2024 能源部门 EV+storage battery demand 约 1 TWh；MIIT：储能**产量** 260 GWh、跨终端装机超过 645 GWh；国家能源局：投运存量 168 GWh。 | Yes | Yes | **No** | 没有中国、2024、与电芯/EV 输入同口径的储能**需求**；产量、装机、累计存量和全球总需求都不是该字段。 |
| `nominal_capacity_gwh` | IEA p.148：中国 2024 锂离子**电芯 nameplate**产能 **2.8 TWh/year**。 | Yes | Yes | **No（仅候选）** | 这是最接近的直接数字，且字节可验证；但它是 IEA 的 China/cell/nameplate 候选范围，不能单独解决其他十项的共同范围、正式机制与来源审阅。可入 candidate，不可使状态 formal。 |
| `commissioned_share` | IEA p.149 只讨论 existing capacity；无已投产比例数列。 | — | — | **No** | 未提供中国行业名义产能中已投产、可连续运行比例；不能以项目公告、公司利用率或 85% 假设代替。 |
| `certified_share` | IEA p.149 明示产能范围包含已获 EV 认证及尚未获 EV 市场认证公司。 | Yes（缺口被确认） | Yes | **No** | 这说明认证边界不等于 100%，却没有可重算的认证比例或产品/客户资格集合。 |
| `yield_rate` | IEA 叙述竞争推动效率和 yields；无中国行业 2024 yield 数值或方法。 | Yes（缺口被确认） | Yes | **No** | 叙述性因果背景不是单位、良率定义、加权方法都明确的 `0–1` 输入。 |
| `shipments_gwh` | CATL p.20 为公司销量 475 GWh；联盟原件本轮不可认证。 | CATL：Yes | **No** | **No** | 公司销售不是行业出货；未取得可认证的中国行业、2024、同产品范围的 shipment 序列。 |
| `production_gwh` | IEA p.149 中国 2024 production **约 0.9 TWh**；MIIT 总产量 1,170 GWh（含消费类）。 | Yes（IEA 图） | Yes | **No（仅候选约值）** | IEA 数字仅为无数据表支撑的图读约值；MIIT 范围又不同。二者不能互换，不能产生精确行业输入。 |
| `cell_asp_cny_per_kwh` | IEA：中国 battery-**pack** price 2024 近 -30% YoY；CATL 收入/销量可做公司混合实现额代理。 | Yes | IEA：Yes | **No** | 变化率、pack 与 cell 不是一项 CNY/kWh cell ASP；CATL 收入/销量也不是行业单一产品合同 ASP。 |
| `unit_cash_cost_cny_per_kwh` | MIIT 原料年均价；CATL 分部成本；IEA 价格/竞争背景。 | Yes | MIIT/IEA：Yes | **No** | 无化学体系 BOM、采购/套保/滞后、良率和制造费用，不能将原料价或会计成本变成行业单位现金成本。 |

“Yes（缺口被确认）”只表示权威来源确实证明该信息**没有**被其数值化披露；绝不等于字段已经得到数值证据。

## 为什么现有候选链仍不能编译

1. **不得把观察、约值和假设混算。** IEA p.149 的 85% 是一个 maximum-average-utilisation **假设**，不是 `commissioned_share`、`certified_share`、`yield_rate`，更不是中国 2024 实测利用率。`0.9 / 2.8` 或 `2.8 × 85%` 都不应命名为实际有效产能/利用率。
2. **不得跨统计宇宙拼接。** IEA 的 China EV/储能电芯名义产能/图示产出、MIIT 的所有锂离子电池产量和装机、以及 CATL 的合并电池系统销售/成本，范围、库存边界、对象和单位不同。
3. **价格链仍然没有被识别。** 即使可保留“低原料价格、竞争和 pack-price 下行”的背景，也缺少可重算的 cell ASP、unit cash cost、滞后、替代解释与 falsifier，不能让“产能 → 利用率 → 价格”成为 formal mechanism。
4. **正式机制也尚未满足。** 编译器还要求经过独立审阅并被提升为 `formal` 的、同一范围机制；候选来源的存在不会自动跨越该治理门槛。

## 放行结论与最低新增材料

| 决策 | 结果 |
| --- | --- |
| 将 IEA 2.8 TWh、约 0.9 TWh、85% 边界、MIIT 汇总或 CATL 公司事实保存为来源受限的候选证据 | **可以**，但必须保持 `source_reported`、`chart_approximation`、`assumption_bound`、`official_aggregate`、`issuer_reported` 等性质和原范围。 |
| 用这些资料填充任一正式状态、派生实际有效产能/利用率，或生成价格/利润/估值/动作 | **不可以**。 |
| CATL 2024 中国动力电池 `IndustryState` | **仍为 `not_answerable` / `wait_for_validation`。** |

要重新审计 formalization，至少需要一个截止日前可认证、可留存的同范围证据包，包含：

1. 中国 2024 动力/储能**电芯**的明确 nominal capacity、commissioned share、certified/qualified share、yield，及所有加权方法；
2. 同一对象的精确 shipment 与 production（或经审阅的等价定义）及库存边界；
3. 同一产品/地域/交付条件的 cell ASP 和 unit cash cost，连同 BOM、采购时点、套保/滞后、回收、良率和制造费用边界；
4. 至少两名独立审阅者批准产能—利用率—价格机制的方向、范围、反例、滞后与 falsifier，且以新的不可变研究版本保存。

在满足这些门槛前，最深且诚实的结果是可追溯的候选证据和明确的 unknown，而不是用完整外观的数字编译一个不可识别的状态。
