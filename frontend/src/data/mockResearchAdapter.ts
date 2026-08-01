import type {
  ActivityEvent,
  CausalStepView,
  CompanyExposure,
  Conclusion,
  EvidenceRecord,
  FundDisclosure,
  MockScenario,
  PageStateErrorKind,
  ResearchCaseDossier,
  ResearchCaseSummary,
  RelationshipGraph,
  DocumentsQuery,
  DossierQuery,
  OverviewQuery,
  RelationshipQuery,
  ResearchFrameworkNode,
  ReviewOutcome,
  SearchHit,
  SourceDocumentView,
  DocumentSpan,
  ReviewQueueItem,
  ThesisAssessment,
  ValuationSnapshot,
  WorkspaceOverview,
} from "../domain/types";
import { PageStateError } from "../domain/types";
import type {
  CaseWorkbenchView,
  DataCenterView,
  LibraryView,
  LinkReviewPayload,
  NewResearchView,
  RelationshipGraphView,
  ResearchClient,
  ResearchPlanView,
  ReviewQueueView,
  ReviewQueueViewItem,
  ThemeIndexView,
  ThesisRerunResult,
  ThemeWorkbenchView,
  VersionsView,
  WorkspaceOverviewScreen,
  WorkspaceOverviewView,
} from "../domain/prototypeTypes";
import {
  buildCaseWorkbenchView,
  buildDataCenterView,
  buildLibraryView,
  buildNewResearchView,
  buildRelationshipGraphView,
  buildResearchPlanView,
  buildThemeIndexView,
  buildThemeWorkbenchView,
  buildVersionsView,
  buildWorkspaceOverview,
  buildWorkspaceOverviewScreen,
  EVIDENCE_LINKS as PROTOTYPE_EVIDENCE_LINKS,
  REVIEW_QUEUE as PROTOTYPE_REVIEW_QUEUE,
  STATEMENTS as PROTOTYPE_STATEMENTS,
} from "./prototypeFixture";

// ── Stable mock data ───────────────────────────────────────────────────────
//
// This adapter does not mirror the current /workbench response. It builds
// the front-end domain model directly from the three reference prototypes:
//  • 设计原型1.png — WorkspaceOverview
//  • 设计原型2.png — ResearchCaseDossier
//  • 设计原型.png  — RelationshipGraph

const CASES: ResearchCaseSummary[] = [
  {
    id: "ai-compute",
    title: "AI 算力链",
    topic: "AI 算力链 · 深度研究",
    author: "陈子仪",
    created_at: "2024-05-24T10:30:00+08:00",
    updated_at: "2024-05-24T10:30:00+08:00",
    has_markdown: true,
  },
  {
    id: "urban-noa",
    title: "城市 NOA 商业化落地路径",
    topic: "智能驾驶 · 行业研究",
    author: "张子仪",
    created_at: "2024-05-15T10:21:00+08:00",
    updated_at: "2024-05-20T14:32:00+08:00",
    has_markdown: true,
  },
];

const FRAMEWORK: ResearchFrameworkNode[] = [
  {
    id: "f1",
    sequence: "1",
    title: "需求端：AI 应用与算力需求",
    children: [
      { id: "f1-1", sequence: "1.1", title: "大模型演进与训练需求" },
      { id: "f1-2", sequence: "1.2", title: "推理需求爆发与商业化落地" },
      { id: "f1-3", sequence: "1.3", title: "端侧行业应用渗透" },
    ],
  },
];

