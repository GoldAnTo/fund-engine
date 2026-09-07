/**
 * Fixture 数据: 两个研究案例。
 * 严格对照 docs/superpowers/specs/2026-09-03-evidence-first-company-research-workbench-design.md §6 输入分类、§7 流程、§8 价值判断、§9 页面。
 *
 * 案例 A: CATL 300750.SZ  - 可回答(answerable)
 * 案例 B: Alphabet GOOGL  - 不可回答(not_answerable)
 *
 * 双案例验收来自 SPEC §12.4。
 */

// ============================ 案例 A: CATL 可回答 ============================

export const catlCompany = {
  external_key: "CATL",
  canonical_name: "宁德时代新能源科技股份有限公司",
  ticker: "300750.SZ",
  exchange: "深圳证券交易所",
  trading_currency: "CNY",
  industry: "动力电池 / 储能系统",
};

export const catlResearch = {
  run_id: "run-catl-2026q1-001",
  stage: "completed",
  answerability: "answerable",
  cutoff_at: "2026-06-30 16:00 CST",
  historical_basis_id: "basis-catl-2026q2",
  strategy_version: "v2025.4",
  model_version: "v2025.4-r3",
  question: "海外扩张是否能在 2027 年将公司净利率拉回 12% 以上?",
  question_template: "可选的关注问题:经营改善 / 海外扩张 / 资本开支强度 / 现金流质量",
  context_meta: {
    basis_window: "2024-07-01 → 2026-06-30",
    sources_active: 41,
    sources_reviewed: 38,
    sources_pending_review: 3,
    evidence_facts: 184,
    evidence_facts_confirmed: 171,
    evidence_facts_rejected: 7,
    gaps_open: 4,
    gaps_closed: 12,
  },
  stages: [
    { key: "collecting_sources", label: "资料采集", state: "completed" },
    { key: "analyzing_company", label: "公司分析", state: "completed" },
    { key: "building_forecast", label: "预测建模", state: "completed" },
    { key: "generating_report", label: "报告撰写", state: "completed" },
    { key: "needs_input", label: "关键输入确认", state: "now" },
    { key: "completed", label: "冻结版本", state: "todo" },
  ],
  next_scenario: "保存并冻结 v3 备忘录",
  blockers: [],
  counterevidence_highlights: [
    "海外建厂政策变动: 欧盟 CBAM 2026 起对电池组件按碳足迹征税",
    "国内价格战: 储能电芯 2025 ASP 同比 -18.3%",
    "反证出处: 摩根士丹利 2026/04/21 / 高工储能 2026/05/09",
  ],
};

// 商业模式
export const catlBusiness = {
  modules: [
    {
      key: "ev_battery",
      label: "动力电池",
      revenue_sources: ["新能源车装机 × 单车带电量 × ASP", "海外 OEM 直供"],
      customers: ["国内主流主机厂 / 海外 OEM 直供", "储能集成商与电厂"],
      unit_economics: "单 GWh 不含税收入 ≈ 6.8 亿元;2025 公司均价 0.62 元/Wh",
      capital: "在建产能 552 GWh, 折旧占成本比 14.9%",
      mechanism: "全球客户结构 + 一体化材料 + 海外建厂确定单位毛利",
      kpi: [
        { label: "全球装机份额 (Q1 2026)", value: "37.4%", meta: "同比 +2.1pct" },
        { label: "单 GWh 毛利 (FY25)", value: "≈ 1.21 亿元", meta: "ASP 持续下行" },
        { label: "海外销售占比 (TTM)", value: "38.7%", meta: "剔除本土" },
        { label: "现金循环周期 (Q1 2026)", value: "47 天", meta: "改善 11 天" },
      ],
    },
    {
      key: "ess_storage",
      label: "储能系统",
      revenue_sources: ["户用储能 / 表前电网储能 / 工商业储能"],
      customers: ["集成商、电网公司、配储开发商"],
      unit_economics: "系统级单价 0.78 元/Wh,毛利率 ≈ 18.6% (仍为正)",
      capital: "与动力电池共用产线,边际资本开支低",
      mechanism: "电网级 EPC 增长 + 美/欧 IRR 套保驱动",
      kpi: [
        { label: "出货同比 (FY25)", value: "+62.4%", meta: "海外户储拉动" },
        { label: "毛利率 (Q1 2026)", value: "18.6%", meta: "环比 -0.8pct" },
      ],
    },
    {
      key: "materials_recycling",
      label: "电池材料与回收",
      revenue_sources: ["正极、电解液、隔膜代工 + 锂电回收"],
      customers: ["自供 + 对外销售"],
      unit_economics: "材料业务 2025 净利率 5.4%,波动较大",
      capital: "材料业务资本开支强度 28% (2025)",
      mechanism: "形成正极闭环,但毛利受锂价冲击",
      kpi: [
        { label: "锂回收处理 (FY25)", value: "12.4 万吨", meta: "同比 +45%" },
      ],
    },
  ],
  mechanisms: [
    {
      title: "海外建厂确定性折价",
      levers: ["欧洲/北美产能利用率", "关税与本地化采购系数"],
      falsifier: "若欧盟 CBAM 提前执行至全产品,ASP 再降 4-6%",
      clickable: true,
    },
    {
      title: "价格战反向抗性",
      levers: ["一体化材料替代率", "宁德 vs 二线价格剪刀差"],
      falsifier: "二线厂商若进入 0.55 元/Wh 以下, 价格战边际扩大",
      clickable: true,
    },
    {
      title: "储能 IRR 套利窗口收窄",
      levers: ["户储 / 表前储能市场结构", "电网级订单确认"],
      falsifier: "若 2026Q3 美户储装机同比转负, TTM 增长终止",
      clickable: true,
    },
  ],
  counterevidence: [
    "海外建厂的不确定性: 波兰工厂 2025Q4 实际产能利用率 64% (vs 82% 计划), 折损约 9% 海外毛利",
    "反证出处: 2025 年报 第 53 页 + 高工锂电 2026/04 调研",
    "国内 ASP 下行: 2026/05 现货均价 0.56 元/Wh, 较年初 -11%",
    "反证出处: 高工储能周度价格 (2026/05/09)",
  ],
};

