# Fund Engine 商业化产品基准：从证据工作台到机构投研产品

> 调研日期：2026-08-08  
> 范围：只核验厂商官网、官方产品页、官方帮助中心或官方价格页。本文将“官方宣传的能力”与“本项目应借鉴的产品模式”分开；不以第三方评测、融资新闻或未公开价格作为事实。

## 结论先行

Fund Engine 有潜在的商业切口，但不应与 Wind、Capital IQ Pro 争做“全量金融终端”，也不应与 AlphaSense 争做“全网金融搜索”。更可成立的定位是：**面向中国股票/行业研究团队的、可审计的事件—命题—证据—复核—暴露监测工作台**。

项目已具备稀缺的产品原则：冻结原文、AI 草案与人工决定分离、命题的支持/反驳/缺口、以及到公司/股票/基金披露持仓的时点化关系。商业化需要把这些原则变成可重复交付的工作流、数据授权与团队治理，而非再加一个聊天入口。

建议首个 ICP（理想客户）限定为 **3–20 人的中国公募、私募或卖方行业研究团队**，选择一个高信息密度行业（半导体、AI 算力、医药之一）做付费设计伙伴验证。首个可卖结果不是“AI 报告”，而是每个重大事件在可控时间内产出：

```text
事件与来源 -> ResearchCase / 相互竞争的 Thesis
-> 原文可定位的支持、反驳与缺口
-> 人工复核的当前判断和版本
-> 受披露时点约束的公司/股票/基金暴露清单
-> 新材料或反证触发的监控与复核任务
```

## 市场参照与可借鉴模式