const OVERVIEW: WorkspaceOverview = {
  case_id: "ai-compute",
  case_title: "AI 算力链",
  case_topic: "AI 算力链 · 深度研究",
  case_count_label: "32",
  case_topic_tags: ["深度研究"],
  last_updated_at: "2024-05-24 10:30",
  bullets: [
    "全球 AI 算力需求将快速增长，2024–2026 年复合增速预计达 56%。推理侧需求成为新增量，结构上以 GPU、HBM、光模块、电源、液冷为支撑，向下游云厂商与智算中心、供服链路逐步分化。",
    "产业链从上游芯片、HBM、先进封装、中游服务器与液冷，向下游云厂与智算中心、供服链路逐步分化，关键环节存在结构性紧缺。",
    "国产替代在部分环节取得进展，但高端 GPU、HBM、先进制程与高端设备仍受到出口、地缘与出口管制带来不确定性扰动。",
  ],
  key_changes: [
    {
      id: "kc1",
      tag: "新增",
      text: "英伟达发布 Blackwell 架构 GB200 NVL72 机柜方案，进一步提升推理性能与能效。",
      detail: "单柜 72 颗 Blackwell GPU，相比 H100 在 FP4 推理性能上有显著提升，对液冷与电源提出新要求。",
      occurred_at: "2024-05-24",
      source_label: "NVIDIA",
      review_state: "machine_generated",
    },
    {
      id: "kc2",
      tag: "更新",
      text: "台积电上调 2024 年 CoWoS 产能指引，全年产能同比增长约 30%。",
      detail: "新增产能主要被英伟达、AMD 与博通锁定，对外可分配给中小客户的产能仍然紧张。",
      occurred_at: "2024-05-23",
      source_label: "工商时报",
      review_state: "reviewed",
    },
    {
      id: "kc3",
      tag: "新增",
      text: "工信部：加快液冷等先进计算关键技术研发，推动智算中心标准化建设。",
      detail: "首次明确液冷为先进计算关键支撑技术，对国内液冷厂商释放中长期需求信号。",
      occurred_at: "2024-05-21",
      source_label: "工信部",
      review_state: "reviewed",
    },
    {
      id: "kc4",
      tag: "风险",
      text: "美国调整高端 GPU 出口管制细则，部分数据中心订单延迟。",
      detail: "数据中心出口许可申请出现积压，部分订单交付节奏被推迟。",
      occurred_at: "2024-05-22",
      source_label: "海关总署",
      review_state: "reviewed",
    },
    {
      id: "kc5",
      tag: "新增",
      text: "中国电信发布 2024–2025 年液冷服务器采购架构，预算规模超 40%。",
      detail: "采购量与单台价值同步提升，国产液冷厂商份额有望显著扩大。",
      occurred_at: "2024-05-20",
      source_label: "招标公告",
      review_state: "machine_generated",
    },
  ],
  framework: FRAMEWORK,
  totals: {
    evidence_total: 1243,
    reliable_pct: 68,
    pending_review: 156,
    major_blockers: 5,
  },
  task_queue: [
    {
      id: "t1",
      category: "待审核",
      title: "英伟达 GB200 NVL72 方案解析",
      source: "NVIDIA 官网",
      updated_at: "10:15",
      assignee: "陈子仪",
    },
    {
      id: "t2",
      category: "待审核",
      title: "台积电 CoWoS 产能指引更新",
      source: "工商时报",
      updated_at: "09:42",
      assignee: "陈子仪",
    },
    {
      id: "t3",
      category: "待审核",
      title: "中国电信液冷服务器集采公告",
      source: "中国电信采购",
      updated_at: "昨天",
      assignee: "陈子仪",
    },
    {
      id: "t4",
      category: "进行中",
      title: "海外云厂商开支跟踪（Q2）",
      source: "公告 · 财报",
      updated_at: "进行中",
      assignee: "陈子仪",
    },
    {
      id: "t5",
      category: "进行中",
      title: "国内智算中心项目跟踪",
      source: "各地政府官网",
      updated_at: "进行中",
      assignee: "陈子仪",
    },
    {
      id: "t6",
      category: "等待",
      title: "HBM 供应链与国产化进展",
      source: "专家访谈",
      updated_at: "等待中",
      assignee: "陈子仪",
    },
    {
      id: "t7",
      category: "主要阻塞",
      title: "高端 GPU 出口管制影响评估",
      source: "BIS · OFAC",
      updated_at: "昨天",
      assignee: "陈子仪",
    },
  ],
  evidence_changes: [
    {
      id: "ec1",
      case_title: "AI 算力链",
      description: "英伟达 GB200 NVL72 方案",
      source: "NVIDIA",
      kind: "行业数据",
      updated_at: "10:15",
    },
    {
      id: "ec2",
      case_title: "AI 算力链",
      description: "台积电 CoWoS 产能指引",
      source: "工商时报",
      kind: "更新数据",
      updated_at: "09:42",
    },
    {
      id: "ec3",
      case_title: "AI 算力链",
      description: "中国电信液冷服务器集采公告",
      source: "中国电信",
      kind: "公告发布",
      updated_at: "昨天",
    },
    {
      id: "ec4",
      case_title: "AI 算力链",
      description: "美国调整高端 GPU 出口管制",
      source: "BIS",
      kind: "监管变化",
      updated_at: "昨天",
    },
    {
      id: "ec5",
      case_title: "AI 算力链",
      description: "工信部：加快液冷关键技术研发",
      source: "工信部",
      kind: "行业数据",
      updated_at: "昨天",
    },
    {
      id: "ec6",
      case_title: "AI 算力链",
      description: "AMD MI300X 发布与供应链",
      source: "AMD",
      kind: "公告发布",
      updated_at: "05-22",
    },
    {
      id: "ec7",
      case_title: "AI 算力链",
      description: "英特尔 Gaudi3 进展跟踪",
      source: "Intel",
      kind: "行业数据",
      updated_at: "05-21",
    },
  ],
  activity: [
    {
      id: "a1",
      actor: "张瑞琦",
      verb: "完成了核心观点",
      target: "推理需求",
      occurred_at: "10:24",
      group: "今天",
    },
    {
      id: "a2",
      actor: "你",
      verb: "标注了证据可靠性",
      target: "GB200 NVL72 方案",
      occurred_at: "09:56",
      group: "今天",
    },
    {
      id: "a3",
      actor: "陈昊",
      verb: "添加了证据",
      target: "台积电产能指引",
      occurred_at: "09:42",
      group: "今天",
    },
    {
      id: "a4",
      actor: "系统",
      verb: "完成数据更新",
      target: "全量研究数据",
      occurred_at: "09:30",
      group: "今天",
    },
    {
      id: "a5",
      actor: "王铭",
      verb: "评论了图表",
      target: "全球算力需求",
      occurred_at: "17:15",
      group: "昨天",
    },
    {
      id: "a6",
      actor: "李想",
      verb: "更新了风险提示",
      target: "全球算力需求",
      occurred_at: "16:40",
      group: "昨天",
    },
    {
      id: "a7",
      actor: "系统",
      verb: "任务状态变更：2 → 5",
      target: "今天",
      occurred_at: "15:33",
      group: "昨天",
    },
    {
      id: "a8",
      actor: "赵显",
      verb: "创建了项目",
      target: "AI 算力链",
      occurred_at: "05-20",
      group: "更早",
    },
    {
      id: "a9",
      actor: "系统",
      verb: "证据库更新 128 条",
      target: "今天",
      occurred_at: "05-20",
      group: "更早",
    },
  ],
};