// 驱动与三情景
export const catlDrivers = [
  { key: "ev_install_gwh",   label: "全球装机 GWh",      type: "fact",        values: [491, 588, 712, 833, 955] }, // FY24-28
  { key: "asp_yuan_per_wh",  label: "电芯 ASP 元/Wh",     type: "assumption",  values: [0.62, 0.6, 0.6, 0.61, 0.62] },
  { key: "utilization",       label: "产能利用率",         type: "assumption",  values: [0.78, 0.81, 0.83, 0.84, 0.84] },
  { key: "overseas_mix",     label: "海外销售占比",       type: "assumption",  values: [0.39, 0.42, 0.45, 0.48, 0.5] },
  { key: "rnd_pct",          label: "研发费率",           type: "derived",     values: [0.066, 0.065, 0.064, 0.063, 0.062] },
];

export const catlBridge = [
  // 5 年 FY24..FY28; 单位 CNY bn (营收, EBIT, FCFF)
  { fy: "FY24",  rev: 362.0, ebit: 55.8,  tax: 0.15, dep: 14.4, capex: 38.9,  d_wc: 6.4,  fcff: 28.4, type: "fact" },
  { fy: "FY25",  rev: 408.7, ebit: 60.2,  tax: 0.155, dep: 16.8, capex: 41.6,  d_wc: 4.9,  fcff: 32.1, type: "fact" },
  { fy: "FY26",  rev: 462.4, ebit: 67.9,  tax: 0.155, dep: 19.5, capex: 44.2,  d_wc: 3.8,  fcff: 41.8, type: "derived" },
  { fy: "FY27",  rev: 542.0, ebit: 81.4,  tax: 0.16,  dep: 22.0, capex: 42.0,  d_wc: 3.2,  fcff: 60.8, type: "derived" },
  { fy: "FY28",  rev: 619.3, ebit: 95.7,  tax: 0.16,  dep: 24.4, capex: 38.1,  d_wc: 2.5,  fcff: 84.9, type: "derived" },
];

export const catlScenarios = {
  base: {
    id: "base",
    title: "Base · 价格战延续, ASP 缓慢修复",
    anchor: "ASP FY27 修复至 0.61 元/Wh;海外占比缓慢爬升至 45%",
    assumptions: [
      { k: "ASP FY27", v: "0.61", note: "修复幅度有限" },
      { k: "海外销售占比 FY27", v: "45%", note: "考虑 CBAM 兑现" },
      { k: "产能利用率 FY27", v: "83%", note: "维持略低于名义" },
    ],
    enterprise_value: { min: 1320, max: 1480, unit: "亿元" },
    dd_cny_per_share: { min: 215, max: 248 },
  },
  bull: {
    id: "bull",
    title: "Bull · 海外建厂顺利,ASP 修复 + 储能放量",
    anchor: "ASP FY27 修复至 0.65 元/Wh;海外占比突破 50%",
    assumptions: [
      { k: "ASP FY27", v: "0.65", note: "海外拉动 +0.04" },
      { k: "海外销售占比 FY27", v: "52%", note: "高于计划" },
      { k: "户储出货 FY27", v: "+75%", note: "对比 +62% 同期" },
    ],
    enterprise_value: { min: 1620, max: 1820, unit: "亿元" },
    dd_cny_per_share: { min: 268, max: 308 },
  },
  bear: {
    id: "bear",
    title: "Bear · CBAM 提前, 价格战扩大",
    anchor: "ASP FY27 跌至 0.57 元/Wh;海外销售利润缩窄",
    assumptions: [
      { k: "ASP FY27", v: "0.57", note: "CBAM 提前触发" },
      { k: "海外销售占比 FY27", v: "38%", note: "本地化挑战" },
      { k: "产能利用率 FY27", v: "76%", note: "下行" },
    ],
    enterprise_value: { min: 980, max: 1140, unit: "亿元" },
    dd_cny_per_share: { min: 152, max: 188 },
  },
};

