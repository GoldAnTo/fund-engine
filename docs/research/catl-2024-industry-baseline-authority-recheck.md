# CATL 2024 行业基线的权威来源复核（研究记录，不是投资结论）

**固定研究截止日：** `2025-05-15T15:59:59Z`  
**研究问题：** 是否能以截至截止日已公开的高权威资料，为中国（或明确的全球）2024 年电池**电芯**行业建立可重放的“名义产能—实际产出/需求—有效产能或利用率定义”基线，并据此放行数值 `IndustryState` 或“产能 → 利用率 → 价格”正式机制？  
**与既有记录的关系：** 本文是对 [CATL 行业基线缺口评估](catl-industry-baseline-gap-assessment.md) 的补充复核。它不改写既有冻结资料、夹具或生产代码；如果未来采用任何发现，必须创建新的、不可变的研究版本。

## 结论

**正式放行结论：不能放行现有的数值 `IndustryState`，也不能把“产能 → 利用率 → 价格”提升为正式机制。**

但原有缺口需要被更精确地描述：IEA 在截止日前发布的 *Global EV Outlook 2025* 的同一份、可版本定位的报告中，确实给出一个中国 2024 年 **EV 与储能电池范围**的强候选链：

- 图表给出中国已安装锂离子电芯**名义**制造产能 **2.8 TWh/年**；
- 相邻图表绘出中国 **2024 production** 约 **0.9 TWh/年**，并定义该 production 为 EV battery 与 battery storage，排除 EV 与电池囤货；
- 同图把“既有产能以 **85%** 运转”称为其假设的 **maximum average utilisation rate**。

这足以创建一个有来源、带范围标签的**候选/人工审阅输入**，但还不足以把 `effective_capacity_gwh` 或实际 `utilisation` 写成行业观察事实：85% 是 IEA 的最大平均利用率**假设**，不是 2024 年中国实际利用率；报告没有公开厂级输入、爬坡、良率、停产、认证转换或可下载原始数表。图中的 2024 产量也仅以读图的“约数”呈现。因此不能用 `0.9 / 2.8`，或用 `2.8 × 85%`，制造带虚假精度的行业利用率/有效产能数字，更不能把该比率传导为 CATL ASP、收入或估值结论。

## 放行判据与结果

| 所需项目 | 截止日前最强证据 | 结果 | 严格处理 |
| --- | --- | --- | --- |
| 同一地理范围的名义电芯产能 | IEA PDF p.148 的“中国已安装锂离子电芯 nameplate manufacturing capacity”图，显示 **2.8 TWh**。 | 通过，但仅适用于 IEA 的中国 EV/储能电芯框架。 | 可作为 `source_reported` 候选观察值，保存“2.8 TWh、China、2024、nameplate、图表页码”。 |
| 同一范围的实际产出/需求 | IEA PDF p.149 的中国 `2024 production` 柱约为 **0.9 TWh**；注释明确是 EV battery + battery storage production，排除库存。 | 部分通过。 | 仅能保存为报告图表的**约值/视觉读数**，不得伪装为 IEA 已发布的精确 GWh 数据列。也不可替换为工信部 1,170 GWh（范围不同）。 |
| 明确的“有效产能”或利用率定义 | IEA p.149 把“既有制造产能以 85% 利用”描述为 assumed maximum average utilisation rate。 | 仅有方法假设，未通过观察性利用率门槛。 | 可保存为 `assumption_bound`：`maximum_average_utilisation = 85%`，而不是 2024 中国实际有效产能或行业利用率。 |
| 可校准的“产能 → 利用率 → 价格”机制 | IEA 也报告价格下行与竞争/低原料价格的背景，但无 CATL 价格、BOM、合同或利润桥。 | 未通过。 | 保持 candidate；不得依此生成价格、估值、加仓或止损结论。 |

因此，**候选基线可前进一小步，正式数值行业状态不能前进。** 这一结论既不忽略 IEA 已发表的中国图表，也不把 IEA 的情景上限误作行业实测数据。

## 主要证据：IEA 的中国 EV/储能电芯候选链

### 来源身份、可得性、权利与保留