const DOSSIER: ResearchCaseDossier = {
  case: CASES[0],
  theses: CASES,
  focus_thesis_id: "t-gpu-demand",
  tabs: ["研究摘要", "关键图表", "核心观点", "风险与假设", "相关公司", "研究日志"],
  assessment: {
    id: "a-gpu-1",
    thesis_id: "t-gpu-demand",
    conclusion: "supported",
    rationale:
      "2024–2026 年，全球新能源汽车净渗透持续提升，中国产业链具备结构性优势，盈利中枢上移。",
    bullets: [
      "政策端持续支持乘商电动化转型，购置税减免、地方以旧换新与路权优惠延续。",
      "动力电池、电机、电控成本继续下探，整车售价中位数同步回落，需求弹性显化。",
      "头部车企通过规模效应、产业链垂直整合与海外渠道扩张，盈利边际改善。",
    ],
    gaps: ["缺反证：行业整体投运比例低于规划"],
    provisional: true,
    review: null,
    major_gap: "缺头部车企跨城覆盖样本",
    status_label: "验证中",
    supply_chain_level: "供应链级",
    updated_at: "2024-05-31",
    confidence_label: "中高",
    focus_axes: ["政策驱动", "成本下降", "需求扩张", "供应链分化"],
  } satisfies ThesisAssessment,
  causal_chain: [
    {
      id: "step-1",
      sequence: 1,
      title: "政策与准入",
      description: "试点扩容 + 路测准入开放",
      status: "ai_pending_review",
    },
    {
      id: "step-2",
      sequence: 2,
      title: "技术方案收敛",
      description: "轻地图 + 端到端提升泛化能力",
      status: "ai_pending_review",
    },
    {
      id: "step-3",
      sequence: 3,
      title: "成本结构优化",
      description: "算力下沉 · 传感器方案优化",
      status: "ai_pending_review",
    },
    {
      id: "step-4",
      sequence: 4,
      title: "产品与商业模式",
      description: "车型覆盖 → 行 → 商保通用门槛",
      status: "ai_pending_review",
    },
    {
      id: "step-5",
      sequence: 5,
      title: "规模化落地",
      description: "2025 拐点 / 2026 规模交付",
      status: "ai_pending_review",
    },
  ] satisfies CausalStepView[],
  evidence: {
    supports: [
      {
        link_id: "ev-support-1",
        statement_id: "st-1",
        statement_text: "工信部：开展智能网联汽车准入试点",
        statement_kind: "disclosed_fact",
        span_id: "sp-1",
        verbatim_text:
          "支持 L3 级及以上自动驾驶功能的智能网联汽车产品开展准入试点。在指定区域道路条件下上路通行。",
        locator: { page: 2 },
        reason: "明确准入试点路径",
        role: "supports",
        scope: { segment: "全行业" },
        period: "2024-04-12",
        available_at: "2024-04-12T00:00:00+08:00",
        review_state: "machine_generated",
        source_label: "工信部官网",
        reliability: 0.92,
      },
      {
        link_id: "ev-support-2",
        statement_id: "st-2",
        statement_text: "北京新能源智能汽车开通道路",
        statement_kind: "disclosed_fact",
        span_id: "sp-2",
        verbatim_text:
          "本次开放的自动驾驶测试道路里程达 6000 公里，覆盖亦庄、通州。",
        locator: { page: 1 },
        reason: "城市级政策推进",
        role: "supports",
        scope: { city: "北京" },
        period: "2024-04-08",
        available_at: "2024-04-08T00:00:00+08:00",
        review_state: "machine_generated",
        source_label: "北京发改委",
        reliability: 0.87,
      },
      {
        link_id: "ev-support-3",
        statement_id: "st-3",
        statement_text: "小鹏 XOS 5.2.0 全国推送城市 NOA",
        statement_kind: "management_attribution",
        span_id: "sp-3",
        verbatim_text:
          "基于端到大模型，已实现不限城市高辅驾的全国范围内 NOA。",
        locator: { page: 1 },
        reason: "代表车企技术兑现",
        role: "supports",
        scope: { company: "小鹏" },
        period: "2024-04-30",
        available_at: "2024-04-30T00:00:00+08:00",
        review_state: "machine_generated",
        source_label: "小鹏专业号",
        reliability: 0.85,
      },
    ] satisfies EvidenceRecord[],
    contradicts: [
      {
        link_id: "ev-contra-1",
        statement_id: "st-1",
        statement_text: "特斯拉 FSD 入华未获批复",
        statement_kind: "disclosed_fact",
        span_id: "sp-4",
        verbatim_text:
          "如准入上路，特斯拉 FSD 在中国的落地时间表仍不确定，需等待进一步批准。",
        locator: { page: 3 },
        reason: "国际玩家入华节奏滞后",
        role: "contradicts",
        scope: { company: "特斯拉" },
        period: "2024-05-10",
        available_at: "2024-05-10T00:00:00+08:00",
        review_state: "machine_generated",
        source_label: "汽车之家解读",
        reliability: 0.78,
      },
      {
        link_id: "ev-contra-2",
        statement_id: "st-1",
        statement_text: "用户对 NOA 接管频次仍存疑虑",
        statement_kind: "research_opinion",
        span_id: "sp-5",
        verbatim_text:
          "调研显示，超过 50% 用户表示在复杂路口仍会频繁接管，信任度仍偏低。",
        locator: { page: 4 },
        reason: "用户体验侧反证",
        role: "contradicts",
        scope: { segment: "用户" },
        period: "2024-05-08",
        available_at: "2024-05-08T00:00:00+08:00",
        review_state: "machine_generated",
        source_label: "汽车之家调研",
        reliability: 0.73,
      },
      {
        link_id: "ev-contra-3",
        statement_id: "st-1",
        statement_text: "高精地图审批流程较稳",
        statement_kind: "disclosed_fact",
        span_id: "sp-6",
        verbatim_text:
          "多家图商反馈，高精地图的测绘与审批周期较长，影响开城节奏。",
        locator: { page: 2 },
        reason: "基础设施约束",
        role: "contradicts",
        scope: { segment: "地图" },
        period: "2024-05-25",
        available_at: "2024-05-25T00:00:00+08:00",
        review_state: "machine_generated",
        source_label: "高德地图书面回复",
        reliability: 0.71,
      },
    ] satisfies EvidenceRecord[],
    contextualizes: [
      {
        link_id: "ev-ctx-1",
        statement_id: "st-1",
        statement_text: "中国 L3 准入试点通知",
        statement_kind: "disclosed_fact",
        span_id: "sp-7",
        verbatim_text:
          "试点用于积累场景数据，不构成量产准入承诺。",
        locator: { page: 2 },
        reason: "明确范围与限制",
        role: "contextualizes",
        scope: { segment: "政策" },
        period: "2024-04-12",
        available_at: "2024-04-12T00:00:00+08:00",
        review_state: "machine_generated",
        source_label: "工信部官网",
        reliability: 0.7,
      },
    ] satisfies EvidenceRecord[],
  },
  competitive_explanations: [
    "轻地图方案在 2024Q1 后成为主流，但仍受高精地图审批制约",
    "端到端模型提升泛化能力，但极端场景接管率仍偏高",
    "成本结构上，激光雷达方案下探至 3000 元区间，纯视觉方案在低端价位段竞争",
  ],
  gaps: [
    "缺头部车企跨城覆盖样本",
    "缺接管率口径下的安全指标",
    "缺保险定价与事故责任划分的现行规范",
  ],
  log: [
    {
      id: "lg-1",
      at: "2024-05-20 14:32",
      text: "已标记为复核：城市 NOA 商业化落地路径",
    },
    {
      id: "lg-2",
      at: "2024-05-15 10:21",
      text: "由 张子仪 创建案例档案",
    },
  ],
};