// 价值判断
export const catlValuation = {
  asof: {
    price: 224.6,
    fx: 7.18,
    market_cap: 9880,
    shares_diluted: 4.40,
    cutoff_at: "2026-06-30 16:00 CST",
  },
  permission: { fxSource: "PBoC 中间价", capStructSource: "深交所披露 + WIND" },
  range: {
    min: 152,
    max: 308,
    base: { lo: 215, hi: 248 },
    bull: { lo: 268, hi: 308 },
    bear: { lo: 152, hi: 188 },
    current_price: 224.6,
  },
  reverse: {
    driver_key: "FCFF 永续增速",
    implied_value: "2.4%",
    achieved_residual: "0.6%",
    iteration_count: 18,
    narrative: "当前价 224.6 隐含 FCFF 永续增速 2.4%, 高于分析师一致 (1.9%), 接近历史均值 ± 1 个标准差。",
  },
  required_return: 0.09,
  required_vs_achieved: [
    { sec: "300750.SZ", minR: 0.064, maxR: 0.197, meets: "取决于情景" },
  ],
  sensitivity: [
    // [WACC, 永续增速]  -> 每股价值
    { wacc: 0.08, g: 0.015, val: 188 },
    { wacc: 0.08, g: 0.020, val: 215 },
    { wacc: 0.08, g: 0.025, val: 248 },
    { wacc: 0.08, g: 0.030, val: 287 },
    { wacc: 0.09, g: 0.015, val: 162 },
    { wacc: 0.09, g: 0.020, val: 184 },
    { wacc: 0.09, g: 0.025, val: 210 },
    { wacc: 0.09, g: 0.030, val: 240 },
    { wacc: 0.10, g: 0.015, val: 142 },
    { wacc: 0.10, g: 0.020, val: 161 },
    { wacc: 0.10, g: 0.025, val: 182 },
    { wacc: 0.10, g: 0.030, val: 207 },
  ],
};

// 证据清单
export const catlEvidence = [
  { type: "fact",      text: "FY25 营收 4087 亿元, 同比 +12.9%", srcRole: "公司年报", locator: "2025 年报 P.10 主要会计数据", hash: "0x9c3a..f121", key: "rev-fy25" },
  { type: "fact",      text: "2025 出货 612 GWh, 海外销售占比 38.7%", srcRole: "公司年报", locator: "2025 年报 P.18 经营情况讨论", hash: "0x1a0d..9e44", key: "shipment-fy25" },
  { type: "guidance",  text: "FY26 资本开支计划 380-440 亿元", srcRole: "业绩说明会", locator: "2025Q4 业绩说明会 PPT 第 23 页", hash: "0x77be..2c08", key: "capex-fy26" },
  { type: "consensus", text: "卖方一致 FY26 营收 4580 亿元 (n=18)", srcRole: "Wind 一致预期", locator: "Wind 2026-06-28 拉取", hash: "0x44ab..1902", key: "cons-fy26" },
  { type: "fact",      text: "2026Q1 单 GWh 不含税收入 ≈ 6.4 亿元", srcRole: "公司季报", locator: "2026Q1 季报 第 5 节", hash: "0x33ff..2ad1", key: "unit-rev-q1" },
  { type: "assumption",text: "FY27 ASP 修复至 0.61 元/Wh (来自研究问题)", srcRole: "AI 假设", locator: "AI:regulatory-extractor v3.2", hash: "0x55cc..f0a1", key: "asp-ai-fy27" },
  { type: "fact",      text: "海外建厂累计资本承诺约 320 亿元", srcRole: "公司公告", locator: "2024/2025 临时公告 7 件", hash: "0x91df..aab2", key: "overseas-capex" },
  { type: "counter",   text: "波兰工厂 Q4 实际产能利用率 64% (计划 82%)", srcRole: "高工锂电", locator: "2026/04 第 3 期 行业调研", hash: "0x70fa..1234", key: "poland-util" },
  { type: "unknown",   text: "FY27 储能户用出货量走势 - 缺权威公开数据", srcRole: "未授权/缺授权", locator: "解决方法: 接入主机厂配储意见 + 季度贴牌数据", hash: "—", key: "ess-household" },
  { type: "counter",   text: "国内 ASP 现货 0.56 元/Wh (2026/05)", srcRole: "高工储能", locator: "2026/05/09 周度价格", hash: "0x32bb..8f00", key: "asp-current" },
  { type: "fact",      text: "现金循环周期 FY25 末 47 天 (改善 11 天)", srcRole: "公司年报", locator: "2025 年报 P.43 营运能力", hash: "0xffc0..2bd3", key: "ccc-fy25" },
  { type: "guidance",  text: "管理层: 海外产能利用率 18 个月内提升至 90%", srcRole: "业绩说明会", locator: "2026Q1 业绩说明会 第 41 页", hash: "0x00ad..a3f3", key: "overseas-tgt" },
];

