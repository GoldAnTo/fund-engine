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
  ResearchTaskItem,
  ResearchTaskStatus,
  CreateResearchTaskInput,
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
import type { components } from "../contracts/v1";
import { PageStateError } from "../domain/types";
import type {
  CreateEventResearchInput,
  CreateUploadedEventResearchInput,
  EventConclusionVersion,
  EventExtraction,
  EventLifecycle,
  EventLifecycleStatus,
  EventResearchListItem,
  EventResearchScopeFactor,
  EventResearchScopeFactorInput,
  EventReviewQueue,
  EventSourceType,
  EventWorkbench,
} from "../domain/eventResearch";
import type {
  AuthorizeResearchPreparationInput,
  ConfirmResearchPreparationClaimsInput,
  ConfirmResearchPreparationProtocolInput,
  ResearchPreparation,
  ResearchPreparationEvent,
  ResearchPreparationEventsPage,
  RetryResearchPreparationInput,
} from "../domain/researchPreparation";
import type {
  AutomaticResearchStart,
  AutomaticResearchView,
} from "../domain/automaticResearch";
import type {
  AssessmentReviewPayload,
  AssessmentReviewResult,
  CaseSummaryItem,
  CaseWorkbenchView,
  CompanyDossierView,
  CompanyListItem,
  CompanyListView,
  CreateCaseInput,
  CreateCaseResult,
  DataCenterView,
  DataMetricSelection,
  ExtractStatementsResult,
  IngestRunResult,
  LibraryView,
  LinkReviewPayload,
  NewResearchView,
  ProposeEvidenceResult,
  RelationshipGraphView,
  ResearchClient,
  ResearchPlanView,
  ReviewQueueView,
  ReviewQueueViewItem,
  ThemeIndexView,
  ThesisRerunResult,
  ThemeWorkbenchView,
  ResearchRunDetail,
  ResearchRunSummary,
  ProposalReviewItem,
  StartResearchRunOptions,
  ProposalReviewPayload,
  TopicListItem,
  TopicPathNode,
  TopicThesisView,
  TopicView,
  VersionsView,
  WorkspaceOverviewScreen,
  WorkspaceOverviewView,
  ConclusionView,
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
  CASE_ID as FIXTURE_CASE_ID,
  CASE_TITLE as FIXTURE_CASE_TITLE,
  CUTOFF as FIXTURE_CUTOFF,
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

function mockSourceContractStatus(
  metadata: Record<string, unknown>,
  allowAiProcessing: boolean,
  allowDisplay: boolean,
): "admitted" | "restricted" {
  if (!allowAiProcessing || !allowDisplay) return "restricted";
  const now = Date.now();
  const effectiveFrom = typeof metadata.effective_from === "string"
    ? Date.parse(metadata.effective_from)
    : Number.NaN;
  const effectiveUntil = typeof metadata.effective_until === "string"
    ? Date.parse(metadata.effective_until)
    : Number.NaN;
  if ((!Number.isNaN(effectiveFrom) && now < effectiveFrom)
    || (!Number.isNaN(effectiveUntil) && now > effectiveUntil)) {
    return "restricted";
  }
  return "admitted";
}

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
  judgement_card: {
    thesis_id: "t-gpu-demand",
    statement: "全球新能源车渗透率提升将推动中国供应链盈利中枢上移",
    conclusion: "supported",
    rationale: "已有政策、成本和需求证据支持该判断。",
    provisional: true,
    review: null,
    support_condition: "新能源车渗透率持续提升",
    falsification_condition: "行业投运比例持续低于规划",
    next_verification_event: "跟踪下一季度行业投运比例",
    evidence_counts: { supports: 2, contradicts: 1, contextualizes: 0 },
    gaps: ["缺反证：行业整体投运比例低于规划"],
    next_action: "补充验证：缺反证：行业整体投运比例低于规划",
    blocking_reason: "缺反证：行业整体投运比例低于规划",
    responsible: "Research Team",
  },
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
  changes: [
    {
      id: "chg-1",
      event_type: "evidence_link_published",
      aggregate_type: "evidence_link",
      summary: "新增城市 NOA 商业化证据",
      source: "工信部官网",
      actor: "ai:research",
      occurred_at: "2024-05-20T14:32:00+08:00",
      payload: { role: "supports" },
    },
  ],
  counter_research: [
    {
      id: "counter-t-gpu-demand",
      thesis_id: "t-gpu-demand",
      thesis_statement: "全球新能源车渗透率提升将推动中国供应链盈利中枢上移",
      assessment_id: "a-gpu-1",
      objective: "缺反证：行业整体投运比例低于规划",
      status: "已有反方证据",
      contradicts_count: 1,
      next_action: "整理反方证据并提交人工复核",
    },
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
    id: "doc-event-tsm-q2",
    source_url: "https://investor.tsmc.com/english/quarterly-results/2026/q2",
    title: "台积电 2026 年第二季度法说会摘要",
    publisher: "台积电",
    document_type: "公司披露",
    publish_date: "2026-08-07",
    available_at: "2026-08-07T09:00:00Z",
    acquired_at: "2026-08-07T09:05:00Z",
    parser_version: "docling-v1.2.3",
    parse_quality: "ok",
    linked_cases: [{ id: "event-tsm", title: "台积电上调 CoWoS 指引后下跌" }],
    span_count: 2,
    statement_count: 1,
    version_label: "v1 · 2026-08-07",
    source_contract: {
      source_type: "company_disclosure",
      provider_or_tenant: "台积电",
      permissions: { ai_processing: true, display: true, export: false, api: false },
      status: "admitted",
      region: "not_recorded",
      retention_policy: "case_retained",
      deletion_policy: "not_recorded",
      downstream_restrictions: ["仅限当前 Case 研究与人工审核"],
      contract_version: null,
    },
  },
  {
    id: "doc-fund-holdings-2026q2",
    title: "演示成长基金 2026 年第二季度持仓披露",
    publisher: "授权基金数据源",
    document_type: "基金持仓披露",
    publish_date: "2026-07-20",
    available_at: "2026-07-20T00:00:00Z",
    acquired_at: "2026-07-21T00:00:00Z",
    parser_version: "provider-v1",
    parse_quality: "ok",
    linked_cases: [{ id: "event-tsm", title: "台积电上调 CoWoS 指引后下跌" }],
    span_count: 1,
    statement_count: 0,
    version_label: "v1 · 2026-07-20",
    source_contract: {
      source_type: "licensed_provider",
      provider_or_tenant: "授权基金数据源",
      permissions: { ai_processing: true, display: true, export: false, api: false },
      status: "admitted",
      region: "not_recorded",
      retention_policy: "case_retained",
      deletion_policy: "not_recorded",
      downstream_restrictions: ["仅限当前 Case 研究与人工审核"],
      contract_version: null,
    },
  },
  {
    id: "doc-event-published-baseline",
    title: "经营数据披露后的基准材料",
    publisher: "公司披露",
    document_type: "公司披露",
    publish_date: "2026-08-02",
    available_at: "2026-08-02T09:00:00Z",
    acquired_at: "2026-08-02T09:05:00Z",
    parser_version: "docling-v1.2.3",
    parse_quality: "ok",
    linked_cases: [{ id: "event-published", title: "经营数据披露后的变动" }],
    span_count: 1,
    statement_count: 1,
    version_label: "v1 · 2026-08-02",
    source_contract: {
      source_type: "company_disclosure",
      provider_or_tenant: "公司披露",
      permissions: { ai_processing: true, display: true, export: false, api: false },
      status: "admitted",
      region: "not_recorded",
      retention_policy: "case_retained",
      deletion_policy: "not_recorded",
      downstream_restrictions: ["仅限当前 Case 研究与人工审核"],
      contract_version: null,
    },
  },
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

// ── 公司研究 / 主题研究（横切）stable mock ────────────────────────────────
//
// 与 AI 算力链 fixture 同一世界：寒武纪 + 工业富联、算力国产化 + 云厂商
// CapEx 两个横切主题。AI 草案与人工复核分离承载；cutoff 过滤与后端读
// 模型语义一致（角色适用窗口、估值 as_of、披露 published_at）。

const COMPANY_LIST: CompanyListItem[] = [
  {
    id: "co-nvda",
    code: "NVDA",
    name: "NVIDIA Corporation",
    type: "listed",
    stockCount: 1,
    themeRoleCount: 3,
    latestReportPeriod: "FY2026 Q1",
  },
  {
    id: "co-tsmc",
    code: "TSM",
    name: "TSMC",
    type: "listed",
    stockCount: 1,
    themeRoleCount: 3,
    latestReportPeriod: "2026-05",
  },
  {
    id: "co-msft",
    code: "MSFT",
    name: "Microsoft Corporation",
    type: "listed",
    stockCount: 1,
    themeRoleCount: 3,
    latestReportPeriod: "FY2025 Q3",
  },
  {
    id: "co-avgo",
    code: "AVGO",
    name: "Broadcom Inc.",
    type: "listed",
    stockCount: 1,
    themeRoleCount: 2,
    latestReportPeriod: "FY2025 Q2",
  },
  {
    id: "co-cambricon",
    code: "688256",
    name: "寒武纪",
    type: "listed",
    stockCount: 1,
    themeRoleCount: 1,
    latestReportPeriod: "2026-03-31",
  },
  {
    id: "co-foxconn",
    code: "601138",
    name: "工业富联",
    type: "listed",
    stockCount: 1,
    themeRoleCount: 1,
    latestReportPeriod: "2026-03-31",
  },
];

const MOCK_CUTOFF_NOW = "2026-08-02T08:00:00+00:00";

const COMPANY_DOSSIERS: Record<string, CompanyDossierView> = {
  "co-cambricon": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    company: {
      id: "co-cambricon",
      code: "688256",
      name: "寒武纪",
      type: "listed",
      createdAt: "2026-05-24T02:30:00+00:00",
    },
    stocks: [
      { id: "st-cambricon", code: "688256.SH", name: "寒武纪-U", market: "SSE" },
    ],
    themeRoles: [
      {
        id: "tr-cambricon-ai",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "算力芯片受益方",
        scope: { chain: "AI 算力", segment: "上游芯片" },
        applicableFrom: "2026-01-01",
        applicableTo: null,
        statementId: "stmt-cambricon-orders",
        statementText: "阿里云 2025 年采购 5-6 万张思元芯片",
        spanId: "span-cambricon-1",
        documentVersionId: "dv-cambricon-1",
      },
    ],
    relatedTheses: [
      {
        thesisId: "th-capex",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        statement: "云厂商 CapEx 将在 2026 年继续增长",
        title: "CapEx 上行",
        aiConclusion: "supported",
        aiProvisional: true,
        assessedAt: "2026-07-28T09:12:00+00:00",
        reviewOutcome: null,
        reviewConclusion: null,
        reviewReason: null,
        reviewer: null,
        reviewedAt: null,
      },
      {
        thesisId: "th-transmit",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        statement: "CapEx 增长将传导至国产 AI 芯片采购放量",
        title: "采购传导",
        aiConclusion: "supported",
        aiProvisional: true,
        assessedAt: "2026-07-28T09:20:00+00:00",
        reviewOutcome: "modified",
        reviewConclusion: "contradicted",
        reviewReason: "订单可见性不足以支撑当前估值隐含的预期",
        reviewer: "陈子仪",
        reviewedAt: "2026-07-30T03:22:00+00:00",
      },
    ],
    valuations: [
      {
        stockId: "st-cambricon",
        stockCode: "688256.SH",
        metricName: "PE_TTM",
        metricValue: 45.2,
        asOfDate: "2026-06-30",
        source: "wind",
        definition: "总市值/近四月归母净利润",
      },
    ],
    fundHolders: [
      {
        fundId: "fd-star50",
        fundCode: "588000",
        fundName: "华夏科创50ETF",
        stockId: "st-cambricon",
        stockCode: "688256.SH",
        weight: 1.27,
        reportPeriod: "2026-03-31",
        publishedAt: "2026-04-22T08:00:00+00:00",
        acquiredAt: "2026-04-22T09:00:00+00:00",
        source: "基金2026年一季报",
      },
    ],
  },
  "co-foxconn": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    company: {
      id: "co-foxconn",
      code: "601138",
      name: "工业富联",
      type: "listed",
      createdAt: "2026-05-24T02:30:00+00:00",
    },
    stocks: [
      { id: "st-foxconn", code: "601138.SH", name: "工业富联", market: "SSE" },
    ],
    themeRoles: [
      {
        id: "tr-foxconn-ai",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "AI服务器代工方",
        scope: { chain: "AI 算力", segment: "中游整机" },
        applicableFrom: "2026-01-01",
        applicableTo: null,
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
    ],
    relatedTheses: [
      {
        thesisId: "th-capex",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        statement: "云厂商 CapEx 将在 2026 年继续增长",
        title: "CapEx 上行",
        aiConclusion: "supported",
        aiProvisional: true,
        assessedAt: "2026-07-28T09:12:00+00:00",
        reviewOutcome: null,
        reviewConclusion: null,
        reviewReason: null,
        reviewer: null,
        reviewedAt: null,
      },
    ],
    valuations: [
      {
        stockId: "st-foxconn",
        stockCode: "601138.SH",
        metricName: "PB",
        metricValue: 3.8,
        asOfDate: "2026-06-30",
        source: "wind",
        definition: "总市值/归母净资产",
      },
    ],
    fundHolders: [
      {
        fundId: "fd-eft",
        fundCode: "005827",
        fundName: "易方达蓝筹精选",
        stockId: "st-foxconn",
        stockCode: "601138.SH",
        weight: 0.82,
        reportPeriod: "2026-03-31",
        publishedAt: "2026-04-22T08:00:00+00:00",
        acquiredAt: "2026-04-22T09:05:00+00:00",
        source: "基金2026年一季报",
      },
    ],
  },
  "co-nvda": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    company: {
      id: "co-nvda",
      code: "NVDA",
      name: "NVIDIA Corporation",
      type: "listed",
      createdAt: "2025-12-01T00:00:00+00:00",
    },
    stocks: [
      { id: "st-nvda", code: "NVDA", name: "NVIDIA Corp", market: "NASDAQ" },
    ],
    themeRoles: [
      {
        id: "tr-nvda-aicompute",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "算力芯片与系统供给",
        scope: { chain: "AI 算力", segment: "上游芯片" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: "stmt-nvda-fy26q1-dc",
        statementText:
          "Data Center revenue reflected continued demand for accelerated computing and AI infrastructure.",
        spanId: "span-nvda-fy26q1-p38",
        documentVersionId: "dv-nvda-fy26q1-v1",
      },
      {
        id: "tr-nvda-capex",
        caseId: "RC-CAPEX-2025-02",
        caseTitle: "云厂商 CapEx",
        role: "资本开支承接对象",
        scope: { chain: "云资本开支", segment: "GPU 与系统" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
      {
        id: "tr-nvda-packaging",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        role: "产能约束对象",
        scope: { chain: "先进封装", segment: "CoWoS-L" },
        applicableFrom: "2025-01-01",
        applicableTo: "2026-12-31",
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
    ],
    relatedTheses: [
      {
        thesisId: "th-capex",
        caseId: "RC-CAPEX-2025-02",
        caseTitle: "云厂商 CapEx",
        statement: "云厂商资本开支形成持续算力需求",
        title: "CapEx 持续",
        aiConclusion: "supported",
        aiProvisional: false,
        assessedAt: "2025-07-30T09:12:00+00:00",
        reviewOutcome: "confirmed",
        reviewConclusion: "supported",
        reviewReason: "三家云厂商财报口径一致",
        reviewer: "林漠",
        reviewedAt: "2025-08-02T11:30:00+00:00",
      },
      {
        thesisId: "th-procurement",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        statement: "订单积压到收入的传导需要独立披露验证",
        title: "订单传导",
        aiConclusion: "insufficient_evidence",
        aiProvisional: true,
        assessedAt: "2025-07-30T09:14:00+00:00",
        reviewOutcome: null,
        reviewConclusion: null,
        reviewReason: null,
        reviewer: null,
        reviewedAt: null,
      },
      {
        thesisId: "th-packaging",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        statement: "先进制程与互连供给决定交付斜率",
        title: "交付斜率",
        aiConclusion: "contradicted",
        aiProvisional: true,
        assessedAt: "2025-07-30T09:20:00+00:00",
        reviewOutcome: null,
        reviewConclusion: null,
        reviewReason: null,
        reviewer: null,
        reviewedAt: null,
      },
    ],
    valuations: [
      {
        stockId: "st-nvda",
        stockCode: "NVDA",
        metricName: "Forward P/E",
        metricValue: 32.4,
        asOfDate: "2025-06-30",
        source: "factset",
        definition: "未来 12 个月一致预期 EPS",
      },
      {
        stockId: "st-nvda",
        stockCode: "NVDA",
        metricName: "EV / Sales",
        metricValue: 18.7,
        asOfDate: "2025-06-30",
        source: "factset",
        definition: "企业价值 / TTM 营收",
      },
    ],
    fundHolders: [
      {
        fundId: "fd-aic-etf",
        fundCode: "159819",
        fundName: "鹏华中证人工智能 ETF",
        stockId: "st-nvda",
        stockCode: "NVDA",
        weight: 8.4,
        reportPeriod: "2025-03-31",
        publishedAt: "2025-04-22T08:00:00+00:00",
        acquiredAt: "2025-04-22T09:00:00+00:00",
        source: "fund-report-2025q1-v2",
      },
      {
        fundId: "fd-semi",
        fundCode: "161725",
        fundName: "招商中证半导体 ETF",
        stockId: "st-nvda",
        stockCode: "NVDA",
        weight: 6.1,
        reportPeriod: "2025-03-31",
        publishedAt: "2025-04-22T08:00:00+00:00",
        acquiredAt: "2025-04-22T09:05:00+00:00",
        source: "fund-report-2025q1-v1",
      },
    ],
  },
  "co-tsmc": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    company: {
      id: "co-tsmc",
      code: "TSM",
      name: "TSMC",
      type: "listed",
      createdAt: "2025-12-01T00:00:00+00:00",
    },
    stocks: [
      { id: "st-tsmc", code: "TSM", name: "Taiwan Semiconductor", market: "NYSE" },
    ],
    themeRoles: [
      {
        id: "tr-tsmc-aicompute",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "先进制程与封装",
        scope: { chain: "AI 算力", segment: "晶圆与封装" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: "stmt-tsmc-2025m05",
        statementText: "5 月合并营收 1,200 亿新台币，环比 +20%",
        spanId: "span-tsmc-m05",
        documentVersionId: "dv-tsmc-m05-v1",
      },
      {
        id: "tr-tsmc-power",
        caseId: "RC-POWER-2025-03",
        caseTitle: "数据中心电力约束",
        role: "电力与并网瓶颈对象",
        scope: { chain: "电力", segment: "数据中心园区" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
      {
        id: "tr-tsmc-packaging",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        role: "产能约束对象",
        scope: { chain: "先进封装", segment: "CoWoS-S" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
    ],
    relatedTheses: [
      {
        thesisId: "th-power",
        caseId: "RC-POWER-2025-03",
        caseTitle: "数据中心电力约束",
        statement: "电力与并网是否成为瓶颈",
        title: "电力瓶颈",
        aiConclusion: "contradicted",
        aiProvisional: false,
        assessedAt: "2025-07-30T09:12:00+00:00",
        reviewOutcome: "modified",
        reviewConclusion: "contradicted",
        reviewReason: "地区级项目已并网或缓建中",
        reviewer: "林漠",
        reviewedAt: "2025-08-02T11:30:00+00:00",
      },
      {
        thesisId: "th-packaging",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        statement: "先进制程与互连供给决定交付斜率",
        title: "交付斜率",
        aiConclusion: "supported",
        aiProvisional: true,
        assessedAt: "2025-07-30T09:20:00+00:00",
        reviewOutcome: null,
        reviewConclusion: null,
        reviewReason: null,
        reviewer: null,
        reviewedAt: null,
      },
    ],
    valuations: [
      {
        stockId: "st-tsmc",
        stockCode: "TSM",
        metricName: "Forward P/E",
        metricValue: 21.5,
        asOfDate: "2025-06-30",
        source: "factset",
        definition: "未来 12 个月一致预期 EPS",
      },
    ],
    fundHolders: [
      {
        fundId: "fd-aic-etf",
        fundCode: "159819",
        fundName: "鹏华中证人工智能 ETF",
        stockId: "st-tsmc",
        stockCode: "TSM",
        weight: 5.2,
        reportPeriod: "2025-03-31",
        publishedAt: "2025-04-22T08:00:00+00:00",
        acquiredAt: "2025-04-22T09:00:00+00:00",
        source: "fund-report-2025q1-v2",
      },
    ],
  },
  "co-msft": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    company: {
      id: "co-msft",
      code: "MSFT",
      name: "Microsoft Corporation",
      type: "listed",
      createdAt: "2025-12-01T00:00:00+00:00",
    },
    stocks: [
      { id: "st-msft", code: "MSFT", name: "Microsoft Corp", market: "NASDAQ" },
    ],
    themeRoles: [
      {
        id: "tr-msft-aicompute",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "需求与资本开支主体",
        scope: { chain: "AI 算力", segment: "云与 AI 基础设施" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: "stmt-msft-fy25q3-call",
        statementText:
          "Demand for our AI services remained higher than our available capacity.",
        spanId: "span-msft-call-p4",
        documentVersionId: "dv-msft-call-2025-04-30-v1",
      },
      {
        id: "tr-msft-capex",
        caseId: "RC-CAPEX-2025-02",
        caseTitle: "云厂商 CapEx",
        role: "需求与资本开支主体",
        scope: { chain: "云资本开支", segment: "Azure / OpenAI" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
      {
        id: "tr-msft-power",
        caseId: "RC-POWER-2025-03",
        caseTitle: "数据中心电力约束",
        role: "系统上线与分部收入",
        scope: { chain: "电力", segment: "数据中心园区" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
    ],
    relatedTheses: [
      {
        thesisId: "th-demand",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        statement: "需求能否穿透至可验证收入",
        title: "需求穿透",
        aiConclusion: "insufficient_evidence",
        aiProvisional: true,
        assessedAt: "2025-07-30T09:12:00+00:00",
        reviewOutcome: "modified",
        reviewConclusion: "supported",
        reviewReason: "订单可见性来自业绩说明会陈述",
        reviewer: "陈子仪",
        reviewedAt: "2025-08-02T11:30:00+00:00",
      },
      {
        thesisId: "th-capex",
        caseId: "RC-CAPEX-2025-02",
        caseTitle: "云厂商 CapEx",
        statement: "云厂商资本开支形成持续算力需求",
        title: "CapEx 持续",
        aiConclusion: "supported",
        aiProvisional: false,
        assessedAt: "2025-07-30T09:14:00+00:00",
        reviewOutcome: "confirmed",
        reviewConclusion: "supported",
        reviewReason: "三家云厂商财报口径一致",
        reviewer: "林漠",
        reviewedAt: "2025-08-02T11:30:00+00:00",
      },
    ],
    valuations: [
      {
        stockId: "st-msft",
        stockCode: "MSFT",
        metricName: "Forward P/E",
        metricValue: 33.1,
        asOfDate: "2025-06-30",
        source: "factset",
        definition: "未来 12 个月一致预期 EPS",
      },
    ],
    fundHolders: [
      {
        fundId: "fd-aic-etf",
        fundCode: "159819",
        fundName: "鹏华中证人工智能 ETF",
        stockId: "st-msft",
        stockCode: "MSFT",
        weight: 4.1,
        reportPeriod: "2025-03-31",
        publishedAt: "2025-04-22T08:00:00+00:00",
        acquiredAt: "2025-04-22T09:00:00+00:00",
        source: "fund-report-2025q1-v2",
      },
    ],
  },
  "co-avgo": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    company: {
      id: "co-avgo",
      code: "AVGO",
      name: "Broadcom Inc.",
      type: "listed",
      createdAt: "2025-12-01T00:00:00+00:00",
    },
    stocks: [
      { id: "st-avgo", code: "AVGO", name: "Broadcom Inc.", market: "NASDAQ" },
    ],
    themeRoles: [
      {
        id: "tr-avgo-aicompute",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "网络与定制芯片",
        scope: { chain: "AI 算力", segment: "网络与 ASIC" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
      {
        id: "tr-avgo-packaging",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        role: "产能的来料依赖",
        scope: { chain: "先进封装", segment: "CoWoS 与 HBM" },
        applicableFrom: "2025-01-01",
        applicableTo: "2026-12-31",
        statementId: null,
        statementText: null,
        spanId: null,
        documentVersionId: null,
      },
    ],
    relatedTheses: [
      {
        thesisId: "th-packaging",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        statement: "CoWoS 与 HBM 共同影响交付",
        title: "封装与 HBM",
        aiConclusion: "supported",
        aiProvisional: true,
        assessedAt: "2025-07-30T09:20:00+00:00",
        reviewOutcome: null,
        reviewConclusion: null,
        reviewReason: null,
        reviewer: null,
        reviewedAt: null,
      },
    ],
    valuations: [
      {
        stockId: "st-avgo",
        stockCode: "AVGO",
        metricName: "Forward P/E",
        metricValue: 28.6,
        asOfDate: "2025-06-30",
        source: "factset",
        definition: "未来 12 个月一致预期 EPS",
      },
    ],
    fundHolders: [
      {
        fundId: "fd-semi",
        fundCode: "161725",
        fundName: "招商中证半导体 ETF",
        stockId: "st-avgo",
        stockCode: "AVGO",
        weight: 3.8,
        reportPeriod: "2025-03-31",
        publishedAt: "2025-04-22T08:00:00+00:00",
        acquiredAt: "2025-04-22T09:05:00+00:00",
        source: "fund-report-2025q1-v1",
      },
    ],
  },
};

const TOPIC_LIST: TopicListItem[] = [
  { tag: "AI 算力基础设施", caseCount: 3, companyCount: 6, thesisCount: 7 },
  { tag: "国产算力替代", caseCount: 2, companyCount: 2, thesisCount: 3 },
  { tag: "数据中心电力约束", caseCount: 2, companyCount: 1, thesisCount: 1 },
  { tag: "云厂商CapEx", caseCount: 1, companyCount: 1, thesisCount: 1 },
  { tag: "先进封装供给", caseCount: 2, companyCount: 4, thesisCount: 3 },
];

const TOPIC_VIEWS: Record<string, TopicView> = {
  算力国产化: {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    tag: "算力国产化",
    cases: [
      {
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        thesisCounts: {
          supported: 0,
          contradicted: 1,
          insufficient_evidence: 0,
          ai_pending: 1,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-capex",
            statement: "云厂商 CapEx 将在 2026 年继续增长",
            title: "CapEx 上行",
            aiConclusion: "supported",
            aiProvisional: true,
            assessedAt: "2026-07-28T09:12:00+00:00",
            reviewOutcome: null,
            reviewConclusion: null,
            reviewReason: null,
            reviewer: null,
            reviewedAt: null,
          },
          {
            thesisId: "th-transmit",
            statement: "CapEx 增长将传导至国产 AI 芯片采购放量",
            title: "采购传导",
            aiConclusion: "supported",
            aiProvisional: true,
            assessedAt: "2026-07-28T09:20:00+00:00",
            reviewOutcome: "modified",
            reviewConclusion: "contradicted",
            reviewReason: "订单可见性不足以支撑当前估值隐含的预期",
            reviewer: "陈子仪",
            reviewedAt: "2026-07-30T03:22:00+00:00",
          },
        ],
      },
      {
        caseId: "RC-CLOUD-2026-02",
        caseTitle: "云端 CapEx 传导",
        thesisCounts: {
          supported: 1,
          contradicted: 0,
          insufficient_evidence: 0,
          ai_pending: 0,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-cloud-capex",
            statement: "头部云厂商 2026 年资本开支指引上调",
            title: "指引上调",
            aiConclusion: "supported",
            aiProvisional: true,
            assessedAt: "2026-07-26T11:00:00+00:00",
            reviewOutcome: "confirmed",
            reviewConclusion: null,
            reviewReason: "三家云厂商财报口径一致",
            reviewer: "林漠",
            reviewedAt: "2026-07-27T02:10:00+00:00",
          },
        ],
      },
    ],
    companyRoles: [
      {
        companyId: "co-cambricon",
        companyCode: "688256",
        companyName: "寒武纪",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "算力芯片受益方",
        scope: { chain: "AI 算力", segment: "上游芯片" },
        applicableFrom: "2026-01-01",
        applicableTo: null,
        statementId: "stmt-cambricon-orders",
      },
      {
        companyId: "co-foxconn",
        companyCode: "601138",
        companyName: "工业富联",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "AI服务器代工方",
        scope: { chain: "AI 算力", segment: "中游整机" },
        applicableFrom: "2026-01-01",
        applicableTo: null,
        statementId: null,
      },
    ],
    fundExposure: [
      {
        fundId: "fd-star50",
        fundCode: "588000",
        fundName: "华夏科创50ETF",
        stockId: "st-cambricon",
        stockCode: "688256.SH",
        stockName: "寒武纪-U",
        weight: 1.27,
        reportPeriod: "2026-03-31",
        source: "基金2026年一季报",
      },
      {
        fundId: "fd-eft",
        fundCode: "005827",
        fundName: "易方达蓝筹精选",
        stockId: "st-foxconn",
        stockCode: "601138.SH",
        stockName: "工业富联",
        weight: 0.82,
        reportPeriod: "2026-03-31",
        source: "基金2026年一季报",
      },
    ],
    derivedFrom: {
      caseIds: ["RC-AIC-2025-01", "RC-CLOUD-2026-02"],
      thesisIds: ["th-capex", "th-transmit", "th-cloud-capex"],
      themeRoleIds: ["tr-cambricon-ai", "tr-foxconn-ai"],
      disclosureIds: ["hd-star50-q1", "hd-eft-q1"],
    },
  },
  云厂商CapEx: {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    tag: "云厂商CapEx",
    cases: [
      {
        caseId: "RC-CLOUD-2026-02",
        caseTitle: "云端 CapEx 传导",
        thesisCounts: {
          supported: 1,
          contradicted: 0,
          insufficient_evidence: 0,
          ai_pending: 0,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-cloud-capex",
            statement: "头部云厂商 2026 年资本开支指引上调",
            title: "指引上调",
            aiConclusion: "supported",
            aiProvisional: true,
            assessedAt: "2026-07-26T11:00:00+00:00",
            reviewOutcome: "confirmed",
            reviewConclusion: null,
            reviewReason: "三家云厂商财报口径一致",
            reviewer: "林漠",
            reviewedAt: "2026-07-27T02:10:00+00:00",
          },
        ],
      },
    ],
    companyRoles: [],
    fundExposure: [],
    derivedFrom: {
      caseIds: ["RC-CLOUD-2026-02"],
      thesisIds: ["th-cloud-capex"],
      themeRoleIds: [],
      disclosureIds: [],
    },
  },
  "AI 算力基础设施": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    tag: "AI 算力基础设施",
    cases: [
      {
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        thesisCounts: {
          supported: 1,
          contradicted: 1,
          insufficient_evidence: 0,
          ai_pending: 1,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-demand",
            statement: "需求能否穿透至可验证收入",
            title: "需求穿透",
            aiConclusion: "supported",
            aiProvisional: false,
            assessedAt: "2025-07-28T09:12:00+00:00",
            reviewOutcome: "modified",
            reviewConclusion: "supported",
            reviewReason: "订单可见性来自业绩说明会陈述",
            reviewer: "陈子仪",
            reviewedAt: "2025-07-30T03:22:00+00:00",
          },
          {
            thesisId: "th-aic-03",
            statement: "需求高于可供容量，收入兑现仍受约束",
            title: "收入兑现约束",
            aiConclusion: "contradicted",
            aiProvisional: true,
            assessedAt: "2025-07-28T09:20:00+00:00",
            reviewOutcome: null,
            reviewConclusion: null,
            reviewReason: null,
            reviewer: null,
            reviewedAt: null,
          },
          {
            thesisId: "th-procurement",
            statement: "订单积压到收入的传导需要独立披露验证",
            title: "订单传导",
            aiConclusion: "insufficient_evidence",
            aiProvisional: true,
            assessedAt: "2025-07-28T09:25:00+00:00",
            reviewOutcome: null,
            reviewConclusion: null,
            reviewReason: null,
            reviewer: null,
            reviewedAt: null,
          },
        ],
      },
      {
        caseId: "RC-CAPEX-2025-02",
        caseTitle: "云厂商 CapEx",
        thesisCounts: {
          supported: 1,
          contradicted: 0,
          insufficient_evidence: 0,
          ai_pending: 0,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-capex",
            statement: "云厂商资本开支形成持续算力需求",
            title: "CapEx 持续",
            aiConclusion: "supported",
            aiProvisional: false,
            assessedAt: "2025-07-30T09:12:00+00:00",
            reviewOutcome: "confirmed",
            reviewConclusion: "supported",
            reviewReason: "三家云厂商财报口径一致",
            reviewer: "林漠",
            reviewedAt: "2025-08-02T11:30:00+00:00",
          },
        ],
      },
      {
        caseId: "RC-POWER-2025-03",
        caseTitle: "数据中心电力约束",
        thesisCounts: {
          supported: 0,
          contradicted: 1,
          insufficient_evidence: 0,
          ai_pending: 0,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-power",
            statement: "电力与并网是否成为瓶颈",
            title: "电力瓶颈",
            aiConclusion: "contradicted",
            aiProvisional: false,
            assessedAt: "2025-07-30T09:14:00+00:00",
            reviewOutcome: "modified",
            reviewConclusion: "contradicted",
            reviewReason: "地区级项目已并网或缓建中",
            reviewer: "林漠",
            reviewedAt: "2025-08-02T11:30:00+00:00",
          },
        ],
      },
    ],
    companyRoles: [
      {
        companyId: "co-nvda",
        companyCode: "NVDA",
        companyName: "NVIDIA · NVDA",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "算力芯片与系统供给",
        scope: { chain: "AI 算力", segment: "上游芯片" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: "stmt-nvda-fy26q1-dc",
      },
      {
        companyId: "co-tsmc",
        companyCode: "TSM",
        companyName: "TSMC · TSM",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "先进制程与封装",
        scope: { chain: "AI 算力", segment: "晶圆与封装" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: "stmt-tsmc-2025m05",
      },
      {
        companyId: "co-msft",
        companyCode: "MSFT",
        companyName: "Microsoft · MSFT",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "需求与资本开支主体",
        scope: { chain: "AI 算力", segment: "云与 AI 基础设施" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: "stmt-msft-fy25q3-call",
      },
    ],
    fundExposure: [
      {
        fundId: "fd-aic-etf",
        fundCode: "159819",
        fundName: "鹏华中证人工智能 ETF",
        stockId: "st-nvda",
        stockCode: "NVDA",
        stockName: "NVIDIA Corp",
        weight: 18.4,
        reportPeriod: "2025-03-31",
        source: "fund-report-2025q1-v2",
      },
      {
        fundId: "fd-semi",
        fundCode: "161725",
        fundName: "招商中证半导体 ETF",
        stockId: "st-tsmc",
        stockCode: "TSM",
        stockName: "TSMC",
        weight: 14.7,
        reportPeriod: "2025-03-31",
        source: "fund-report-2025q1-v1",
      },
    ],
    derivedFrom: {
      caseIds: ["RC-AIC-2025-01", "RC-CAPEX-2025-02", "RC-POWER-2025-03"],
      thesisIds: [
        "th-demand",
        "th-aic-03",
        "th-procurement",
        "th-capex",
        "th-power",
      ],
      themeRoleIds: [
        "tr-nvda-aicompute",
        "tr-tsmc-aicompute",
        "tr-msft-aicompute",
      ],
      disclosureIds: ["fd-aic-etf", "fd-semi"],
    },
  },
  "国产算力替代": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    tag: "国产算力替代",
    cases: [
      {
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        thesisCounts: {
          supported: 0,
          contradicted: 0,
          insufficient_evidence: 1,
          ai_pending: 1,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-domestic",
            statement: "国产算力能否承接替代",
            title: "国产承接",
            aiConclusion: "insufficient_evidence",
            aiProvisional: true,
            assessedAt: "2025-07-28T09:30:00+00:00",
            reviewOutcome: null,
            reviewConclusion: null,
            reviewReason: null,
            reviewer: null,
            reviewedAt: null,
          },
        ],
      },
    ],
    companyRoles: [
      {
        companyId: "co-cambricon",
        companyCode: "688256",
        companyName: "寒武纪",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "国产算力代表",
        scope: { chain: "国产算力", segment: "上游芯片" },
        applicableFrom: "2026-01-01",
        applicableTo: null,
        statementId: null,
      },
      {
        companyId: "co-foxconn",
        companyCode: "601138",
        companyName: "工业富联",
        caseId: "RC-AIC-2025-01",
        caseTitle: "AI 算力链",
        role: "AI 服务器代工",
        scope: { chain: "国产算力", segment: "中游整机" },
        applicableFrom: "2026-01-01",
        applicableTo: null,
        statementId: null,
      },
    ],
    fundExposure: [
      {
        fundId: "fd-star50",
        fundCode: "588000",
        fundName: "华夏科创50ETF",
        stockId: "st-cambricon",
        stockCode: "688256.SH",
        stockName: "寒武纪-U",
        weight: 1.27,
        reportPeriod: "2025-03-31",
        source: "fund-report-2025q1-v2",
      },
    ],
    derivedFrom: {
      caseIds: ["RC-AIC-2025-01"],
      thesisIds: ["th-domestic"],
      themeRoleIds: ["tr-cambricon-ai", "tr-foxconn-ai"],
      disclosureIds: ["fd-star50"],
    },
  },
  "数据中心电力约束": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    tag: "数据中心电力约束",
    cases: [
      {
        caseId: "RC-POWER-2025-03",
        caseTitle: "数据中心电力约束",
        thesisCounts: {
          supported: 0,
          contradicted: 1,
          insufficient_evidence: 0,
          ai_pending: 0,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-power",
            statement: "电力与并网是否成为瓶颈",
            title: "电力瓶颈",
            aiConclusion: "contradicted",
            aiProvisional: false,
            assessedAt: "2025-07-30T09:14:00+00:00",
            reviewOutcome: "modified",
            reviewConclusion: "contradicted",
            reviewReason: "地区级项目已并网或缓建中",
            reviewer: "林漠",
            reviewedAt: "2025-08-02T11:30:00+00:00",
          },
        ],
      },
    ],
    companyRoles: [
      {
        companyId: "co-tsmc",
        companyCode: "TSM",
        companyName: "TSMC · TSM",
        caseId: "RC-POWER-2025-03",
        caseTitle: "数据中心电力约束",
        role: "电力与并网瓶颈对象",
        scope: { chain: "电力", segment: "数据中心园区" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
      },
    ],
    fundExposure: [],
    derivedFrom: {
      caseIds: ["RC-POWER-2025-03"],
      thesisIds: ["th-power"],
      themeRoleIds: ["tr-tsmc-power"],
      disclosureIds: [],
    },
  },
  "先进封装供给": {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    tag: "先进封装供给",
    cases: [
      {
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        thesisCounts: {
          supported: 1,
          contradicted: 1,
          insufficient_evidence: 0,
          ai_pending: 0,
          rejected: 0,
          no_assessment: 0,
        },
        theses: [
          {
            thesisId: "th-packaging",
            statement: "CoWoS 与 HBM 共同影响交付",
            title: "封装与 HBM",
            aiConclusion: "supported",
            aiProvisional: false,
            assessedAt: "2025-07-30T09:20:00+00:00",
            reviewOutcome: "confirmed",
            reviewConclusion: "supported",
            reviewReason: "公司财报与渠道一致",
            reviewer: "林漠",
            reviewedAt: "2025-08-02T11:30:00+00:00",
          },
          {
            thesisId: "th-slope",
            statement: "先进制程与互连供给决定交付斜率",
            title: "交付斜率",
            aiConclusion: "contradicted",
            aiProvisional: true,
            assessedAt: "2025-07-30T09:22:00+00:00",
            reviewOutcome: null,
            reviewConclusion: null,
            reviewReason: null,
            reviewer: null,
            reviewedAt: null,
          },
        ],
      },
    ],
    companyRoles: [
      {
        companyId: "co-tsmc",
        companyCode: "TSM",
        companyName: "TSMC · TSM",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        role: "产能约束对象",
        scope: { chain: "先进封装", segment: "CoWoS-S" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
      },
      {
        companyId: "co-nvda",
        companyCode: "NVDA",
        companyName: "NVIDIA · NVDA",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        role: "产能约束对象",
        scope: { chain: "先进封装", segment: "CoWoS-L" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
      },
      {
        companyId: "co-avgo",
        companyCode: "AVGO",
        companyName: "Broadcom · AVGO",
        caseId: "RC-PACK-2025-04",
        caseTitle: "先进封装供给",
        role: "产能的来料依赖",
        scope: { chain: "先进封装", segment: "CoWoS 与 HBM" },
        applicableFrom: "2025-01-01",
        applicableTo: null,
        statementId: null,
      },
    ],
    fundExposure: [
      {
        fundId: "fd-semi",
        fundCode: "161725",
        fundName: "招商中证半导体 ETF",
        stockId: "st-tsmc",
        stockCode: "TSM",
        stockName: "TSMC",
        weight: 9.5,
        reportPeriod: "2025-03-31",
        source: "fund-report-2025q1-v1",
      },
    ],
    derivedFrom: {
      caseIds: ["RC-PACK-2025-04"],
      thesisIds: ["th-packaging", "th-slope"],
      themeRoleIds: ["tr-tsmc-packaging", "tr-nvda-packaging", "tr-avgo-packaging"],
      disclosureIds: ["fd-semi"],
    },
  },
};

function emptyTopicView(tag: string): TopicView {
  return {
    cutoff: MOCK_CUTOFF_NOW,
    isHistorical: false,
    tag,
    cases: [],
    companyRoles: [],
    fundExposure: [],
    derivedFrom: {
      caseIds: [],
      thesisIds: [],
      themeRoleIds: [],
      disclosureIds: [],
    },
  };
}

/** 从 topic view 自动派生 5 节点关系路径（设计图 9 底部链），并选择
 *  一个主题内最值得固定的命题（右栏检查器默认展示）。 */
function deriveTopicPathNodes(view: TopicView): {
  pathNodes: TopicPathNode[];
  pinnedThesisId: string | null;
} {
  const firstCase = view.cases[0];
  const firstThesis = firstCase?.theses[0];
  const firstRole = view.companyRoles[0];
  const firstFund = view.fundExposure[0];
  // 选最有意思的命题：优先 ai_pending/contradicted/insufficient_evidence，
  // 否则取第一条。设计图 9 默认为 th-aic-03 (contradicted) 的证据。
  const pinnedEntry = view.cases
    .flatMap((c) => c.theses.map((t) => ({ caseId: c.caseId, t })))
    .find(
      ({ t }) =>
        t.aiConclusion === "contradicted" ||
        t.aiConclusion === "insufficient_evidence" ||
        t.aiProvisional,
    );
  const pinnedThesis: TopicThesisView | undefined =
    pinnedEntry?.t ?? firstCase?.theses[0];
  return {
    pathNodes: [
      firstThesis
        ? {
            kind: "evidence",
            label: "冻结证据",
            refId: firstThesis.thesisId,
            meta: firstCase?.caseTitle ?? "案例",
          }
        : { kind: "evidence", label: "冻结证据", refId: "", meta: "—" },
      firstThesis
        ? {
            kind: "thesis",
            label: firstThesis.title ?? firstThesis.statement.slice(0, 12),
            refId: firstThesis.thesisId,
            meta: firstCase?.caseTitle ?? "—",
          }
        : { kind: "thesis", label: "命题", refId: "", meta: "—" },
      firstRole
        ? {
            kind: "role",
            label: firstRole.role,
            refId: firstRole.companyId,
            meta: firstRole.companyName,
          }
        : { kind: "role", label: "公司角色", refId: "", meta: "—" },
      firstFund
        ? {
            kind: "stock",
            label: firstFund.stockName,
            refId: firstFund.stockId,
            meta: firstFund.stockCode,
          }
        : { kind: "stock", label: "股票映射", refId: "", meta: "—" },
      firstFund
        ? {
            kind: "fund",
            label: firstFund.fundName,
            refId: firstFund.fundId,
            meta: `${firstFund.weight.toFixed(1)}% · ${firstFund.reportPeriod}`,
          }
        : { kind: "fund", label: "基金披露", refId: "", meta: "—" },
    ],
    pinnedThesisId: pinnedThesis?.thesisId ?? null,
  };
}

/** 从 company dossier 派生 5 节点关系路径（设计图 10 底部链）。 */
function deriveCompanyPathNodes(dossier: CompanyDossierView): {
  pathNodes: TopicPathNode[];
  pinnedThesisId: string | null;
} {
  const firstThesis = dossier.relatedTheses[0];
  const firstRole = dossier.themeRoles[0];
  const firstFund = dossier.fundHolders[0];
  return {
    pathNodes: [
      firstThesis
        ? {
            kind: "evidence",
            label: "冻结证据",
            refId: firstThesis.thesisId,
            meta: firstThesis.caseTitle,
          }
        : { kind: "evidence", label: "冻结证据", refId: "", meta: "—" },
      firstThesis
        ? {
            kind: "thesis",
            label: firstThesis.title ?? firstThesis.statement.slice(0, 12),
            refId: firstThesis.thesisId,
            meta: firstThesis.caseTitle,
          }
        : { kind: "thesis", label: "命题", refId: "", meta: "—" },
      firstRole
        ? {
            kind: "role",
            label: firstRole.role,
            refId: dossier.company.id,
            meta: firstRole.caseTitle ?? "—",
          }
        : { kind: "role", label: "公司角色", refId: "", meta: "—" },
      firstFund
        ? {
            kind: "stock",
            label: firstFund.stockCode,
            refId: firstFund.stockId,
            meta: firstFund.fundName,
          }
        : { kind: "stock", label: "股票", refId: "", meta: "—" },
      firstFund
        ? {
            kind: "fund",
            label: firstFund.fundName,
            refId: firstFund.fundId,
            meta: `${firstFund.weight.toFixed(1)}% · ${firstFund.reportPeriod}`,
          }
        : { kind: "fund", label: "基金披露", refId: "", meta: "—" },
    ],
    pinnedThesisId: firstThesis?.thesisId ?? null,
  };
}

// ── 装饰：用设计图 9/10 的文案/状态字段补全 mock view ──────────────────────
//
// 设计图 9 默认选中「AI 算力基础设施」、设计图 10 默认选中「NVIDIA」；
// 此处为 case card / role row / valuation cell 提供符合设计图视觉的硬编码
// 文案，避免每次都重新编辑 mock 主表。

const CASE_CARD_DECORATION: Record<
  string,
  {
    summary: string;
    rebuttalBullet?: string;
    nextEventBullet?: string;
    statusLabel: string;
    statusVariant: "support" | "contradict" | "warning" | "ai" | "draft";
  }
> = {
  "RC-AIC-2025-01": {
    summary: "订单交付成立，但收入确认仍存在口径缺口。",
    rebuttalBullet: "主要反证：需求高于可供容量",
    statusLabel: "证据不足 · 继续验证",
    statusVariant: "warning",
  },
  "RC-CAPEX-2025-02": {
    summary: "三家云厂商维持 AI 基础设施扩张。",
    nextEventBullet: "下一事件：FY26 Q2 指引",
    statusLabel: "支持 · 已复核",
    statusVariant: "support",
  },
  "RC-POWER-2025-03": {
    summary: "部分地区项目已晚于芯片供给。",
    nextEventBullet: "缺口：项目级并网数据",
    statusLabel: "反证关系 · 已复核",
    statusVariant: "contradict",
  },
  "RC-PACK-2025-04": {
    summary: "CoWoS 与 HBM 共同影响交付节奏。",
    nextEventBullet: "缺口：HBM 3D 库存口径",
    statusLabel: "支持 · 已复核",
    statusVariant: "support",
  },
  "RC-CLOUD-2026-02": {
    summary: "头部云厂商上调 2026 年资本开支指引。",
    nextEventBullet: "下一事件：FY26 Q2 指引",
    statusLabel: "支持 · 已复核",
    statusVariant: "support",
  },
  "RC-SUPPLY-2025-04": {
    summary: "先进制程与互连供给决定交付斜率。",
    nextEventBullet: "缺口：HBM 3D 库存口径",
    statusLabel: "AI 提议 · 待复核",
    statusVariant: "ai",
  },
};

const COMPANY_ROLE_DECORATION: Record<
  string,
  {
    transmission: string;
    statusLabel: string;
    statusVariant: "reviewed" | "warning" | "ai" | "support" | "contradict" | "draft";
    applicableScope?: string;
  }
> = {
  tr_nvda_aicompute: {
    transmission: "资本开支 → 设备交付",
    statusLabel: "已复核支持",
    statusVariant: "reviewed",
    applicableScope: "2025H1 · 全球云商",
  },
  tr_nvda_capex: {
    transmission: "订单积压与交付节奏需单独验证",
    statusLabel: "待补证据",
    statusVariant: "warning",
  },
  tr_nvda_packaging: {
    transmission: "CoWoS 与 HBM 共同影响交付",
    statusLabel: "AI 提议 · 待复核",
    statusVariant: "ai",
  },
  tr_tsmc_aicompute: {
    transmission: "产能约束 → 交付节奏",
    statusLabel: "待补证据",
    statusVariant: "warning",
    applicableScope: "CoWoS · 2025-2026",
  },
  tr_tsmc_power: {
    transmission: "电力与并网瓶颈对象",
    statusLabel: "已复核",
    statusVariant: "reviewed",
  },
  tr_tsmc_packaging: {
    transmission: "产能约束对象",
    statusLabel: "已复核",
    statusVariant: "reviewed",
  },
  tr_msft_aicompute: {
    transmission: "系统上线 → 分部收入",
    statusLabel: "AI 提议 · 待复核",
    statusVariant: "ai",
    applicableScope: "Azure AI · FY25",
  },
  tr_msft_capex: {
    transmission: "需求与资本开支主体",
    statusLabel: "已复核",
    statusVariant: "reviewed",
  },
  tr_msft_power: {
    transmission: "系统上线 → 分部收入",
    statusLabel: "已复核",
    statusVariant: "reviewed",
  },
  tr_avgo_aicompute: {
    transmission: "需求与资本开支主体",
    statusLabel: "AI 提议 · 待复核",
    statusVariant: "ai",
  },
  tr_avgo_packaging: {
    transmission: "产能的来料依赖",
    statusLabel: "AI 提议 · 待复核",
    statusVariant: "ai",
  },
  tr_cambricon_ai: {
    transmission: "国产承接",
    statusLabel: "已复核",
    statusVariant: "reviewed",
  },
  tr_foxconn_ai: {
    transmission: "AI 服务器代工",
    statusLabel: "已复核",
    statusVariant: "reviewed",
  },
};

const COMPANY_ROLE_LABEL_DECORATION: Record<string, { statusLabel: string; statusVariant: "reviewed" | "warning" | "ai" }> = {
  tr_nvda_aicompute: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_nvda_capex: { statusLabel: "待补证据", statusVariant: "warning" },
  tr_nvda_packaging: { statusLabel: "AI 提议 · 待复核", statusVariant: "ai" },
  tr_tsmc_aicompute: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_tsmc_power: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_tsmc_packaging: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_msft_aicompute: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_msft_capex: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_msft_power: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_avgo_aicompute: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_avgo_packaging: { statusLabel: "AI 提议 · 待复核", statusVariant: "ai" },
  tr_cambricon_ai: { statusLabel: "已复核", statusVariant: "reviewed" },
  tr_foxconn_ai: { statusLabel: "已复核", statusVariant: "reviewed" },
};

const COMPANY_REPORT_PERIOD: Record<string, { period: string; note: string }> = {
  "co-nvda": { period: "FY2026 Q1", note: "2025-05-28 可用" },
  "co-tsmc": { period: "2026-05", note: "2026-05-10 可用" },
  "co-msft": { period: "FY2025 Q3", note: "2025-04-30 可用" },
  "co-avgo": { period: "FY2025 Q2", note: "2025-06-12 可用" },
  "co-cambricon": { period: "2026-03-31", note: "2026-04-22 可用" },
  "co-foxconn": { period: "2026-03-31", note: "2026-04-22 可用" },
};

const COMPANY_LISTING: Record<string, { market: string; listedLabel: string }> = {
  "co-nvda": { market: "NASDAQ", listedLabel: "已上市" },
  "co-tsmc": { market: "NYSE", listedLabel: "已上市" },
  "co-msft": { market: "NASDAQ", listedLabel: "已上市" },
  "co-avgo": { market: "NASDAQ", listedLabel: "已上市" },
  "co-cambricon": { market: "上交所", listedLabel: "已上市" },
  "co-foxconn": { market: "上交所", listedLabel: "已上市" },
};

function decorateTopicView(view: TopicView): TopicView {
  return {
    ...view,
    cases: view.cases.map((c) => {
      const deco = CASE_CARD_DECORATION[c.caseId];
      if (!deco) return c;
      return {
        ...c,
        summary: deco.summary,
        rebuttalBullet: deco.rebuttalBullet,
        nextEventBullet: deco.nextEventBullet,
        statusLabel: deco.statusLabel,
        statusVariant: deco.statusVariant,
      };
    }),
    companyRoles: view.companyRoles.map((r) => {
      const deco = COMPANY_ROLE_DECORATION[r.role + ":" + r.caseId]
        ?? COMPANY_ROLE_DECORATION[
            `${r.companyId.replace("co-", "")}_${(
              r.caseId ?? ""
            ).replace("RC-", "").toLowerCase()}`
          ];
      if (!deco) return r;
      return {
        ...r,
        transmission: deco.transmission,
        statusLabel: deco.statusLabel,
        statusVariant: deco.statusVariant,
        applicableScope: deco.applicableScope,
      };
    }),
  };
}

function decorateCompanyDossier(dossier: CompanyDossierView): CompanyDossierView {
  const report = COMPANY_REPORT_PERIOD[dossier.company.id];
  const listing = COMPANY_LISTING[dossier.company.id];
  return {
    ...dossier,
    themeRoles: dossier.themeRoles.map((r) => {
      const deco = COMPANY_ROLE_LABEL_DECORATION[r.id];
      if (!deco) return r;
      return {
        ...r,
        statusLabel: deco.statusLabel,
        statusVariant: deco.statusVariant,
        transmission: COMPANY_ROLE_DECORATION[r.id]?.transmission,
      };
    }),
    company: {
      ...dossier.company,
      ...(listing
        ? { market: listing.market, listedLabel: listing.listedLabel }
        : {}),
      ...(report
        ? { reportPeriod: report.period, reportNote: report.note }
        : {}),
    },
  };
}

/** cutoff 过滤与后端读模型语义一致：角色适用窗口、估值 as_of、披露
 * published_at、assessment/review 的创建时间均按 cutoff 截断。 */
function applyCompanyCutoff(
  dossier: CompanyDossierView,
  cutoff: string,
): CompanyDossierView {
  const day = cutoff.slice(0, 10);
  return {
    ...dossier,
    cutoff,
    isHistorical: true,
    themeRoles: dossier.themeRoles.filter(
      (r) =>
        (!r.applicableFrom || r.applicableFrom <= day) &&
        (!r.applicableTo || r.applicableTo >= day),
    ),
    valuations: dossier.valuations.filter((v) => v.asOfDate <= day),
    fundHolders: dossier.fundHolders.filter(
      (h) => (h.publishedAt ?? "") <= cutoff,
    ),
  };
}

function applyTopicCutoff(view: TopicView, cutoff: string): TopicView {
  const day = cutoff.slice(0, 10);
  return {
    ...view,
    cutoff,
    isHistorical: true,
    companyRoles: view.companyRoles.filter(
      (r) =>
        (!r.applicableFrom || r.applicableFrom <= day) &&
        (!r.applicableTo || r.applicableTo >= day),
    ),
    // 持仓过滤：fund_exposure 没有 published_at 字段，按主题内已映射股票
    // 的 latest report_period ≤ cutoff 过滤（与 dossier 行为对齐）。
    fundExposure: view.fundExposure.filter(
      (p) => (p.reportPeriod ?? "") <= day,
    ),
  };
}

// ── Adapter ───────────────────────────────────────────────────────────────

const MOCK_RESEARCH_RUNS: ResearchRunDetail[] = [
  {
    id: "run-aic-001",
    case_id: FIXTURE_CASE_ID,
    status: "running",
    stage: "evidence_search",
    round: 2,
    stop_reason: null,
    created_at: "2026-08-05T09:10:00+08:00",
    next_action: "等待人工审核 2 条提议关系",
    progress: { tasks_total: 6, tasks_completed: 4, tasks_failed: 1 },
    evidence: { discovered: 18, accepted: 11, pending: 3 },
    pending_proposals: [{ id: "proposal-1", thesis_id: "TH-AIC-03", task_id: "task-3", status: "pending" }],
    pending_assessments: [],
    review_tasks: [{ id: "review-1", status: "open", task_type: "evidence_link", ref_type: "evidence_link", ref_id: "EL-003" }],
    gap_tasks: [{ id: "gap-1", status: "open", task_type: "gap", stage: "evidence_search", round: 2, query: "订单到收入的独立披露", evidence_count: 0, gap_reason: "缺少同主体连续披露" }],
    failed_tasks: [{ id: "failed-1", status: "failed", task_type: "provider_query", stage: "evidence_search", round: 2, query: "历史持仓明细", evidence_count: 0, gap_reason: "权限不足" }],
  },
];

function cloneResearchRun(run: ResearchRunDetail): ResearchRunDetail {
  return {
    ...run,
    progress: { ...run.progress },
    evidence: { ...run.evidence },
    pending_proposals: run.pending_proposals.map((proposal) => ({ ...proposal })),
    pending_assessments: run.pending_assessments.map((assessment) => ({
      ...assessment,
      gaps: [...assessment.gaps],
    })),
    review_tasks: run.review_tasks.map((task) => ({ ...task })),
    gap_tasks: run.gap_tasks.map((task) => ({ ...task })),
    failed_tasks: run.failed_tasks.map((task) => ({ ...task })),
  };
}

type EventTsmReviewOutcome = "confirmed" | "needs_more_evidence" | "rejected";

type PreparationScenario =
  | "preparing"
  | "review_claims"
  | "review_protocol"
  | "review_plan"
  | "recoverable_failure"
  | "authorized";

type EventTsmReviewDecision = {
  outcome: EventTsmReviewOutcome;
  reason: string;
  reviewerId: string;
};

type EventTsmProjection = {
  lifecycle: EventLifecycle;
  verified: number;
  pending: number;
  conclusionState: EventWorkbench["conclusion"]["state"];
  nextAction: EventWorkbench["nextAction"];
};

function mockResearchPreparation(scenario: PreparationScenario): ResearchPreparation {
  const review = {
    candidateClaims: { state: "awaiting_review" as const },
    protocol: { state: "locked" as const },
    evidencePlan: { state: "locked" as const },
  };
  const preparation: ResearchPreparation = {
    caseId: "event-preparation",
    caseTitle: "AI 服务器需求研究",
    initialMaterial: { documentVersionId: "91c8e13c-f649-4f6b-9330-0c9ae7cb6641", title: "模拟冻结原文", parseState: "success" },
    progress: { completedSteps: 1, totalSteps: 3, currentStep: "parse_claims", failedStep: null },
    revision: 1,
    status: "awaiting_claim_review",
    researchRunId: null,
    system: {
      candidateClaims: { state: "succeeded", artifactSequence: 1 },
      protocol: { state: "succeeded", artifactSequence: 2 },
      evidencePlan: { state: "succeeded", artifactSequence: 3 },
    },
    review,
    nextAttemptAt: null,
    lastErrorMessage: null,
    artifacts: {
      candidateClaims: { sequence: 1, state: "current", payload: { candidates: [{ candidate_id: "8a23ef12-9b37-4f54-8f2d-b938605a1d8d", normalized_text: "订单增长可以转化为收入", quote: "订单增长可以转化为收入" }] }, contextFingerprint: "mock-source-v1", displayWithheld: false },
      protocol: { sequence: 2, state: "current", payload: { research_question: "事件是否改变关键因素？" }, contextFingerprint: "mock-source-v1", displayWithheld: false },
      evidencePlan: { sequence: 3, state: "current", payload: { sources: ["公司公告"] }, contextFingerprint: "mock-source-v1", displayWithheld: false },
    },
    authorizedEvidencePlan: null,
    authorizedEvidencePlanDisplayWithheld: false,
  };
  if (scenario === "preparing") {
    preparation.status = "preparing";
    preparation.system.candidateClaims = { state: "running", artifactSequence: null };
    preparation.system.protocol = { state: "queued", artifactSequence: null };
    preparation.system.evidencePlan = { state: "queued", artifactSequence: null };
    preparation.review.candidateClaims = { state: "locked" };
    preparation.progress = { completedSteps: 0, totalSteps: 3, currentStep: "parse_claims", failedStep: null };
  }
  if (scenario === "review_protocol") {
    preparation.status = "awaiting_protocol_confirmation";
    preparation.review.candidateClaims = { state: "confirmed" };
    preparation.progress = { completedSteps: 2, totalSteps: 3, currentStep: "draft_protocol", failedStep: null };
  }
  if (scenario === "review_plan") {
    preparation.status = "awaiting_plan_authorization";
    preparation.review.candidateClaims = { state: "confirmed" };
    preparation.review.protocol = { state: "confirmed" };
    preparation.review.evidencePlan = { state: "awaiting_review" };
    preparation.progress = { completedSteps: 3, totalSteps: 3, currentStep: "draft_evidence_plan", failedStep: null };
  }
  if (scenario === "recoverable_failure") {
    preparation.status = "recoverable_failure";
    preparation.system.candidateClaims = { state: "failed", artifactSequence: null };
    preparation.system.protocol = { state: "stale", artifactSequence: null };
    preparation.system.evidencePlan = { state: "stale", artifactSequence: null };
    preparation.review.candidateClaims = { state: "locked" };
    preparation.nextAttemptAt = "2026-08-15T10:05:00Z";
    preparation.lastErrorMessage = "准备任务暂时未完成，可由研究员重试。";
    preparation.progress = { completedSteps: 0, totalSteps: 3, currentStep: null, failedStep: "parse_claims" };
  }
  if (scenario === "authorized") {
    preparation.status = "authorized";
    preparation.researchRunId = "run-preparation-authorized";
    preparation.review.candidateClaims = { state: "confirmed" };
    preparation.review.protocol = { state: "confirmed" };
    preparation.review.evidencePlan = { state: "confirmed" };
    preparation.authorizedEvidencePlan = { sources: ["公司公告"] };
    preparation.progress = { completedSteps: 3, totalSteps: 3, currentStep: null, failedStep: null };
  }
  return preparation;
}

export class MockResearchAdapter implements ResearchClient {
  private scenario: MockScenario;
  private readonly preparationScenario: PreparationScenario;
  private preparation: ResearchPreparation;
  // mutable per-instance copies for tests that write review decisions.
  private queue: ReviewQueueItem[];
  private researchRuns: ResearchRunDetail[];
  private createdResearchRunCount = 0;
  private createdAutomaticResearchCount = 0;
  // track decision history so submitReviewDecision has stable semantics.
  private decisions: { itemId: string; outcome: ReviewOutcome; reason: string }[] = [];
  private eventTsmReviewDecision: EventTsmReviewDecision | null = null;
  private eventTsmConclusionVersions: EventConclusionVersion[] = [];
  private eventConclusionVersions = new Map<string, EventConclusionVersion[]>();
  private eventConclusionPublicationsInFlight = new Set<string>();
  private eventMutationRevisions = new Map<string, number>();
  private eventStates = new Map<string, {
    event?: EventResearchListItem;
    lifecycle: EventLifecycle;
    scope: { version: number; factors: EventResearchScopeFactor[]; unmappedEvidenceCount: number };
    lifecycleSource?: "tsm_review" | "tsm_publication";
  }>();

  async startAutomaticResearch(input: string): Promise<AutomaticResearchStart> {
    this.throwIfOffline();
    this.createdAutomaticResearchCount += 1;
    return simulateLatency({
      caseId: `automatic-case-${this.createdAutomaticResearchCount}`,
      runId: `automatic-run-${this.createdAutomaticResearchCount}`,
      status: "queued",
    });
  }

  async getAutomaticResearch(caseId: string): Promise<AutomaticResearchView> {
    this.throwIfOffline();
    return simulateLatency({
      caseId,
      runId: `automatic-run-${this.createdAutomaticResearchCount || 1}`,
      title: "自动研究",
      status: "completed",
      stages: [
        { key: "acquire", label: "采集", status: "completed", summary: "已完成", startedAt: null, completedAt: null },
        { key: "parse", label: "解析", status: "completed", summary: "已完成", startedAt: null, completedAt: null },
        { key: "admit", label: "准入", status: "completed", summary: "已完成", startedAt: null, completedAt: null },
        { key: "analyze", label: "分析", status: "completed", summary: "已完成", startedAt: null, completedAt: null },
        { key: "conclude", label: "结论", status: "completed", summary: "已完成", startedAt: null, completedAt: null },
      ],
      stats: { sourceCount: 0, admittedEvidenceCount: 0, skippedCount: 0, durationSeconds: 0 },
      recentActivity: [],
      exceptions: [],
      failureReason: null,
      result: {
        label: "系统生成，未经人工审核",
        humanReviewed: false,
        conclusion: "自动研究已完成。",
        keyFindings: [],
        counterEvidence: [],
        limitations: [],
        sources: [],
      },
    });
  }

  async retryAutomaticResearch(caseId: string): Promise<AutomaticResearchStart> {
    this.throwIfOffline();
    this.createdAutomaticResearchCount += 1;
    return simulateLatency({
      caseId,
      runId: `automatic-run-${this.createdAutomaticResearchCount}`,
      status: "queued",
    });
  }
  private createdDocuments = new Map<string, {
    document: SourceDocumentView;
    spans: DocumentSpan[];
  }>();
  private createdEventCount = 0;
  private createdSupplementCount = 0;
  private extractedDocumentIds = new Set<string>();

  constructor(opts: { scenario?: MockScenario; preparationScenario?: PreparationScenario } = {}) {
    this.scenario = opts.scenario ?? "typical";
    this.preparationScenario = opts.preparationScenario ?? "review_claims";
    this.preparation = mockResearchPreparation(this.preparationScenario);
    this.queue = REVIEW_QUEUE.map((r) => ({ ...r }));
    this.researchRuns = MOCK_RESEARCH_RUNS.map(cloneResearchRun);
  }

  setScenario(scenario: MockScenario): void {
    this.scenario = scenario;
    this.queue = REVIEW_QUEUE.map((r) => ({ ...r }));
    this.researchRuns = MOCK_RESEARCH_RUNS.map(cloneResearchRun);
    this.decisions = [];
    this.eventTsmReviewDecision = null;
    this.eventTsmConclusionVersions = [];
    this.eventConclusionVersions.clear();
    this.eventConclusionPublicationsInFlight.clear();
    this.eventMutationRevisions.clear();
    this.eventStates.clear();
    this.createdDocuments.clear();
    this.createdEventCount = 0;
    this.createdSupplementCount = 0;
    this.extractedDocumentIds.clear();
    this.preparation = mockResearchPreparation(this.preparationScenario);
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

  private projectResearchRun(run: ResearchRunDetail): ResearchRunDetail {
    return cloneResearchRun(run);
  }

  private eventTsmProjection(): EventTsmProjection {
    if (this.eventTsmPublishedConclusion()) {
      return {
        lifecycle: {
          status: "published",
          activeRunId: null,
          currentRound: 1,
          summary: "结论已由研究员发布，进入持续跟踪",
          currentGap: null,
          nextHumanAction: null,
        },
        verified: 1,
        pending: 0,
        conclusionState: "published",
        nextAction: {
          kind: "wait",
          label: "当前没有需要处理的任务",
        },
      };
    }

    const decision = this.eventTsmReviewDecision;
    if (!decision) {
      return {
        lifecycle: {
          status: "awaiting_key_review",
          activeRunId: "run-mock",
          currentRound: 1,
          summary: "已筛出 1 条可处理的关键证据，等待审核",
          currentGap: null,
          nextHumanAction: "审核 1 条关键证据",
        },
        verified: 0,
        pending: 1,
        conclusionState: "cannot_conclude",
        nextAction: {
          kind: "review_evidence",
          label: "审核 1 条关键证据",
          count: 1,
        },
      };
    }

    switch (decision.outcome) {
      case "confirmed":
        return {
          lifecycle: {
            status: "draft_ready",
            activeRunId: null,
            currentRound: 1,
            summary: "关键证据已审核，等待结论复核",
            currentGap: null,
            nextHumanAction: "审核结论草案",
          },
          verified: 1,
          pending: 0,
          conclusionState: "ai_draft",
          nextAction: { kind: "review_conclusion", label: "审核结论草案" },
        };
      case "needs_more_evidence":
        return {
          lifecycle: {
            status: "researching",
            activeRunId: "run-mock",
            currentRound: 2,
            summary: "审核已完成，系统正在按补证要求继续研究",
            currentGap: decision.reason,
            nextHumanAction: null,
          },
          verified: 0,
          pending: 0,
          conclusionState: "cannot_conclude",
          nextAction: { kind: "wait", label: "系统补证中" },
        };
      case "rejected":
        return {
          lifecycle: {
            status: "exhausted",
            activeRunId: null,
            currentRound: 1,
            summary: "当前候选已驳回，需要调整研究范围",
            currentGap: "当前候选被驳回，需要调整因素或补充来源",
            nextHumanAction: "编辑并继续自动研究",
          },
          verified: 0,
          pending: 0,
          conclusionState: "cannot_conclude",
          nextAction: { kind: "edit_factors", label: "编辑并继续自动研究" },
        };
      default: {
        const unsupportedOutcome: never = decision.outcome;
        throw new Error(`unsupported TSM review outcome: ${unsupportedOutcome}`);
      }
    }
  }

  private eventTsmPublishedConclusion(): EventConclusionVersion | null {
    return this.eventTsmConclusionVersions.find((version) => version.state === "published") ?? null;
  }

  private eventTsmDraftSnapshot(): EventConclusionVersion {
    const scope = this.eventStates.get("event-tsm")?.scope;
    return {
      id: "draft-event-tsm-v1",
      sequence: 1,
      state: "ai_draft",
      text: "当前结论草案等待人工复核。",
      primaryFactor: scope
        ? scope.factors[0]?.statement ?? null
        : "资本开支 / 自由现金流担忧",
      scopeVersion: scope?.version ?? 1,
      basedOnConclusionId: null,
      reviewer: null,
      evidenceCount: 1,
      createdAt: "2026-08-07T09:30:00Z",
    };
  }

  private eventMutationRevision(caseId: string): number {
    return this.eventMutationRevisions.get(caseId) ?? 0;
  }

  private advanceEventMutationRevision(caseId: string): void {
    this.eventMutationRevisions.set(
      caseId,
      this.eventMutationRevision(caseId) + 1,
    );
  }

  async getOverview(query?: OverviewQuery): Promise<WorkspaceOverview> {
    this.throwIfOffline();
    if (this.scenario === "empty") return simulateLatency(emptyOverview());
    return simulateLatency(OVERVIEW);
  }

  async createResearchTask(input: CreateResearchTaskInput): Promise<ResearchTaskItem> {
    const task: ResearchTaskItem = {
      id: `mock-task-${Date.now()}`,
      title: input.title,
      description: input.description ?? null,
      status: "open",
      priority: input.priority ?? "normal",
      task_type: input.task_type ?? "counter_research",
      ref_type: input.ref_type ?? null,
      ref_id: input.ref_id ?? null,
      research_case_id: input.research_case_id ?? null,
      assignee: input.assignee ?? null,
      created_at: new Date().toISOString(),
      due_at: null,
    };
    return simulateLatency(task);
  }

  async updateResearchTask(
    taskId: string,
    status: ResearchTaskStatus,
    assignee?: string,
  ): Promise<ResearchTaskItem> {
    const current = DOSSIER.counter_research[0];
    return simulateLatency({
      id: taskId,
      title: current?.objective ?? "反方研究",
      description: current?.next_action ?? null,
      status,
      priority: "normal",
      task_type: "counter_research",
      ref_type: "thesis",
      ref_id: current?.thesis_id ?? null,
      research_case_id: "ai-compute",
      assignee: assignee ?? null,
      created_at: new Date().toISOString(),
      due_at: null,
    });
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
    const docs = [
      ...(this.scenario === "parse_failed" ? parseFailedDocs() : DOCUMENTS),
      ...[...this.createdDocuments.values()].map(({ document }) => document),
    ];
    const q = (query?.query ?? "").toLowerCase();
    const filtered = q
      ? docs.filter(
          (d) =>
            (d.title ?? "").toLowerCase().includes(q) ||
            (d.publisher ?? "").toLowerCase().includes(q) ||
            d.linked_cases.some((c) => c.title.toLowerCase().includes(q))
        )
      : docs;
    const caseScoped = query?.caseId
      ? filtered.filter((document) => document.linked_cases.some((item) => item.id === query.caseId))
      : filtered;
    return simulateLatency(caseScoped);
  }

  async getDocumentDetail(documentId: string, _caseId?: string): Promise<{
    document: SourceDocumentView;
    spans: DocumentSpan[];
  }> {
    this.throwIfOffline();
    const created = this.createdDocuments.get(documentId);
    if (created) return simulateLatency(created);
    const docs = this.scenario === "parse_failed" ? parseFailedDocs() : DOCUMENTS;
    const document = docs.find((d) => d.id === documentId) ?? docs[0];
    const spans: DocumentSpan[] =
      document.parse_quality === "failed"
        ? []
        : document.id === "doc-event-tsm-q2"
          ? [
              {
                id: "sp-tsm-capex",
                document_id: document.id,
                locator: { page: 12, section: "资本开支" },
                verbatim_text: "公司上调全年资本开支指引，同时市场关注自由现金流承压。",
                cited_by: [{ evidence_id: "event-tsm-evidence-1", thesis_id: "event-tsm-factor-1", role: "supports" }],
              },
              {
                id: "sp-tsm-cowos",
                document_id: document.id,
                locator: { page: 4, section: "先进封装" },
                verbatim_text: "管理层说明 CoWoS 产能扩充仍在按既定节奏推进。",
                cited_by: [],
              },
            ]
        : document.id === "doc-fund-holdings-2026q2"
          ? [
              {
                id: "sp-fund-holdings-tsm",
                document_id: document.id,
                locator: { table: "前十大持仓", row: 3 },
                verbatim_text: "截至 2026 年 6 月 30 日，台积电占基金资产净值 3.80%。",
                cited_by: [],
              },
            ]
        : document.id === "doc-event-published-baseline"
          ? [
              {
                id: "sp-published-baseline",
                document_id: document.id,
                locator: { page: 1, section: "经营数据" },
                verbatim_text: "基准披露已按当时可得资料审核，发布结论不自动随新材料改写。",
                cited_by: [],
              },
            ]
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
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const all: SearchHit[] = [
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
    ];
    // 与真实后端一致的诚实语义：按分组/标题/提示过滤，
    // 无匹配时返回空数组，前端据此展示「无匹配结果」空态。
    return simulateLatency<SearchHit[]>(
      all.filter((h) =>
        `${h.group} ${h.title} ${h.hint}`.toLowerCase().includes(q),
      ),
    );
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

  // ── Created cases (screen 2 → screen 4 chain) ─────────────────────────
  // Cases created via createCase live in memory so the case workbench and
  // relationship canvas render the case the user just created instead of
  // falling back to the frozen fixture case.

  private createdCases = new Map<
    string,
    { input: CreateCaseInput; result: CreateCaseResult }
  >();

  async createCase(input: CreateCaseInput): Promise<CreateCaseResult> {
    const result: CreateCaseResult = {
      caseId: `RC-MOCK-${Date.now()}`,
      thesisIds: input.theses.map((_, i) => `TH-MOCK-${i + 1}`),
    };
    this.createdCases.set(result.caseId, { input, result });
    return simulateLatency(result);
  }

  async getResearchPlanView(_caseId?: string): Promise<ResearchPlanView> {
    return simulateLatency(buildResearchPlanView());
  }

  async listCaseSummaries(): Promise<CaseSummaryItem[]> {
    const created = [...this.createdCases.values()].map(({ input, result }) => ({
      id: result.caseId,
      title: input.title,
      topic: input.industryTopic,
      updatedAt: new Date().toISOString(),
    }));
    return simulateLatency([
      ...created,
      {
        id: FIXTURE_CASE_ID,
        title: FIXTURE_CASE_TITLE,
        topic: "ai_compute",
        updatedAt: FIXTURE_CUTOFF,
      },
    ]);
  }

  async getCaseWorkbenchView(
    caseId: string,
    _options?: { thesisId?: string },
  ): Promise<CaseWorkbenchView> {
    if (this.createdCases.has(caseId)) {
      return simulateLatency(this.buildCreatedCaseWorkbenchView(caseId));
    }
    return simulateLatency(buildCaseWorkbenchView());
  }

  private buildCreatedCaseWorkbenchView(caseId: string): CaseWorkbenchView {
    const record = this.createdCases.get(caseId)!;
    const { input, result } = record;
    return {
      case: {
        id: caseId,
        title: input.title,
        question: input.coreQuestion ?? "",
        researchObject: input.researchObject || input.industryTopic,
        researchPeriod:
          [input.periodStart, input.periodEnd].filter(Boolean).join(" — ") ||
          "未设定",
        cutoff: input.periodEnd
          ? `${input.periodEnd}T23:59:59+08:00`
          : new Date().toISOString(),
        snapshotId: "尚未冻结",
        aiState: "草稿",
        humanReviewState: "未人工复核",
      },
      tabs: ["研究摘要", "关键图表", "核心观点", "风险与假设", "相关公司", "研究日志"],
      formalJudgment: {
        text: "该案例尚未形成正式结论：请先执行研究计划、收集证据并提交人工复核。",
        rationale: "新建案例暂无已审核证据，正式判断留空。",
        reviewState: "pending",
        snapshotId: "",
        reviewedAt: "",
      },
      aiDraft: "",
      contradiction: { id: "", label: "暂无反驳线索" },
      gap: { id: "", label: "尚未识别缺口", explanation: "" },
      nextValidation: {
        thesisId: result.thesisIds[0] ?? "",
        event:
          input.theses[0]?.nextVerificationEvent ||
          "制定研究计划并开始收集证据。",
      },
      thesisRows: input.theses.map((t, i) => ({
        id: result.thesisIds[i] ?? `TH-MOCK-${i + 1}`,
        title: t.title || t.statement.slice(0, 30),
        supportCondition: t.supportCondition ?? "",
        evidenceState: "尚无证据关系",
        relationLabels: "",
        scope: input.researchObject || input.industryTopic,
        falsifier: t.falsificationCondition ?? "",
        reviewState: "pending",
        evidenceReviewState: "no_links",
        frozenEligibility: "excluded" as const,
        selected: i === 0,
      })),
      rebuttal: {
        id: "-",
        statement: "暂无反驳证据。",
        documentId: "-",
        documentTitle: "-",
        sourceVersion: "-",
        publishedDate: "-",
        sourceSpan: "-",
        reviewLabel: "无",
        reviewState: "pending",
        relation: "",
        snapshotMembership: "-",
        frozenEligibility: "-",
      },
      factorRows: [],
      selectedFactor: {
        factorId: "",
        groupLabel: "",
        roleLabel: "",
        statusLabel: "",
        label: "暂无因素",
        timeOrder: "",
        mechanism: "",
        directEvidence: "",
        alternatives: "",
        differenceExplanation: "",
        scope: "",
        falsifier: "",
        counterexample: "",
      },
      sources: [],
    };
  }

  async getRelationshipGraphView(
    caseId: string,
    _thesisId?: string,
  ): Promise<RelationshipGraphView> {
    const record = this.createdCases.get(caseId);
    if (!record) return simulateLatency(buildRelationshipGraphView());
    const { input, result } = record;
    // Newly created cases have no reviewed evidence yet: only the thesis
    // layer is populated, the other four layers start empty.
    const thesisNodes: RelationshipGraphView["nodes"] = input.theses.map(
      (t, i) => ({
        id: result.thesisIds[i] ?? `TH-MOCK-${i + 1}`,
        layer: "命题",
        title: t.title || t.statement.slice(0, 30),
        meta: t.creatorType === "ai" ? "AI 提议" : "人工创建",
        kind: t.creatorType === "ai" ? "ai-proposed" : "reviewed",
        kindLabel: t.creatorType === "ai" ? "AI 提议" : "人工命题",
        relation: "",
        review: "待复核",
        sourceName: "-",
        sourceSpan: "-",
        sourceHref: "",
        attachment: "-",
        publicationDate: "-",
        asOf: input.periodEnd || "-",
        scope: input.researchObject || input.industryTopic,
        citations: [],
        note: t.statement,
      }),
    );
    return simulateLatency({
      case: {
        id: caseId,
        title: input.title,
        question: input.coreQuestion ?? "",
        cutoff: input.periodEnd
          ? `${input.periodEnd}T23:59:59+08:00`
          : new Date().toISOString(),
        snapshotId: "尚未冻结",
      },
      layers: [
        { key: "evidence", label: "证据", nodes: [] },
        { key: "thesis", label: "命题", nodes: thesisNodes },
        { key: "causal", label: "因果链", nodes: [] },
        { key: "company", label: "公司", nodes: [] },
        { key: "fund", label: "基金", nodes: [] },
      ],
      nodes: thesisNodes,
      edges: [],
      selectedNodeId: thesisNodes[0]?.id ?? "",
    });
  }

  async getLibraryView(): Promise<LibraryView> {
    return simulateLatency(buildLibraryView());
  }

  async getDataCenterView(): Promise<DataCenterView> {
    return simulateLatency(buildDataCenterView());
  }

  async getDataCenterMetric(
    _stockId: string,
    _metricName: string,
  ): Promise<DataMetricSelection> {
    this.throwIfOffline();
    const view = buildDataCenterView();
    return simulateLatency({
      selectedMetric: view.selectedMetric,
      series: view.series,
    });
  }

  async getVersionsView(
    _caseId?: string,
    _options?: { base?: string; compare?: string },
  ): Promise<VersionsView> {
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

  async getReviewQueueView(caseId?: string): Promise<ReviewQueueView> {
    this.throwIfOffline();
    const reviewed = new Set(this.linkReviews.map((r) => r.linkId));
    const items: ReviewQueueViewItem[] = PROTOTYPE_REVIEW_QUEUE.filter(
      (item) => !reviewed.has(item.id),
    )
      .filter((item) => {
        if (!caseId || caseId === "RC-AIC-2025-01") return true;
        return false; // 离线原型只有 AI 算力案例一份数据
      })
      .map((item) => {
        const st = PROTOTYPE_STATEMENTS.find((s) => s.id === item.targetId);
        const link = PROTOTYPE_EVIDENCE_LINKS.find(
          (l) => l.statementId === item.targetId,
        );
        return {
          linkId: item.id,
          thesisId: link?.thesisId ?? "",
          caseId: caseId ?? "RC-AIC-2025-01",
          thesisStatement: "",
          aiRole: link?.role ?? "gap",
          aiReason: link?.rationale ?? item.task,
          aiScope: {},
          statementId: st?.id ?? item.targetId,
          statementText: st?.text ?? item.task,
          statementKind: "disclosed_fact",
          verbatimText: link?.sourceSpan ?? item.sourceSpan,
          locator: {},
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

  private assessmentReviews: {
    assessmentId: string;
    payload: AssessmentReviewPayload;
  }[] = [];

  async reviewAssessment(
    assessmentId: string,
    payload: AssessmentReviewPayload,
  ): Promise<AssessmentReviewResult> {
    this.throwIfOffline();
    this.throwIfPermissionDenied();
    this.assessmentReviews.push({ assessmentId, payload });
    return simulateLatency({
      id: `AR-MOCK-${this.assessmentReviews.length}`,
      outcome: payload.outcome,
      reviewer: payload.reviewer,
      createdAt: new Date().toISOString(),
    });
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

  async proposeEvidence(thesisId: string): Promise<ProposeEvidenceResult> {
    this.throwIfOffline();
    // 离线原型无法真正运行 AI 提议；如实返回 0 条，由页面提示用户。
    return simulateLatency({ thesisId, mode: "mock", linkCount: 0 });
  }

  async ingestDocuments(caseId?: string): Promise<IngestRunResult> {
    this.throwIfOffline();
    // 离线原型不接外部数据源；如实返回全 0，由页面提示用户。
    return simulateLatency({
      researchReports: 0,
      announcements: 0,
      news: 0,
      macroSeries: 0,
      spans: 0,
      valuationsWritten: 0,
      valuationsSkipped: 0,
      caseId: caseId ?? null,
    });
  }

  async extractStatements(
    documentVersionId: string,
  ): Promise<ExtractStatementsResult> {
    this.throwIfOffline();
    const created = this.createdDocuments.get(documentVersionId);
    if (created && created.document.source_contract?.permissions.ai_processing !== false) {
      this.extractedDocumentIds.add(documentVersionId);
      return simulateLatency({ documentVersionId, mode: "mock", candidateCount: 1, reason: null });
    }
    // Seed documents deliberately model an extraction gap; they remain recoverable.
    return simulateLatency({
      documentVersionId,
      mode: "mock",
      candidateCount: 0,
      reason: "离线原型未运行 LLM 抽取",
    });
  }

  getAtomicClaimCandidates(caseId: string): components["schemas"]["AtomicClaimCandidateDTO"][] {
    return [...this.extractedDocumentIds].flatMap((documentId) => {
      const item = this.createdDocuments.get(documentId);
      const span = item?.spans[0];
      if (!item || !span || !item.document.linked_cases.some((linked) => linked.id === caseId)) return [];
      return [{ id: `atomic-${documentId}`, source_span_id: span.id, document_version_id: documentId, document_source_url: "", locator: span.locator, quote: span.verbatim_text, quote_start: 0, quote_end: span.verbatim_text.length, quote_sha256: "b".repeat(64), normalized_text: span.verbatim_text, claim_type: "source_excerpt", assertion_actor: item.document.publisher, authority_level: item.document.source_authority ?? "unknown", structured_fields: { run_ref: `mock-extract:${documentId}` }, validation_result: { quote_continuous: true }, created_at: "2026-08-09T12:06:00Z", review_state: "awaiting_review", review_history: [], published_source_statement: null }];
    });
  }

  /** Test-only mock equivalent of the append-only supplement endpoint. */
  async createDocumentSupplement(input: {
    caseId: string;
    documentId: string;
    rawText: string;
    claimedPageReference: string;
    createdBy: string;
  }): Promise<{ documentVersionId: string; originalDocumentVersionId: string; extractionAllowed: boolean }> {
    this.throwIfOffline();
    const original = await this.getDocumentDetail(input.documentId);
    if (!original.document.linked_cases.some((item) => item.id === input.caseId)) {
      throw new PageStateError("stale", "资料不属于当前 Case");
    }
    const documentVersionId = `document-supplement-${++this.createdSupplementCount}`;
    const contract = original.document.source_contract;
    const extractionAllowed = original.document.parse_quality !== "failed" && contract?.permissions.ai_processing !== false;
    const document: SourceDocumentView = {
      ...original.document,
      id: documentVersionId,
      title: `${original.document.title || "未命名资料"} · 补充正文`,
      publisher: input.createdBy,
      document_type: "pasted_snapshot",
      available_at: "2026-08-09T12:05:00Z",
      acquired_at: "2026-08-09T12:05:00Z",
      parser_version: "user-supplement-v1",
      parse_quality: "partial",
      span_count: 1,
      statement_count: 0,
      version_label: `${original.document.version_label || "v1"} · 补充 v${this.createdSupplementCount}`,
      source_contract: contract ? { ...contract, provider_or_tenant: input.createdBy } : contract,
    };
    this.createdDocuments.set(documentVersionId, {
      document,
      spans: [{ id: `span-supplement-${this.createdSupplementCount}`, document_id: documentVersionId, locator: { claimed_page_reference: input.claimedPageReference }, verbatim_text: input.rawText, cited_by: [] }],
    });
    return simulateLatency({ documentVersionId, originalDocumentVersionId: input.documentId, extractionAllowed });
  }

  // ── 公司研究（/companies）───────────────────────────────────────────────

  async listCompanies(
    query?: string,
    cursor?: string | null,
  ): Promise<CompanyListView> {
    this.throwIfOffline();
    if (this.scenario === "empty") {
      return simulateLatency({ items: [], hasMore: false, nextCursor: null });
    }
    const q = query?.trim().toLowerCase();
    const items = q
      ? COMPANY_LIST.filter(
          (c) =>
            c.name.toLowerCase().includes(q) ||
            c.code.toLowerCase().includes(q),
        )
      : COMPANY_LIST;
    // mock 数据量小：cursor 仅透传分页契约，不真正切片。
    void cursor;
    return simulateLatency({ items, hasMore: false, nextCursor: null });
  }

  async getCompanyDossier(
    companyId: string,
    opts?: { cutoff?: string },
  ): Promise<CompanyDossierView> {
    this.throwIfOffline();
    let base: CompanyDossierView;
    if (!COMPANY_DOSSIERS[companyId] || this.scenario === "empty") {
      base = {
        cutoff: opts?.cutoff ?? MOCK_CUTOFF_NOW,
        isHistorical: Boolean(opts?.cutoff),
        company: {
          id: companyId,
          code: "",
          name: "",
          type: "",
          createdAt: null,
        },
        stocks: [],
        themeRoles: [],
        relatedTheses: [],
        valuations: [],
        fundHolders: [],
      };
    } else {
      base = COMPANY_DOSSIERS[companyId];
    }
    if (opts?.cutoff) base = applyCompanyCutoff(base, opts.cutoff);
    // 派生 5 节点关系路径 + 右栏检查器固定命题（设计图 10 视觉）
    const derived = deriveCompanyPathNodes(base);
    // 装饰公司元信息（市场 / 上市 / 最近披露期）+ 主题角色状态（设计图 10 视觉）
    return simulateLatency(decorateCompanyDossier({ ...base, ...derived }));
  }

  // ── 主题研究（/topics · 横切主题）───────────────────────────────────────

  async listThemes(): Promise<TopicListItem[]> {
    this.throwIfOffline();
    if (this.scenario === "empty") return simulateLatency([]);
    return simulateLatency(TOPIC_LIST);
  }

  async getThemeView(
    tag: string,
    opts?: { cutoff?: string },
  ): Promise<TopicView> {
    this.throwIfOffline();
    let base: TopicView;
    if (this.scenario === "empty") {
      base = emptyTopicView(tag);
    } else {
      base = TOPIC_VIEWS[tag] ?? emptyTopicView(tag);
    }
    if (opts?.cutoff) base = applyTopicCutoff(base, opts.cutoff);
    // 派生 5 节点关系路径 + 右栏检查器固定命题（设计图 9 视觉）
    const derived = deriveTopicPathNodes(base);
    // 装饰案例卡片 / 角色行的设计图文案（设计图 9/10 视觉）
    return simulateLatency(decorateTopicView({ ...base, ...derived }));
  }

  async listResearchRuns(caseId: string): Promise<ResearchRunSummary[]> {
    this.throwIfOffline();
    return simulateLatency(this.researchRuns.filter((run) => run.case_id === caseId).map(({ id, status, stage, round, stop_reason, created_at, next_action }) => ({ id, status, stage, round, stop_reason, created_at, next_action })));
  }

  async getResearchRun(runId: string): Promise<ResearchRunDetail> {
    this.throwIfOffline();
    const run = this.researchRuns.find((item) => item.id === runId);
    if (!run) throw new PageStateError("parse_failed", "研究运行不存在");
    return simulateLatency(this.projectResearchRun(run));
  }

  async startResearchRun(caseId: string, options: StartResearchRunOptions): Promise<ResearchRunDetail> {
    this.throwIfOffline();
    const runId = `run-created-${++this.createdResearchRunCount}`;
    const run = cloneResearchRun({
      ...this.researchRuns[0],
      id: runId,
      case_id: caseId,
      status: options.auto_execute ? "waiting_for_review" : "queued",
      round: 0,
      pending_proposals: this.researchRuns[0].pending_proposals.map((proposal, index) => ({
        ...proposal,
        id: `proposal-${runId}-${index + 1}`,
        status: "pending",
      })),
    });
    this.researchRuns.push(run);
    return simulateLatency(this.projectResearchRun(run));
  }

  async cancelResearchRun(runId: string): Promise<ResearchRunSummary> {
    this.throwIfOffline();
    const run = this.researchRuns.find((item) => item.id === runId);
    if (!run) throw new PageStateError("parse_failed", "研究运行不存在");
    run.status = "cancelled";
    return simulateLatency({ id: run.id, status: run.status, stage: run.stage, round: run.round, stop_reason: "cancelled", created_at: run.created_at, next_action: "查看取消前进度" });
  }

  async listReviewProposals(caseId?: string): Promise<ProposalReviewItem[]> {
    this.throwIfOffline();
    const eventProposal: ProposalReviewItem[] = caseId === "event-tsm" && !this.eventTsmReviewDecision
      ? [{
          id: "proposal-event-tsm", kind: "evidence_link",
          payload: { source_statement_id: "event-tsm-statement", role: "supports", reason: "资本开支与现金流担忧的原始披露" },
          target_context: { thesis_id: "event-tsm-factor-1" }, proposed_by_type: "ai",
          proposed_by_ref: "mock-auto-research", proposed_at: "2026-08-07T09:00:00Z",
          basis_cutoff: null, status: "pending", version: 1,
        }]
      : [];
    return simulateLatency(
      [...eventProposal, ...this.researchRuns.flatMap((rawRun) => {
        const run = this.projectResearchRun(rawRun);
        if (caseId && run.case_id !== caseId) return [];
        return run.pending_proposals
          .filter((proposal) => proposal.status === "pending")
          .map((proposal) => ({
            id: proposal.id,
            kind: "evidence_link",
            payload: { source_statement_id: "mock-statement", role: "supports", reason: "Mock 自动研究提议" },
            target_context: { thesis_id: proposal.thesis_id ?? "" },
            proposed_by_type: "ai",
            proposed_by_ref: "mock-auto-research",
            proposed_at: run.created_at,
            basis_cutoff: null,
            status: proposal.status,
            version: 1,
          }));
      })],
    );
  }

  async reviewProposal(proposalId: string, payload: ProposalReviewPayload): Promise<void> {
    this.throwIfOffline();
    if (proposalId === "proposal-event-tsm") {
      if (
        payload.expected_version !== 1
        || this.eventTsmReviewDecision !== null
        || this.eventTsmPublishedConclusion() !== null
      ) {
        throw new Error("proposal version or state conflict");
      }
      switch (payload.outcome) {
        case "modified":
          throw new Error("modified evidence review outcome is unsupported");
        case "confirmed":
        case "needs_more_evidence":
        case "rejected": {
          const previous = this.eventStates.get("event-tsm");
          this.eventTsmReviewDecision = {
            outcome: payload.outcome,
            reason: payload.reason,
            reviewerId: payload.reviewer_id,
          };
          if (previous) {
            this.eventStates.set("event-tsm", {
              ...previous,
              lifecycle: this.eventTsmProjection().lifecycle,
              lifecycleSource: "tsm_review",
            });
          }
          if (
            payload.outcome === "confirmed"
            && !this.eventTsmConclusionVersions.some((version) => version.state === "ai_draft")
          ) {
            this.eventTsmConclusionVersions = [this.eventTsmDraftSnapshot()];
          }
          break;
        }
        default: {
          const unsupportedOutcome: never = payload.outcome;
          throw new Error(`unsupported TSM review outcome: ${unsupportedOutcome}`);
        }
      }
      return simulateLatency(undefined);
    }
    const owningRun = this.researchRuns.find((run) =>
      run.pending_proposals.some((proposal) => proposal.id === proposalId),
    );
    const proposal = owningRun?.pending_proposals.find(
      (item) => item.id === proposalId,
    );
    if (
      !proposal
      || proposal.status !== "pending"
      || payload.expected_version !== 1
    ) {
      throw new Error("proposal version or state conflict");
    }
    proposal.status = "decided";
    return simulateLatency(undefined);
  }

  async extractEventResearch(input: { rawInput: string; sourceUrl?: string; sourceType?: EventSourceType; sourceMetadata?: Record<string, unknown> }): Promise<EventExtraction> {
    this.throwIfOffline();
    return simulateLatency({
      eventTitle: input.rawInput.trim().slice(0, 80) || null,
      companyName: null, ticker: null, eventAt: null, marketReaction: "盘后下跌",
      summary: null, researchQuestion: "这次市场反应的主要可验证因素是什么？",
      candidateFactors: ["资本开支 / 自由现金流担忧", "盈利预期变化", "估值与市场环境"],
      confirmationRequired: true,
    });
  }

  async createEventResearch(input: CreateEventResearchInput): Promise<{ caseId: string; briefId: string; lifecycle: EventLifecycle }> {
    this.throwIfOffline();
    const caseId = `event-created-${++this.createdEventCount}`;
    const sourceType = input.sourceType ?? "pasted_snapshot";
    const sourceMetadata = input.sourceMetadata ?? {};
    const permissions = sourceMetadata.permissions && typeof sourceMetadata.permissions === "object"
      ? sourceMetadata.permissions as Record<string, unknown>
      : {};
    const userControlled = sourceType === "pasted_snapshot" || sourceType === "uploaded_file";
    const aiProcessing = typeof permissions.ai_processing === "boolean" ? permissions.ai_processing : userControlled;
    const display = typeof permissions.display === "boolean" ? permissions.display : userControlled;
    const documentId = `document-created-${this.createdEventCount}`;
    const document: SourceDocumentView = {
      id: documentId,
      source_url: input.sourceUrl ?? null,
      title: typeof sourceMetadata.file_name === "string" ? sourceMetadata.file_name : "事件原始材料快照",
      publisher: typeof sourceMetadata.provider_name === "string" ? sourceMetadata.provider_name : input.createdBy,
      document_type: sourceType,
      publish_date: input.eventAt?.slice(0, 10) ?? null,
      available_at: "2026-08-09T12:00:00Z",
      acquired_at: "2026-08-09T12:00:00Z",
      parser_version: sourceType === "pasted_snapshot" ? "user-pasted-v1" : sourceType === "uploaded_file" ? "uploaded-text-v1" : sourceType === "public_url" ? "user-pasted-public-url-v1" : "provider-snapshot-v1",
      source_authority: typeof sourceMetadata.authority_level === "string" ? sourceMetadata.authority_level : "unknown",
      parse_quality: "partial",
      linked_cases: [{ id: caseId, title: input.eventTitle }],
      span_count: 1,
      statement_count: 0,
      version_label: "v1 · 2026-08-09",
      source_contract: {
        source_type: sourceType,
        provider_or_tenant: typeof sourceMetadata.provider_name === "string" ? sourceMetadata.provider_name : input.createdBy,
        permissions: { ai_processing: aiProcessing, display, export: typeof permissions.export === "boolean" ? permissions.export : false, api: typeof permissions.api === "boolean" ? permissions.api : false },
        status: mockSourceContractStatus(sourceMetadata, aiProcessing, display),
        region: typeof sourceMetadata.region === "string" ? sourceMetadata.region : "not_recorded",
        effective_from: typeof sourceMetadata.effective_from === "string" ? sourceMetadata.effective_from : null,
        effective_until: typeof sourceMetadata.effective_until === "string" ? sourceMetadata.effective_until : null,
        retention_policy: typeof sourceMetadata.retention_policy === "string" ? sourceMetadata.retention_policy : "case_retained",
        deletion_policy: typeof sourceMetadata.deletion_policy === "string" ? sourceMetadata.deletion_policy : "not_recorded",
        downstream_restrictions: Array.isArray(sourceMetadata.downstream_restrictions) ? sourceMetadata.downstream_restrictions.filter((value): value is string => typeof value === "string") : userControlled ? ["仅限当前 Case 研究与人工审核"] : ["权限未完整记录；不得作为正式证据"],
        contract_version: typeof sourceMetadata.contract_version === "string" ? sourceMetadata.contract_version : null,
        provider_record: sourceType === "licensed_provider" && typeof sourceMetadata.provider_name === "string" && typeof sourceMetadata.provider_record_id === "string" ? {
          provider_name: sourceMetadata.provider_name,
          provider_record_id: sourceMetadata.provider_record_id,
          request_scope: sourceMetadata.request_scope && typeof sourceMetadata.request_scope === "object" ? sourceMetadata.request_scope as Record<string, unknown> : {},
          retrieval_reference: typeof sourceMetadata.retrieval_reference === "string" ? sourceMetadata.retrieval_reference : null,
          content_sha256: `mock-provider-response-${documentId}`,
          retrieved_at: "2026-08-09T12:00:00Z",
          contract_version: typeof sourceMetadata.contract_version === "string" ? sourceMetadata.contract_version : null,
        } : null,
      },
    };
    this.createdDocuments.set(documentId, {
      document,
      spans: [{
        id: `span-created-${this.createdEventCount}`,
        document_id: documentId,
        locator: { kind: sourceType, source_metadata: sourceMetadata },
        verbatim_text: input.rawInput,
        cited_by: [],
      }],
    });
    const lifecycle: EventLifecycle = {
      status: "awaiting_key_review",
      activeRunId: null,
      currentRound: 0,
      summary: "资料已冻结，等待核验原文与研究协议；尚未启动后台研究",
      currentGap: "原文资料、来源许可与研究协议尚未完成核验",
      nextHumanAction: "核验原文资料并完成研究协议",
    };
    this.eventStates.set(caseId, {
      event: {
        id: caseId,
        workflowMode: "reviewed",
        eventTitle: input.eventTitle,
        companyName: input.companyName,
        ticker: input.ticker,
        eventAt: input.eventAt,
        status: lifecycle.status,
        statusSummary: lifecycle.summary,
        nextHumanAction: lifecycle.nextHumanAction,
        updatedAt: "2026-08-09T12:00:00Z",
      },
      lifecycle,
      scope: {
        version: 1,
        factors: input.candidateFactors.map((statement) => ({ statement, description: null })),
        unmappedEvidenceCount: 0,
      },
    });
    return simulateLatency({
      caseId, briefId: `brief-created-${this.createdEventCount}`,
      lifecycle,
    });
  }

  async createEventResearchFromUpload(input: CreateUploadedEventResearchInput): Promise<{ caseId: string; briefId: string; lifecycle: EventLifecycle }> {
    return this.createEventResearch({
      ...input,
      sourceType: "uploaded_file",
      sourceMetadata: { ...input.sourceMetadata, file_name: input.file.name, mime_type: input.file.type, byte_size: input.file.size },
    });
  }

  async attachEventMaterial(input: { caseId: string; rawInput: string; sourceUrl?: string; sourceType: EventSourceType; sourceMetadata: Record<string, unknown>; actor: string }): Promise<{ documentVersionId: string }> {
    this.throwIfOffline();
    const caseItem = this.eventResearchItems().find((item) => item.id === input.caseId);
    if (!caseItem) throw new Error("event research case not found");
    if (caseItem.status === "published") throw new Error("published Case requires an explicit material decision");
    const sequence = this.createdDocuments.size + 1;
    const documentVersionId = `document-attached-${sequence}`;
    const userControlled = input.sourceType === "pasted_snapshot" || input.sourceType === "uploaded_file";
    const permissions = input.sourceMetadata.permissions && typeof input.sourceMetadata.permissions === "object"
      ? input.sourceMetadata.permissions as Record<string, unknown>
      : {};
    const aiProcessing = typeof permissions.ai_processing === "boolean" ? permissions.ai_processing : userControlled;
    const display = typeof permissions.display === "boolean" ? permissions.display : userControlled;
    this.createdDocuments.set(documentVersionId, {
      document: {
        id: documentVersionId,
        source_url: input.sourceUrl ?? null,
        title: typeof input.sourceMetadata.file_name === "string" ? input.sourceMetadata.file_name : "收件箱新增材料",
        publisher: input.actor,
        document_type: input.sourceType,
        publish_date: null,
        available_at: "2026-08-09T12:00:00Z",
        acquired_at: "2026-08-09T12:00:00Z",
        parser_version: input.sourceType === "uploaded_file" ? "uploaded-text-v1" : input.sourceType === "licensed_provider" ? "provider-snapshot-v1" : input.sourceType === "public_url" ? "user-pasted-public-url-v1" : "user-pasted-v1",
        source_authority: typeof input.sourceMetadata.authority_level === "string" ? input.sourceMetadata.authority_level : "unknown",
        parse_quality: "partial",
        linked_cases: [{ id: input.caseId, title: caseItem.eventTitle }],
        span_count: 1,
        statement_count: 0,
        version_label: "v1 · 2026-08-09",
        source_contract: {
          source_type: input.sourceType,
          provider_or_tenant: input.actor,
          permissions: { ai_processing: aiProcessing, display, export: typeof permissions.export === "boolean" ? permissions.export : false, api: typeof permissions.api === "boolean" ? permissions.api : false },
          status: mockSourceContractStatus(input.sourceMetadata, aiProcessing, display),
          region: typeof input.sourceMetadata.region === "string" ? input.sourceMetadata.region : "not_recorded",
          effective_from: typeof input.sourceMetadata.effective_from === "string" ? input.sourceMetadata.effective_from : null,
          effective_until: typeof input.sourceMetadata.effective_until === "string" ? input.sourceMetadata.effective_until : null,
          retention_policy: typeof input.sourceMetadata.retention_policy === "string" ? input.sourceMetadata.retention_policy : "case_retained",
          deletion_policy: typeof input.sourceMetadata.deletion_policy === "string" ? input.sourceMetadata.deletion_policy : "not_recorded",
          downstream_restrictions: Array.isArray(input.sourceMetadata.downstream_restrictions) ? input.sourceMetadata.downstream_restrictions.filter((value): value is string => typeof value === "string") : userControlled ? ["仅限当前 Case 研究与人工审核"] : ["权限未完整记录；不得作为正式证据"],
          contract_version: typeof input.sourceMetadata.contract_version === "string" ? input.sourceMetadata.contract_version : null,
        },
      },
      spans: [{
        id: `span-attached-${sequence}`,
        document_id: documentVersionId,
        locator: { kind: input.sourceType, source_metadata: input.sourceMetadata, intake: "existing_case" },
        verbatim_text: input.rawInput,
        cited_by: [],
      }],
    });
    return simulateLatency({ documentVersionId });
  }

  async uploadEventMaterial(input: { caseId: string; file: File; sourceMetadata: Record<string, unknown>; actor: string }): Promise<{ documentVersionId: string; parseState: "parsed" | "partial" | "failed"; nextAction: "review_original" | "supplement_original" }> {
    this.throwIfOffline();
    const caseItem = this.eventResearchItems().find((item) => item.id === input.caseId);
    if (!caseItem) throw new Error("event research case not found");
    if (caseItem.status === "published") throw new Error("published Case requires an explicit material decision");
    const sequence = this.createdDocuments.size + 1;
    const documentVersionId = `document-original-upload-${sequence}`;
    const isPdf = input.file.type === "application/pdf";
    const parseState = isPdf ? "failed" as const : "partial" as const;
    const rawText = isPdf ? "" : await input.file.text();
    const permissions = input.sourceMetadata.permissions && typeof input.sourceMetadata.permissions === "object"
      ? input.sourceMetadata.permissions as Record<string, unknown>
      : {};
    this.createdDocuments.set(documentVersionId, {
      document: {
        id: documentVersionId,
        title: input.file.name,
        publisher: input.actor,
        document_type: "uploaded_file",
        publish_date: null,
        available_at: "2026-08-09T12:00:00Z",
        acquired_at: "2026-08-09T12:00:00Z",
        parser_version: isPdf ? "pypdf-v1" : "uploaded-text-v1",
        source_authority: typeof input.sourceMetadata.authority_level === "string" ? input.sourceMetadata.authority_level : "user_supplied",
        parse_quality: parseState,
        linked_cases: [{ id: input.caseId, title: caseItem.eventTitle }],
        span_count: isPdf ? 0 : 1,
        statement_count: 0,
        version_label: "v1 · 2026-08-09",
        source_contract: {
          source_type: "uploaded_file",
          provider_or_tenant: input.actor,
          permissions: {
            ai_processing: typeof permissions.ai_processing === "boolean" ? permissions.ai_processing : true,
            display: typeof permissions.display === "boolean" ? permissions.display : true,
            export: typeof permissions.export === "boolean" ? permissions.export : false,
            api: typeof permissions.api === "boolean" ? permissions.api : false,
          },
          status: mockSourceContractStatus(
            input.sourceMetadata,
            typeof permissions.ai_processing === "boolean" ? permissions.ai_processing : true,
            typeof permissions.display === "boolean" ? permissions.display : true,
          ),
          region: typeof input.sourceMetadata.region === "string" ? input.sourceMetadata.region : "not_recorded",
          effective_from: typeof input.sourceMetadata.effective_from === "string" ? input.sourceMetadata.effective_from : null,
          effective_until: typeof input.sourceMetadata.effective_until === "string" ? input.sourceMetadata.effective_until : null,
          retention_policy: typeof input.sourceMetadata.retention_policy === "string" ? input.sourceMetadata.retention_policy : "case_retained",
          deletion_policy: typeof input.sourceMetadata.deletion_policy === "string" ? input.sourceMetadata.deletion_policy : "not_recorded",
          downstream_restrictions: Array.isArray(input.sourceMetadata.downstream_restrictions) ? input.sourceMetadata.downstream_restrictions.filter((value): value is string => typeof value === "string") : ["仅限当前 Case 研究与人工审核"],
          contract_version: typeof input.sourceMetadata.contract_version === "string" ? input.sourceMetadata.contract_version : null,
        },
        original_file: {
          file_name: input.file.name,
          mime_type: input.file.type || "text/plain",
          byte_size: input.file.size,
          object_version: `sha256:mock-${documentVersionId}`,
          uploaded_by: input.actor,
          retention_policy: typeof input.sourceMetadata.retention_policy === "string" ? input.sourceMetadata.retention_policy : "case_retained",
        },
      },
      spans: isPdf ? [] : [{
        id: `span-original-upload-${sequence}`,
        document_id: documentVersionId,
        locator: { kind: "uploaded_file", file_name: input.file.name, mime_type: input.file.type || "text/plain", line_start: 1 },
        verbatim_text: rawText,
        cited_by: [],
      }],
    });
    return simulateLatency({
      documentVersionId,
      parseState,
      nextAction: isPdf ? "supplement_original" as const : "review_original" as const,
    });
  }

  async getResearchPreparation(caseId: string): Promise<ResearchPreparation> {
    this.throwIfOffline();
    if (caseId !== this.preparation.caseId) throw new Error("event research case not found");
    return simulateLatency(this.copyPreparation());
  }

  async listResearchPreparationEvents(
    caseId: string,
    cursor: { afterSeq?: number; limit?: number } = {},
  ): Promise<ResearchPreparationEventsPage> {
    this.throwIfOffline();
    if (caseId !== this.preparation.caseId) throw new Error("event research case not found");
    const allEvents: ResearchPreparationEvent[] = [
      { seq: 1, type: "preparation_queued", step: "parse_claims", message: "系统开始准备研究材料", detail: null, createdAt: "2026-08-15T09:00:00Z" },
      { seq: 2, type: "draft_ready", step: "draft_protocol", message: "草案已生成，等待人工确认", detail: null, createdAt: "2026-08-15T09:01:00Z" },
    ];
    const events = allEvents.filter((event) => event.seq > (cursor.afterSeq ?? 0));
    const page = events.slice(0, cursor.limit ?? events.length);
    return simulateLatency({ items: page, nextAfterSeq: page.length > 0 ? page[page.length - 1].seq : null });
  }

  async confirmResearchPreparationClaims(
    input: ConfirmResearchPreparationClaimsInput,
  ): Promise<ResearchPreparation> {
    this.requirePreparation(input.caseId, input.revision, "awaiting_claim_review");
    this.preparation = {
      ...this.preparation,
      revision: this.preparation.revision + 1,
      status: "awaiting_protocol_confirmation",
      review: {
        ...this.preparation.review,
        candidateClaims: { state: "confirmed" },
      },
    };
    return simulateLatency(this.copyPreparation());
  }

  async confirmResearchPreparationProtocol(
    input: ConfirmResearchPreparationProtocolInput,
  ): Promise<ResearchPreparation> {
    this.requirePreparation(input.caseId, input.revision, "awaiting_protocol_confirmation");
    this.preparation = {
      ...this.preparation,
      revision: this.preparation.revision + 1,
      status: "awaiting_plan_authorization",
      review: {
        ...this.preparation.review,
        protocol: { state: "confirmed" },
      },
    };
    return simulateLatency(this.copyPreparation());
  }

  async retryResearchPreparation(
    input: RetryResearchPreparationInput,
  ): Promise<ResearchPreparation> {
    this.requirePreparation(input.caseId, input.revision, "recoverable_failure");
    this.preparation = mockResearchPreparation("preparing");
    this.preparation.revision = input.revision + 1;
    return simulateLatency(this.copyPreparation());
  }

  async authorizeResearchPreparation(
    input: AuthorizeResearchPreparationInput,
  ): Promise<ResearchPreparation> {
    this.requirePreparation(input.caseId, input.revision, "awaiting_plan_authorization");
    this.preparation = {
      ...this.preparation,
      revision: this.preparation.revision + 1,
      status: "authorized",
      researchRunId: "run-preparation-authorized",
      review: {
        ...this.preparation.review,
        evidencePlan: { state: "confirmed" },
      },
      authorizedEvidencePlan: { ...(this.preparation.artifacts.evidencePlan?.payload ?? {}) },
    };
    return simulateLatency(this.copyPreparation());
  }

  private requirePreparation(caseId: string, revision: number, expectedStatus: string): void {
    this.throwIfOffline();
    if (caseId !== this.preparation.caseId) throw new Error("event research case not found");
    if (revision !== this.preparation.revision || this.preparation.status !== expectedStatus) {
      throw new Error("preparation state has changed");
    }
  }

  private copyPreparation(): ResearchPreparation {
    return {
      ...this.preparation,
      system: {
        candidateClaims: { ...this.preparation.system.candidateClaims },
        protocol: { ...this.preparation.system.protocol },
        evidencePlan: { ...this.preparation.system.evidencePlan },
      },
      review: {
        candidateClaims: { ...this.preparation.review.candidateClaims },
        protocol: { ...this.preparation.review.protocol },
        evidencePlan: { ...this.preparation.review.evidencePlan },
      },
      artifacts: {
        candidateClaims: this.preparation.artifacts.candidateClaims ? { ...this.preparation.artifacts.candidateClaims, payload: { ...this.preparation.artifacts.candidateClaims.payload } } : null,
        protocol: this.preparation.artifacts.protocol ? { ...this.preparation.artifacts.protocol, payload: { ...this.preparation.artifacts.protocol.payload } } : null,
        evidencePlan: this.preparation.artifacts.evidencePlan ? { ...this.preparation.artifacts.evidencePlan, payload: { ...this.preparation.artifacts.evidencePlan.payload } } : null,
      },
      authorizedEvidencePlan: this.preparation.authorizedEvidencePlan ? { ...this.preparation.authorizedEvidencePlan } : null,
    };
  }

  async listEventResearch(_status?: EventLifecycleStatus): Promise<EventResearchListItem[]> {
    this.throwIfOffline();
    const events = this.eventResearchItems().map((event) => {
      const state = this.eventStates.get(event.id);
      return state
        ? {
            ...event,
            status: state.lifecycle.status,
            statusSummary: state.lifecycle.summary,
            nextHumanAction: state.lifecycle.nextHumanAction,
          }
        : event;
    });
    return simulateLatency(_status ? events.filter((event) => event.status === _status) : events);
  }

  private eventResearchItems(): EventResearchListItem[] {
    const tsmLifecycle = this.eventTsmProjection().lifecycle;
    return [
      { id: "event-alphabet", workflowMode: "reviewed", eventTitle: "Alphabet 财报超预期后股价下跌", companyName: "Alphabet", ticker: "GOOGL", eventAt: "2026-08-07T00:00:00Z", status: "researching", statusSummary: "正在核验资本开支是否足以解释盘后跌幅", nextHumanAction: null, updatedAt: "2026-08-07T10:30:00Z" },
      { id: "event-tsm", workflowMode: "reviewed", eventTitle: "台积电上调 CoWoS 指引后下跌", companyName: "台积电", ticker: "TSM", eventAt: "2026-08-06T00:00:00Z", status: tsmLifecycle.status, statusSummary: tsmLifecycle.summary, nextHumanAction: tsmLifecycle.nextHumanAction, updatedAt: "2026-08-07T09:00:00Z" },
      { id: "event-cannot-conclude", workflowMode: "reviewed", eventTitle: "公司上调投入指引后下跌", companyName: "样例公司", ticker: null, eventAt: "2026-08-05T00:00:00Z", status: "exhausted", statusSummary: "当前证据不足以区分主要解释", nextHumanAction: null, updatedAt: "2026-08-07T08:30:00Z" },
      { id: "event-exhausted", workflowMode: "reviewed", eventTitle: "行业指引调整后的价格反应", companyName: null, ticker: null, eventAt: "2026-08-04T00:00:00Z", status: "exhausted", statusSummary: "当前范围已穷尽，建议调整因素", nextHumanAction: null, updatedAt: "2026-08-07T08:00:00Z" },
      { id: "event-draft", workflowMode: "reviewed", eventTitle: "季度业绩发布后的波动", companyName: null, ticker: null, eventAt: "2026-08-03T00:00:00Z", status: "draft_ready", statusSummary: "关键证据已审核，等待结论复核", nextHumanAction: "审核结论草案", updatedAt: "2026-08-07T07:30:00Z" },
      { id: "event-published", workflowMode: "reviewed", eventTitle: "经营数据披露后的变动", companyName: null, ticker: null, eventAt: "2026-08-02T00:00:00Z", status: "published", statusSummary: "结论已发布", nextHumanAction: null, updatedAt: "2026-08-07T07:00:00Z" },
      ...[...this.eventStates.values()].flatMap((state) => state.event ? [state.event] : []),
    ];
  }

  async getEventWorkbench(caseId: string): Promise<EventWorkbench> {
    const baseEvent = this.eventResearchItems().find((item) => item.id === caseId)
      ?? this.eventResearchItems()[0];
    const saved = this.eventStates.get(baseEvent.id);
    const tsmProjection = caseId === "event-tsm" ? this.eventTsmProjection() : null;
    const publishedConclusion = caseId === "event-tsm"
      ? this.eventTsmPublishedConclusion()
      : this.eventConclusionVersions.get(caseId)?.find(
          (version) => version.state === "published",
        ) ?? null;
    const tsmProjectionOwnsLifecycle = tsmProjection !== null
      && (!saved || saved.lifecycleSource === "tsm_review" || saved.lifecycleSource === "tsm_publication");
    const currentGap = baseEvent.status === "exhausted"
      ? "缺少能区分主要解释的反证" : null;
    const lifecycle: EventLifecycle = saved?.lifecycle ?? tsmProjection?.lifecycle ?? {
      status: baseEvent.status,
      activeRunId: baseEvent.status === "published" ? null : "run-mock",
      currentRound: 1,
      summary: baseEvent.statusSummary,
      currentGap,
      nextHumanAction: baseEvent.nextHumanAction,
    };
    const event: EventResearchListItem = saved
      ? {
          ...baseEvent,
          status: lifecycle.status,
          statusSummary: lifecycle.summary,
          nextHumanAction: lifecycle.nextHumanAction,
        }
      : baseEvent;
    const factorStatements = ["资本开支 / 自由现金流担忧", "盈利预期变化", "估值与市场环境"];
    const activeFactors = saved?.scope.factors ?? factorStatements.map((statement) => ({ statement, description: null }));
    const evidence = caseId === "event-tsm" ? [{ caseId, factorStatement: activeFactors[0]?.statement ?? factorStatements[0], role: "supports", reviewState: this.eventTsmReviewDecision?.outcome === "confirmed" ? "reviewed" : "machine_generated", sourceTitle: "公司季度财报与电话会", sourceUrl: "https://investor.tsmc.com/english/quarterly-results/2026/q2", documentVersionId: "doc-event-tsm-q2", sourceVisibleInCase: true, excerpt: "公司上调全年资本开支指引，同时市场关注自由现金流承压。", locator: { page: 12, section: "资本开支" }, availableAt: "2026-08-07T09:00:00Z" }] : [];
    const conclusionCitations = publishedConclusion && caseId === "event-tsm"
      ? evidence.map((citation) => ({
          ...citation,
          factorStatement: publishedConclusion.primaryFactor ?? citation.factorStatement,
        }))
      : evidence;
    const reviewedCount = tsmProjection?.verified ?? (["draft_ready", "published"].includes(event.status) ? 3 : 0);
    const nextAction: EventWorkbench["nextAction"] = publishedConclusion
      ? { kind: "wait", label: "当前没有需要处理的任务" }
      : tsmProjectionOwnsLifecycle ? tsmProjection.nextAction : (event.status === "awaiting_key_review"
      ? lifecycle.activeRunId === null && lifecycle.nextHumanAction === "核验原文资料并完成研究协议"
        ? { kind: "review_intake", label: lifecycle.nextHumanAction }
        : { kind: "review_evidence", label: event.nextHumanAction || "审核关键证据", count: 2 }
      : event.status === "draft_ready"
        ? { kind: "review_conclusion", label: "审核结论草案" }
        : event.status === "published"
          ? { kind: "view_conclusion_change", label: "查看结论变更" }
          : ["awaiting_scope", "exhausted"].includes(event.status)
            ? { kind: "edit_factors", label: "编辑并继续自动研究" }
            : { kind: "wait", label: "系统继续处理" });
    return simulateLatency({
      event, lifecycle,
      conclusion: publishedConclusion
        ? { state: "published", text: publishedConclusion.text, confidence: "high", citations: conclusionCitations }
        : tsmProjection?.conclusionState === "ai_draft"
        ? { state: "ai_draft", text: "当前结论草案等待人工复核。", confidence: "medium", citations: [] }
        : event.status === "published"
        ? { state: "published", text: "人工确认：当前材料不足以断定唯一原因。", confidence: "high", citations: [] }
        : event.status === "draft_ready"
          ? { state: "ai_draft", text: "当前结论草案等待人工复核。", confidence: "medium", citations: [] }
          : { state: "cannot_conclude", text: "尚不能下结论：系统正在核验不同解释及其反证。", confidence: "low", citations: [] },
      factors: activeFactors.map((factor, index) => { const pendingProposalCount = tsmProjection && index === 0 ? tsmProjection.pending : 0; const reviewedSupportCount = tsmProjection ? index === 0 ? tsmProjection.verified : 0 : reviewedCount ? 1 : 0; return { thesisId: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`, statement: factor.statement, description: factor.description, position: index + 1, reviewedSupportCount, reviewedContradictionCount: 0, pendingProposalCount, currentGap: tsmProjectionOwnsLifecycle && index === 0 && lifecycle.currentGap ? lifecycle.currentGap : pendingProposalCount ? "有关键证据待审核" : reviewedSupportCount ? null : "尚缺少可采纳证据" }; }),
      evidence,
      progress: { verified: reviewedCount, pending: tsmProjection?.pending ?? 0, invalidSource: caseId === "event-tsm" ? 1 : 0, currentGap: lifecycle.currentGap },
      scope: saved?.scope ?? { version: 1, factors: activeFactors, unmappedEvidenceCount: 0 },
      nextAction,
      preparation: caseId === this.preparation.caseId ? {
        status: this.preparation.status,
        revision: this.preparation.revision,
        researchRunId: this.preparation.researchRunId,
        nextAttemptAt: this.preparation.nextAttemptAt,
        lastErrorMessage: this.preparation.lastErrorMessage,
        system: {
          candidateClaims: { state: this.preparation.system.candidateClaims.state },
          protocol: { state: this.preparation.system.protocol.state },
          evidencePlan: { state: this.preparation.system.evidencePlan.state },
        },
        review: {
          candidateClaims: { state: this.preparation.review.candidateClaims.state },
          protocol: { state: this.preparation.review.protocol.state },
          evidencePlan: { state: this.preparation.review.evidencePlan.state },
        },
      } : null,
    });
  }

  async getEventConclusionHistory(caseId: string): Promise<import("../domain/eventResearch").EventConclusionVersion[]> {
    this.throwIfOffline();
    if (caseId === "event-tsm") {
      return simulateLatency(
        this.eventTsmConclusionVersions.map((version) => ({ ...version })),
      );
    }
    const storedVersions = this.eventConclusionVersions.get(caseId);
    if (storedVersions) {
      return simulateLatency(storedVersions.map((version) => ({ ...version })));
    }
    const isPublished = (await this.getEventWorkbench(caseId)).conclusion.state === "published";
    return simulateLatency(isPublished ? [
      { id: `draft-${caseId}-v1`, sequence: 1, state: "ai_draft" as const, text: "当前结论草案等待人工复核。", primaryFactor: "资本开支 / 自由现金流担忧", scopeVersion: 1, basedOnConclusionId: null, reviewer: null, evidenceCount: 3, createdAt: "2026-08-07T07:00:00Z" },
      { id: `published-${caseId}-v1`, sequence: 2, state: "published" as const, text: "人工确认：当前材料不足以断定唯一原因。", primaryFactor: "资本开支 / 自由现金流担忧", scopeVersion: 1, basedOnConclusionId: `draft-${caseId}-v1`, reviewer: "human:researcher", evidenceCount: 3, createdAt: "2026-08-07T08:00:00Z" },
    ] : []);
  }

  async continueEventResearch(input: { caseId: string; documentVersionId: string; reason: string; triggeredBy: string }): Promise<import("../domain/eventResearch").EventResearchContinuation> {
    this.throwIfOffline();
    if (!input.reason.trim()) throw new Error("continuation reason is required");
    const document = this.createdDocuments.get(input.documentVersionId)?.document
      ?? DOCUMENTS.find((item) => item.id === input.documentVersionId);
    const contract = document?.source_contract;
    if (
      contract?.status !== "admitted"
      || !contract.permissions.display
      || !contract.permissions.ai_processing
    ) {
      throw new Error("continuation document source contract does not permit research");
    }
    const runId = `run-continuation-${input.caseId}`;
    const lifecycle: EventLifecycle = { status: "researching", activeRunId: runId, currentRound: 0, summary: "已记录新材料触发原因，开始新的受控补证周期", currentGap: "新材料尚未经过原文与证据审核；此前发布结论保持不变", nextHumanAction: null };
    const previous = this.eventStates.get(input.caseId);
    this.eventStates.set(input.caseId, { event: previous?.event, scope: previous?.scope ?? { version: 1, factors: ["资本开支 / 自由现金流担忧", "盈利预期变化", "估值与市场环境"].map((statement) => ({ statement, description: null })), unmappedEvidenceCount: 0 }, lifecycle });
    void input.documentVersionId; void input.triggeredBy;
    return simulateLatency({ runId, lifecycle });
  }

  async decidePublishedMaterial(input: { caseId: string; rawInput: string; sourceUrl?: string; sourceType: EventSourceType; sourceMetadata: Record<string, unknown>; decision: "reopen" | "no_change"; reason: string; actor: string }): Promise<import("../domain/eventResearch").PublishedMaterialDecision> {
    this.throwIfOffline();
    if (!input.rawInput.trim() || !input.reason.trim()) throw new Error("material and decision reason are required");
    const sequence = this.createdDocuments.size + 1;
    const documentVersionId = `document-published-material-${input.caseId}-${sequence}`;
    const userControlled = input.sourceType === "pasted_snapshot" || input.sourceType === "uploaded_file";
    const permissions = input.sourceMetadata.permissions && typeof input.sourceMetadata.permissions === "object"
      ? input.sourceMetadata.permissions as Record<string, unknown>
      : {};
    this.createdDocuments.set(documentVersionId, {
      document: {
        id: documentVersionId,
        source_url: input.sourceUrl ?? null,
        title: typeof input.sourceMetadata.file_name === "string" ? input.sourceMetadata.file_name : "新增待比较材料",
        publisher: typeof input.sourceMetadata.provider_name === "string" ? input.sourceMetadata.provider_name : input.actor,
        document_type: input.sourceType,
        publish_date: null,
        available_at: "2026-08-09T12:00:00Z",
        acquired_at: "2026-08-09T12:00:00Z",
        parser_version: input.sourceType === "uploaded_file" ? "uploaded-text-v1" : input.sourceType === "licensed_provider" ? "provider-snapshot-v1" : input.sourceType === "public_url" ? "user-pasted-public-url-v1" : "user-pasted-v1",
        source_authority: typeof input.sourceMetadata.authority_level === "string" ? input.sourceMetadata.authority_level : "unknown",
        parse_quality: "partial",
        linked_cases: [{ id: input.caseId, title: (await this.getEventWorkbench(input.caseId)).event.eventTitle }],
        span_count: 1,
        statement_count: 0,
        version_label: "v1 · 2026-08-09",
        source_contract: {
          source_type: input.sourceType,
          provider_or_tenant: typeof input.sourceMetadata.provider_name === "string" ? input.sourceMetadata.provider_name : input.actor,
          permissions: {
            ai_processing: typeof permissions.ai_processing === "boolean" ? permissions.ai_processing : userControlled,
            display: typeof permissions.display === "boolean" ? permissions.display : userControlled,
            export: typeof permissions.export === "boolean" ? permissions.export : false,
            api: typeof permissions.api === "boolean" ? permissions.api : false,
          },
          status: mockSourceContractStatus(
            input.sourceMetadata,
            typeof permissions.ai_processing === "boolean" ? permissions.ai_processing : userControlled,
            typeof permissions.display === "boolean" ? permissions.display : userControlled,
          ),
          region: typeof input.sourceMetadata.region === "string" ? input.sourceMetadata.region : "not_recorded",
          effective_from: typeof input.sourceMetadata.effective_from === "string" ? input.sourceMetadata.effective_from : null,
          effective_until: typeof input.sourceMetadata.effective_until === "string" ? input.sourceMetadata.effective_until : null,
          retention_policy: typeof input.sourceMetadata.retention_policy === "string" ? input.sourceMetadata.retention_policy : "case_retained",
          deletion_policy: typeof input.sourceMetadata.deletion_policy === "string" ? input.sourceMetadata.deletion_policy : "not_recorded",
          downstream_restrictions: Array.isArray(input.sourceMetadata.downstream_restrictions) ? input.sourceMetadata.downstream_restrictions.filter((value): value is string => typeof value === "string") : userControlled ? ["仅限当前 Case 研究与人工审核"] : ["权限未完整记录；不得作为正式证据"],
          contract_version: typeof input.sourceMetadata.contract_version === "string" ? input.sourceMetadata.contract_version : null,
          provider_record: input.sourceType === "licensed_provider" && typeof input.sourceMetadata.provider_name === "string" && typeof input.sourceMetadata.provider_record_id === "string" ? {
            provider_name: input.sourceMetadata.provider_name,
            provider_record_id: input.sourceMetadata.provider_record_id,
            request_scope: input.sourceMetadata.request_scope && typeof input.sourceMetadata.request_scope === "object" ? input.sourceMetadata.request_scope as Record<string, unknown> : {},
            retrieval_reference: typeof input.sourceMetadata.retrieval_reference === "string" ? input.sourceMetadata.retrieval_reference : null,
            content_sha256: `mock-provider-response-${documentVersionId}`,
            retrieved_at: "2026-08-09T12:00:00Z",
            contract_version: typeof input.sourceMetadata.contract_version === "string" ? input.sourceMetadata.contract_version : null,
          } : null,
        },
      },
      spans: [{ id: `span-published-material-${sequence}`, document_id: documentVersionId, locator: { kind: input.sourceType, source_metadata: input.sourceMetadata, intake: "published_material" }, verbatim_text: input.rawInput, cited_by: [] }],
    });
    if (input.decision === "reopen") {
      const next = await this.continueEventResearch({ caseId: input.caseId, documentVersionId, reason: input.reason, triggeredBy: input.actor });
      return { documentVersionId, decision: "reopen", decisionEventId: `decision-${input.caseId}`, runId: next.runId, recoveryRequired: false, lifecycle: next.lifecycle };
    }
    const current = (await this.getEventWorkbench(input.caseId)).lifecycle;
    void input.sourceUrl;
    return simulateLatency({ documentVersionId, decision: "no_change", decisionEventId: `decision-${input.caseId}`, runId: null, recoveryRequired: false, lifecycle: current });
  }

  async decidePublishedUploadedMaterial(input: { caseId: string; file: File; sourceMetadata: Record<string, unknown>; decision: "reopen" | "no_change"; reason: string; actor: string }): Promise<import("../domain/eventResearch").PublishedMaterialDecision> {
    this.throwIfOffline();
    const isPdf = input.file.type === "application/pdf";
    const rawInput = isPdf
      ? "[PDF 原件已冻结；浏览器未解析正文，等待服务端解析结果]"
      : await input.file.text();
    const recoveryRequired = isPdf && input.decision === "reopen";
    const result = await this.decidePublishedMaterial({
      caseId: input.caseId,
      rawInput,
      sourceUrl: `upload://mock-published-${input.file.name}`,
      sourceType: "uploaded_file",
      sourceMetadata: {
        ...input.sourceMetadata,
        file_name: input.file.name,
        mime_type: input.file.type || "text/plain",
        byte_size: input.file.size,
      },
      decision: recoveryRequired ? "no_change" : input.decision,
      reason: input.reason,
      actor: input.actor,
    });
    const created = this.createdDocuments.get(result.documentVersionId);
    if (created) {
      created.document.original_file = {
        file_name: input.file.name,
        mime_type: input.file.type || "text/plain",
        byte_size: input.file.size,
        object_version: `sha256:mock-${result.documentVersionId}`,
        uploaded_by: input.actor,
        retention_policy: typeof input.sourceMetadata.retention_policy === "string"
          ? input.sourceMetadata.retention_policy
          : "case_retained",
      };
      if (isPdf) {
        created.document.parser_version = "pypdf-v1";
        created.document.parse_quality = "failed";
        created.document.span_count = 0;
        created.spans = [];
      }
    }
    return {
      ...result,
      decision: input.decision,
      recoveryRequired,
    };
  }

  async updateEventResearchScope(input: { caseId: string; factors: EventResearchScopeFactorInput[]; changedBy: string; changeReason: string }): Promise<{ version: number; factors: EventResearchScopeFactor[]; reclassifiedEvidenceCount: number; unmappedEvidenceCount: number }> {
    this.throwIfOffline();
    const event = this.eventResearchItems().find((item) => item.id === input.caseId)
      ?? this.eventResearchItems()[0];
    const previous = this.eventStates.get(event.id);
    const currentGap = previous?.lifecycle.currentGap
      ?? (event.status === "exhausted" ? "缺少能区分主要解释的反证" : null);
    const scope = {
      version: (previous?.scope.version ?? 1) + 1,
      factors: input.factors.map((factor) => typeof factor === "string" ? { statement: factor, description: null } : { ...factor }),
      unmappedEvidenceCount: 0,
    };
    this.eventStates.set(event.id, {
      event: previous?.event,
      scope,
      lifecycle: {
        status: "continuing",
        activeRunId: "run-mock",
        currentRound: previous?.lifecycle.currentRound ?? 1,
        summary: "研究范围已更新，系统继续自动研究",
        currentGap,
        nextHumanAction: null,
      },
    });
    this.advanceEventMutationRevision(event.id);
    void input.changedBy; void input.changeReason;
    return simulateLatency({
      version: scope.version,
      factors: scope.factors,
      reclassifiedEvidenceCount: 0,
      unmappedEvidenceCount: 0,
    });
  }

  async getEventReviewQueue(caseId: string): Promise<EventReviewQueue> {
    this.throwIfOffline();
    const isTsm = caseId === "event-tsm";
    const projection = isTsm ? this.eventTsmProjection() : null;
    const hasPending = projection?.pending === 1;
    const items: EventReviewQueue["items"] = isTsm ? [
      ...(hasPending ? [{
        proposalId: "proposal-event-tsm", proposalVersion: 1, status: "pending", proposedAt: "2026-08-07T09:00:00Z", linkId: "link-event-tsm", thesisId: "thesis-event-tsm", caseId,
        thesisStatement: "资本开支 / 自由现金流担忧", aiRole: "supports", aiReason: "自由现金流承压", aiScope: { period: "2026Q2" },
        statementId: "statement-event-tsm", statementText: "资本开支指引上调", statementKind: "management_attribution", spanId: "span-event-tsm", verbatimText: "全年资本开支预计上调。", locator: { page: 12 },
        documentVersionId: "document-event-tsm", documentSourceUrl: "https://investor.tsmc.com/english/quarterly-results/2026/q2", documentPublishedAt: "2026-08-07T00:00:00Z", availableAt: "2026-08-07T09:00:00Z",
        sourceTitle: "台积电季度财报与电话会", sourceStatus: "accessible" as const, sourceStatusReason: "公司投资者关系页面可验证且已冻结", canAccept: true, proposalReason: "自由现金流承压", position: 1,
      }] : []),
      {
        proposalId: "proposal-event-tsm-pasted", proposalVersion: 1, status: "pending", proposedAt: "2026-08-07T09:00:30Z", linkId: "link-event-tsm-pasted", thesisId: "thesis-event-tsm", caseId,
        thesisStatement: "资本开支 / 自由现金流担忧", aiRole: "contextualizes", aiReason: "来源尚未完成内容验证", aiScope: {}, statementId: null, statementText: null, statementKind: null, spanId: null, verbatimText: null, locator: {},
        documentVersionId: null, documentSourceUrl: "https://www.reuters.com/technology/tsmc", documentPublishedAt: null, availableAt: null, sourceTitle: "用户粘贴的市场报道", sourceStatus: "pasted_unverified" as const, sourceStatusReason: "来源由用户粘贴解析，尚未完成内容验证", canAccept: false, proposalReason: "来源尚未完成内容验证", position: null,
      },
      {
        proposalId: "proposal-event-tsm-invalid", proposalVersion: 1, status: "pending", proposedAt: "2026-08-07T09:01:00Z", linkId: "link-event-tsm-invalid", thesisId: "thesis-event-tsm", caseId,
        thesisStatement: "资本开支 / 自由现金流担忧", aiRole: "supports", aiReason: "来源不可验证", aiScope: {}, statementId: null, statementText: null, statementKind: null, spanId: null, verbatimText: null, locator: {},
        documentVersionId: null, documentSourceUrl: "https://unverified-source.invalid/evidence", documentPublishedAt: null, availableAt: null, sourceTitle: "未验证测试来源", sourceStatus: "invalid" as const, sourceStatusReason: "测试域名不能作为正式证据来源", canAccept: false, proposalReason: "来源不可验证", position: null,
      },
    ] : [];
    return simulateLatency({
      summary: {
        total: isTsm ? 3 : 0,
        reviewed: isTsm && !hasPending ? 1 : 0,
        pending: projection?.pending ?? 0,
        invalidSource: isTsm ? 1 : 0,
        currentRound: projection?.lifecycle.currentRound ?? 0,
        nextAction: hasPending ? "审核 1 条关键证据" : null,
      },
      items,
    });
  }

  async publishEventConclusion(input: { caseId: string; text: string; reviewer: string }): Promise<{ conclusionId: string; state: "published" }> {
    this.throwIfOffline();
    if (input.caseId === "event-tsm") {
      if (this.eventTsmReviewDecision?.outcome !== "confirmed") {
        throw new Error("confirmed evidence review is required before publication");
      }
      const currentLifecycle = this.eventStates.get(input.caseId)?.lifecycle
        ?? this.eventTsmProjection().lifecycle;
      const draft = this.eventTsmConclusionVersions.find(
        (version) => version.state === "ai_draft",
      );
      const draftConsumed = draft
        ? this.eventTsmConclusionVersions.some(
            (version) => version.basedOnConclusionId === draft.id,
          )
        : true;
      if (currentLifecycle.status !== "draft_ready" || !draft || draftConsumed) {
        throw new Error("unconsumed draft_ready conclusion is required before publication");
      }
      const text = input.text.trim();
      if (!text) {
        throw new Error("conclusion text is required");
      }

      const published: EventConclusionVersion = {
        id: "published-event-tsm-v1",
        sequence: 2,
        state: "published",
        text,
        primaryFactor: draft.primaryFactor,
        scopeVersion: draft.scopeVersion,
        basedOnConclusionId: draft.id,
        reviewer: input.reviewer,
        evidenceCount: draft.evidenceCount,
        createdAt: "2026-08-07T10:00:00Z",
      };
      this.eventTsmConclusionVersions = [
        ...this.eventTsmConclusionVersions,
        published,
      ];
      const previous = this.eventStates.get(input.caseId);
      if (previous) {
        this.eventStates.set(input.caseId, {
          ...previous,
          lifecycle: this.eventTsmProjection().lifecycle,
          lifecycleSource: "tsm_publication",
        });
      }
      return simulateLatency({ conclusionId: published.id, state: "published" });
    }
    const text = input.text.trim();
    if (!text) {
      throw new Error("conclusion text is required");
    }
    if (this.eventConclusionVersions.has(input.caseId)) {
      throw new Error("unconsumed draft_ready conclusion is required before publication");
    }
    if (this.eventConclusionPublicationsInFlight.has(input.caseId)) {
      throw new Error("unconsumed draft_ready conclusion is required before publication");
    }
    this.eventConclusionPublicationsInFlight.add(input.caseId);
    const startingRevision = this.eventMutationRevision(input.caseId);
    try {
      const workbench = await this.getEventWorkbench(input.caseId);
      if (
        workbench.lifecycle.status !== "draft_ready"
        || workbench.nextAction.kind !== "review_conclusion"
        || workbench.conclusion.state !== "ai_draft"
      ) {
        throw new Error("unconsumed draft_ready conclusion is required before publication");
      }
      if (
        this.eventMutationRevision(input.caseId) !== startingRevision
        || this.eventConclusionVersions.has(input.caseId)
      ) {
        throw new Error("event research state changed during publication");
      }
      const draft: EventConclusionVersion = {
        id: `draft-${input.caseId}-v1`,
        sequence: 1,
        state: "ai_draft",
        text: workbench.conclusion.text,
        primaryFactor: workbench.scope.factors[0]?.statement ?? null,
        scopeVersion: workbench.scope.version,
        basedOnConclusionId: null,
        reviewer: null,
        evidenceCount: workbench.progress.verified,
        createdAt: "2026-08-07T07:00:00Z",
      };
      const published: EventConclusionVersion = {
        id: `published-${input.caseId}-v1`,
        sequence: 2,
        state: "published",
        text,
        primaryFactor: draft.primaryFactor,
        scopeVersion: draft.scopeVersion,
        basedOnConclusionId: draft.id,
        reviewer: input.reviewer,
        evidenceCount: draft.evidenceCount,
        createdAt: "2026-08-07T08:00:00Z",
      };
      this.eventConclusionVersions.set(input.caseId, [draft, published]);
      const previous = this.eventStates.get(input.caseId);
      this.eventStates.set(input.caseId, {
        event: previous?.event,
        scope: previous?.scope ?? workbench.scope,
        lifecycle: {
          status: "published",
          activeRunId: null,
          currentRound: workbench.lifecycle.currentRound,
          summary: "结论已由研究员发布，进入持续跟踪",
          currentGap: null,
          nextHumanAction: null,
        },
      });
      this.advanceEventMutationRevision(input.caseId);
      return await simulateLatency({ conclusionId: published.id, state: "published" });
    } finally {
      this.eventConclusionPublicationsInFlight.delete(input.caseId);
    }
  }

  async getConclusionView(
    caseId: string,
    opts?: { cutoff?: string },
  ): Promise<ConclusionView> {
    this.throwIfOffline();
    const base = buildConclusionView(caseId);
    if (opts?.cutoff) return simulateLatency({ ...base, basis: { ...base.basis, cutoff: opts.cutoff, isHistorical: true } });
    return simulateLatency(base);
  }
}

// ── 结论页 mock 数据 ─────────────────────────────────────────────────
function buildConclusionView(caseId: string): ConclusionView {
  return {
    basis: {
      cutoff: "2025-06-30T22:40:00+08:00",
      isHistorical: true,
      ledgerHighWatermark: null,
      projectionBuiltAt: null,
      projectionSchemaVersion: null,
    },
    header: {
      researchCaseId: caseId,
      caseTitle: "AI 算力链",
      industryTopic: "AI 算力链 · 深度研究",
      evidenceCutoff: "2025-06-30",
      conclusionText:
        "结论优先，底图回到因素，因果边、支持与反驳，正反判断只由冻结输入与人工复核产生。",
      conclusionStatus: "insufficient_evidence",
      rationale: "证据不足，继续验证：订单与交付披露。",
      reviewState: "reviewed",
      reviewer: "已人工复核",
      reviewedAt: "2025-06-30T22:40:00+08:00",
      snapshotId: "JD-2025-06-30-v3",
      aiProvisional: false,
    },
    keyFactors: [
      {
        factorId: "F-1-01",
        thesisId: "th-1",
        thesisTitle: "云厂商资本开支",
        thesisStatement: "2026年云厂商资本开支高增长将持续驱动AI算力需求扩张",
        statusLabel: "已复现",
        roleLabel: "已复现",
        factorLabel: "云厂商资本开支",
        timeOrder:
          "因素定义 (2025-06-30) → 因素依赖 (2025-05-28) → 结论判定 (2025-06-30)",
        mechanism: "多源云厂商指引支撑需求扩张",
        directEvidence: "Microsoft / NVIDIA / Blackwell 交付与同步披露",
        alternatives: "无",
        differenceExplanation: "无显著反证",
        scopeWarning: null,
        falsifier: "资本开支指引回落同向降至两位数以下",
      },
      {
        factorId: "F-1-02",
        thesisId: "th-2",
        thesisTitle: "订单与交付",
        thesisStatement: "云厂商算力采购将沿供应链向代工/ODM端传导",
        statusLabel: "待人工",
        roleLabel: "待人工",
        factorLabel: "订单与交付",
        timeOrder:
          "因素定义 (2025-06-30) → 因素依赖 (2025-06-15) → 结论判定 (2025-06-30)",
        mechanism: "代工厂财务披露与管理层访谈",
        directEvidence: "富士康 AI 服务器订单能见度",
        alternatives: "代工毛利率偏低、季度波动",
        differenceExplanation: "（待人工补充）",
        scopeWarning: null,
        falsifier: "毛利率持续下降并带动交付延期",
      },
      {
        factorId: "F-1-03",
        thesisId: "th-3",
        thesisTitle: "数据中心电力与并网周期",
        thesisStatement: "数据中心电力与并网周期约束AI算力扩张",
        statusLabel: "待证据",
        roleLabel: "待证据",
        factorLabel: "数据中心电力与并网周期",
        timeOrder:
          "因素定义 (2025-06-30) → 因素依赖 (—) → 结论判定 (2025-06-30)",
        mechanism: "暂无已审核来源直接连接并网与收入",
        directEvidence: "（暂无直接证据）",
        alternatives: "（暂无反证）",
        differenceExplanation: "（暂无分歧）",
        scopeWarning: null,
        falsifier: "电力许可被否",
      },
    ],
    comparison: {
      columns: ["评审维度", "直接证据", "佐证证据", "范围警示", "替代解释", "评审角色", "限制因素"],
      rows: [
        {
          factorId: "F-1-01",
          factorLabel: "云厂商资本开支",
          cells: [
            { factorId: "F-1-01", factorLabel: "云厂商资本开支", columnId: "factor_dimension", columnLabel: "评审维度", text: "云厂商CapEx" },
            { factorId: "F-1-01", factorLabel: "云厂商资本开支", columnId: "direct_evidence", columnLabel: "直接证据", text: "多家云厂商指引" },
            { factorId: "F-1-01", factorLabel: "云厂商资本开支", columnId: "backing_evidence", columnLabel: "佐证证据", text: "订单与交付披露" },
            { factorId: "F-1-01", factorLabel: "云厂商资本开支", columnId: "scope_warning", columnLabel: "范围警示", text: "—" },
            { factorId: "F-1-01", factorLabel: "云厂商资本开支", columnId: "alternative", columnLabel: "替代解释", text: "外部约束" },
            { factorId: "F-1-01", factorLabel: "云厂商资本开支", columnId: "reviewer_role", columnLabel: "评审角色", text: "已复现" },
            { factorId: "F-1-01", factorLabel: "云厂商资本开支", columnId: "gate_result", columnLabel: "限制因素", text: "—" },
          ],
        },
        {
          factorId: "F-1-02",
          factorLabel: "订单与交付",
          cells: [
            { factorId: "F-1-02", factorLabel: "订单与交付", columnId: "factor_dimension", columnLabel: "评审维度", text: "代工ODM" },
            { factorId: "F-1-02", factorLabel: "订单与交付", columnId: "direct_evidence", columnLabel: "直接证据", text: "投资项目材料不足" },
            { factorId: "F-1-02", factorLabel: "订单与交付", columnId: "backing_evidence", columnLabel: "佐证证据", text: "管理层访谈" },
            { factorId: "F-1-02", factorLabel: "订单与交付", columnId: "scope_warning", columnLabel: "范围警示", text: "—" },
            { factorId: "F-1-02", factorLabel: "订单与交付", columnId: "alternative", columnLabel: "替代解释", text: "毛利率偏低" },
            { factorId: "F-1-02", factorLabel: "订单与交付", columnId: "reviewer_role", columnLabel: "评审角色", text: "待人工" },
            { factorId: "F-1-02", factorLabel: "订单与交付", columnId: "gate_result", columnLabel: "限制因素", text: "—" },
          ],
        },
        {
          factorId: "F-1-03",
          factorLabel: "数据中心电力与并网周期",
          cells: [
            { factorId: "F-1-03", factorLabel: "数据中心电力与并网周期", columnId: "factor_dimension", columnLabel: "评审维度", text: "数据中心" },
            { factorId: "F-1-03", factorLabel: "数据中心电力与并网周期", columnId: "direct_evidence", columnLabel: "直接证据", text: "并网披露项目" },
            { factorId: "F-1-03", factorLabel: "数据中心电力与并网周期", columnId: "backing_evidence", columnLabel: "佐证证据", text: "电信数据" },
            { factorId: "F-1-03", factorLabel: "数据中心电力与并网周期", columnId: "scope_warning", columnLabel: "范围警示", text: "—" },
            { factorId: "F-1-03", factorLabel: "数据中心电力与并网周期", columnId: "alternative", columnLabel: "替代解释", text: "限电因素" },
            { factorId: "F-1-03", factorLabel: "数据中心电力与并网周期", columnId: "reviewer_role", columnLabel: "评审角色", text: "待证据" },
            { factorId: "F-1-03", factorLabel: "数据中心电力与并网周期", columnId: "gate_result", columnLabel: "限制因素", text: "—" },
          ],
        },
      ],
    },
    sourceGroups: [
      {
        sectionLabel: "支持 · 已复现",
        relations: [
          {
            label: "支持 · 已复现",
            relation: "supports",
            documentTitle: "Blackwell 交付与同步披露",
            publisher: "NVIDIA",
            citation: "Blackwell 交付与同步披露",
            locator: "P10-Q·P.38",
          },
        ],
      },
      {
        sectionLabel: "支持 · 已传导",
        relations: [
          {
            label: "支持 · 已传导",
            relation: "supports",
            documentTitle: "Microsoft FY2025 Q3 call",
            publisher: "Microsoft",
            citation: "需求高于可供给，收入与可见约束",
            locator: "P4-5",
          },
        ],
      },
    ],
    reproductionManifest: {
      currentSelectionLabel: "F-1-01",
      currentSelectionState: "F-1-01",
      formalJudgment: "订单与交付实际安排",
      researchSnapshot: "RS-2025-06-30-v3",
      documentVersion: "sec-10q-2025-05-28-v1",
      publisherRecord: "issuer-call-2025-04-30-v1",
      availableAt: "2025-06-30",
      reproducer: "林岚 · 2026-06-30 22:40 CST",
      factorCompareVersion: "factor-compare-v2 · evidence-role-v1",
      recheckManifest:
        "snapshot: RS-2025-06-30-v3 | inputs: 4 documents / 2 series | citations: 6 sourceSpans | output_hash: 9c72a59e",
    },
    causalPath: [
      { sequence: 1, description: "订单与交付披露" },
      { sequence: 2, description: "因素 → 交付" },
      { sequence: 3, description: "交付 → 收入" },
      { sequence: 4, description: "证据判断与证据保存" },
      { sequence: 5, description: "正式缺证据" },
    ],
    gapExplanation: {
      factorId: "F-1-02",
      factorLabel: "位于需求到收入的必要传导环节",
      why:
        "资本开支定义还需进入收入确认的必要传导环节，具有主要解释力。",
      applicableScope: "同一主体 · 同一业务口径",
      category: "适用边界",
      dataPattern:
        "订单与交付披露：2025-01-01 至 2027-12-31；云厂商、网络交换机、存储可构成口径均需拆分；任何一项拆分存在即不构成同口径对照。",
      categoryAlt: "假设",
      rationale: "收入与可信确认不予确认。",
    },
  };
}

export const mockExposures = {
  companies: COMPANIES,
  funds: FUNDS,
  valuations: VALUATIONS,
  staleValuations,
};