const RELATIONSHIP: RelationshipGraph = {
  case: { ...CASES[0], title: "新能源汽车产业链研究", topic: "新能源汽车产业链研究" },
  nodes: [
    // 证据列（Evidence）
    {
      id: "ev-1",
      kind: "statement",
      label: "中汽协：4 月新能源汽车销量 85.0 万辆，环比 +32.3%，渗透率 36.0%",
      group: "evidence",
      chip: "行业数据",
      publisher: "中汽协",
      publish_date: "2024-05-10",
      reliability_bar: 0.92,
    },
    {
      id: "ev-2",
      kind: "statement",
      label: "欧洲议会通过《Fit for 55》",
      group: "evidence",
      chip: "行业数据",
      publisher: "European Parliament",
      publish_date: "2024-04-24",
      reliability_bar: 0.82,
    },
    {
      id: "ev-3",
      kind: "statement",
      label: "麒麟锂硫电池 -6.2%",
      group: "evidence",
      chip: "存储数据",
      publisher: "SMM",
      publish_date: "2024-05-08",
      reliability_bar: 0.74,
    },
    {
      id: "ev-4",
      kind: "statement",
      label: "宁德时代发布神行超充电池",
      group: "evidence",
      chip: "价格数据",
      publisher: "公司发布",
      publish_date: "2024-04-25",
      reliability_bar: 0.88,
    },
    {
      id: "ev-5",
      kind: "statement",
      label: "国内动力电池装机量 TOP10",
      group: "evidence",
      chip: "行业数据",
      publisher: "GGII",
      publish_date: "2024-05-07",
      reliability_bar: 0.81,
    },
    // 命题列（Propositions）
    {
      id: "pr-1",
      kind: "statement",
      label: "政策支持传导：利好行业政策",
      group: "proposition",
      chip: "利好的行业政策",
      description: "国家与地方政策持续支持乘商电动化。",
      reliability_bar: 0.78,
    },
    {
      id: "pr-2",
      kind: "statement",
      label: "成本持续下降：驱动盈利改善",
      group: "proposition",
      chip: "成本持续下降",
      description: "原材料与制造端降本同步显现，毛利率改善。",
      reliability_bar: 0.65,
    },
    {
      id: "pr-3",
      kind: "statement",
      label: "需求维持高增长：渗透率提升",
      group: "proposition",
      chip: "需求扩张 + 渗透率提升",
      description: "乘用车与商用车需求双轮驱动，渗透率持续抬升。",
      reliability_bar: 0.86,
    },
    {
      id: "pr-4",
      kind: "statement",
      label: "供给格局优化：头部集中度提升",
      group: "proposition",
      chip: "头部格局优化",
      description: "CR5 进一步提升，二线品牌份额被挤压。",
      reliability_bar: 0.7,
    },
    // 因果链（Causal）
    { id: "step-1", kind: "step", sequence: 1, label: "政策驱动乘商需求", group: "causal", chapter: "三、需求", description: "政策推动乘商需求扩张" },
    { id: "step-2", kind: "step", sequence: 2, label: "驱动功率提升", group: "causal", chapter: "三、需求", description: "需求扩张驱动车型功率密度提升" },
    { id: "step-3", kind: "step", sequence: 3, label: "规模效应释放", group: "causal", chapter: "四、兑现", description: "规模效应推动单位制造成本下降" },
    { id: "step-4", kind: "step", sequence: 4, label: "成本下降", group: "causal", chapter: "四、兑现", description: "成本结构进一步优化" },
    { id: "step-5", kind: "step", sequence: 5, label: "毛利率上移", group: "causal", chapter: "四、兑现", description: "毛利率中枢上移" },
    { id: "step-6", kind: "step", sequence: 6, label: "现金流改善", group: "causal", chapter: "五、估值", description: "现金流改善" },
    { id: "step-7", kind: "step", sequence: 7, label: "研发投入增强", group: "causal", chapter: "五、估值", description: "研发投入强度提升" },
    { id: "step-8", kind: "step", sequence: 8, label: "技术领先与创新提升", group: "causal", chapter: "六、护城河", description: "技术领先与创新提升" },
    // 公司列
    {
      id: "co-1",
      kind: "company",
      label: "宁德时代",
      code: "300750.SZ",
      group: "company",
      sector: "关键电池",
      relevance: 0.86,
    },
    {
      id: "co-2",
      kind: "company",
      label: "比亚迪",
      code: "002594.SZ",
      group: "company",
      sector: "整车制造",
      relevance: 0.78,
    },
    {
      id: "co-3",
      kind: "company",
      label: "恩捷股份",
      code: "002812.SZ",
      group: "company",
      sector: "隔膜",
      relevance: 0.65,
    },
    {
      id: "co-4",
      kind: "company",
      label: "天赐材料",
      code: "002709.SZ",
      group: "company",
      sector: "电池电解液",
      relevance: 0.42,
    },
    // 基金列
    {
      id: "fd-1",
      kind: "fund",
      label: "景顺长城新能源产业",
      code: "011328",
      group: "fund",
      weight: "持仓占比 8.72%",
      report_period: "2024-03-31",
      relevance_score: 0.75,
    },
    {
      id: "fd-2",
      kind: "fund",
      label: "汇添富中证电池主题 ETF",
      code: "159755",
      group: "fund",
      weight: "持仓占比 6.31%",
      report_period: "2024-03-31",
      relevance_score: 0.75,
    },
    {
      id: "fd-3",
      kind: "fund",
      label: "工银瑞信医疗保健 A",
      code: "001717",
      group: "fund",
      weight: "持仓占比 5.15%",
      report_period: "2024-03-31",
      relevance_score: 0.61,
    },
    {
      id: "fd-4",
      kind: "fund",
      label: "广发高端制造股票 A",
      code: "004997",
      group: "fund",
      weight: "持仓占比 3.88%",
      report_period: "2024-03-31",
      relevance_score: 0.58,
    },
  ],
  edges: [
    // 证据 → 命题
    { id: "e-1", kind: "evidence", source: "ev-1", target: "pr-3", role: "supports", reason: "销量数据支持增长", review_state: "machine_generated" },
    { id: "e-2", kind: "evidence", source: "ev-2", target: "pr-1", role: "supports", reason: "海外政策支持", review_state: "machine_generated" },
    { id: "e-3", kind: "evidence", source: "ev-3", target: "pr-2", role: "contradicts", reason: "部分原材料涨价", review_state: "machine_generated" },
    { id: "e-4", kind: "evidence", source: "ev-4", target: "pr-3", role: "supports", reason: "新技术推动渗透", review_state: "machine_generated" },
    { id: "e-5", kind: "evidence", source: "ev-5", target: "pr-4", role: "contextualizes", reason: "集中度数据背景", review_state: "machine_generated" },
    // 命题 → 因果链
    { id: "e-6", kind: "causal", source: "pr-1", target: "step-1", role: "supports", reason: "政策直接传导", review_state: "machine_generated" },
    { id: "e-7", kind: "causal", source: "pr-2", target: "step-2", reason: "成本→毛利" },
    { id: "e-8", kind: "causal", source: "pr-3", target: "step-3" },
    // 因果链内部
    { id: "e-9", kind: "causal", source: "step-1", target: "step-2" },
    { id: "e-10", kind: "causal", source: "step-2", target: "step-3" },
    { id: "e-11", kind: "causal", source: "step-3", target: "step-4" },
    { id: "e-12", kind: "causal", source: "step-4", target: "step-5" },
    { id: "e-13", kind: "causal", source: "step-5", target: "step-6" },
    { id: "e-14", kind: "causal", source: "step-6", target: "step-7" },
    { id: "e-15", kind: "causal", source: "step-7", target: "step-8" },
    // 因果链 → 公司
    { id: "e-16", kind: "theme_role", source: "step-8", target: "co-1" },
    { id: "e-17", kind: "theme_role", source: "step-5", target: "co-2" },
    { id: "e-18", kind: "theme_role", source: "step-3", target: "co-3" },
    { id: "e-19", kind: "theme_role", source: "step-4", target: "co-4" },
    // 公司 → 基金
    { id: "e-20", kind: "holding", source: "co-1", target: "fd-1", weight: "持仓 8.72%", report_period: "2024-03-31" },
    { id: "e-21", kind: "holding", source: "co-2", target: "fd-2", weight: "持仓 6.31%", report_period: "2024-03-31" },
    { id: "e-22", kind: "holding", source: "co-3", target: "fd-3", weight: "持仓 5.15%", report_period: "2024-03-31" },
    { id: "e-23", kind: "holding", source: "co-4", target: "fd-4", weight: "持仓 3.88%", report_period: "2024-03-31" },
  ],
  legend: [
    { id: "lg-evidence", label: "证据", group: "evidence" },
    { id: "lg-prop", label: "命题", group: "proposition" },
    { id: "lg-causal", label: "因果链", group: "causal" },
    { id: "lg-company", label: "公司", group: "company" },
    { id: "lg-fund", label: "基金", group: "fund" },
  ],
};