// 关键输入决策队列
export const catlCriticalInputs = [
  {
    key: "ci-fy27-asp",
    label: "FY27 ASP 修复路径",
    type: "assumption",
    blocker: false,
    now: { val: "0.61 元/Wh", src: "AI 假设 v3.2 · 来自关键机制推演" },
    impact: "估值 ± 38 元/股, 影响 Base / Bear 切换",
    decisions: [
      { actor: "你 (本地)", when: "2026/06/22", type: "确认", note: "保留原始 0.61" },
    ],
  },
  {
    key: "ci-overseas-mix",
    label: "FY27 海外销售占比",
    type: "assumption",
    blocker: false,
    now: { val: "45%", src: "AI 假设 v3.2" },
    impact: "估值 ± 27 元/股, 影响 Bull / Base 切换",
    decisions: [
      { actor: "你 (本地)", when: "2026/06/22", type: "编辑", note: "从 48% 改为 45%" },
    ],
  },
  {
    key: "ci-capex-fy27",
    label: "FY27 资本开支",
    type: "assumption",
    blocker: false,
    now: { val: "420 亿元", src: "管理层指引 380-440 亿元区间中值" },
    impact: "FCFF 直接传导 ± 18 元/股",
    decisions: [],
  },
  {
    key: "ci-poland-util",
    label: "波兰工厂产能利用率",
    type: "counter",
    blocker: true,
    now: { val: "反证: 64% (Q4 实际)", src: "高工锂电 2026/04" },
    impact: "若纳入, 海外销售毛利下修约 9%",
    decisions: [],
  },
  {
    key: "ci-wacc",
    label: "WACC (估值)",
    type: "assumption",
    blocker: false,
    now: { val: "9.0%", src: "默认策略版本 v2025.4 (本土可比 + ERP 5.5%)" },
    impact: "敏感性 ± 26 元/股",
    decisions: [],
  },
  {
    key: "ci-ess-gap",
    label: "FY27 户用储能出货",
    type: "unknown",
    blocker: true,
    now: { val: "Unknown", src: "缺少权威公开口径" },
    impact: "阻值 Bear 情景置信度, 不构成全局阻塞",
    decisions: [
      { actor: "你 (本地)", when: "2026/06/22", type: "接受缺口", note: "标注为研究债务" },
    ],
  },
];

// 版本
export const catlVersions = [
  {
    id: "rev-catl-3",
    seq: 3,
    timestamp: "2026-06-22 14:08 CST",
    status: "draft-ready",
    summary_tags: ["确认 4 项假设", "修改 1 项 (海外占比)", "新增 2 项反证引用"],
    hash: "0xc2b8..af30",
    basis: "basis-catl-2026q2",
    cutoff: "2026-06-30",
    is_frozen: false,
  },
  {
    id: "rev-catl-2",
    seq: 2,
    timestamp: "2026-04-12 10:21 CST",
    status: "frozen",
    summary_tags: ["FY25 年报季后冻结", "首次建立三情景", "封闭 11 项研究债务"],
    hash: "0x8831..4d50",
    basis: "basis-catl-2026q1",
    cutoff: "2026-04-12",
    is_frozen: true,
  },
  {
    id: "rev-catl-1",
    seq: 1,
    timestamp: "2025-12-30 11:02 CST",
    status: "frozen",
    summary_tags: ["初始骨架", "证据全部未经复核"],
    hash: "0x0091..7080",
    basis: "basis-catl-2025q4",
    cutoff: "2025-12-30",
    is_frozen: true,
  },
];

// 精确原文阅读 fixture
export const catlReader = {
  document: {
    title: "宁德时代 2025 年年度报告",
    publisher: "宁德时代新能源科技股份有限公司",
    issued_at: "2026-03-21",
    capture_at: "2026-03-22 02:14 UTC",
    policy: "深交所披露 + 自动下载",
    hash: "0x9c3a18d7d7a7f121",
    page_label: "P.43 · 营运能力",
    locator_summary: "营运能力分析表 · 周转率行",
    permission_note: "授权: 公司年报披露, 可展示引用条款 + 段落, 受限表头不展示, 仅展示被引单元格",
  },
  snippet_html: `
    <div class="pd-header">
      <span>2025 年年度报告 (P.43)</span>
      <span class="pd-pg">第 53 页 / 共 287 页</span>
    </div>
    <h2 style="font-size:16px;margin:0 0 12px">四、 主营业务分析</h2>
    <p><b>2. 收入和成本变动情况:</b>报告期内公司实现营业收入 4087 亿元,
    较上年同期增长 12.9%, 主要系动力电池销量增长。</p>
    <h3 style="font-size:14px;margin:16px 0 6px">(3) 营运能力分析</h3>
    <p>本集团持续推进应收账款管理与库存周转优化,
    <mark class="locator">应收账款周转天数 2025 年末为 61 天, 较 2024 年末 73 天改善 12 天;
    存货周转天数 76 天, 较 2024 年末 88 天改善 12 天。</mark>
    公司现金循环周期由 2024 年末 58 天降至 47 天, 改善 11 天。</p>
    <h3 style="font-size:14px;margin:16px 0 6px">经营情况讨论 (节选)</h3>
    <p>本集团继续推进匈牙利、墨西哥工厂建设,
    <mark class="locator">报告期内累计海外资本承诺约 320 亿元,
    预计 2026-2027 年陆续投产。</mark>
    波兰工厂于 2024Q3 投产, 实际产能利用率符合预期。</p>
    <h3 style="font-size:14px;margin:16px 0 6px">主要财务数据</h3>
    <table class="pd-table">
      <thead><tr><th>科目</th><th>2025 (亿元)</th><th>2024 (亿元)</th></tr></thead>
      <tbody>
        <tr><td>营业收入</td><td class="cell-hl">4087</td><td>3620</td></tr>
        <tr><td>归母净利润</td><td>507</td><td>442</td></tr>
        <tr><td>EBIT</td><td>602</td><td>558</td></tr>
        <tr><td>经营性现金流</td><td>876</td><td>730</td></tr>
      </tbody>
    </table>
  `,
  lineage: {
    source_register: {
      institution: "宁德时代新能源科技股份有限公司",
      issuance: "深交所披露 / 巨潮资讯网",
      reference: "公告编号 2026-031",
      license: "公开披露,允许引用 + 摘要",
    },
    frozen_artifact: {
      hash: "0x9c3a18d7d7a7f121",
      capture_at: "2026-03-22 02:14 UTC",
      parser: "Docling v2.4 + PJ-Hash v1",
      locator: "page=43 § 营运能力分析",
    },
    atomic_claim: [
      { text: "现金循环周期 FY25 末 47 天 (改善 11 天)", review: "确认", when: "2026-04-12" },
      { text: "海外资本承诺 ~320 亿元", review: "确认", when: "2026-04-12" },
    ],
    facts_into: [
      { key: "ccc-fy25", used_in: ["business_map", "driver_map", "memo"] },
      { key: "overseas-capex", used_in: ["business_map", "driver_map", "memo"] },
    ],
    memo_use: [
      "备忘录 §2 商业模式 → 单 GWh 现金循环指标",
      "备忘录 §4 情景 → 海外建厂确定性折价",
    ],
    permission_note: "许可允许段落引用与被引单元格;表头不展示,仅展示被命中的单元格",
  },
};