| 字段 | 可核验记录 |
| --- | --- |
| 发布者 | International Energy Agency (IEA)。 |
| 文献 | [*Global EV Outlook 2025* 官方报告页](https://www.iea.org/reports/global-ev-outlook-2025)；该页标注 **Published 14 May 2025** 与 **CC BY 4.0**。 |
| 截止日前可得性 | 官方发布日为 2025-05-14，早于截止日。官方 PDF 的文档元数据为 `CreationDate 2025-05-13T12:59:33+02:00`、`ModDate 2025-05-14T17:09:25+02:00`；后者为 `2025-05-14T15:09:25Z`，早于固定截止日。静态发布版本：[PDF（IEA blob asset `0aa4762f-…`）](https://iea.blob.core.windows.net/assets/0aa4762f-c1cb-4495-987a-25945d6de5e8/GlobalEVOutlook2025.pdf)。 |
| 内容指纹 | 对上述静态 PDF 的 SHA-256：`12a76f53d9a72755b107d812f4b71d4adcf8dc09ccf4a8f38920a3f6a9267bc2`。这是研究复核时取得的字节指纹；未来入库仍须重新取得并验证。 |
| 权利/保留边界 | IEA 对报告标为 CC BY 4.0，但图表的底层数据来自 Benchmark Mineral Intelligence、BloombergNEF 与 EV Volumes。可保留 URL、页码、哈希和派生观察/范围，不应把未获许可的底层厂级数据或商业数据集当作本项目资产。建议现有系统采用 `display_policy: derived_only` 与 `retention: hash_locator_and_derived_observations`。 |

### 精确定位与指标范围

| 项目 | 精确定位、原文/忠实转述 | 口径与可用性 |
| --- | --- | --- |
| 中国名义产能 | PDF **p.148** 图题为 *Installed lithium-ion battery cell nameplate manufacturing capacity by region and location of manufacturer’s headquarters, 2024*；中国栏的图上总量为 **2.8 TWh**。相邻在线章节说明 2024 年 global battery **cell** manufacturing capacity 超过 3 TWh，并在图注中明确“numbers … indicate total **nameplate manufacturing capacity** … per year”。 | 地理范围是工厂所在地 China；单位 TWh/年；对象是 lithium-ion battery **cell** manufacturing capacity。按制造商总部分类的颜色不改变工厂所在地总量。图注称数据到 2025 Q1；它不是“截至 2024-12-31 物理审计”，而是 IEA 截止 2025 Q1 对 2024 已安装产能的回溯估计。 |
| 中国 2024 产出 | PDF **p.149** 图题为 *Historical production and announced expansion of battery manufacturing maximum output by region, 2023, 2024 and 2030*。中国 `2024 production` 紫色柱位于约 **0.9 TWh/年**。 | p.149 注释明示：`Production refers to EV battery and battery storage, and EV and battery stockpiling are excluded from the analysis.` 因此它是 IEA 的制造产出估计，而不是中国车辆装车、公司销量、消费者电池或库存变化。原图未给出可下载的精确数据表或柱顶标签，本文保留“约 0.9 TWh”的原始精度。 |
| 有效产能的唯一明示处理 | PDF **p.149** 注释：`Increased utilisation refers to the gap between 2023 production levels and existing capacity being utilised at 85%, which is assumed to be the maximum average utilisation rate.` 并说 2030 已承诺/初步产能也使用 85%。 | 这定义的是 IEA 对“最大平均利用率下的可产出上限”的**假设性情景**，不是实际观察到的 2024 中国 industry utilisation。其缺少厂级良率、认证、停线、产品规格和产线爬坡输入，不能命名为实际 `effective_capacity_gwh`。 |
| 数据来源与范围风险 | p.149 标注为 `IEA analysis based on data from Benchmark Mineral Intelligence, Bloomberg New Energy Finance, and EV Volumes`。p.149 对制造产能的范围还包括“已认证服务 EV 与储能市场的公司，以及尚未获 EV 市场认证的公司”。 | IEA 的公开结论可被引用；它不授予商业数据供应商的原始数据再发布权。生产与名义产能虽在同一 IEA 电池/储能框架中，但认证状态的处理意味着产能分母并不是“已达到相同质量/客户验证标准的实际可供产能”。 |

### 为什么不把候选链算成“实际利用率”

1. **观察与假设不同。** `85%` 是明确标注的最大平均利用率假设；它不能变成中国 2024 实测利用率，也不能证明 IEA 的 2024 产量由同一批、全部达到生产条件的名义产线制造。
2. **图表精度有限。** 2.8 TWh 有图上标签，而 2024 production 只有柱形与刻度；公开材料没有可下载数列。即使作内部范围估计，也必须保留“约值”和来源图页，不能生成小数点后百分比。
3. **能力范围不等于可销售产能。** IEA 将尚未获 EV 市场认证的公司纳入制造产能范围；这恰是“名义”与“可在特定市场交付”的差异，且不等同于公司或行业的良率/有效产能。
4. **中国行业不等于 CATL。** CATL 年报的合并电池系统口径、76.33% 公司报告利用率、516 GWh 产量和 475 GWh 销量仍是公司级事实。不能从中国 IEA 图表反推 CATL 的份额、价格、毛利或边际产能。

## 交叉核验和被拒绝的替代拼接

### 工信部：强产量锚点，但不是这一条电芯基线的分母

[工业和信息化部电子信息司《2024 年全国锂离子电池行业运行情况》](https://www.miit.gov.cn/jgsj/dzs/gzdt/art/2025/art_dc20a4d4b3a74dcf91ac1b212f87bbfc.html)发布于 `2025-02-27 15:06`。其第 5–7 行称全国锂离子电池总产量为 **1,170 GWh**，其中消费/储能/动力为 **84/260/826 GWh**，并称新能源汽车与新型储能合计装机超过 **645 GWh**。

它是高权威的中国锂电制造观察，但产品范围包含消费类，且其“装机”不是 IEA 的生产。**不得**用 `1,170 / 2,800`、`826 / 2,800` 或 `645 / 2,800` 宣称 IEA 中国电芯利用率；应将其保留为单独、`reference_only` 的宏观/制造事实。

### 国家统计局：有严谨定义，但行业分类过宽

[国家统计局《2024 年四季度全国规模以上工业产能利用率为 76.2%》](https://www.stats.gov.cn/xxgk/sjfb/zxfb2020/202501/t20250117_1958324.html)成文于 2025-01-17。其指标解释（第 65–85 行）将产能利用率定义为实际产出与生产能力之比，且二者均以**价值量**计；生产能力是在劳动力、原料、燃料、运输供给保证下设备正常运行、可以长期维持的产品产出。该页年度数据最高只到“电气机械和器材制造业”（75.1%），不是电芯 GWh 行业。

这可用于系统术语审计，却不能把 75.1% 映射为中国电池电芯的 GWh 利用率，也不能补足 IEA 的良率/认证/停线细节。

### IEA 2024 版与截止日后的材料

- [*Global EV Outlook 2024* 电池章节](https://www.iea.org/reports/global-ev-outlook-2024/outlook-for-battery-and-energy-demand) 的 85% 是面向 2030 情景的最大利用率假设，不能作为 2024 实测观察；它不补足本次候选链。
- 任何 2025-05-15 之后的协会报告、商业数据库二次转述或 2026 IEA 图表，即使声称 2024 中国产能/利用率，也违反固定研究截止日，不能倒填本基线。
- 证券研究、GGII 预测或发行人对第三方预测的转述，可能提供“有效产能”的市场说法，但既不是可验证的协会原件，也不是 2024 已观察到的结果；不应用来消除未知项。

## 建议的研究对象表示（尚未实施）

若研究系统允许显式区分观察值、图表约值和模型假设，可创建**新的候选研究版本**，而不是覆写原版本。最小结构应为：

| 字段 | 可安全写入的内容 | 不可写入的内容 |
| --- | --- | --- |
| `industry.nameplate_capacity` | `2.8 TWh/year`；`scope=China, lithium-ion cell, IEA 2024 installed`；`status=source_reported`。 | “中国全部电池有效产能”。 |
| `industry.production` | `approximately 0.9 TWh/year`；`scope=China, EV + stationary-storage battery production; stockpiling excluded`；`status=chart_approximation`。 | 精确到个位 GWh 的制造产量、CATL 出货量或中国装机。 |
| `industry.max_average_utilisation_assumption` | `85%`；`status=assumption_bound`；`purpose=IEA existing-capacity scenario`。 | `industry.actual_utilisation` 或 `industry.effective_capacity_gwh`。 |
| 所有派生比率 | 默认 `unknown`，除非新版本有人工审阅的范围、图表读取方法、同范围确认和明确的 `derived` 标记。 | 自动计算一个“32.1% 利用率”并将其视为行业事实。 |

即使上述候选版本被采纳，它也只能增强“供给冗余/竞争压力”这一**可证伪的背景假说**。要放行正式机制，仍至少需要：

1. 可保存、可复算的中国 2024 电芯实际有效产能或已观察利用率（而非 85% 上限假设）；
2. 与产能/产出同一范围的价格、产品组合或毛利桥，及其滞后和反例；
3. 独立研究者审阅范围、图表约值读取、产能认证边界和反事实；
4. 对 CATL 的公司级销量、库存、实现价格/成本和财务结果的桥接，且不以行业总量替代公司事实。

## 最终门槛决定

| 门槛 | 决定 |
| --- | --- |
| 资料发现是否有增量价值 | **是。** IEA p.148–149 是截止日前、同一发布者、同一中国 EV/储能框架内最接近完整链的公开证据，应加入下一次研究审阅包。 |
| 可否新建“IEA 候选/假设边界”研究版本 | **可以，但须人工审阅，并将产量标为图表约值、85% 标为假设。** |
| 可否填充现有 `industry.effective_capacity_gwh`、`industry.utilisation`，或正式化价格机制 | **不可以。** 证据没有测得行业有效产能/利用率，更没有公司价格/利润传导验证。 |
| 当前答案状态 | 维持 `not_answerable` / `wait_for_validation`，直到最低新增证据与人工审阅均完成。 |