const DOCUMENTS: SourceDocumentView[] = [
  {
    id: "doc-1",
    title: "中汽协：2024 年 4 月新能源汽车产销数据 PDF",
    publisher: "中汽协",
    document_type: "行业资料",
    publish_date: "2024-05-10",
    available_at: "2024-05-10T00:00:00+08:00",
    acquired_at: "2024-05-10T09:00:00+08:00",
    parser_version: "docling-v1.2.3",
    parse_quality: "ok",
    linked_cases: [
      { id: "ai-compute", title: "AI 算力链" },
      { id: "ev-battery", title: "动力电池产业链" },
    ],
    span_count: 4128,
    statement_count: 38,
    version_label: "v3 · 2024-05-10",
  },
  {
    id: "doc-2",
    title: "European Parliament: Fit for 55",
    publisher: "European Parliament",
    document_type: "行业资料",
    publish_date: "2024-04-24",
    available_at: "2024-04-24T00:00:00+02:00",
    acquired_at: "2024-05-01T08:00:00+08:00",
    parser_version: "docling-v1.2.3",
    parse_quality: "ok",
    linked_cases: [{ id: "ev-battery", title: "动力电池产业链" }],
    span_count: 3120,
    statement_count: 27,
    version_label: "v1 · 2024-04-24",
  },
  {
    id: "doc-3",
    title: "麒麟锂硫电池量产公告",
    publisher: "麒麟电池",
    document_type: "公告",
    publish_date: "2024-05-08",
    available_at: "2024-05-08T00:00:00+08:00",
    acquired_at: "2024-05-09T08:00:00+08:00",
    parser_version: "docling-v1.2.3",
    parse_quality: "partial",
    parse_failure_stage: "table-row-extraction",
    linked_cases: [{ id: "ev-battery", title: "动力电池产业链" }],
    span_count: 980,
    statement_count: 12,
    version_label: "v2 · 2024-05-08",
  },
  {
    id: "doc-4",
    title: "宁德时代神行超充电池发布会",
    publisher: "宁德时代",
    document_type: "公告",
    publish_date: "2024-04-25",
    available_at: "2024-04-25T00:00:00+08:00",
    acquired_at: "2024-04-26T08:00:00+08:00",
    parser_version: "docling-v1.2.3",
    parse_quality: "ok",
    linked_cases: [{ id: "ev-battery", title: "动力电池产业链" }],
    span_count: 1230,
    statement_count: 18,
    version_label: "v1 · 2024-04-25",
  },
  {
    id: "doc-5",
    title: "国内动力电池装机量月度报告",
    publisher: "GGII",
    document_type: "行业资料",
    publish_date: "2024-05-07",
    available_at: "2024-05-07T00:00:00+08:00",
    acquired_at: "2024-05-08T08:00:00+08:00",
    parser_version: "docling-v1.2.3",
    parse_quality: "failed",
    parse_failure_stage: "table-extraction",
    linked_cases: [{ id: "ev-battery", title: "动力电池产业链" }],
    span_count: 0,
    statement_count: 0,
    version_label: "v1 · 2024-05-07",
  },
];