// 备忘录全文
export const catlMemo = `
# 关于宁德时代"海外扩张能否将净利率拉回 12% 以上"的公司研究

**基础截止日:** 2026-06-30 16:00 CST  
**回答能力:** 可回答  
**当前价格:** ¥224.6 (2026-06-30)  
**基础 ID:** basis-catl-2026q2  
**策略版本:** v2025.4 · **模型版本:** v2025.4-r3

---

## §1 · 边界与基础

研究在 basis-catl-2026q2 下完成。公司为宁德时代新能源科技股份有限公司
(300750.SZ),单一深交所上市证券,货币 CNY。我们只使用截止 2026-06-30
16:00 CST 之前已发布或已公开可得的研究资料与市场信息,后续公司公告、
年报补丁与最新数据库状态被冻结,不进入本版本。

研究问题:**海外扩张是否能在 2027 年将公司净利率拉回 12% 以上?**  
可回答边界覆盖:商业模式、动力电池单位经济、储能 IRR 套利窗口、
海外建厂确定性与折价;未覆盖:路线变更、锂矿政策剧变等结构性情景。

## §2 · 公司如何赚钱

宁德时代依靠"全球客户结构 + 一体化材料 + 海外建厂"锁定电池单位毛利,
并通过储能 IRR 套利将毛利转化为运营现金流[^ccc][^overseas]。
该机制不依赖单一产品或单一客户,但 2025 ASP 的连续下行
(0.78 元/Wh → 0.62 元/Wh) 已经导致单位毛利占比下降约 4.1pct。

动力电池:2025 出货 612 GWh, 海外销售占比 38.7%[^shipment]。

储能系统:户储 + 表前储能双轮, 2025 同比 +62.4%[^essgrowth]。
我们认为这部分不是"想象空间",而是"已可观测的、独立的现金流贡献"。

材料与回收业务净利率 5.4% (2025), 波动较大,本研究中作为相对独立的
现金流分支, 不参与主营 Drivers 的核心传导。

## §3 · 经营驱动与传导

| 驱动 | 类型 | 当前口径 | 关键风险 |
| --- | --- | --- | --- |
| 全球装机 GWh | fact | 588 (FY25) | — |
| 电芯 ASP 元/Wh | assumption | 0.60 | 价格战持续 |
| 产能利用率 | assumption | 81% (FY25) | 二线冲击 |
| 海外销售占比 | assumption | 42% | CBAM / 政策 |

财务桥接(单位:CNY 亿元):

| 期间 | 营收 | EBIT | FCFF | 类型 |
| --- | --- | --- | --- | --- |
| FY24 | 362.0 | 55.8 | 28.4 | fact |
| FY25 | 408.7 | 60.2 | 32.1 | fact |
| FY26 | 462.4 | 67.9 | 41.8 | derived |
| FY27 | 542.0 | 81.4 | 60.8 | derived |
| FY28 | 619.3 | 95.7 | 84.9 | derived |

## §4 · 三情景预测与价值判断

Base:ASP FY27 修复至 0.61 元/Wh,海外占比 45%,产能利用率 83%。
企业价值 ¥1320-1480 亿 (按敏感性中段),对应 215-248 元/股。

Bull:ASP FY27 修复至 0.65 元/Wh,海外占比突破 50%,户储出货 +75%。
企业价值 ¥1620-1820 亿,对应 268-308 元/股。

Bear:CBAM 提前至 2026 全部生效,ASP FY27 跌至 0.57 元/Wh,海外占比 38%。
企业价值 ¥980-1140 亿,对应 152-188 元/股。

当前价 ¥224.6 隐含 FCFF 永续增速 2.4%, 高于分析师一致 (1.9%)[^revimp]。
差距说明:市场已经对海外扩张给予了部分预期定价,
但仍未对 CBAM 触发场景(本 Bear 情景)定价。

## §5 · 反证与下一步

- 波兰工厂 Q4 实际产能利用率 64% (计划 82%), 折损约 9% 海外毛利[^poland]。
- 国内 ASP 现货 0.56 元/Wh (2026/05), 较年初 -11%[^asp]。
- 研究债务:FY27 户储出货量走势缺权威公开口径[^gap]。

下一步需要验证:2026Q2 财报海外销售披露口径 (2026-08-30);
2026H2 储能出海订单签订;欧盟 CBAM 实施细则 (Q4 2026 评估窗口)。
在以上条件验证前, FY27 净利率 12% 以上的目标在 Base 内成立[^baseanswer],
Bull 情景下成立概率较高[^bullanswer], Bear 情景不成立[^bearanswer]。

[^ccc]: 现金循环周期 FY25 末 47 天 (改善 11 天), 宁德时代 2025 年报 P.43。
[^shipment]: 2025 出货 612 GWh, 海外占比 38.7%, 2025 年报 P.18。
[^essgrowth]: 2025 同比 +62.4%, 2026Q1 季报 第 5 节。
[^overseas]: 海外资本承诺约 320 亿元, 2025 年报 P.41 + 2024 临时公告。
[^revimp]: 反向 DCF 隐含 FCFF 永续增速 2.4%, iteration=18, 算法版本 valuation_set.v1。
[^poland]: 高工锂电 2026/04 第 3 期 行业调研。
[^asp]: 高工储能 2026/05/09 周度价格。
[^gap]: 解决方法: 接入主机厂配储意见 + 季度贴牌数据。
[^baseanswer]: memo §4, Base 假设情景覆盖 FY27 净利率 12% 上限。
[^bullanswer]: memo §4, Bull 假设情景净利率 13.4%, 满足。
[^bearanswer]: memo §4, Bear 假设情景净利率 10.6%, 不满足。
`;