| 产品 | 官方定位、客户与公开商业信息 | 已被官方说明的能力 | 对 Fund Engine 可借鉴的模式 |
|---|---|---|---|
| [AlphaSense](https://www.alpha-sense.com/pricing/) | 面向金融服务、企业与专业服务的市场情报平台；官网列出按席位和企业级年度订阅，具体金额需联系销售。 | [平台页](https://prod.alpha-sense.com/platform/) 把检索、深度研究、内部内容、金融数据、工作流 agent、监控/预警放在同一研究循环；其 [企业文档](https://developer.alpha-sense.com/enterprise/) 还列出 SaaS、BYOK、BYOB 和私有云部署。 | “发现—尽调—持续监控”应是一条工作流；企业版必须把内部材料、权限和部署边界视为产品能力。借鉴其预警和工作区，不借鉴无边界的自动结论。 |
| [Quartr Pro](https://quartr.com/products/quartr-pro) | 定位为金融专业人士的定性上市公司研究平台，面向对冲基金、资管、卖方与 IR；[价格页](https://quartr.com/pricing) 公开为多席位/企业/API 方案、联系销售，无标价。 | 官方称 AI 基于一手 IR 材料，答案可回到来源；提供电话会/转录、公告和路演材料、关键词预警、slide 历史比较、表格图形提取、watchlist 和数据导出。 | 把“只在已许可的一手材料内回答”和“答案旁的来源回跳”做成默认体验；将叙事、KPI、措辞的跨期变化变成事件卡与反证提示。可把合规的中国公告/业绩会材料数据层与工作台分售。 |
| [FactSet Research Management Solutions](https://www.factset.com/solutions/investment-research) | 官方明确服务研究分析师、基金经理和研究团队，主张集中、协作地管理研究并保持透明与合规；公开页面引导咨询而非列出金额。 | 其 [资产管理机构介绍](https://go.factset.com/hubfs/Resources%20Section/Brochures/research-management-solutions-brochure.pdf) 说明集中存储/检索/共享内部研究、与实时提醒联动、分析师绩效度量、审计透明度和与 Excel 模型/市场数据整合。 | 研究案例要成为团队共同资产，而非个人 AI 对话历史：补齐项目、角色、审批、责任人、审计导出、研究连续性和贡献/时效 KPI。严禁把“产出数量”误当研究质量或投资业绩归因。 |
| [S&P Capital IQ Pro](https://www.spglobal.com/market-intelligence/en/solutions/products/sp-capital-iq-pro) | 官方将其作为综合桌面金融情报平台，服务股权研究、投行、资管等；页面为 Request a Demo，未公开订阅金额。 | 官方列有标准化财报、估值/一致预期、同业、所有权、新闻及 Document Intelligence/ChatIQ；并把公司、机构和基金所有权等实体关系放进同一数据产品。 | 证据结论必须能接到**有权使用、可追溯且有时点的结构化实体数据**：公司—证券—基金—持仓—估值/指标。价值重点是“解释某暴露为何相关、数据截至何时”，不是复制其全市场覆盖。Excel/研究备忘录导出应服务现有工作习惯。 |
| [LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace) | 服务分析师、组合经理、投行、财富管理等用户的金融工作台；官方为 Request details，未公开价。 | [AI Search 更新说明](https://www.lseg.com/en/data-analytics/products/workspace/updates/act-with-the-same-confidence-at-a-new-speed-introducing-lseg-workspace-ai-search) 描述跨数据、文档与新闻检索，并连接 Python 和 Office 等常用工具。 | 可信数据、分析、AI 不应各自成为孤岛。按角色设计工作台，并将研究结果嵌入 Excel/PPT/API 和持续监控，而非要求团队改掉所有既有分析工具。 |
| [Bloomberg Terminal：Research / Portfolio Analytics](https://professional.bloomberg.com/products/bloomberg-terminal/research/) | 面向专业金融市场用户的终端和组合分析产品；官网引导联系销售，未公开价。 | 官方的 [Portfolio Analytics](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/) 将持仓、风险、绩效、归因、优化和情景压力置于同一工作流。 | 研究的终点要是“对哪类已知暴露/风险应继续关注”的可审计动作，而不是一张结论图；但研究结论不能越级成为自动交易或推荐。 |
| [Wind 金融终端](https://www.wind.com.cn/portal/zh/WFT/index.html) | 中国机构级综合金融终端，官方面向金融机构、政府、企业和媒体，覆盖股票、基金、宏观行业、研报、组合管理和 API；官网仅给销售联系渠道，未列公开价。 | 官网声明其研报平台分发近五十家证券或行业研究机构的官方授权研报，提供基金研究、组合风险分析、Excel 插件与 Client API。 | 中国市场的护城河首先是资料与数据的**授权、时点、口径、可复用连接器**，不是模型。Fund Engine 应支持接入已购 Wind/其他合规数据，而不是默认抓取或转售；每条输出要保留来源许可证、报告期、公开期与采集时间。 |

### 从竞品得到的边界判断

1. **AlphaSense/Quartr 的共同点**：付费价值来自可查询、可授权、可追溯的内容库加持续预警；通用大模型只是界面，不是护城河。
2. **FactSet 的共同点**：机构愿意为团队工作流、合规留痕、连续性和既有工具整合付费；“帮助研究员完成工作”比“展示漂亮图谱”更接近预算拥有者。
3. **Capital IQ Pro/Wind 的共同点**：金融实体、价格、持仓和研报内容均是昂贵的数据授权生意。Fund Engine 应做它们上层的研究证据与决策治理，避免在早期承诺终端级数据覆盖。
4. 以上官方材料均不能证明其功能质量、客户效果或适合中国材料；这些须用本项目的真实案例、数据许可与用户试用单独验收。

## 为获得商业价值必须补齐的改善

### P0：把“能力集合”收束为一个可付费闭环

当前产品原则很强，但买方不会为抽象的“证据图谱”采购。应将入口固定为“重大事件研究”：一条公告/业绩会/政策/产业数据进入后，系统创建或更新案例，明确 1–3 个相互竞争的命题、证据截止点、正反证据缺口、人工责任人和下一验证事件。结论页只交付：当前判断、主要反证、未决问题、引用链和暴露影响范围。

验收指标应是团队可感知的运营指标，而不是模型分数：首次可复核草案耗时、来源可回跳率、关键结论人工复核覆盖率、反证被发现的提前量、已过期基金持仓暴露的显著标识率。先在 10–20 个真实事件上记录基线，再谈 ROI。

### P0：数据许可、可复现性与合规产品化

将每个来源纳入 `SourceContract`：来源方、许可范围（展示/检索/导出/模型处理）、租户、有效期、地域、原文保留策略与下游使用限制。任何 AI 运行、导出、共享链接和 API 都在运行时检查该合同；无权使用的材料不得进入索引或回答。

与之配套的客户可见能力是：单点登录/RBAC、案例级共享范围、不可修改审计记录、导出审计包、数据删除/保留策略、内部文档与外部资料隔离。对涉证券观点要显著区分“未经人工复核的 AI 草案”“人工研究判断”“不构成投资建议”，并保留冲突、缺口和适用范围。

### P0：将基金穿透从展示特性变成可信的数据契约

基金暴露必须同时呈现 `report_period`（报告期）、`published_at`（披露日）、`acquired_at`（采集日）、来源和缺失/估算标记；不得把滞后披露伪装成实时持仓。公司名、股票、基金份额类别、行业标签与事件对象的对齐应有候选、人工决定和版本历史。没有这些，基金映射会成为最容易伤害可信度的一层。

### P1：让人审成为效率产品，而非合规阻力

采用 FactSet 的团队治理目标、Quartr 的来源并置体验：审核者在一屏完成原文定位、AI 提议、支持/反驳理由、适用范围和决定；提交后进入下一条。按 `statement / evidence link / causal link / entity alignment` 分队列，支持草稿、退回、升级和 SLA，但不提供高风险的一键批量通过。

产品管理视图应只显示从账本推导的研究效能：待审年龄、证据到判断的时滞、人机分歧率、被反证的结论比例、无来源/过期来源比例。不要承诺“AI 推荐收益率”或从相关性归因投资表现。

### P1：嵌入现有投研工作流，形成留存

第一批集成应是：已采购数据源的只读连接器、公告/IR 订阅、Excel 导出、研究备忘录/PDF 的带引用导出、邮箱/企业 IM 事件预警、以及受权限约束的 API。按 Quartr 的两层思路提供产品：研究员桌面工作台 + 面向机构内部系统的 API/事件流；但 API 只输出被授权且带来源/审核状态的数据。

### P2：可重复的商业包装，而不是“按功能报价”

可测试的报价结构是：平台/租户基础费 + 研究席位费 + 已购数据源连接器费 + 私有部署、迁移、定制术语和合规包。不要把第三方内容成本包进低价 SaaS；内容许可、检索/模型处理和导出权应单独核算。竞品官网普遍采用联系销售、企业/席位和 API 分层，而非公布低价自助套餐，说明首期应以设计伙伴和实施型销售验证愿付价格。

## 建议的 90 天商业验证

| 阶段 | 交付 | 停止条件 |
|---|---|---|
| 0–30 天 | 选定一个行业和 2 家设计伙伴；用 10 个真实事件完成可复核案例；签清材料可用于检索、AI 处理、导出和保留的许可边界。 | 无法取得材料权利，或研究员不愿用案例替代个人文档/聊天记录。 |
| 31–60 天 | 上线事件进入、正反证据任务、人审队列、来源回跳、结论版本与时点化基金暴露；记录前述五项基线指标。 | 关键结论不能稳定回到原文，或人工复核成本高于当前手工流程且没有明显质量提升。 |
| 61–90 天 | 交付团队周报、预警、审计导出和 Excel/API 集成；用设计伙伴的真实续用频率、被反证案例与愿付费访谈验证。 | 只能被当作一次性报告生成器，或没有负责人愿为团队授权/工作流付费。 |

## 不应投入的方向

- 早期自建全量行情、资讯、研报和估值数据库，或与 Wind/Capital IQ Pro 比数据广度。
- 用“知识图谱可视化”替代案例、任务、原文与审核；图只在帮助下钻和发现缺口时出现。
- 让 AI 自动发布结论、交易信号或基金推荐；AI 必须保留为可追溯、可拒绝的草案。
- 用基金历史持仓或相关性叙述即时暴露、因果传导或预期收益。
- 在没有明确许可、租户隔离和可删除策略前，汇聚客户内部材料训练或共享模型。

## 参考资料（均为官方一手来源）

- [AlphaSense 平台](https://prod.alpha-sense.com/platform/)；[公开定价与部署说明](https://www.alpha-sense.com/pricing/)；[Enterprise Intelligence 文档](https://developer.alpha-sense.com/enterprise/)。
- [Quartr Pro](https://quartr.com/products/quartr-pro)；[公开 pricing 页面](https://quartr.com/pricing)；[AI Chat 公告](https://quartr.com/newsroom/press-release/quartr-pro-introduces-its-ai-chat)。
- [FactSet Investment Research / RMS](https://www.factset.com/solutions/investment-research)；[RMS for Asset Managers 官方手册](https://go.factset.com/hubfs/Resources%20Section/Brochures/research-management-solutions-brochure.pdf)。
- [S&P Capital IQ Pro](https://www.spglobal.com/market-intelligence/en/solutions/products/sp-capital-iq-pro)；[Document Intelligence 与 AI 更新](https://press.spglobal.com/2024-11-12-S-P-Global-Transforms-S-P-Capital-IQ-Pro-Experience-with-the-Launch-of-New-Generative-AI-Powered-Capabilities?asPDF=1)。
- [LSEG Workspace](https://www.lseg.com/en/data-analytics/products/workspace)；[AI Search 官方更新](https://www.lseg.com/en/data-analytics/products/workspace/updates/act-with-the-same-confidence-at-a-new-speed-introducing-lseg-workspace-ai-search)。
- [Bloomberg Terminal Research](https://professional.bloomberg.com/products/bloomberg-terminal/research/)；[Portfolio Analytics](https://professional.bloomberg.com/products/bloomberg-terminal/portfolio-analytics/)。
- [Wind 金融终端](https://www.wind.com.cn/portal/zh/WFT/index.html)。