const REVIEW_QUEUE: ReviewQueueItem[] = [
  {
    id: "rq-1",
    kind: "evidence_link",
    case_id: "ai-compute",
    case_title: "AI 算力链",
    thesis_id: "t-gpu-demand",
    thesis_title: "GPU 需求将增长",
    proposed_by: "ai",
    proposed_at: "2024-05-24 09:30",
    preview:
      "台积电上调 2024 年 CoWoS 产能指引，全年产能同比增长约 30%。",
    reason: "与上游供应链传导环节存在一致证据",
    scope: { segment: "晶圆代工", company: "台积电" },
    available_at: "2024-05-23T09:00:00+08:00",
    status: "pending",
  },
  {
    id: "rq-2",
    kind: "causal_edge",
    case_id: "ai-compute",
    case_title: "AI 算力链",
    thesis_id: "t-gpu-demand",
    thesis_title: "GPU 需求将增长",
    proposed_by: "ai",
    proposed_at: "2024-05-24 08:55",
    preview: "云厂商 CapEx 增加 → GPU/服务器采购增加 → 公司收入增长",
    reason: "需要验证上游传导证据门槛",
    scope: { segment: "云服务" },
    available_at: "2024-05-24T08:00:00+08:00",
    status: "pending",
  },
  {
    id: "rq-3",
    kind: "statement",
    case_id: "ai-compute",
    case_title: "AI 算力链",
    thesis_id: "t-gpu-demand",
    thesis_title: "GPU 需求将增长",
    proposed_by: "ai",
    proposed_at: "2024-05-23 17:00",
    preview: "工信部：加快液冷等先进计算关键技术研发",
    reason: "原文已切分；需确认规范化文本",
    scope: { segment: "政策" },
    available_at: "2024-05-21T00:00:00+08:00",
    status: "pending",
  },
  {
    id: "rq-4",
    kind: "entity_alignment",
    case_id: "ai-compute",
    case_title: "AI 算力链",
    thesis_id: "t-gpu-demand",
    thesis_title: "GPU 需求将增长",
    proposed_by: "ai",
    proposed_at: "2024-05-23 11:20",
    preview: "宁德时代 vs 麒麟电池：股票代码冲突",
    reason: "两家不同主体，疑似不同公司被错误对齐",
    scope: { entity: "宁德时代" },
    available_at: "2024-05-23T11:00:00+08:00",
    status: "pending",
  },
];

const COMPANIES: CompanyExposure[] = [
  {
    company_id: "co-1",
    company_name: "宁德时代",
    role: "动力电池龙头 · 主供应商",
    scope: "全球新能源整车厂",
    stocks: [{ stock_id: "st-1", code: "300750.SZ", name: "宁德时代", market: "SZ" }],
  },
];

const FUNDS: FundDisclosure[] = [
  {
    disclosure_id: "fd-1",
    fund_id: "fd-1",
    fund_code: "011328",
    fund_name: "景顺长城新能源产业",
    stock_id: "st-1",
    stock_code: "300750.SZ",
    stock_name: "宁德时代",
    weight: "8.72%",
    report_period: "2024-03-31",
    published_at: "2024-04-22",
    acquired_at: "2024-04-23T08:00:00+08:00",
    source: "基金季报",
  },
];

const VALUATIONS: ValuationSnapshot[] = [
  {
    stock_id: "st-1",
    stock_code: "300750.SZ",
    stock_name: "宁德时代",
    as_of_date: "2024-05-20",
    metric_name: "PE_TTM",
    metric_value: "22.6",
    source: "Wind",
    definition: "最近 12 个月归母净利润口径",
  },
];

// ── Helpers ───────────────────────────────────────────────────────────────

function simulateLatency<T>(value: T): Promise<T> {
  return new Promise((resolve) => {
    setTimeout(() => resolve(value), 60);
  });
}

function matchCutoff<T extends { available_at?: string }>(
  items: T[],
  cutoff?: string
): T[] {
  if (!cutoff) return items;
  const cutoffMs = Date.parse(cutoff);
  return items.filter((it) => {
    if (!it.available_at) return true;
    return Date.parse(it.available_at) <= cutoffMs;
  });
}

// ── Scenarios ────────────────────────────────────────────────────────────
//
// Scenarios mutate the data the adapter returns. Switching scenarios does
// not require touching any page; the adapter is the single integration point.

function emptyOverview(): WorkspaceOverview {
  return {
    ...OVERVIEW,
    case_title: "（新案例）",
    case_topic: "未命名 · 首次使用",
    bullets: [],
    key_changes: [],
    framework: [],
    totals: { evidence_total: 0, reliable_pct: 0, pending_review: 0, major_blockers: 0 },
    task_queue: [],
    evidence_changes: [],
    activity: [],
  };
}

function emptyDossier(caseId: string): ResearchCaseDossier {
  return {
    ...DOSSIER,
    case: CASES.find((c) => c.id === caseId) ?? CASES[0],
    assessment: {
      ...DOSSIER.assessment!,
      conclusion: "insufficient_evidence",
      rationale: "尚无证据进入此案例。",
      gaps: ["待导入第一份资料"],
      provisional: true,
      major_gap: "无证据",
    },
    evidence: { supports: [], contradicts: [], contextualizes: [] },
    causal_chain: [],
    competitive_explanations: [],
    gaps: ["待导入第一份资料"],
    log: [],
  };
}

function conflictDossier(caseId: string): ResearchCaseDossier {
  // Force both sides to be visible, exaggerate the conflict for the test.
  return {
    ...DOSSIER,
    case: CASES.find((c) => c.id === caseId) ?? CASES[0],
    assessment: {
      ...DOSSIER.assessment!,
      conclusion: "supported",
      rationale: "支持证据略多于反证，但分歧明显。",
      major_gap: "需要独立第三方证据",
    },
  };
}

function insufficientDossier(caseId: string): ResearchCaseDossier {
  return {
    ...DOSSIER,
    case: CASES.find((c) => c.id === caseId) ?? CASES[0],
    assessment: {
      ...DOSSIER.assessment!,
      conclusion: "insufficient_evidence",
      rationale: "证据不足以作出支持或反证判断。",
      gaps: [
        "缺直接 CapEx 披露",
        "缺跨城覆盖样本",
        "缺第三方独立验证",
      ],
      major_gap: "缺直接证据",
    },
    evidence: { supports: [], contradicts: [], contextualizes: [] },
  };
}

function staleValuations(): ValuationSnapshot[] {
  // as_of_date 在 cutoff 之前，触发 stale 状态
  return VALUATIONS.map((v) => ({ ...v, as_of_date: "2023-12-31" }));
}