// ============================ 案例 B: Alphabet 不可回答 ============================

export const alphaCompany = {
  external_key: "ALPHABET",
  canonical_name: "Alphabet Inc.",
  ticker: "GOOGL",
  exchange: "NASDAQ Global Select",
  trading_currency: "USD",
  industry: "搜索引擎 / 在线广告 / 云计算",
};

export const alphaResearch = {
  run_id: "run-alph-2026q1-008",
  stage: "needs_input",
  answerability: "not_answerable",
  cutoff_at: "2026-06-30 16:00 ET",
  historical_basis_id: "basis-alph-2026q2",
  strategy_version: "v2025.4",
  model_version: "v2025.4-r3",
  question: "Alphabet 在 AI 推理工作负载下的资本开支节奏是否可持续?",
  question_template: "可选的关注问题:AI 资本开支 / 云业务毛利率 / 搜索份额 / 监管影响",
  context_meta: {
    basis_window: "2024-07-01 → 2026-06-30",
    sources_active: 38,
    sources_reviewed: 11,
    sources_pending_review: 27,
    evidence_facts: 96,
    evidence_facts_confirmed: 41,
    evidence_facts_rejected: 9,
    gaps_open: 17, // 关键缺口
    gaps_closed: 4,
  },
  stages: [
    { key: "collecting_sources", label: "资料采集", state: "completed" },
    { key: "analyzing_company", label: "公司分析", state: "blocked" },
    { key: "building_forecast", label: "预测建模", state: "blocked" },
    { key: "generating_report", label: "报告撰写", state: "blocked" },
    { key: "completed", label: "冻结版本", state: "blocked" },
  ],
  next_scenario: "补齐 Cloud 单位经济 + AI 资本回报口径 (阻塞)",
  blockers: [
    "AI 资本回报分割口径不可获取, Server / Cloud 财务未在年报中拆分",
    "搜索业务流量份额的第三方监控数据缺授权",
    "司法部反垄断最终裁决结果尚未公开",
    "Google Cloud 在 AI 推理工作负载下的毛利率没有可靠披露",
  ],
  counterevidence_highlights: [
    "管理层指引与卖方一致对 Cloud 增速口径不一致 (管理层 30%+ vs 一致 24%)",
    "反证出处: 2025Q4 业绩说明会 transcript + 一致预期",
    "Search 增长在 2026Q1 加速至 9.7% (管理层归因于 AI Overview), 与 Bing 第三方数据冲突",
  ],
};

export const alphaBusiness = {
  modules: [
    {
      key: "search",
      label: "搜索",
      revenue_sources: ["搜索广告 (北美/EMEA/APAC)"],
      customers: ["直接广告主 + DSP 媒介购买"],
      unit_economics: "搜索广告 CPM 与点击率, 但 TOC 缺失第三方份额监控",
      capital: "数据中⼼ + 服务器, 与 Cloud 共用",
      mechanism: "搜索份额 + AI Overview 渗透, 但份额口径缺失",
      kpi: [
        { label: "搜索广告收入 (Q1 2026)", value: "$48.7bn", meta: "公开但口径有争议" },
        { label: "全球搜索份额", value: "Unknown", meta: "无授权第三方" },
      ],
    },
    {
      key: "cloud",
      label: "Google Cloud",
      revenue_sources: ["基础设施 + 平台服务 + AI 推理"],
      customers: ["企业 / 初创 AI 公司"],
      unit_economics: "毛利率公开 23.9% (Q1 26), 但 AI 推理未单独披露",
      capital: "Server / DC, 资本开支 2025 75bn USD",
      mechanism: "GPU 产能 + 训练/推理利率, 但拆分不可获取",
      kpi: [
        { label: "Cloud 营收 (Q1 2026)", value: "$12.3bn", meta: "+30% YoY 管理层口径" },
        { label: "AI 推理毛利", value: "Unknown", meta: "无披露" },
      ],
    },
    {
      key: "youtube",
      label: "YouTube",
      revenue_sources: ["广告 + 订阅"],
      customers: ["广告主 + Premium 订阅"],
      unit_economics: "广告 + ARPU, 但订阅份额与广告互替不可观测",
      capital: "内容 + 基础设施",
      mechanism: "短视频竞争激烈, 份额监控不可得",
      kpi: [
        { label: "广告收入 (Q1 2026)", value: "$8.9bn", meta: "管理层口径" },
        { label: "订阅份额", value: "Unknown", meta: "无授权" },
      ],
    },
  ],
  mechanisms: [
    { title: "AI 资本回报拆分", levers: ["Server / Cloud 财务披露"], falsifier: "在 Cloud 财务未拆分前不能验证机制", clickable: true },
    { title: "搜索份额监控", levers: ["第三方份额数据授权"], falsifier: "缺授权监控时份额不可证", clickable: true },
    { title: "反垄断裁决现金流影响", levers: ["最终裁决结果披露"], falsifier: "裁决未发布前情景不可知", clickable: true },
  ],
  counterevidence: [
    "搜索份额的第三方监控数据缺授权 (SimilarWeb / Statcounter 受地理采样限制)",
    "Cloud AI 推理工作负载未披露, 不确定后续摊销政策",
    "管理层指引与卖方一致对 Cloud 增速口径不一致",
  ],
};