function largeRelationship(): RelationshipGraph {
  // 1428 节点 / 3264 边（页面要做虚拟化）
  const nodes = [...RELATIONSHIP.nodes];
  const edges = [...RELATIONSHIP.edges];
  const extraGroups = ["evidence", "proposition", "causal", "company", "fund"] as const;
  let i = 0;
  while (nodes.length < 1428) {
    const g = extraGroups[i % extraGroups.length];
    nodes.push({
      id: `gen-${i}`,
      kind:
        g === "evidence" ? "statement"
        : g === "proposition" ? "statement"
        : g === "causal" ? "step"
        : g === "company" ? "company"
        : "fund",
      label: `派生节点 ${i}`,
      group: g,
    });
    i++;
  }
  while (edges.length < 3264) {
    const a = nodes[(i * 7) % nodes.length];
    const b = nodes[(i * 13 + 5) % nodes.length];
    if (a.id !== b.id) {
      edges.push({
        id: `gen-e-${i}`,
        kind: i % 4 === 0 ? "evidence" : i % 4 === 1 ? "causal" : i % 4 === 2 ? "theme_role" : "holding",
        source: a.id,
        target: b.id,
      });
    }
    i++;
  }
  return { ...RELATIONSHIP, nodes, edges };
}

function parseFailedDocs(): SourceDocumentView[] {
  // parse_failed scenario: make every previously-parseable document fail so
  // the inspector and table both visibly switch to the failure state.
  return DOCUMENTS.map((d) => ({
    ...d,
    parse_quality: "failed" as const,
    parse_failure_stage: "table-extraction",
    span_count: 0,
    statement_count: 0,
  }));
}

// ── Adapter ───────────────────────────────────────────────────────────────

export class MockResearchAdapter implements ResearchClient {
  private scenario: MockScenario;
  // mutable per-instance copies for tests that write review decisions.
  private queue: ReviewQueueItem[];
  // track decision history so submitReviewDecision has stable semantics.
  private decisions: { itemId: string; outcome: ReviewOutcome; reason: string }[] = [];

  constructor(opts: { scenario?: MockScenario } = {}) {
    this.scenario = opts.scenario ?? "typical";
    this.queue = REVIEW_QUEUE.map((r) => ({ ...r }));
  }

  setScenario(scenario: MockScenario): void {
    this.scenario = scenario;
    this.queue = REVIEW_QUEUE.map((r) => ({ ...r }));
    this.decisions = [];
  }

  getDecisions() {
    return [...this.decisions];
  }

  private throwIfOffline(): void {
    if (this.scenario === "offline") {
      throw new PageStateError("backend_unavailable", "后端不可用");
    }
  }

  private throwIfPermissionDenied(): void {
    if (this.scenario === "permission") {
      throw new PageStateError("permission_denied", "权限不足");
    }
  }

  async getOverview(query?: OverviewQuery): Promise<WorkspaceOverview> {
    this.throwIfOffline();
    if (this.scenario === "empty") return simulateLatency(emptyOverview());
    return simulateLatency(OVERVIEW);
  }

  async getCaseDossier(
    caseId: string,
    query?: DossierQuery
  ): Promise<ResearchCaseDossier> {
    this.throwIfOffline();
    const cutoff = query?.cutoff;

    if (this.scenario === "empty") return simulateLatency(emptyDossier(caseId));
    if (this.scenario === "insufficient")
      return simulateLatency(insufficientDossier(caseId));
    if (this.scenario === "conflict") return simulateLatency(conflictDossier(caseId));

    const dossier: ResearchCaseDossier = {
      ...DOSSIER,
      case: CASES.find((c) => c.id === caseId) ?? CASES[0],
      evidence: {
        supports: matchCutoff(DOSSIER.evidence.supports, cutoff),
        contradicts: matchCutoff(DOSSIER.evidence.contradicts, cutoff),
        contextualizes: matchCutoff(DOSSIER.evidence.contextualizes, cutoff),
      },
    };
    return simulateLatency(dossier);
  }

  async getRelationshipGraph(
    caseId: string,
    query?: RelationshipQuery
  ): Promise<RelationshipGraph> {
    this.throwIfOffline();
    if (this.scenario === "large") return simulateLatency(largeRelationship());
    return simulateLatency({
      ...RELATIONSHIP,
      case: CASES.find((c) => c.id === caseId) ?? CASES[0],
    });
  }

  async getDocuments(query?: DocumentsQuery): Promise<SourceDocumentView[]> {
    this.throwIfOffline();
    const docs = this.scenario === "parse_failed" ? parseFailedDocs() : DOCUMENTS;
    const q = (query?.query ?? "").toLowerCase();
    const filtered = q
      ? docs.filter(
          (d) =>
            (d.title ?? "").toLowerCase().includes(q) ||
            (d.publisher ?? "").toLowerCase().includes(q) ||
            d.linked_cases.some((c) => c.title.toLowerCase().includes(q))
        )
      : docs;
    return simulateLatency(filtered);
  }

  async getDocumentDetail(documentId: string): Promise<{
    document: SourceDocumentView;
    spans: DocumentSpan[];
  }> {
    this.throwIfOffline();
    const docs = this.scenario === "parse_failed" ? parseFailedDocs() : DOCUMENTS;
    const document = docs.find((d) => d.id === documentId) ?? docs[0];
    const spans: DocumentSpan[] =
      document.parse_quality === "failed"
        ? []
        : [
            {
              id: "sp-1",
              document_id: document.id,
              locator: { page: 1, table: 1 },
              verbatim_text:
                "4 月新能源汽车销量 85.0 万辆，同比 +32.3%，环比 +6.8%；渗透率 36.0%，同比 +6.4pct。",
              cited_by: [
                { evidence_id: "ev-support-1", thesis_id: "t-gpu-demand", role: "supports" },
              ],
            },
            {
              id: "sp-2",
              document_id: document.id,
              locator: { page: 2, table: 2 },
              verbatim_text:
                "国内动力电池装机量 TOP10 合计 28.5 GWh，占总装机量 81.4%。",
              cited_by: [
                { evidence_id: "ev-support-2", thesis_id: "t-gpu-demand", role: "supports" },
              ],
            },
          ];
    return simulateLatency({ document, spans });
  }