export const alphaDrivers = [
  { key: "search_rev_yoy",   label: "搜索广告 YoY",   type: "fact",        values: [0.082, 0.097, null, null, null] },
  { key: "cloud_growth",     label: "Cloud 增速",      type: "fact",        values: [0.286, 0.30,  null, null, null] },
  { key: "capex_intensity",  label: "资本开支/收入",   type: "unknown",     values: ["—", "—", "—", "—", "—"] },
  { key: "ai_inference_margin","label": "AI 推理单位毛利",type: "unknown",   values: ["—", "—", "—", "—", "—"] },
];

export const alphaBridge = null; // 关键基线缺失, 不导出财务桥接 (按 SPEC §8 阻塞)

export const alphaValuation = null; // 不可回答, 不展示

export const alphaEvidence = [
  { type: "fact",      text: "10-K 2025 营收 $350.0bn",                srcRole: "10-K 年报",   locator: "10-K 2025 P.45", hash: "0x01ae..0091", key: "rev-fy25" },
  { type: "fact",      text: "Q1 2026 搜索广告 $48.7bn (+9.7% YoY)",   srcRole: "10-Q 季报",   locator: "10-Q Q1 2026 P.6", hash: "0x02ee..a921", key: "search-q1" },
  { type: "guidance",  text: "管理层: FY26 capex ~$75bn",               srcRole: "业绩说明会",  locator: "Earnings call Q1 2026 transcript", hash: "0x0441..d309", key: "capex-fy26" },
  { type: "consensus", text: "卖方一致 FY26 营收 $398bn (n=27)",         srcRole: "Bloomberg 共识", locator: "Bloomberg 2026-06-29", hash: "0x0b00..7e8c", key: "cons-fy26" },
  { type: "counter",   text: "管理层 vs 一致对 Cloud 增速口径不一致",   srcRole: "业绩说明会",  locator: "Earnings call Q1 2026 transcript", hash: "—", key: "conflict-cloud-g" },
  { type: "unknown",   text: "AI 推理毛利率 — 公司不单独披露",          srcRole: "披露缺失",   locator: "解决方法: 等待 Q3 2026 财务披露", hash: "—", key: "ai-margin" },
  { type: "unknown",   text: "搜索份额 — 第三方监控无授权",              srcRole: "授权缺失",   locator: "解决方法: 接入 SimilarWeb / Statcounter 采样授权", hash: "—", key: "search-share" },
];

export const alphaCriticalInputs = [
  {
    key: "ci-ai-margin",
    label: "AI 推理单位毛利",
    type: "unknown",
    blocker: true,
    now: { val: "Unknown", src: "披露缺失" },
    impact: "无法建立 Cloud 财务桥接;阻塞整个估值门禁",
    decisions: [],
  },
  {
    key: "ci-search-share",
    label: "搜索业务全球份额",
    type: "unknown",
    blocker: true,
    now: { val: "Unknown", src: "无授权第三方监控" },
    impact: "阻塞商业模式分析 / 反证强度评估",
    decisions: [],
  },
  {
    key: "ci-antitrust",
    label: "反垄断最终裁决",
    type: "unknown",
    blocker: true,
    now: { val: "待公布", src: "司法部案件进度 2026/Q2" },
    impact: "裁决结果可同时影响 Search 与 AI 业务现金流",
    decisions: [],
  },
  {
    key: "ci-cloud-disc",
    label: "Cloud 增速口径冲突",
    type: "counter",
    blocker: true,
    now: { val: "管理层 30% vs 一致 24%", src: "Earnings call Q1 2026" },
    impact: "需要确认采用何种口径, 或并列保留",
    decisions: [],
  },
];

export const alphaVersions = [
  {
    id: "rev-alph-1",
    seq: 1,
    timestamp: "2026-06-22 16:34 CST",
    status: "draft",
    summary_tags: ["不可回答", "未保存为正式版本", "17 项缺口开放"],
    hash: "—",
    basis: "basis-alph-2026q2",
    cutoff: "2026-06-30",
    is_frozen: false,
  },
];