  async getReviewQueue(): Promise<ReviewQueueItem[]> {
    this.throwIfOffline();
    return simulateLatency(this.queue.map((r) => ({ ...r })));
  }

  async search(query: string): Promise<SearchHit[]> {
    this.throwIfOffline();
    const q = query.trim();
    if (!q) return [];
    return simulateLatency<SearchHit[]>([
      {
        group: "案例",
        id: "ai-compute",
        title: "AI 算力链",
        hint: "GPU/服务器/光模块产业链",
        navigate_to: `/cases/ai-compute`,
      },
      {
        group: "命题",
        id: "t-gpu-demand",
        title: "GPU 需求将增长",
        hint: "上游 CapEx → 硬件采购 → 公司兑现",
        navigate_to: `/cases/ai-compute?thesis=t-gpu-demand`,
      },
      {
        group: "公司",
        id: "co-1",
        title: "宁德时代",
        hint: "动力电池龙头 · 主供应商",
        navigate_to: `/companies/co-1`,
      },
      {
        group: "基金",
        id: "fd-1",
        title: "景顺长城新能源产业",
        hint: "持仓宁德时代 8.72%",
        navigate_to: `/funds/fd-1`,
      },
    ]);
  }

  async getCaseSummaries(): Promise<ResearchCaseSummary[]> {
    this.throwIfOffline();
    return simulateLatency(CASES);
  }

  async submitReviewDecision(
    itemId: string,
    decision: { outcome: ReviewOutcome; conclusion: Conclusion | null; reason: string }
  ): Promise<void> {
    this.throwIfOffline();
    this.throwIfPermissionDenied();
    this.decisions.push({ itemId, outcome: decision.outcome, reason: decision.reason });
    this.queue = this.queue.filter((q) => q.id !== itemId);
    return simulateLatency(undefined);
  }

  // ── Prototype screens ───────────────────────────────────────────────────
  // Each method returns the deterministic fixture that mirrors the
  // prototype/ui/data.js fixture. They are read-only and not affected by
  // scenario mutation; the prototype screens are intentionally always
  // available because the fixture is frozen at snapshot RS-2025-06-30-v3.

  async getWorkspaceOverviewView(): Promise<WorkspaceOverviewView> {
    return simulateLatency(buildWorkspaceOverview());
  }

  async getWorkspaceOverviewScreen(): Promise<WorkspaceOverviewScreen> {
    return simulateLatency(buildWorkspaceOverviewScreen());
  }

  async getNewResearchView(): Promise<NewResearchView> {
    return simulateLatency(buildNewResearchView());
  }

  async getResearchPlanView(): Promise<ResearchPlanView> {
    return simulateLatency(buildResearchPlanView());
  }

  async getCaseWorkbenchView(_caseId: string): Promise<CaseWorkbenchView> {
    return simulateLatency(buildCaseWorkbenchView());
  }

  async getRelationshipGraphView(_caseId: string): Promise<RelationshipGraphView> {
    return simulateLatency(buildRelationshipGraphView());
  }

  async getLibraryView(): Promise<LibraryView> {
    return simulateLatency(buildLibraryView());
  }

  async getDataCenterView(): Promise<DataCenterView> {
    return simulateLatency(buildDataCenterView());
  }

  async getVersionsView(): Promise<VersionsView> {
    return simulateLatency(buildVersionsView());
  }

  async getThemeIndexView(): Promise<ThemeIndexView> {
    return simulateLatency(buildThemeIndexView());
  }

  async getThemeWorkbenchView(themeId: string): Promise<ThemeWorkbenchView> {
    return simulateLatency(buildThemeWorkbenchView(themeId));
  }

  // ── Review queue (screen 6) ────────────────────────────────────────────
  // Built from the same fixture join the screen used to do inline; submitted
  // link reviews drop the item from the queue like the live backend does.

  private linkReviews: { linkId: string; payload: LinkReviewPayload }[] = [];

  getLinkReviews() {
    return [...this.linkReviews];
  }

  async getReviewQueueView(): Promise<ReviewQueueView> {
    this.throwIfOffline();
    const reviewed = new Set(this.linkReviews.map((r) => r.linkId));
    const items: ReviewQueueViewItem[] = PROTOTYPE_REVIEW_QUEUE.filter(
      (item) => !reviewed.has(item.id),
    ).map((item) => {
      const st = PROTOTYPE_STATEMENTS.find((s) => s.id === item.targetId);
      const link = PROTOTYPE_EVIDENCE_LINKS.find(
        (l) => l.statementId === item.targetId,
      );
      return {
        linkId: item.id,
        thesisId: link?.thesisId ?? "",
        caseId: "RC-AIC-2025-01",
        thesisStatement: "",
        aiRole: link?.role ?? "gap",
        aiReason: link?.rationale ?? item.task,
        aiScope: {},
        statementId: st?.id ?? item.targetId,
        statementText: st?.text ?? item.task,
        statementKind: "disclosed_fact",
        verbatimText: link?.sourceSpan ?? item.sourceSpan,
        documentVersionId: link?.sourceVersion ?? item.sourceVersion,
        documentSourceUrl: st?.documentId ?? "",
        documentPublishedAt: st?.publishedAt ?? item.publishedAt,
        availableAt: item.availableAt,
      };
    });
    return simulateLatency({ items });
  }

  async submitLinkReview(
    linkId: string,
    payload: LinkReviewPayload,
  ): Promise<void> {
    this.throwIfOffline();
    this.throwIfPermissionDenied();
    this.linkReviews.push({ linkId, payload });
    return simulateLatency(undefined);
  }

  async rerunThesis(thesisId: string): Promise<ThesisRerunResult> {
    this.throwIfOffline();
    return simulateLatency({
      thesisId,
      mode: "mock",
      assessmentId: "ASSESS-MOCK-RERUN",
      snapshotId: "RS-MOCK-RERUN",
      conclusion: "insufficient_evidence",
      rationale: "mock rerun：结论与证据集合无漂移。",
      gaps: ["需要补充直接传导证据"],
      createdAt: new Date().toISOString(),
    });
  }
}

export const mockExposures = {
  companies: COMPANIES,
  funds: FUNDS,
  valuations: VALUATIONS,
  staleValuations,
};