export const alphaMemo = `
# 关于 Alphabet"AI 资本开支节奏是否可持续"的研究

**基础截止日:** 2026-06-30 16:00 ET  
**回答能力:** **不可回答** (not_answerable)  
**关键门禁阻塞:** 见 §5  
**基础 ID:** basis-alph-2026q2

---

## §1 · 边界与基础

本研究在 basis-alph-2026q2 下进行, 截止 2026-06-30 16:00 ET。
研究对象为 Alphabet Inc. (GOOGL, NASDAQ), 即单一证券, USD 计价。
我们不使用截止日之后的任何资料, 包括 AI 推理市场最新份额数据、
2026Q2 财报与未公开监管裁决。

研究问题:**Alphabet 在 AI 推理工作负载下的资本开支节奏是否可持续?**

## §2 · 部分可回答的商业分析

即使整体不可回答, 我们可以提供部分经营分析。

### 2.1 公司当前的现金流形状

Alphabet 2025 全年披露营收 $350.0bn (10-K)。云业务披露
Q1 2026 营收 $12.3bn (+30% YoY, 管理层口径)[^search]。搜索
广告业务在 Q1 2026 +9.7% YoY (管理层归因 AI Overview 上线)[^search2]。

### 2.2 已识别机制

我们识别出三条可能机制, 但**没有一条目前能完整核实**:

1. **AI 资本回报拆分**: 资本开支 75bn USD (FY26 管理层指引)
   摊销到 Cloud / Search 之间缺乏可靠的口径切分[^capex]。
2. **搜索份额监控**: 全球搜索份额的第三方监测 (SimilarWeb,
   Statcounter) 受地理采样限制, 现有授权不接受作为可阅读来源。
3. **反垄断现金流影响**: 司法部反垄断案件的最终裁决尚未公开, 任何
   情景假设均不能被视为可证。

### 2.3 已识别的口径冲突

管理层与卖方一致对 Cloud 增速口径不一致:管理层 30% vs 一致 24%[^conflict]。
我们在此并列保留两种口径, 不由 AI 选择。

## §3 · 已被阻断的环节 (SPEC §11 · 关键基线缺失)

按 SPEC §11 行为, **不允许生成伪精确估值**。

我们因此停止于:

- 不生成三情景预测的 FY26/27/28 数字;
- 不导出 DCF 价值区间;
- 不展示市值隐含条件 (反向 DCF);
- 不展示目标价值 / 回报区间。

我们仍可保存研究为一个 not_answerable 版本, 用作下一步研究债务管理。

## §4 · 反证、缺口与下一步

### 4.1 现有的反证 (按 SPEC §6.3 要求保留)

- 第三方监控数据缺授权: 搜索份额不能建立来源 [^share]。
- 公司不单独披露 AI 推理毛利率 (披露缺失) [^ai]。
- 管理层与卖方对 Cloud 增速口径不一致。

### 4.2 研究债务 (必须解决才能升级可回答)

按严重程度排列:

1. **AI 推理单位毛利** —— 必须等 Q3 2026 财务披露 / 接入授权
2. **搜索业务份额监控** —— 接入 SimilarWeb / Statcounter 采样授权
3. **反垄断最终裁决** —— 等待司法部公布
4. **Cloud 增速口径冲突** —— 用户选择采用管理层 / 一致 / 并列

## §5 · 为什么现在不可回答

按 SPEC §8 价值判断边界:

> 关键经营基线、市场输入或资本结构缺失时, 估值必须停止,
> 而不是生成完整外观的答案。

我们的关键基线 (AI 推理单位毛利) 缺失。同时, 关键估值口径
(份额 + 反垄断) 缺失且其严重程度都意味着不能"用默认值补齐"。

我们因此诚实地输出:**当前不可判断**。

公司分析部分 (已经可获得的公开事实) 仍然有效; 这部分会被保存
作为商业分析的参考。**不展示目标价、估值区间、回报区间、买卖指令。**

我们计划在以下条件之一满足后升级可回答:

- Q3 2026 财务披露 (2026/10) 提供 AI 推理口径; 或
- 接入 SimilarWeb / Statcounter 第三方监控授权;
- 反垄断最终裁决公开后能够建立确定性情景。

[^search]: Alphabet 10-Q Q1 2026 P.6 主要数据表
[^search2]: Alphabet Earnings call Q1 2026 transcript, 管理层 comment
[^capex]: Alphabet Earnings call Q1 2026 transcript;管理层 capex 75bn FY26
[^conflict]: 同 §4.1 反证 (管理层 30% vs 一致 24%)
[^share]: 解决方法见 §4.2 第 2 项
[^ai]: 解决方法见 §4.2 第 1 项
`;

// 案例 B 关键场景导入 demo 数据
export function criticalInputsFor(caseId) {
  return caseId === "catl" ? catlCriticalInputs : alphaCriticalInputs;
}
export function evidenceFor(caseId) {
  return caseId === "catl" ? catlEvidence : alphaEvidence;
}
export function memoFor(caseId) {
  return caseId === "catl" ? catlMemo : alphaMemo;
}
export function readerFor(caseId) {
  return catlReader; // 阅读器 demo 统一用 CATL 来源
}
export function versionsFor(caseId) {
  return caseId === "catl" ? catlVersions : alphaVersions;
}

// 工作面: 从 URL 取当前 case
export function getCurrentCase() {
  if (typeof window === "undefined") return "catl";
  const params = new URLSearchParams(window.location.search);
  return params.get("case") === "alphabet" ? "alphabet" : "catl";
}
