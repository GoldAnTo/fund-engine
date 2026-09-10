// Deterministic fixture data for the prototype screens. Mirrors the
// `prototype/ui/data.js` fixture (RC-AIC-2025-01, snapshot RS-2025-06-30-v3,
// cutoff 2025-06-30). Single source of truth so that all prototype pages
// render against the same underlying case.

import type {
  CaseWorkbenchView,
  DataCenterView,
  LibraryView,
  NewResearchView,
  RelationshipGraphView,
  ResearchPlanView,
  ThemeIndexView,
  ThemeWorkbenchView,
  VersionsView,
  WorkspaceOverviewScreen,
  WorkspaceOverviewView,
} from "../domain/prototypeTypes";

// ── Case core ───────────────────────────────────────────────────────────

export const CASE_ID = "RC-AIC-2025-01";
export const CASE_TITLE =
  "AI 算力需求能否穿透至可验证的收入与持仓表达";
export const CASE_QUESTION =
  "截至 2025-06-30，AI 算力资本开支能否通过已披露订单、交付与收入，形成可审计且仍需持续验证的产业链判断？";
export const CUTOFF = "2025-06-30";
export const SNAPSHOT_ID = "RS-2025-06-30-v3";
export const AI_LABEL = "AI 草案 · 未经人工复核";
export const PROVISIONAL_ASSESSMENT =
  "已复核材料支持需求扩张与部分设备交付，但供给约束和收入确认节奏仍构成缺口；现阶段只形成待持续验证的研究判断。";

export const RESEARCH_PERIOD = { start: "2025-01-01", end: "2027-12-31" };

// ── Documents / statements / links ──────────────────────────────────────

export const DOCUMENTS = [
  {
    id: "DOC-MSFT-FY25Q3",
    title: "Microsoft FY2025 Q3 Form 10-Q",
    sourceName: "SEC EDGAR · Microsoft",
    sourceVersion: "sec-10q-2025-04-30-v1",
    documentType: "监管披露",
    entity: "Microsoft",
    reuseCount: 3,
    reviewState: "reviewed" as const,
    publishedAt: "2025-04-30T20:10:00Z",
    availableAt: "2025-04-30T20:18:00Z",
    acquiredAt: "2025-04-30T20:30:00Z",
    previousVersion: "sec-10q-2025-01-29-v1",
    linkedCaseIds: [CASE_ID],
    reuseHistory: [{ caseId: CASE_ID, label: "AI 算力需求到收入传导", reusedAt: "2025-05-01 09:18" }],
    sourceExcerpt:
      "资本开支继续用于支持云与 AI 基础设施...我们在本季度看到 Azure AI 收入同比增长，需求仍超过可用容量。",
    exactSpan: "pp. 35-39, capital expenditures and cloud infrastructure",
    sourceSpan: "pp. 35-39, capital expenditures and cloud infrastructure",
    spanCount: 1,
    statementCount: 1,
  },
  {
    id: "DOC-NVDA-FY26Q1",
    title: "NVIDIA FY2026 Q1 Form 10-Q",
    sourceName: "SEC EDGAR · NVIDIA",
    sourceVersion: "sec-10q-2025-05-28-v1",
    documentType: "监管披露",
    entity: "NVIDIA",
    reuseCount: 2,
    reviewState: "reviewed" as const,
    publishedAt: "2025-05-28T20:05:00Z",
    availableAt: "2025-05-28T20:13:00Z",
    acquiredAt: "2025-05-28T20:25:00Z",
    previousVersion: "sec-10q-2025-02-26-v1",
    linkedCaseIds: [CASE_ID],
    reuseHistory: [{ caseId: CASE_ID, label: "AI 算力需求到收入传导", reusedAt: "2025-05-30 11:00" }],
    sourceExcerpt:
      "数据中心收入增长与 Blackwell 系统交付同时披露...Data Center revenue grew to $39.1bn.",
    exactSpan: "pp. 22-27, Data Center revenue and supply commitments",
    sourceSpan: "pp. 22-27, Data Center revenue and supply commitments",
    spanCount: 1,
    statementCount: 1,
  },
  {
    id: "DOC-MSFT-FY25Q3-CALL",
    title: "Microsoft FY2025 Q3 业绩说明会记录",
    sourceName: "Microsoft Investor Relations",
    sourceVersion: "issuer-call-2025-04-30-v1",
    documentType: "业绩说明会",
    entity: "Microsoft",
    reuseCount: 2,
    reviewState: "reviewed" as const,
    publishedAt: "2025-04-30T21:30:00Z",
    availableAt: "2025-04-30T21:38:00Z",
    acquiredAt: "2025-04-30T21:44:00Z",
    previousVersion: "issuer-call-2025-04-30-v0 · 初始转录",
    linkedCaseIds: [CASE_ID, "RC-CLOUD-CAPACITY-2025-02"],
    reuseHistory: [
      { caseId: CASE_ID, label: "AI 算力需求到收入传导", reusedAt: "2025-05-01 09:18" },
      { caseId: "RC-CLOUD-CAPACITY-2025-02", label: "云基础设施供给约束", reusedAt: "2025-05-06 14:32" },
    ],
    sourceExcerpt:
      "Demand for our AI services remained higher than our available capacity. Capital investments will support growth as new capacity comes online.",
    exactSpan: "prepared remarks, pp. 4-5, capacity constraints and revenue timing",
    sourceSpan: "prepared remarks, pp. 4-5, capacity constraints and revenue timing",
    spanCount: 1,
    statementCount: 1,
  },
  {
    id: "DOC-TSMC-2025M05",
    title: "TSMC 2025 年 5 月月度营收",
    sourceName: "TSMC Investor Relations",
    sourceVersion: "ir-monthly-2025-06-10-v1",
    documentType: "月度经营数据",
    entity: "TSMC",
    reuseCount: 1,
    reviewState: "reviewed" as const,
    publishedAt: "2025-06-10T05:30:00Z",
    availableAt: "2025-06-10T05:35:00Z",
    acquiredAt: "2025-06-10T08:00:00Z",
    previousVersion: "ir-monthly-2025-05-10-v1",
    linkedCaseIds: [CASE_ID],
    reuseHistory: [{ caseId: CASE_ID, label: "AI 算力需求到收入传导", reusedAt: "2025-06-11 10:00" }],
    sourceExcerpt: "TSMC 2025 年 5 月净营收同比增长 34.8%，反映先进制程与封装需求持续。",
    exactSpan: "table 1, net revenue May 2025",
    sourceSpan: "table 1, net revenue May 2025",
    // 该版本已分出 1 段原文（营收表），但还没生成陈述；用于演示"待抽取"状态。
    spanCount: 1,
    statementCount: 0,
  },
  {
    id: "DOC-BRCM-FY25Q2",
    title: "Broadcom FY2025 Q2 业绩公告",
    sourceName: "Broadcom IR",
    sourceVersion: "ir-release-2025-06-05-v1",
    documentType: "业绩公告",
    entity: "Broadcom",
    reuseCount: 0,
    reviewState: "pending_review" as const,
    publishedAt: "2025-06-05T20:15:00Z",
    availableAt: "2025-06-05T20:21:00Z",
    acquiredAt: "2025-06-05T20:40:00Z",
    previousVersion: "首个归档版本 · 无前序版本",
    linkedCaseIds: [CASE_ID],
    reuseHistory: [],
    sourceExcerpt:
      "AI revenue, which came in at $4.4 billion, accelerated to 46% growth year-on-year.",
    exactSpan: "outlook paragraph 4",
    sourceSpan: "outlook paragraph 4",
    spanCount: 1,
    statementCount: 1,
  },
];

export const STATEMENTS = [
  {
    id: "ST-001",
    documentId: "DOC-MSFT-FY25Q3",
    text: "资本开支继续用于支持云与 AI 基础设施。",
    sourceVersion: "sec-10q-2025-04-30-v1",
    sourceSpan: "p. 38, paragraphs 2-3",
    publishedAt: "2025-04-30T20:10:00Z",
    availableAt: "2025-04-30T20:18:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "ST-002",
    documentId: "DOC-NVDA-FY26Q1",
    text: "数据中心收入增长与 Blackwell 系统交付同时披露。",
    sourceVersion: "sec-10q-2025-05-28-v1",
    sourceSpan: "p. 24, Data Center discussion",
    publishedAt: "2025-05-28T20:05:00Z",
    availableAt: "2025-05-28T20:13:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "ST-003",
    documentId: "DOC-BRCM-FY25Q2",
    text: "AI 相关收入展望上调，但交付与分部口径仍待人工核对。",
    sourceExcerpt:
      "AI revenue, which came in at $4.4 billion, accelerated to 46% growth year-on-year.",
    sourceVersion: "ir-release-2025-06-05-v1",
    sourceSpan: "outlook paragraph 4",
    publishedAt: "2025-06-05T20:15:00Z",
    availableAt: "2025-06-05T20:21:00Z",
    reviewState: "pending_review" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "ST-004",
    documentId: "DOC-MSFT-FY25Q3-CALL",
    text: "AI 基础设施需求高于可供容量，资本投入需待产能上线后支持收入。",
    sourceExcerpt:
      "Demand for our AI services remained higher than our available capacity. Capital investments will support growth as new capacity comes online.",
    sourceVersion: "issuer-call-2025-04-30-v1",
    sourceSpan: "prepared remarks, pp. 4-5, capacity constraints and revenue timing",
    publishedAt: "2025-04-30T21:30:00Z",
    availableAt: "2025-04-30T21:38:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
];

export const EVIDENCE_LINKS = [
  {
    id: "EL-001",
    thesisId: "TH-AIC-01",
    statementId: "ST-001",
    role: "support",
    rationale: "披露同时限定投入方向与时间。",
    sourceVersion: "sec-10q-2025-04-30-v1",
    sourceSpan: "p. 38, paragraphs 2-3",
    publishedAt: "2025-04-30T20:10:00Z",
    availableAt: "2025-04-30T20:18:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "EL-002",
    thesisId: "TH-AIC-03",
    statementId: "ST-002",
    role: "support",
    rationale:
      "订单需求与交付、分部收入具备同主体披露入口，但机制链仍需逐段审核。",
    sourceVersion: "sec-10q-2025-05-28-v1",
    sourceSpan: "p. 24, Data Center discussion",
    publishedAt: "2025-05-28T20:05:00Z",
    availableAt: "2025-05-28T20:13:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "EL-003",
    thesisId: "TH-AIC-03",
    statementId: "ST-003",
    role: "gap",
    rationale: "展望尚不能替代实际交付与收入确认记录。",
    sourceVersion: "ir-release-2025-06-05-v1",
    sourceSpan: "outlook paragraph 4",
    publishedAt: "2025-06-05T20:15:00Z",
    availableAt: "2025-06-05T20:21:00Z",
    reviewState: "pending_review" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "EL-004",
    thesisId: "TH-AIC-03",
    statementId: "ST-004",
    role: "contradict",
    rationale:
      "需求和资本投入不等同于当期可用容量或收入确认，构成对即时完整传导的反面证据。",
    sourceVersion: "issuer-call-2025-04-30-v1",
    sourceSpan: "prepared remarks, pp. 4-5, capacity constraints and revenue timing",
    publishedAt: "2025-04-30T21:30:00Z",
    availableAt: "2025-04-30T21:38:00Z",
    reviewState: "reviewed" as const,
    reviewedBy: "林岚 · 行业研究",
    reviewedAt: "2025-05-02T10:26:00+08:00",
    snapshotMembership: [SNAPSHOT_ID],
  },
];

export const METRICS = [
  {
    id: "M-NVDA-DC-REV",
    name: "Data Center revenue",
    value: "$39.1bn",
    period: "FY2026 Q1",
    sourceVersion: "sec-10q-2025-05-28-v1",
    sourceSpan: "p. 24, segment revenue table",
    publishedAt: "2025-05-28T20:05:00Z",
    availableAt: "2025-05-28T20:13:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "M-TSMC-M05-YOY",
    name: "May monthly revenue year-on-year change",
    value: "34.8%",
    period: "2025-05",
    sourceVersion: "ir-monthly-2025-06-10-v1",
    sourceSpan: "table 1, net revenue May 2025",
    publishedAt: "2025-06-10T05:30:00Z",
    availableAt: "2025-06-10T05:35:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
];

export const COMPANIES = [
  {
    id: "CO-NVDA",
    name: "NVIDIA",
    mappingRole: "compute-system-supplier",
    disclosureDate: "2025-05-28",
    sourceVersion: "sec-10q-2025-05-28-v1",
    sourceSpan: "cover and segment note",
    publishedAt: "2025-05-28T20:05:00Z",
    availableAt: "2025-05-28T20:13:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "CO-TSM",
    name: "TSMC",
    mappingRole: "foundry-and-advanced-packaging",
    disclosureDate: "2025-06-10",
    sourceVersion: "ir-monthly-2025-06-10-v1",
    sourceSpan: "issuer and revenue table",
    publishedAt: "2025-06-10T05:30:00Z",
    availableAt: "2025-06-10T05:35:00Z",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
];

export const FUNDS = [
  {
    id: "FUND-ETF-AI-INFRA",
    name: "示例算力基础设施 ETF",
    mappingRole: "holding-disclosure-only",
    companyId: "CO-NVDA",
    disclosedWeight: "8.4%",
    disclosureDate: "2025-03-31",
    sourceVersion: "fund-report-2025q1-v1",
    sourceSpan: "top ten holdings, row 2",
    publishedAt: "2025-04-21T08:00:00+08:00",
    availableAt: "2025-04-21T08:07:00+08:00",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
    boundary: "持仓披露映射，不构成投资建议。",
  },
  {
    id: "FUND-SEMI-INDEX",
    name: "示例半导体指数基金",
    mappingRole: "holding-disclosure-only",
    companyId: "CO-TSM",
    disclosedWeight: "6.1%",
    disclosureDate: "2025-03-31",
    sourceVersion: "fund-report-2025q1-v2",
    sourceSpan: "top ten holdings, row 5",
    publishedAt: "2025-04-22T08:00:00+08:00",
    availableAt: "2025-04-22T08:06:00+08:00",
    reviewState: "pending_review" as const,
    snapshotMembership: [SNAPSHOT_ID],
    boundary: "待核对份额类别与披露口径，不构成投资建议。",
  },
];

export const THESES = [
  {
    id: "TH-AIC-01",
    origin: "ai" as const,
    title: "云厂商资本开支形成持续算力需求",
    statement:
      "主要云厂商已披露的资本开支与 AI 基础设施投入在截止日前保持扩张。",
    supportCondition:
      "至少两家主要云厂商在正式披露中同时给出资本开支扩张与 AI 基础设施用途。",
    falsifier:
      "主要云厂商下调相关资本开支，或披露投入未转化为部署。",
    nextValidationEvent: "核对 2025 年第二季度云厂商财报与资本开支指引。",
    observationStart: "2025-01-01",
    observationEnd: "2027-12-31",
    evidenceReviewState: "reviewed_links_present" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "TH-AIC-02",
    origin: "ai" as const,
    title: "先进芯片与互连供给决定交付斜率",
    statement:
      "晶圆、先进封装与高速互连约束影响系统交付节奏，不能仅凭需求推断收入。",
    supportCondition:
      "供应商披露扩产、交期或产能利用信息，并能与交付指标交叉核对。",
    falsifier:
      "关键产能快速转为宽松且系统交付没有改善，或约束与交付时点不匹配。",
    nextValidationEvent: "等待先进封装月度营收与交换芯片交付更新。",
    observationStart: "2025-01-01",
    observationEnd: "2027-12-31",
    evidenceReviewState: "no_evidence_links" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "TH-AIC-03",
    origin: "ai" as const,
    title: "订单到收入的传导需要独立披露验证",
    statement:
      "只有经审核的订单、交付、收入确认链条，才能支持需求向公司业绩传导。",
    supportCondition:
      "同一主体的订单或积压、实际交付与分部收入均有点时披露，且口径可对齐。",
    falsifier:
      "积压订单取消、交付延迟，或相关收入增长主要来自不同业务。",
    nextValidationEvent: "复核下一期分部收入、递延收入与订单履约说明。",
    observationStart: "2025-01-01",
    observationEnd: "2027-12-31",
    evidenceReviewState: "pending_relationship_review" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
];

export const FACTORS = [
  {
    id: "F-D-01",
    group: "demand",
    label: "云厂商 AI 基础设施资本开支",
    stance: "support",
    status: "candidate",
    proposedRole: "candidate",
    role: "candidate_factor",
    timeOrder: "资本开支指引先于交付与收入披露。",
    mechanism: "预算经设备采购形成需求入口。",
    directEvidence: "微软披露支出用于云与 AI 基础设施。",
    alternatives: "常规云扩容也能解释部分支出。",
    differenceExplanation: "不能解释供应约束造成的收入差异。",
    scope: "主要云厂商，2025—2027 年。",
    falsifier: "支出下调或未转化为部署。",
    counterexample: "未观察到下调或撤回动作。",
    impactObject: "上游芯片与系统厂商订单池。",
  },
  {
    id: "F-T-01",
    group: "transmission",
    label: "订单积压到实际交付",
    stance: "support",
    status: "candidate",
    proposedRole: "transmission_factor",
    role: "transmission_factor",
    timeOrder: "订单先于交付，交付先于收入确认。",
    mechanism: "逐段连接订单、交付与分部收入。",
    directEvidence: "NVIDIA 同期披露系统交付与分部收入。",
    alternatives: "价格、产品组合或非 AI 业务增长。",
    differenceExplanation: "解释订单强但交付兑现不同的主体。",
    scope: "同一主体、业务口径与可对齐报告期。",
    falsifier: "订单取消、交付延迟或收入来自别的业务。",
    counterexample: "Broadcom 交付口径未对齐。",
    impactObject: "云厂商收入兑现节奏。",
  },
  {
    id: "F-C-01",
    group: "constraints",
    label: "数据中心电力与并网周期",
    stance: "warning",
    status: "candidate",
    proposedRole: "constraint",
    role: "constraint_factor",
    timeOrder: "并网周期先于数据中心投产。",
    mechanism: "电力可得性限制设备部署与利用率。",
    directEvidence: "暂无已审核来源直接连接并网与收入。",
    alternatives: "封装、互连供给或客户验收延后。",
    differenceExplanation: "可能解释地区间投产差异。",
    scope: "具体项目、地区与并网窗口。",
    falsifier: "电力到位而投产仍延迟。",
    counterexample: "Microsoft 业绩说明会提到容量受限。",
    impactObject: "AI 数据中心投产时点。",
  },
  {
    id: "F-X-01",
    group: "contradiction",
    label: "高投入但相关收入确认滞后",
    stance: "contradict",
    status: "candidate",
    proposedRole: "background",
    role: "background_factor",
    timeOrder: "投入增长后，相关收入未同步确认。",
    mechanism: "交付、验收滞后切断当期同步关系。",
    directEvidence: "投入扩张，但订单到收入链仍待核对。",
    alternatives: "产品组合、价格或非 AI 业务增长。",
    differenceExplanation: "识别收入确认节奏不同的主体。",
    scope: "可对齐投入、交付与收入的披露主体。",
    falsifier: "交付与同口径收入按期同步兑现。",
    counterexample: "Microsoft 业绩说明会证伪即时同步。",
    impactObject: "需求—兑现传导效率评估。",
  },
];

export const REVIEW_QUEUE = [
  {
    id: "RQ-001",
    targetId: "ST-003",
    task: "核对 AI 收入口径、对应交付期与分部归属",
    sourceVersion: "ir-release-2025-06-05-v1",
    sourceSpan: "outlook paragraph 4",
    publishedAt: "2025-06-05T20:15:00Z",
    availableAt: "2025-06-05T20:21:00Z",
    reviewState: "pending_review",
    priority: "high",
    snapshotMembership: [SNAPSHOT_ID],
    aiLabel: AI_LABEL,
  },
  {
    id: "RQ-002",
    targetId: "FUND-SEMI-INDEX",
    task: "确认基金份额类别与报告期持仓口径",
    sourceVersion: "fund-report-2025q1-v2",
    sourceSpan: "top ten holdings, row 5",
    publishedAt: "2025-04-22T08:00:00+08:00",
    availableAt: "2025-04-22T08:06:00+08:00",
    reviewState: "pending_review",
    priority: "medium",
    snapshotMembership: [SNAPSHOT_ID],
    aiLabel: AI_LABEL,
  },
];

export const SNAPSHOTS = [
  {
    id: SNAPSHOT_ID,
    cutoff: CUTOFF,
    frozenAt: "2025-06-30T23:59:59+08:00",
    label: "当前冻结快照",
    sourceVersion: "research-case-RC-AIC-2025-01-v3",
    sourceSpan: "complete citation manifest",
    publishedAt: "2025-06-30T23:59:59+08:00",
    availableAt: "2025-06-30T23:59:59+08:00",
    reviewState: "reviewed" as const,
    snapshotMembership: [SNAPSHOT_ID],
  },
  {
    id: "RS-2025-03-31-v2",
    cutoff: "2025-03-31",
    frozenAt: "2025-04-01T09:12:00+08:00",
    label: "上一季度冻结快照",
    sourceVersion: "research-case-RC-AIC-2025-01-v2",
    sourceSpan: "complete citation manifest",
    publishedAt: "2025-04-01T09:12:00+08:00",
    availableAt: "2025-04-01T09:12:00+08:00",
    reviewState: "reviewed" as const,
    snapshotMembership: ["RS-2025-03-31-v2"],
  },
  {
    id: "RS-2024-12-31-v1",
    cutoff: "2024-12-31",
    frozenAt: "2025-01-02T10:05:00+08:00",
    label: "初始冻结快照",
    sourceVersion: "research-case-RC-AIC-2025-01-v1",
    sourceSpan: "complete citation manifest",
    publishedAt: "2025-01-02T10:05:00+08:00",
    availableAt: "2025-01-02T10:05:00+08:00",
    reviewState: "reviewed" as const,
    snapshotMembership: ["RS-2024-12-31-v1"],
  },
];

export const PROVIDER_RUNS = [
  {
    id: "PR-001",
    provider: "SEC EDGAR",
    outcome: "success",
    observedAt: "2025-06-30T18:10:00+08:00",
    detail: "4 filings normalized",
  },
  {
    id: "PR-002",
    provider: "Issuer IR",
    outcome: "success",
    observedAt: "2025-06-30T18:14:00+08:00",
    detail: "3 releases preserved with source versions",
  },
  {
    id: "PR-003",
    provider: "Market data quota",
    outcome: "quota_failure",
    observedAt: "2025-06-30T18:20:00+08:00",
    detail: "Daily call limit exceeded; no inferred replacement values",
  },
  {
    id: "PR-004",
    provider: "Licensed holdings feed",
    outcome: "permission_gap",
    observedAt: "2025-06-30T18:22:00+08:00",
    detail: "Current credential lacks historical holdings permission",
  },
  {
    id: "PR-005",
    provider: "Research operations",
    outcome: "manual_upload",
    observedAt: "2025-06-30T18:35:00+08:00",
    sourceVersion: "fund-report-2025q1-v2",
    reviewQueueId: "RQ-002",
    detail: "Fund reports uploaded and queued for review",
  },
];

// ── Provider / case state labels (mirror prototype/ui/app.js) ────────────

export const PROVIDER_NAMES: Record<string, string> = {
  juyuan: "聚源",
  "SEC EDGAR": "监管披露",
  "Issuer IR": "公司投资者关系披露",
  "Market data quota": "市场数据接口",
  "Licensed holdings feed": "持仓数据接口",
  "Research operations": "研究资料补录",
};

export const PROVIDER_DETAILS: Record<string, string> = {
  "Daily call limit exceeded; no inferred replacement values": "当日调用额度已用尽，未使用推测值替代。",
  "Current credential lacks historical holdings permission": "当前凭证缺少历史持仓读取权限。",
  "Fund reports uploaded and queued for review": "基金报告曾由人工补录，并进入审核队列。",
};

export const PROVIDER_OUTCOMES: Record<string, string> = {
  quota_failure: "配额受限",
  permission_gap: "权限缺口",
  manual_upload: "人工补录",
  success: "成功",
};

export const PROVIDER_CAPABILITIES: Record<string, string> = {
  industry_analysis_view: "行业分析观点",
  announcement_filing_fulltext: "公告财报原文",
  fund_holding_detail: "基金持股明细",
};

export const METRIC_NAMES: Record<string, string> = {
  "Data Center revenue": "NVIDIA 数据中心业务收入",
  "May monthly revenue year-on-year change": "台积电月度营收同比增幅",
};

export const METRIC_VALUES: Record<string, string> = {
  "$39.1bn": "391 亿美元",
  "34.8%": "34.8%",
};

export const METRIC_PERIODS: Record<string, string> = {
  "FY2026 Q1": "2026 财年第一季度",
  "2025-05": "2025 年 5 月",
};

export const PLAN_STATES: Record<string, string> = {
  planned: "计划",
  awaiting_capability_probe: "等待能力探测",
  blocked_permission: "权限阻塞",
  reused_frozen: "已复用并冻结",
  running: "正在获取",
};

export const EXPOSURE_STATES: Record<string, string> = {
  probe_required: "尚待探测是否实际暴露并获授权",
};

export const QUERY_MODES: Record<string, string> = {
  capability_probe: "能力探测",
};

export const ASSET_KINDS: Record<string, string> = {
  document: "冻结文档",
  statement: "来源陈述",
  metric: "结果数据",
  evidence_link: "已审核关系",
};

export const REVIEW_STATES: Record<string, string> = {
  reviewed: "已人工复核",
  pending_review: "待人工审核",
};

export const GAP_TYPES: Record<string, string> = {
  factor: "因素缺口",
  positive: "正面证据检索",
  negative: "反面证据检索",
};

// ── View-model builders ────────────────────────────────────────────────

function displayLabel(map: Record<string, string>, value: string, fallback: string): string {
  return map[value] ?? fallback;
}

export function buildWorkspaceOverview(): WorkspaceOverviewView {
  const primaryItem = REVIEW_QUEUE[0];
  const statement = STATEMENTS.find((s) => s.id === primaryItem.targetId);
  const link = EVIDENCE_LINKS.find((l) => l.statementId === primaryItem.targetId);
  const thesis = link ? THESES.find((t) => t.id === link.thesisId) : undefined;
  const contradiction = FACTORS.find((f) => f.group === "contradiction")!;
  const metric = METRICS.find((m) => m.id === "M-NVDA-DC-REV")!;
  const hasPriorMetricVersion = metric.snapshotMembership.some((id) => id !== SNAPSHOT_ID);
  const recentSnapshot = SNAPSHOTS.find((s) => s.id === SNAPSHOT_ID)!;
  const providers = PROVIDER_RUNS.filter((r) =>
    ["quota_failure", "permission_gap"].includes(r.outcome),
  ).map((run) => ({
    id: run.id,
    displayName: displayLabel(PROVIDER_NAMES, run.provider, "外部数据接口"),
    outcomeLabel: displayLabel(PROVIDER_OUTCOMES, run.outcome, "运行异常"),
    detailLabel: displayLabel(PROVIDER_DETAILS, run.detail, "提供方返回未分类错误。"),
  }));

  return {
    case: {
      id: CASE_ID,
      title: CASE_TITLE,
      question: CASE_QUESTION,
      cutoff: CUTOFF,
      snapshotId: SNAPSHOT_ID,
      state: "awaiting_validation",
      stateLabel: "持续验证中",
      aiLabel: AI_LABEL,
      provisionalAssessment: PROVISIONAL_ASSESSMENT,
    },
    workItem: {
      id: primaryItem.id,
      label: thesis?.title ?? `待审核事项 ${primaryItem.id}`,
      task: primaryItem.task,
      targetId: primaryItem.targetId,
      sourceId: link?.id ?? statement?.id ?? primaryItem.targetId,
      sourceVersion: link?.sourceVersion ?? statement?.sourceVersion ?? primaryItem.sourceVersion,
      reviewStatusLabel: displayLabel(REVIEW_STATES, primaryItem.reviewState, "状态待确认"),
      actionLabel: `审核：${primaryItem.task}`,
      actionRoute: `?screen=review&item=${primaryItem.id}`,
    },
    contradiction: {
      id: contradiction.id,
      label: contradiction.label,
      stateLabel: displayLabel(
        { candidate: "候选线索" },
        contradiction.status,
        "线索状态待确认",
      ),
    },
    metric: {
      id: metric.id,
      displayName: displayLabel(METRIC_NAMES, metric.name, "关键业务指标"),
      value: displayLabel(METRIC_VALUES, metric.value, metric.value),
      period: displayLabel(METRIC_PERIODS, metric.period, metric.period),
      sourceVersion: metric.sourceVersion,
      gapLabel: hasPriorMetricVersion ? "已有跨版本口径" : "缺少前次快照对照",
    },
    providers,
    recentSnapshot: {
      id: recentSnapshot.id,
      label: recentSnapshot.label,
      cutoff: recentSnapshot.cutoff,
      frozenAt: recentSnapshot.frozenAt,
    },
  };
}

export function buildNewResearchView(): NewResearchView {
  const studyRange = `${RESEARCH_PERIOD.start} 至 ${RESEARCH_PERIOD.end}`;
  const theses = THESES.map((thesis) => ({
    id: thesis.id,
    origin: thesis.origin,
    lastEditedBy: "ai" as const,
    title: thesis.title,
    statement: thesis.statement,
    observationStart: thesis.observationStart,
    observationEnd: thesis.observationEnd,
    supportCondition: thesis.supportCondition,
    falsifier: thesis.falsifier,
    nextValidationEvent: thesis.nextValidationEvent,
  }));
  return {
    caseId: CASE_ID,
    caseTitle: CASE_TITLE,
    caseQuestion: CASE_QUESTION,
    researchObject:
      "从云厂商资本开支，经芯片、互连与系统交付，到分部收入的 AI 算力产业链",
    phenomenon:
      "AI 资本开支持续扩张，但订单、交付与收入确认的节奏出现分化",
    researchPeriod: RESEARCH_PERIOD,
    studyRange,
    cutoff: CUTOFF,
    snapshotId: SNAPSHOT_ID,
    theses,
    confirmedTheses: theses,
    activeStep: 2,
    stageStatus: "当前阶段 · 命题待人工确认",
    assets: {
      documentCount: 4,
      statementCount: 3,
      metricCount: 2,
      reviewedLinkCount: 3,
      relatedCaseIds: ["RC-AIC-2025-01"],
    },
    plan: {
      providerQueries: [
        {
          id: "PQ-JY-INDUSTRY",
          provider: "juyuan",
          providerLabel: PROVIDER_NAMES.juyuan,
          capability: "industry_analysis_view",
          capabilityLabel: PROVIDER_CAPABILITIES.industry_analysis_view,
          mode: "capability_probe",
          modeLabel: QUERY_MODES.capability_probe,
          status: "planned",
          statusLabel: PLAN_STATES.planned,
          purpose: "查找产业链供需与交付约束的可核验出处，不直接采用观点结论",
          dateScope: { start: "2025-01-01", end: "2025-06-30" },
          cutoff: CUTOFF,
          intendedArtifact: "带来源版本与原文区段的行业材料候选件",
        },
        {
          id: "PQ-JY-FILING",
          provider: "juyuan",
          providerLabel: PROVIDER_NAMES.juyuan,
          capability: "announcement_filing_fulltext",
          capabilityLabel: PROVIDER_CAPABILITIES.announcement_filing_fulltext,
          mode: "capability_probe",
          modeLabel: QUERY_MODES.capability_probe,
          status: "planned",
          statusLabel: PLAN_STATES.planned,
          purpose: "核对订单、交付与分部收入是否在同一主体披露中对齐",
          dateScope: { start: "2025-01-01", end: "2025-06-30" },
          cutoff: CUTOFF,
          intendedArtifact: "截止日前公告财报原文的冻结文档版本",
        },
        {
          id: "PQ-JY-HOLDING",
          provider: "juyuan",
          providerLabel: PROVIDER_NAMES.juyuan,
          capability: "fund_holding_detail",
          capabilityLabel: PROVIDER_CAPABILITIES.fund_holding_detail,
          mode: "capability_probe",
          modeLabel: QUERY_MODES.capability_probe,
          status: "planned",
          statusLabel: PLAN_STATES.planned,
          purpose: "补齐披露期持仓映射，仅用于表达层核对",
          dateScope: { start: "2025-03-31", end: "2025-06-30" },
          cutoff: CUTOFF,
          intendedArtifact: "带报告期、份额类别与来源版本的持仓披露件",
        },
      ],
      positiveEvidenceSearches: [
        {
          id: "PS-001",
          label: "云厂商 AI 资本开支、订单积压与分部收入同向披露",
          scope: "2025-01-01 至 2025-06-30 · 云厂商正式披露",
        },
      ],
      negativeEvidenceSearches: [
        {
          id: "NS-001",
          label: "资本开支下调、交付延迟、订单取消或收入口径不匹配",
          scope: "2025-01-01 至 2025-06-30 · 发行人披露与反面线索",
        },
      ],
      resultMetrics: [
        {
          id: "M-NVDA-DC-REV",
          name: METRIC_NAMES["Data Center revenue"],
          value: METRIC_VALUES["$39.1bn"],
          period: METRIC_PERIODS["FY2026 Q1"],
        },
        {
          id: "M-TSMC-M05-YOY",
          name: METRIC_NAMES["May monthly revenue year-on-year change"],
          value: METRIC_VALUES["34.8%"],
          period: METRIC_PERIODS["2025-05"],
        },
      ],
      gaps: [
        { id: "F-C-01", label: "数据中心电力与并网周期" },
        { id: "F-X-01", label: "高投入但相关收入确认滞后" },
      ],
    },
  };
}

export function buildResearchPlanView(): ResearchPlanView {
  const existingAssets = [
    {
      id: "DOC-MSFT-FY25Q3",
      kind: "document" as const,
      label: "Microsoft FY2025 Q3 Form 10-Q",
      sourceVersion: "sec-10q-2025-04-30-v1",
      sourceSpan: "pp. 35-39, capital expenditures",
      reviewState: "reviewed" as const,
      reviewCount: 3,
      selected: true,
    },
    {
      id: "DOC-NVDA-FY26Q1",
      kind: "document" as const,
      label: "NVIDIA FY2026 Q1 Form 10-Q",
      sourceVersion: "sec-10q-2025-05-28-v1",
      sourceSpan: "pp. 22-27, Data Center revenue",
      reviewState: "reviewed" as const,
      reviewCount: 2,
      selected: true,
    },
    {
      id: "DOC-MSFT-FY25Q3-CALL",
      kind: "document" as const,
      label: "Microsoft FY2025 Q3 业绩说明会记录",
      sourceVersion: "issuer-call-2025-04-30-v1",
      sourceSpan: "prepared remarks, pp. 4-5",
      reviewState: "reviewed" as const,
      reviewCount: 2,
      selected: false,
    },
    {
      id: "DOC-TSMC-2025M05",
      kind: "document" as const,
      label: "TSMC 2025 年 5 月月度营收",
      sourceVersion: "ir-monthly-2025-06-10-v1",
      sourceSpan: "table 1, net revenue May 2025",
      reviewState: "reviewed" as const,
      reviewCount: 1,
      selected: true,
    },
    {
      id: "ST-001",
      kind: "statement" as const,
      label: "资本开支继续用于支持云与 AI 基础设施",
      sourceVersion: "sec-10q-2025-04-30-v1",
      sourceSpan: "p. 38, paragraphs 2-3",
      reviewState: "reviewed" as const,
      reviewCount: 2,
      selected: true,
    },
    {
      id: "ST-002",
      kind: "statement" as const,
      label: "数据中心收入增长与 Blackwell 系统交付同时披露",
      sourceVersion: "sec-10q-2025-05-28-v1",
      sourceSpan: "p. 24, Data Center discussion",
      reviewState: "reviewed" as const,
      reviewCount: 2,
      selected: true,
    },
    {
      id: "ST-004",
      kind: "statement" as const,
      label: "AI 基础设施需求高于可供容量",
      sourceVersion: "issuer-call-2025-04-30-v1",
      sourceSpan: "prepared remarks, pp. 4-5",
      reviewState: "reviewed" as const,
      reviewCount: 1,
      selected: false,
    },
    {
      id: "EL-001",
      kind: "evidence_link" as const,
      label: "TH-AIC-01 ← ST-001 · 支持关系",
      sourceVersion: "sec-10q-2025-04-30-v1",
      sourceSpan: "p. 38, paragraphs 2-3",
      reviewState: "reviewed" as const,
      reviewCount: 1,
      selected: true,
    },
    {
      id: "EL-002",
      kind: "evidence_link" as const,
      label: "TH-AIC-03 ← ST-002 · 支持关系",
      sourceVersion: "sec-10q-2025-05-28-v1",
      sourceSpan: "p. 24, Data Center discussion",
      reviewState: "reviewed" as const,
      reviewCount: 1,
      selected: true,
    },
    {
      id: "EL-004",
      kind: "evidence_link" as const,
      label: "TH-AIC-03 ← ST-004 · 反驳关系",
      sourceVersion: "issuer-call-2025-04-30-v1",
      sourceSpan: "prepared remarks, pp. 4-5",
      reviewState: "reviewed" as const,
      reviewCount: 1,
      selected: false,
    },
    {
      id: "M-NVDA-DC-REV",
      kind: "metric" as const,
      label: "NVIDIA 数据中心业务收入 · 391 亿美元 · 2026 财年第一季度",
      metricName: "Data Center revenue",
      metricValue: "$39.1bn",
      metricPeriod: "FY2026 Q1",
      sourceVersion: "sec-10q-2025-05-28-v1",
      sourceSpan: "p. 24, segment revenue table",
      reviewState: "reviewed" as const,
      reviewCount: 2,
      selected: true,
    },
    {
      id: "M-TSMC-M05-YOY",
      kind: "metric" as const,
      label: "台积电月度营收同比增幅 · 34.8% · 2025 年 5 月",
      metricName: "May monthly revenue year-on-year change",
      metricValue: "34.8%",
      metricPeriod: "2025-05",
      sourceVersion: "ir-monthly-2025-06-10-v1",
      sourceSpan: "table 1, net revenue May 2025",
      reviewState: "reviewed" as const,
      reviewCount: 1,
      selected: false,
    },
  ];
  return {
    case: {
      id: CASE_ID,
      researchPeriod: "2025-01-01 — 2027-12-31",
      cutoff: CUTOFF,
      revision: "RP-AIC-2025-01-v1",
    },
    existingAssets,
    orderedAssets: existingAssets,
    assetPageSize: 6,
    providerQueries: [
      {
        id: "PQ-JY-INDUSTRY",
        provider: "juyuan",
        capability: "industry_analysis_view",
        purpose: "查找产业链供需与交付约束的可核验出处，不直接采用观点结论",
        dateScope: { start: "2025-01-01", end: "2025-06-30" },
        cutoff: CUTOFF,
        intendedArtifact: "带来源版本与原文区段的行业材料候选件",
        status: "planned",
        exposureStatus: "probe_required",
      },
      {
        id: "PQ-JY-FILING",
        provider: "juyuan",
        capability: "announcement_filing_fulltext",
        purpose: "核对订单、交付与分部收入是否在同一主体披露中对齐",
        dateScope: { start: "2025-01-01", end: "2025-06-30" },
        cutoff: CUTOFF,
        intendedArtifact: "截止日前公告财报原文的冻结文档版本",
        status: "planned",
        exposureStatus: "probe_required",
      },
      {
        id: "PQ-JY-HOLDING",
        provider: "juyuan",
        capability: "fund_holding_detail",
        purpose: "补齐披露期持仓映射，仅用于表达层核对",
        dateScope: { start: "2025-03-31", end: "2025-06-30" },
        cutoff: CUTOFF,
        intendedArtifact: "带报告期、份额类别与来源版本的持仓披露件",
        status: "planned",
        exposureStatus: "probe_required",
      },
    ],
    collection: {
      reused: [
        {
          id: "CT-REUSE-FROZEN",
          label: "复用当前快照中的已冻结资料与数据",
          cutoff: CUTOFF,
        },
      ],
      awaitingProbe: [
        {
          id: "CT-PROBE-JY",
          label: "确认计划能力是否实际暴露并已获授权",
          cutoff: CUTOFF,
        },
      ],
      blocked: [
        {
          id: "CT-HOLDING-PERMISSION",
          label: "历史持仓读取受当前凭证权限阻塞",
          cutoff: CUTOFF,
        },
      ],
      running: [],
    },
    pendingResults: REVIEW_QUEUE.map((item) => ({
      id: item.id,
      targetLabel: STATEMENTS.find((s) => s.id === item.targetId)?.text ?? item.targetId,
      task: item.task,
      sourceId: item.targetId,
      sourceVersion: item.sourceVersion,
      reviewLabel: displayLabel(REVIEW_STATES, item.reviewState, "状态待确认"),
    })),
    gaps: [
      {
        id: "F-C-01",
        label: "数据中心电力与并网周期",
        scope: "具体项目、地区与并网窗口",
        type: "factor",
      },
      {
        id: "F-X-01",
        label: "高投入但相关收入确认滞后",
        scope: "可对齐投入、交付与收入的披露主体",
        type: "factor",
      },
      {
        id: "PS-001",
        label: "正面证据：订单与收入同向披露",
        scope: "2025-01-01 至 2025-06-30",
        type: "positive",
      },
      {
        id: "NS-001",
        label: "反面证据：交付延迟或收入不匹配",
        scope: "2025-01-01 至 2025-06-30",
        type: "negative",
      },
    ],
    resultMetrics: [
      {
        id: "M-NVDA-DC-REV",
        name: METRIC_NAMES["Data Center revenue"],
        value: METRIC_VALUES["$39.1bn"],
        period: METRIC_PERIODS["FY2026 Q1"],
      },
      {
        id: "M-TSMC-M05-YOY",
        name: METRIC_NAMES["May monthly revenue year-on-year change"],
        value: METRIC_VALUES["34.8%"],
        period: METRIC_PERIODS["2025-05"],
      },
    ],
    failures: PROVIDER_RUNS.filter((r) => r.outcome === "quota_failure").map((run) => ({
      id: run.id,
      provider: run.provider,
      outcome: run.outcome,
      observedAt: run.observedAt,
      detail: run.detail,
      sourceVersion: run.sourceVersion,
    })),
    permissionGaps: PROVIDER_RUNS.filter((r) => r.outcome === "permission_gap").map((run) => ({
      id: run.id,
      provider: run.provider,
      outcome: run.outcome,
      observedAt: run.observedAt,
      detail: run.detail,
      sourceVersion: run.sourceVersion,
    })),
    manualUploads: PROVIDER_RUNS.filter((r) => r.outcome === "manual_upload").map((run) => ({
      id: run.id,
      provider: run.provider,
      outcome: run.outcome,
      observedAt: run.observedAt,
      detail: run.detail,
      sourceVersion: run.sourceVersion,
    })),
  };
}

export function buildCaseWorkbenchView(): CaseWorkbenchView {
  const factorRows = FACTORS.map((factor) => ({
    factorId: factor.id,
    groupLabel:
      factor.group === "demand"
        ? "需求"
        : factor.group === "supply"
        ? "供给"
        : factor.group === "transmission"
        ? "传导"
        : factor.group === "constraints"
        ? "约束"
        : factor.group === "alternatives"
        ? "替代"
        : "反面",
    roleLabel:
      factor.proposedRole === "candidate"
        ? "候选因素"
        : factor.proposedRole === "transmission_factor"
        ? "传导因素"
        : factor.proposedRole === "constraint"
        ? "约束因素"
        : factor.proposedRole === "background"
        ? "背景因素"
        : "候选因素",
    statusLabel:
      factor.status === "candidate" ? "候选线索" : factor.status,
    label: factor.label,
    timeOrder: factor.timeOrder,
    mechanism: factor.mechanism,
    directEvidence: factor.directEvidence,
    alternatives: factor.alternatives,
    differenceExplanation: factor.differenceExplanation,
    scope: factor.scope,
    falsifier: factor.falsifier,
    counterexample: factor.counterexample,
    impactObject: factor.impactObject,
  }));
  const selectedFactor = factorRows.find((f) => f.factorId === "F-T-01")!;
  const sources: CaseWorkbenchView["sources"] = EVIDENCE_LINKS.map((link) => {
    const statement = STATEMENTS.find((s) => s.id === link.statementId)!;
    const document = DOCUMENTS.find((d) => d.id === statement.documentId)!;
    const isExcluded = link.role === "gap";
    return {
      id: link.id,
      relation: link.role,
      relationLabel:
        link.role === "support"
          ? "支持"
          : link.role === "contradict"
          ? "反驳"
          : "缺口",
      statement: statement.text,
      documentId: document.id,
      sourceVersion: link.sourceVersion,
      publishedDate: statement.publishedAt.slice(0, 10),
      sourceSpan: link.sourceSpan,
      reviewState: link.reviewState,
      reviewLabel: displayLabel(REVIEW_STATES, link.reviewState, "状态待确认"),
      snapshotMembership: link.snapshotMembership.join(", "),
      frozenEligibility: isExcluded ? "excluded" : "reviewed",
    };
  });
  const rebuttalStatement = STATEMENTS.find((s) => s.id === "ST-004")!;
  const rebuttalDocument = DOCUMENTS.find((d) => d.id === rebuttalStatement.documentId)!;
  const rebuttalLink = EVIDENCE_LINKS.find((l) => l.statementId === rebuttalStatement.id)!;
  return {
    case: {
      id: CASE_ID,
      title: CASE_TITLE,
      question: CASE_QUESTION,
      researchObject:
        "从云厂商资本开支，经芯片、互连与系统交付，到分部收入的 AI 算力产业链",
      researchPeriod: "2025-01-01 — 2027-12-31",
      cutoff: CUTOFF,
      snapshotId: SNAPSHOT_ID,
      aiState: AI_LABEL,
      humanReviewState: "已审核 · 林岚",
    },
    tabs: ["研究摘要", "关键图表", "核心观点", "风险与假设", "相关公司", "研究日志"],
    formalJudgment: {
      text: "截至证据截止日，人工复核材料支持 AI 基础设施投入扩张与部分系统交付，但尚不足以确认需求已完整传导为可持续收入；正式判断为证据不足，继续验证。",
      rationale: "正式判断仅使用当前冻结快照中的已审核来源陈述与证据关系，不纳入待审核展望。",
      reviewState: "reviewed",
      snapshotId: SNAPSHOT_ID,
      reviewedAt: "2025-06-30T22:40:00+08:00",
    },
    aiDraft: "新采集的 10-Q 来源确认没有改变数值；模型建议复核传导因素措辞。",
    contradiction: FACTORS.find((f) => f.id === "F-X-01")!,
    gap: {
      id: "F-C-01",
      label: "数据中心电力与并网周期",
      explanation: "暂无已审核来源直接连接并网与收入，需补项目级披露。",
    },
    nextValidation: {
      thesisId: "TH-AIC-03",
      event: "复核下一期分部收入、递延收入与订单履约说明。",
    },
    thesisRows: THESES.map((thesis, index) => ({
      id: thesis.id,
      title: thesis.title,
      supportCondition: thesis.supportCondition,
      evidenceState:
        thesis.evidenceReviewState === "reviewed_links_present"
          ? "已有已审核关系"
          : thesis.evidenceReviewState === "pending_relationship_review"
          ? "已有已审核关系 · 另有待审核关系"
          : "尚无证据关系",
      relationLabels:
        EVIDENCE_LINKS.filter((l) => l.thesisId === thesis.id)
          .map((l) => (l.role === "support" ? "支持" : l.role === "contradict" ? "反驳" : "缺口"))
          .join(" · "),
      scope: "AI 算力产业链 · 主要云厂商",
      falsifier: thesis.falsifier,
      reviewState: index === 0 ? "reviewed" : index === 1 ? "pending" : "reviewed",
      evidenceReviewState: thesis.evidenceReviewState,
      frozenEligibility: "excluded",
      selected: index === 0,
    })),
    rebuttal: {
      id: rebuttalLink.id,
      statement: rebuttalStatement.text,
      documentId: rebuttalDocument.id,
      documentTitle: rebuttalDocument.title,
      sourceVersion: rebuttalLink.sourceVersion,
      publishedDate: rebuttalStatement.publishedAt.slice(0, 10),
      sourceSpan: rebuttalLink.sourceSpan,
      reviewLabel: "已人工复核 · 林岚",
      reviewState: rebuttalLink.reviewState,
      relation: rebuttalLink.role,
      snapshotMembership: rebuttalLink.snapshotMembership.join(", "),
      frozenEligibility: "reviewed",
    },
    factorRows,
    selectedFactor,
    sources,
  };
}

export function buildRelationshipGraphView(): RelationshipGraphView {
  const msftDoc = DOCUMENTS.find((d) => d.id === "DOC-MSFT-FY25Q3")!;
  const nvdaDoc = DOCUMENTS.find((d) => d.id === "DOC-NVDA-FY26Q1")!;
  const callDoc = DOCUMENTS.find((d) => d.id === "DOC-MSFT-FY25Q3-CALL")!;
  const tsmcDoc = DOCUMENTS.find((d) => d.id === "DOC-TSMC-2025M05")!;
  const st001 = STATEMENTS.find((s) => s.id === "ST-001")!;
  const st002 = STATEMENTS.find((s) => s.id === "ST-002")!;
  const st004 = STATEMENTS.find((s) => s.id === "ST-004")!;
  const el001 = EVIDENCE_LINKS.find((l) => l.id === "EL-001")!;
  const el002 = EVIDENCE_LINKS.find((l) => l.id === "EL-002")!;
  const el004 = EVIDENCE_LINKS.find((l) => l.id === "EL-004")!;
  const th01 = THESES.find((t) => t.id === "TH-AIC-01")!;
  const th02 = THESES.find((t) => t.id === "TH-AIC-02")!;
  const th03 = THESES.find((t) => t.id === "TH-AIC-03")!;
  const coNvda = COMPANIES.find((c) => c.id === "CO-NVDA")!;
  const coTsm = COMPANIES.find((c) => c.id === "CO-TSM")!;
  const fundAi = FUNDS.find((f) => f.id === "FUND-ETF-AI-INFRA")!;
  const fundSemi = FUNDS.find((f) => f.id === "FUND-SEMI-INDEX")!;
  const sourceHref = (documentId: string, sourceSpan: string) =>
    `/library?document=${documentId}&span=${encodeURIComponent(sourceSpan)}`;

  const layers = [
    {
      key: "evidence" as const,
      label: "证据",
      nodes: [
        {
          id: nvdaDoc.id,
          layer: "DocumentVersion",
          title: nvdaDoc.title,
          meta: "2025-05-28 · 冻结版本",
          kind: "source-fact",
          kindLabel: "来源事实",
          relation: "冻结文档版本界定可引用信息边界。",
          review: "已人工复核",
          sourceName: "SEC EDGAR · NVIDIA",
          sourceSpan: nvdaDoc.sourceSpan,
          sourceHref: sourceHref(nvdaDoc.id, nvdaDoc.sourceSpan),
          attachment: `${nvdaDoc.title} · ${nvdaDoc.sourceVersion}`,
          publicationDate: nvdaDoc.publishedAt.slice(0, 10),
          asOf: nvdaDoc.availableAt.slice(0, 10),
          scope: "AI 算力产业链 · 当前 ResearchCase",
          citations: ["TH-AIC-03 引用", "分部收入关系复用 2 次"],
        },
        {
          id: st001.id,
          layer: "SourceStatement",
          title: st001.text,
          meta: "支持 · 已人工复核",
          kind: "source-fact",
          kindLabel: "来源事实",
          relation: el001.rationale,
          review: "已人工复核关系",
          sourceName: "SEC EDGAR · Microsoft",
          sourceSpan: st001.sourceSpan,
          sourceHref: sourceHref(msftDoc.id, st001.sourceSpan),
          attachment: `${msftDoc.title} · ${st001.sourceVersion}`,
          publicationDate: st001.publishedAt.slice(0, 10),
          asOf: st001.availableAt.slice(0, 10),
          scope: "云厂商资本开支",
          citations: ["TH-AIC-01 引用", "资本开支命题复用 2 次"],
        },
        {
          id: st002.id,
          layer: "SourceStatement",
          title: st002.text,
          meta: "支持 · 已人工复核",
          kind: "reviewed-relation",
          kindLabel: "已人工复核关系",
          relation: el002.rationale,
          review: "已人工复核关系",
          sourceName: "SEC EDGAR · NVIDIA",
          sourceSpan: st002.sourceSpan,
          sourceHref: sourceHref(nvdaDoc.id, st002.sourceSpan),
          attachment: `${nvdaDoc.title} · ${st002.sourceVersion}`,
          publicationDate: st002.publishedAt.slice(0, 10),
          asOf: st002.availableAt.slice(0, 10),
          scope: "系统交付与分部收入",
          citations: ["TH-AIC-03 引用", "交付关系复用 3 次"],
        },
        {
          id: st004.id,
          layer: "SourceStatement",
          title: st004.text,
          meta: "反驳即时传导 · 已人工复核",
          kind: "contradictory",
          kindLabel: "已人工复核关系",
          relation: el004.rationale,
          review: "已人工复核 · 林岚",
          sourceName: "Microsoft IR · 业绩说明会",
          sourceSpan: st004.sourceSpan,
          sourceHref: sourceHref(callDoc.id, st004.sourceSpan),
          attachment: `${callDoc.title} · ${st004.sourceVersion}`,
          publicationDate: st004.publishedAt.slice(0, 10),
          asOf: st004.availableAt.slice(0, 10),
          scope: "云厂商容量约束",
          citations: ["TH-AIC-03 反面引用", "RC-CLOUD-CAPACITY-2025-02 复用"],
        },
      ],
    },
    {
      key: "thesis" as const,
      label: "命题",
      nodes: [
        {
          id: th01.id,
          layer: "ReviewedFactor",
          title: th01.title,
          meta: "已存在人工复核关系",
          kind: "thesis-node",
          kindLabel: "已人工复核关系",
          relation: "由 EL-001 支持，仍保留独立证伪条件。",
          review: "证据关系已人工复核",
          sourceName: "Microsoft Form 10-Q",
          sourceSpan: st001.sourceSpan,
          sourceHref: sourceHref(msftDoc.id, st001.sourceSpan),
          attachment: msftDoc.sourceVersion,
          publicationDate: msftDoc.publishedAt.slice(0, 10),
          asOf: msftDoc.availableAt.slice(0, 10),
          scope: "主要云厂商资本开支",
          citations: ["EL-001 支持", "持续验证"],
        },
        {
          id: th02.id,
          layer: "Thesis",
          title: th02.title,
          meta: "AI 提议 · 未经人工复核",
          kind: "ai-proposed",
          kindLabel: "AI 提议关系 · 未经人工复核",
          relation: "先进封装与互连可能限制交付斜率，等待直接证据。",
          review: "未经人工复核",
          sourceName: "TSMC Investor Relations",
          sourceSpan: tsmcDoc.sourceSpan,
          sourceHref: sourceHref(tsmcDoc.id, tsmcDoc.sourceSpan),
          attachment: tsmcDoc.sourceVersion,
          publicationDate: tsmcDoc.publishedAt.slice(0, 10),
          asOf: tsmcDoc.availableAt.slice(0, 10),
          scope: "先进封装与互连",
          citations: ["尚未建立证据关系"],
        },
        {
          id: th03.id,
          layer: "Thesis",
          title: th03.title,
          meta: "待关系审核 · 含反面证据",
          kind: "thesis-node",
          kindLabel: "命题判断",
          relation: "支持与反面材料并存，不把需求直接等同于收入。",
          review: "关系待人工审核",
          sourceName: "NVIDIA Form 10-Q / Microsoft IR",
          sourceSpan: el004.sourceSpan,
          sourceHref: sourceHref(callDoc.id, el004.sourceSpan),
          attachment: callDoc.sourceVersion,
          publicationDate: callDoc.publishedAt.slice(0, 10),
          asOf: callDoc.availableAt.slice(0, 10),
          scope: "需求到收入传导",
          citations: ["EL-002 支持", "EL-004 反面"],
        },
      ],
    },
    {
      key: "causal" as const,
      label: "因果链",
      nodes: [
        {
          id: "CS-CAPEX",
          layer: "CausalStep",
          title: "云厂商资本开支扩张",
          meta: "01 · 需求入口",
          kind: "causal-node",
          kindLabel: "已人工复核关系",
          relation: "投入方向由正式披露限定。",
          review: "已人工复核关系",
          sourceName: "Microsoft Form 10-Q",
          sourceSpan: st001.sourceSpan,
          sourceHref: sourceHref(msftDoc.id, st001.sourceSpan),
          attachment: msftDoc.sourceVersion,
          publicationDate: st001.publishedAt.slice(0, 10),
          asOf: st001.availableAt.slice(0, 10),
          scope: "投入入口",
          citations: ["EL-001 支持"],
        },
        {
          id: "CS-PROCUREMENT",
          layer: "CausalStep",
          title: "AI 基础设施采购",
          meta: "02 · 需求转为订单",
          kind: "causal-node",
          kindLabel: "AI 提议 · 未经人工复核",
          relation: "采购形成系统与芯片订单的提议关系。",
          review: "未经人工复核",
          sourceName: "当前 ResearchCase",
          sourceSpan: "关系提议，无独立 SourceSpan",
          sourceHref: "?screen=plan",
          attachment: SNAPSHOT_ID,
          publicationDate: "-",
          asOf: "-",
          scope: "采购动作",
          citations: ["AI 提议关系 · 未经人工复核"],
        },
        {
          id: "CS-BACKLOG",
          layer: "CausalStep",
          title: "订单积压与供给排期",
          meta: "03 · 供给约束",
          kind: "causal-node",
          kindLabel: "AI 提议 · 未经人工复核",
          relation: "订单仍需经过供给、容量与验收约束。",
          review: "未经人工复核",
          sourceName: "NVIDIA Form 10-Q",
          sourceSpan: nvdaDoc.sourceSpan,
          sourceHref: sourceHref(nvdaDoc.id, nvdaDoc.sourceSpan),
          attachment: nvdaDoc.sourceVersion,
          publicationDate: nvdaDoc.publishedAt.slice(0, 10),
          asOf: nvdaDoc.availableAt.slice(0, 10),
          scope: "供给排期",
          citations: ["EL-004 反面 · 容量约束"],
        },
        {
          id: "EL-PROPOSED-CAUSAL",
          layer: "CausalStep",
          title: "系统交付与容量上线",
          meta: "04 · 交付 → 收入",
          kind: "ai-proposed",
          kindLabel: "AI 提议关系 · 未经人工复核",
          relation: "实际交付是订单积压进入收入确认的必要中间环节。",
          review: "未经人工复核",
          sourceName: "NVIDIA Form 10-Q",
          sourceSpan: st002.sourceSpan,
          sourceHref: sourceHref(nvdaDoc.id, st002.sourceSpan),
          attachment: nvdaDoc.sourceVersion,
          publicationDate: st002.publishedAt.slice(0, 10),
          asOf: st002.availableAt.slice(0, 10),
          scope: "NVIDIA 数据中心业务 · 2026 财年第一季度",
          citations: ["EL-002 支持"],
          actions: true,
        },
        {
          id: "CS-REVENUE",
          layer: "CausalStep",
          title: "分部收入确认",
          meta: "05 · 结果口径",
          kind: "causal-node",
          kindLabel: "已人工复核关系",
          relation: "只使用同主体分部收入披露，不用订单推算。",
          review: "已人工复核关系",
          sourceName: "NVIDIA Form 10-Q",
          sourceSpan: "p. 24, segment revenue table",
          sourceHref: sourceHref(nvdaDoc.id, "p. 24, segment revenue table"),
          attachment: nvdaDoc.sourceVersion,
          publicationDate: nvdaDoc.publishedAt.slice(0, 10),
          asOf: nvdaDoc.availableAt.slice(0, 10),
          scope: "结果口径",
          citations: ["M-NVDA-DC-REV 引用"],
        },
      ],
    },
    {
      key: "company" as const,
      label: "公司",
      nodes: [
        {
          id: coNvda.id,
          layer: "Company",
          title: "NVIDIA · NVDA",
          meta: "系统与加速芯片",
          kind: "company-node",
          kindLabel: "投影节点",
          relation: "已披露主体映射至上市证券。",
          review: "公司/证券映射已复核",
          sourceName: "NVIDIA Form 10-Q",
          sourceSpan: coNvda.sourceSpan,
          sourceHref: sourceHref(nvdaDoc.id, coNvda.sourceSpan),
          attachment: coNvda.sourceVersion,
          publicationDate: coNvda.disclosureDate,
          asOf: coNvda.disclosureDate,
          scope: "系统与芯片供应",
          citations: ["EL-002 支持"],
        },
        {
          id: coTsm.id,
          layer: "Stock",
          title: "TSMC · TSM",
          meta: "晶圆与先进封装",
          kind: "company-node",
          kindLabel: "投影节点",
          relation: "发行人实体映射至上市证券。",
          review: "公司/证券映射已复核",
          sourceName: "TSMC Investor Relations",
          sourceSpan: coTsm.sourceSpan,
          sourceHref: sourceHref(tsmcDoc.id, coTsm.sourceSpan),
          attachment: coTsm.sourceVersion,
          publicationDate: coTsm.disclosureDate,
          asOf: coTsm.disclosureDate,
          scope: "晶圆与封装",
          citations: ["M-TSMC-M05-YOY 引用"],
        },
        {
          id: "CO-MSFT",
          layer: "Company",
          title: "Microsoft · MSFT",
          meta: "云与 AI 基础设施",
          kind: "company-node",
          kindLabel: "投影节点",
          relation: "披露主体映射至上市证券，仅作研究表达。",
          review: "公司/证券映射已复核",
          sourceName: "Microsoft Form 10-Q",
          sourceSpan: msftDoc.sourceSpan,
          sourceHref: sourceHref(msftDoc.id, msftDoc.sourceSpan),
          attachment: msftDoc.sourceVersion,
          publicationDate: msftDoc.publishedAt.slice(0, 10),
          asOf: msftDoc.availableAt.slice(0, 10),
          scope: "云与 AI 基础设施",
          citations: ["EL-001 · EL-004"],
        },
      ],
    },
    {
      key: "fund" as const,
      label: "基金",
      nodes: [
        {
          id: "HOLDING-FUND-NVDA-2025Q1",
          layer: "HoldingDisclosure",
          title: "NVDA 披露持仓 8.4%",
          meta: "as_of_date 2025-03-31",
          kind: "fund-node",
          kindLabel: "投影节点",
          relation: "定期报告点时持仓，不代表当前敞口。",
          review: "披露记录已人工复核",
          sourceName: "基金 2025 年一季报",
          sourceSpan: fundAi.sourceSpan,
          sourceHref: "/library?document=fund-report-2025q1-v1",
          attachment: fundAi.sourceVersion,
          publicationDate: fundAi.publishedAt.slice(0, 10),
          asOf: fundAi.disclosureDate,
          scope: "披露持仓映射",
          citations: ["示例 ETF 持仓 8.4%"],
        },
        {
          id: fundAi.id,
          layer: "Fund",
          title: fundAi.name,
          meta: "披露持仓 8.4% · as-of 2025-03-31",
          kind: "fund-node",
          kindLabel: "投影节点",
          relation: "由已披露持仓连接的基金表达，仅用于点时穿透。",
          review: "披露记录已人工复核",
          sourceName: "基金 2025 年一季报",
          sourceSpan: fundAi.sourceSpan,
          sourceHref: "/library?document=fund-report-2025q1-v1",
          attachment: fundAi.sourceVersion,
          publicationDate: fundAi.publishedAt.slice(0, 10),
          asOf: fundAi.disclosureDate,
          scope: "披露持仓映射",
          citations: ["CO-NVDA · 8.4%"],
        },
        {
          id: fundSemi.id,
          layer: "Fund",
          title: fundSemi.name,
          meta: "披露持仓 6.1% · as-of 2025-03-31",
          note: "披露口径待人工审核",
          kind: "fund-node",
          kindLabel: "投影节点",
          relation: "由 TSMC 披露持仓连接，份额类别仍待核对。",
          review: "待人工审核",
          sourceName: "基金 2025 年一季报",
          sourceSpan: fundSemi.sourceSpan,
          sourceHref: "/library?document=fund-report-2025q1-v2",
          attachment: fundSemi.sourceVersion,
          publicationDate: fundSemi.publishedAt.slice(0, 10),
          asOf: fundSemi.disclosureDate,
          scope: "披露持仓映射",
          citations: ["CO-TSM · 6.1%"],
        },
      ],
    },
  ];
  // Wiki 图谱使用的真实边：证据→命题（支持/反驳）、命题→因果链、链内步骤
  // 顺序、因果→公司投影、公司→基金持仓。source/target 方向仅作语义记录，
  // 展示层按层序左右排布。
  const edges: RelationshipGraphView["edges"] = [
    { id: "EL-001", source: "ST-001", target: "TH-AIC-01", kind: "evidence", label: "支持", role: "supports", reviewState: "reviewed" },
    { id: "EL-002", source: "ST-002", target: "TH-AIC-03", kind: "evidence", label: "支持", role: "supports", reviewState: "reviewed" },
    { id: "EL-004", source: "ST-004", target: "TH-AIC-03", kind: "evidence", label: "反驳", role: "contradicts", reviewState: "reviewed" },
    { id: "EG-TH01-CAPEX", source: "TH-AIC-01", target: "CS-CAPEX", kind: "causal", label: "因果", reviewState: "reviewed" },
    { id: "EG-TH03-DELIVERY", source: "TH-AIC-03", target: "EL-PROPOSED-CAUSAL", kind: "causal", label: "因果 · AI 提议" },
    { id: "EG-STEP-1", source: "CS-CAPEX", target: "CS-PROCUREMENT", kind: "contains_step", label: "下一步" },
    { id: "EG-STEP-2", source: "CS-PROCUREMENT", target: "CS-BACKLOG", kind: "contains_step", label: "下一步" },
    { id: "EG-STEP-3", source: "CS-BACKLOG", target: "EL-PROPOSED-CAUSAL", kind: "contains_step", label: "下一步" },
    { id: "EG-STEP-4", source: "EL-PROPOSED-CAUSAL", target: "CS-REVENUE", kind: "contains_step", label: "下一步", reviewState: "reviewed" },
    { id: "EG-CAPEX-MSFT", source: "CS-CAPEX", target: "CO-MSFT", kind: "company_stock", label: "主体映射", reviewState: "reviewed" },
    { id: "EG-REV-NVDA", source: "CS-REVENUE", target: "CO-NVDA", kind: "company_stock", label: "主体映射", reviewState: "reviewed" },
    { id: "EG-TH02-TSM", source: "TH-AIC-02", target: "CO-TSM", kind: "company_stock", label: "主体映射", reviewState: "reviewed" },
    { id: "EG-NVDA-HOLD", source: "CO-NVDA", target: "HOLDING-FUND-NVDA-2025Q1", kind: "holding", label: "披露持仓 8.4%", reviewState: "reviewed" },
    { id: "EG-NVDA-FUND", source: "CO-NVDA", target: "FUND-ETF-AI-INFRA", kind: "holding", label: "持仓 8.4%", reviewState: "reviewed" },
    { id: "EG-TSM-FUND", source: "CO-TSM", target: "FUND-SEMI-INDEX", kind: "holding", label: "持仓 6.1%" },
  ];
  return {
    case: { id: CASE_ID, title: CASE_TITLE, question: CASE_QUESTION, cutoff: CUTOFF, snapshotId: SNAPSHOT_ID },
    layers,
    nodes: layers.flatMap((layer) => layer.nodes),
    edges,
    selectedNodeId: "ST-004",
  };
}

export function buildLibraryView(): LibraryView {
  const documents = DOCUMENTS.map((document) => ({
    id: document.id,
    title: document.title,
    sourceName: document.sourceName,
    sourceVersion: document.sourceVersion,
    documentType: document.documentType,
    entity: document.entity,
    reuseCount: document.reuseCount,
    reviewState: document.reviewState,
    publishedLabel: document.publishedAt.replace("T", " ").replace(/Z$/u, " UTC"),
    availableLabel: document.availableAt.replace("T", " ").replace(/Z$/u, " UTC"),
    acquiredLabel: document.acquiredAt.replace("T", " ").replace(/Z$/u, " UTC"),
    previousVersion: document.previousVersion,
    linkedCaseIds: document.linkedCaseIds,
    reuseHistory: document.reuseHistory,
    sourceExcerpt: document.sourceExcerpt,
    exactSpan: document.exactSpan,
    // mock 模式下 exactSpan 已经是人话（"pp. 35-39, ..."），直接复用。
    humanSpan: document.exactSpan,
    spanCount: document.spanCount,
    statementCount: document.statementCount,
    pendingExtraction: document.spanCount > 0 && document.statementCount === 0,
  }));
  const selected = documents.find((d) => d.id === "DOC-MSFT-FY25Q3-CALL") ?? documents[0];
  const statement = STATEMENTS.find((s) => s.documentId === selected.id)!;
  const link = EVIDENCE_LINKS.find((l) => l.statementId === statement.id);
  const thesis = link ? THESES.find((t) => t.id === link.thesisId) : null;
  const factor = link ? FACTORS.find((f) => f.id === "F-T-01") : null;
  const pendingStatement = STATEMENTS.find((s) => s.reviewState === "pending_review");
  const pendingLink = pendingStatement
    ? EVIDENCE_LINKS.find((l) => l.statementId === pendingStatement.id)
    : null;

  return {
    cutoff: CUTOFF,
    snapshotId: SNAPSHOT_ID,
    documents,
    selected,
    knowledge: link
      ? {
          statement: { id: statement.id, text: statement.text },
          link: { id: link.id, role: link.role, reviewedBy: link.reviewedBy, reviewedAt: link.reviewedAt },
          roleLabel:
            link.role === "support"
              ? "支持 · support"
              : link.role === "contradict"
              ? "反驳 · contradict"
              : "缺口 · gap",
          thesis: thesis ? { id: thesis.id, title: thesis.title } : null,
          factor: factor ? { id: factor.id, label: factor.label } : null,
          reviewedBy: link.reviewedBy ?? "研究审核组",
          reviewedAt: (link.reviewedAt ?? "2025-06-30T22:40:00+08:00").replace("T", " ").replace(/Z$/u, " UTC"),
        }
      : null,
    proposal:
      pendingStatement && pendingLink
        ? {
            statement: { id: pendingStatement.id, text: pendingStatement.text },
            link: { id: pendingLink.id, role: pendingLink.role },
            roleLabel:
              pendingLink.role === "support"
                ? "支持 · support"
                : pendingLink.role === "contradict"
                ? "反驳 · contradict"
                : "缺口 · gap",
          }
        : null,
  };
}

export function buildDataCenterView(): DataCenterView {
  return {
    cutoff: CUTOFF,
    snapshotId: SNAPSHOT_ID,
    // Mirrors the seeded AI-compute slice so the research-ops section is
    // reviewable without a backend: 3 confirmed assessment reviews, 15
    // human-curated gold links still pending link-level review.
    researchOps: {
      asOf: CUTOFF,
      throughput: {
        linkReviewsTotal: 0,
        linkReviewsLast7d: 0,
        assessmentReviewsTotal: 3,
        assessmentReviewsLast7d: 3,
        reviewsByReviewer: [{ reviewer: "seed-human-reviewer", count: 3 }],
        pendingLinkReviews: 15,
        pendingAssessmentReviews: 0,
      },
      agreement: {
        assessmentAgreementRate: 1.0,
        assessmentOutcomes: [{ outcome: "confirmed", count: 3 }],
        conclusionChanged: 0,
        linkAgreementRate: null,
        linkModified: 0,
        linkOutcomes: [],
      },
      latency: {
        evidenceToAssessmentAvgDays: 0.0,
        evidenceToAssessmentMaxDays: 0.0,
        assessmentToReviewAvgDays: 0.0,
        assessmentToReviewMaxDays: 0.0,
      },
    },
    catalog: [
      {
        id: "M-NVDA-DC-REV",
        label: METRIC_NAMES["Data Center revenue"],
        entity: "NVIDIA",
        cadence: "季度",
        state: "部分观测可用于截止日",
        stockId: "CO-NVDA",
        metricName: "Data Center revenue",
      },
      {
        id: "M-TSMC-M05-YOY",
        label: METRIC_NAMES["May monthly revenue year-on-year change"],
        entity: "TSMC",
        cadence: "月度",
        state: "截止日可用",
        stockId: "CO-TSM",
        metricName: "May monthly revenue year-on-year change",
      },
      {
        id: "M-FUND-HOLDING-HIST",
        label: "基金历史持仓权重",
        entity: "示例基金范围",
        cadence: "报告期",
        state: "权限缺口 · 暂无冻结值",
        stockId: "FUND-SEMI-INDEX",
        metricName: "基金历史持仓权重",
      },
    ],
    selectedMetricId: "M-NVDA-DC-REV",
    selectedMetric: {
      id: "M-NVDA-DC-REV",
      name: METRIC_NAMES["Data Center revenue"],
      entity: "NVIDIA",
      value: "391",
      unit: "亿美元",
      period: METRIC_PERIODS["FY2026 Q1"],
      asOf: "2025-04-27",
      publishedAt: "2025-05-28 20:05 UTC",
      availableAt: "2025-05-28 20:13 UTC",
      acquiredAt: "2025-06-30 18:10 CST",
      source: "NVIDIA Form 10-Q · sec-10q-2025-05-28-v1",
      methodology: "公司披露的 Data Center 分部收入；不以资本开支、订单或模型推算替代。",
      revision: "2025-07-02 来源确认修订",
      providerRunId: "PR-001",
      failureMeaning: "刷新失败只表示本次未取得新版本，不撤销或推测替换已冻结观测。",
    },
    series: [
      { period: "FY25 Q2", value: "263", numericValue: 263, acquiredAt: "2024-08-28", cutoffUsable: true, status: "截止日可用" },
      { period: "FY25 Q3", value: "308", numericValue: 308, acquiredAt: "2024-11-20", cutoffUsable: true, status: "截止日可用" },
      { period: "FY25 Q4", value: "356", numericValue: 356, acquiredAt: "2025-02-26", cutoffUsable: true, status: "截止日可用" },
      { period: "FY26 Q1", value: "391", numericValue: 391, acquiredAt: "2025-06-30", cutoffUsable: true, status: "截止日可用" },
      { period: "来源确认", value: "391", numericValue: 391, acquiredAt: "2025-07-02", cutoffUsable: false, status: "案例截止日不可用 · 现在已可用" },
    ],
    revisionComparison: {
      oldValue: METRIC_VALUES["$39.1bn"],
      oldSource: "发行人业绩材料 · 2025-05-28 已公开",
      oldCutoffMeaning: `可用于 ${SNAPSHOT_ID}`,
      newValue: METRIC_VALUES["$39.1bn"],
      newSource: "Form 10-Q 来源确认 · 2025-07-02 才采集",
      newCutoffMeaning: "案例截止日不可用 · 现在已可用",
      whyItMatters:
        "数值未变，但来源从初始披露升级为监管文件。新来源只能进入后续快照，不能回写 2025-06-30 当时可知的信息。",
    },
    plannedAttempt: {
      id: "PQ-JY-FILING",
      label: "公告财报原文能力探测",
      state: "计划中 · 尚未执行",
      meaning: "这是下一次能力探测，不是历史成功，也不是正在运行的任务。",
    },
    historicalRuns: PROVIDER_RUNS.filter((r) =>
      ["success", "quota_failure", "permission_gap"].includes(r.outcome),
    )
      .slice(0, 4)
      .map((run) => ({
        id: run.id,
        providerLabel: displayLabel(PROVIDER_NAMES, run.provider, run.provider),
        outcome: run.outcome as "success" | "quota_failure" | "permission_gap",
        outcomeLabel: run.outcome === "success" ? "成功" : displayLabel(PROVIDER_OUTCOMES, run.outcome, "失败"),
        detailLabel: displayLabel(PROVIDER_DETAILS, run.detail, run.detail),
        observedAt: run.observedAt,
      })),
  };
}

export function buildVersionsView(): VersionsView {
  return {
    case: { id: CASE_ID, title: CASE_TITLE },
    focusThesisId: "TH-AIC-01",
    beforeSnapshot: { id: "RS-2025-03-31-v2", cutoff: "2025-03-31", freezeTime: "2025-04-01 09:12" },
    afterSnapshot: { id: SNAPSHOT_ID, cutoff: CUTOFF, freezeTime: "2025-06-30 23:59" },
    before: {
      formalConclusion: {
        state: "开放判断",
        text: "需求扩张已有披露支持，但订单、交付与收入尚未形成经审核的传导关系。",
      },
      inputs: [
        {
          id: "DOC-MSFT-FY25Q2",
          kind: "DocumentVersion",
          label: "Microsoft FY2025 Q2 Form 10-Q",
          version: "sec-10q-2025-01-29-v1",
          state: undefined,
          role: undefined,
          reviewState: undefined,
        },
        {
          id: "SERIES-NVDA-DC-v2",
          kind: "数据序列",
          label: "NVIDIA Data Center revenue · 截至 FY25 Q4",
          version: "series-nvda-dc-2025-02-26-v2",
          state: undefined,
          role: undefined,
          reviewState: undefined,
        },
      ],
      relationships: [
        { id: "EL-CAPEX-01", label: "资本开支 → AI 基础设施投入", role: "支持", reviewState: "已审核" },
        { id: "EL-TRANSMISSION", label: "订单 → 交付 → 分部收入", role: "关系缺失", reviewState: "未形成" },
      ],
      factors: [
        { id: "F-D-01", label: "云厂商资本开支", role: "候选需求因素" },
        { id: "F-T-01", label: "订单到实际交付", role: "待定义" },
      ],
      gaps: [
        { id: "G-CAPEX-PURPOSE", label: "资本开支是否明确用于 AI 基础设施", state: "未解决" },
        { id: "G-REVENUE-CHAIN", label: "订单、交付与分部收入能否同主体对齐", state: "未解决" },
      ],
    },
    after: {
      formalConclusion: {
        state: "证据不足 · 继续验证",
        text: "投入扩张与部分系统交付成立，但尚不足以确认需求已完整传导为可持续收入。",
      },
      inputs: [
        {
          id: "DOC-MSFT-FY25Q3",
          kind: "DocumentVersion",
          label: "Microsoft FY2025 Q3 Form 10-Q",
          version: "sec-10q-2025-04-30-v1",
          state: undefined,
          role: undefined,
          reviewState: undefined,
        },
        {
          id: "DOC-NVDA-FY26Q1",
          kind: "DocumentVersion",
          label: "NVIDIA FY2026 Q1 Form 10-Q",
          version: "sec-10q-2025-05-28-v1",
          state: undefined,
          role: undefined,
          reviewState: undefined,
        },
        {
          id: "SERIES-NVDA-DC-v3",
          kind: "数据序列",
          label: "NVIDIA Data Center revenue · 截至 FY26 Q1",
          version: "series-nvda-dc-2025-05-28-v3",
          state: undefined,
          role: undefined,
          reviewState: undefined,
        },
      ],
      relationships: [
        { id: "EL-002", label: "系统交付 ↔ 分部收入", role: "支持", reviewState: "已审核" },
        { id: "EL-004", label: "可用容量不足 → 收入确认滞后", role: "反面证据", reviewState: "已审核" },
      ],
      factors: [
        { id: "F-D-01", label: "云厂商资本开支", role: "背景条件" },
        { id: "F-T-01", label: "订单到实际交付", role: "传导因素" },
        { id: "F-C-01", label: "容量与并网周期", role: "限制因素" },
      ],
      gaps: [
        { id: "G-CAPEX-PURPOSE", label: "资本开支用于 AI 基础设施", state: "已解决" },
        { id: "G-REVENUE-CHAIN", label: "完整收入传导仍缺连续点时证据", state: "仍未解决" },
        { id: "G-CAPACITY", label: "容量约束影响收入确认的持续期", state: "新增缺口" },
      ],
    },
    changeRail: {
      inputSummary: "新增 2 个 DocumentVersion · 数据序列 v2 → v3 · 6 项未变输入已折叠",
      relationshipSummary: "新增 1 条支持关系与 1 条反面关系，均经人工审核",
      factorSummary: "候选需求因素 → 背景条件；订单交付被定义为传导因素",
      conclusionSummary: "开放判断 → 证据不足 · 继续验证",
      gapSummary: "已解决 1 · 新增 1 · 仍未解决 1",
      rationale:
        "审核人将需求披露与收入传导拆开判断：新增交付证据只支持局部链条；EL-004 证明可用容量与确认节奏仍会切断即时传导，因此正式结论收敛为「证据不足，继续验证」。",
      reviewedBy: "林岚 · 行业研究",
      reviewedAt: "2025-06-30 22:40 CST",
    },
    aiProposal: {
      runId: "AI-RERUN-2025-07-02-01",
      observedAt: "2025-07-02 08:40 CST",
      label: "未经人工复核",
      text: "新采集的 10-Q 来源确认没有改变数值；模型建议复核传导因素措辞。",
      boundary: "发生在 v3 冻结之后，不属于本次正式快照变更原因；仅可进入下一快照的审核队列。",
    },
    perThesisChanges: [
      {
        thesisId: "thesis-fixture-1",
        statement: "本季度收入因价格回升显著改善",
        conclusionBefore: "insufficient_evidence",
        conclusionAfter: "supported",
        gapsBeforeCount: 4,
        gapsAfterCount: 2,
        addedLinks: 7,
        removedLinks: 0,
      },
      {
        thesisId: "thesis-fixture-2",
        statement: "细分市场销量已恢复至同期水平",
        conclusionBefore: null,
        conclusionAfter: "supported",
        gapsBeforeCount: 0,
        gapsAfterCount: 1,
        addedLinks: 5,
        removedLinks: 0,
      },
    ],
    availableCutoffs: ["2025-03-31", "2025-04-15", "2025-05-15", "2025-06-30"],
    snapshotPoints: [
      { id: "RS-2025-03-31-v2", cutoff: "2025-03-31", linkCount: 4, eventSummary: null },
      {
        id: "RS-2025-04-15-v2",
        cutoff: "2025-04-15",
        linkCount: 9,
        eventSummary: {
          linkDelta: 5,
          removedLinkDelta: 0,
          conclusionFlips: [],
          gapsDelta: {},
          reviewedDelta: 0,
        },
      },
      {
        id: "RS-2025-05-15-v3",
        cutoff: "2025-05-15",
        linkCount: 17,
        eventSummary: {
          linkDelta: 8,
          removedLinkDelta: 0,
          conclusionFlips: [
            {
              thesisId: "thesis-fixture-1",
              from: "insufficient_evidence",
              to: "supported",
              statement: "本季度收入因价格回升显著改善",
            },
          ],
          gapsDelta: { "thesis-fixture-1": -2 },
          reviewedDelta: 1,
        },
      },
      {
        id: "RS-2025-06-30-v4",
        cutoff: "2025-06-30",
        linkCount: 28,
        eventSummary: {
          linkDelta: 11,
          removedLinkDelta: 0,
          conclusionFlips: [],
          gapsDelta: {},
          reviewedDelta: 0,
        },
      },
    ],
  };
}

// ── Workspace overview (设计原型1) ─────────────────────────────────────
// Mirrors the AI 算力链 overview shown in prototype/ui/app.js. Independent
// of the AIC research-case fixture so that the workspace stays focused on
// the always-on research queue rather than a single frozen research case.

export function buildWorkspaceOverviewScreen(): WorkspaceOverviewScreen {
  return {
    caseId: "ai-compute",
    caseTitle: "AI 算力链",
    caseTopic: "AI 算力链 · 深度研究",
    caseTopicTags: ["深度研究"],
    lastUpdatedAt: "2024-05-24 10:30",
    caseCountLabel: "32",
    tabs: [
      { id: "summary", label: "研究摘要", count: 32, active: true },
      { id: "charts", label: "关键图表", count: 12 },
      { id: "viewpoints", label: "核心观点", count: 18 },
      { id: "risk", label: "风险与假设", count: 12 },
      { id: "companies", label: "相关公司", count: 48 },
      { id: "log", label: "研究日志", count: 0 },
    ],
    bullets: [
      "全球 AI 算力需求将快速增长，2024–2026 年复合增速预计达 56%。推理侧需求成为新增量，结构上以 GPU、HBM、光模块、电源、液冷为支撑，向下游云厂商与智算中心、供服链路逐步分化。",
      "产业链从上游芯片、HBM、先进封装、中游服务器与液冷，向下游云厂与智算中心、供服链路逐步分化，关键环节存在结构性紧缺。",
      "国产替代在部分环节取得进展，但高端 GPU、HBM、先进制程与高端设备仍受到出口、地缘与出口管制带来不确定性扰动。",
    ],
    keyChanges: [
      {
        id: "kc-1",
        tag: "新增",
        text: "英伟达发布 Blackwell 架构 GB200 NVL72 机柜方案，进一步提升推理性能与能效。",
        detail:
          "单柜 72 颗 Blackwell GPU，相比 H100 在 FP4 推理性能上有显著提升，对液冷与电源提出新要求。",
        occurredAt: "2024-05-24",
        sourceLabel: "NVIDIA",
      },
      {
        id: "kc-2",
        tag: "更新",
        text: "台积电上调 2024 年 CoWoS 产能指引，全年产能同比增长约 30%。",
        detail: "新增产能主要被英伟达、AMD 与博通锁定，对外可分配给中小客户的产能仍然紧张。",
        occurredAt: "2024-05-23",
        sourceLabel: "工商时报",
      },
      {
        id: "kc-3",
        tag: "新增",
        text: "工信部：加快液冷等先进计算关键技术研发，推动智算中心标准化建设。",
        detail: "首次明确液冷为先进计算关键支撑技术，对国内液冷厂商释放中长期需求信号。",
        occurredAt: "2024-05-21",
        sourceLabel: "工信部",
      },
      {
        id: "kc-4",
        tag: "风险",
        text: "美国调整高端 GPU 出口管制细则，部分数据中心订单延迟。",
        detail: "数据中心出口许可申请出现积压，部分订单交付节奏被推迟。",
        occurredAt: "2024-05-22",
        sourceLabel: "海关总署",
      },
      {
        id: "kc-5",
        tag: "新增",
        text: "中国电信发布 2024–2025 年液冷服务器采购架构，预算规模超 40%。",
        detail: "采购量与单台价值同步提升，国产液冷厂商份额有望显著扩大。",
        occurredAt: "2024-05-20",
        sourceLabel: "招标公告",
      },
    ],
    framework: [
      {
        id: "f-demand",
        sequence: "1",
        title: "需求端：AI 应用与算力需求",
        description: "已更新 3 项证据",
        expanded: true,
        children: [
          { id: "f-1-1", sequence: "1.1", title: "大模型演进与训练需求" },
          { id: "f-1-2", sequence: "1.2", title: "推理需求爆发与商业化落地" },
          { id: "f-1-3", sequence: "1.3", title: "端侧行业应用渗透" },
        ],
      },
    ],
    totals: {
      evidenceTotal: 1243,
      reliablePct: 68,
      pendingReview: 156,
      majorBlockers: 5,
    },
    taskQueue: [
      {
        id: "t-1",
        category: "待审核",
        title: "英伟达 GB200 NVL72 方案解析",
        source: "NVIDIA 官网",
        updatedAt: "10:15",
        assignee: "陈子仪",
      },
      {
        id: "t-2",
        category: "待审核",
        title: "台积电 CoWoS 产能指引更新",
        source: "工商时报",
        updatedAt: "09:42",
        assignee: "陈子仪",
      },
      {
        id: "t-3",
        category: "待审核",
        title: "中国电信液冷服务器集采公告",
        source: "中国电信采购",
        updatedAt: "昨天",
        assignee: "陈子仪",
      },
      {
        id: "t-4",
        category: "进行中",
        title: "海外云厂商开支跟踪（Q2）",
        source: "公告 · 财报",
        updatedAt: "进行中",
        assignee: "陈子仪",
      },
      {
        id: "t-5",
        category: "进行中",
        title: "国内智算中心项目跟踪",
        source: "各地政府官网",
        updatedAt: "进行中",
        assignee: "陈子仪",
      },
      {
        id: "t-6",
        category: "等待中",
        title: "HBM 供应链与国产化进展",
        source: "专家访谈",
        updatedAt: "等待中",
        assignee: "陈子仪",
      },
      {
        id: "t-7",
        category: "主要阻塞",
        title: "高端 GPU 出口管制影响评估",
        source: "BIS · OFAC",
        updatedAt: "昨天",
        assignee: "陈子仪",
      },
    ],
    evidenceChanges: [
      {
        id: "ec-1",
        caseTitle: "AI 算力链",
        description: "英伟达 GB200 NVL72 方案",
        source: "NVIDIA",
        kind: "行业数据",
        updatedAt: "10:15",
      },
      {
        id: "ec-2",
        caseTitle: "AI 算力链",
        description: "台积电 CoWoS 产能指引",
        source: "工商时报",
        kind: "更新数据",
        updatedAt: "09:42",
      },
      {
        id: "ec-3",
        caseTitle: "AI 算力链",
        description: "中国电信液冷服务器集采公告",
        source: "中国电信",
        kind: "公告发布",
        updatedAt: "昨天",
      },
      {
        id: "ec-4",
        caseTitle: "AI 算力链",
        description: "美国调整高端 GPU 出口管制",
        source: "BIS",
        kind: "监管变化",
        updatedAt: "昨天",
      },
      {
        id: "ec-5",
        caseTitle: "AI 算力链",
        description: "工信部：加快液冷关键技术研发",
        source: "工信部",
        kind: "行业数据",
        updatedAt: "昨天",
      },
      {
        id: "ec-6",
        caseTitle: "AI 算力链",
        description: "AMD MI300X 发布与供应链",
        source: "AMD",
        kind: "公告发布",
        updatedAt: "05-22",
      },
      {
        id: "ec-7",
        caseTitle: "AI 算力链",
        description: "英特尔 Gaudi3 进展跟踪",
        source: "Intel",
        kind: "行业数据",
        updatedAt: "05-21",
      },
    ],
    activity: [
      {
        id: "a-1",
        actor: "张瑞琦",
        verb: "完成了核心观点",
        target: "推理需求",
        occurredAt: "10:24",
        group: "今天",
      },
      {
        id: "a-2",
        actor: "你",
        verb: "标注了证据可靠性",
        target: "GB200 NVL72 方案",
        occurredAt: "09:56",
        group: "今天",
      },
      {
        id: "a-3",
        actor: "陈昊",
        verb: "添加了证据",
        target: "台积电产能指引",
        occurredAt: "09:42",
        group: "今天",
      },
      {
        id: "a-4",
        actor: "系统",
        verb: "完成数据更新",
        target: "全量研究数据",
        occurredAt: "09:30",
        group: "今天",
      },
      {
        id: "a-5",
        actor: "王铭",
        verb: "评论了图表",
        target: "全球算力需求",
        occurredAt: "17:15",
        group: "昨天",
      },
      {
        id: "a-6",
        actor: "李想",
        verb: "更新了风险提示",
        target: "全球算力需求",
        occurredAt: "16:40",
        group: "昨天",
      },
      {
        id: "a-7",
        actor: "系统",
        verb: "任务状态变更：2 → 5",
        target: "今天",
        occurredAt: "15:33",
        group: "昨天",
      },
      {
        id: "a-8",
        actor: "赵显",
        verb: "创建了项目",
        target: "AI 算力链",
        occurredAt: "05-20",
        group: "更早",
      },
      {
        id: "a-9",
        actor: "系统",
        verb: "证据库更新 128 条",
        target: "今天",
        occurredAt: "05-20",
        group: "更早",
      },
    ],
  };
}
// ── Theme (主题) ────────────────────────────────────────────────────────
// 设计文档 §3：Theme 是「我相信的这件事」的锚点，下面挂证据（Claim）/
// 穿透（Stock / Fund）。本 fixture 提供两个互相对照的主题样例，主题 1
// 是「AI 算力链」演示「主题 → 证据 → 股票 / 基金穿透」完整闭环。

export function buildThemeIndexView(): ThemeIndexView {
  return {
    themes: [
      {
        id: "ai-compute",
        name: "AI 算力链",
        industry: "半导体 / 信息技术",
        hypothesis: "AI 算力需求未来 3 年 CAGR ≥ 45%，上游高景气可验证",
        status: "validating",
        statusLabel: "持续验证",
        claimCount: 32,
        conflictCount: 5,
        lastUpdatedAt: "2025-06-30",
      },
      {
        id: "city-noa",
        name: "城市 NOA 商业化落地",
        industry: "汽车 / 智能驾驶",
        hypothesis: "2025 是城市 NOA 商业化关键拐点，2026 进入规模化交付期",
        status: "validating",
        statusLabel: "持续验证",
        claimCount: 28,
        conflictCount: 3,
        lastUpdatedAt: "2025-06-30",
      },
      {
        id: "hbm-supply",
        name: "HBM 国产化突围",
        industry: "半导体",
        hypothesis: "HBM 国产替代可在 2026 进入小批量交付",
        status: "monitoring",
        statusLabel: "监测中",
        claimCount: 14,
        conflictCount: 4,
        lastUpdatedAt: "2025-05-28",
      },
      {
        id: "robotaxi",
        name: "Robotaxi 商业化",
        industry: "汽车",
        hypothesis: "L4 Robotaxi 在限定区域可商业化，但全国铺开仍需 24 个月",
        status: "frozen",
        statusLabel: "已冻结 v3",
        claimCount: 19,
        conflictCount: 2,
        lastUpdatedAt: "2025-06-30",
      },
      {
        id: "us-export-curb",
        name: "高端 GPU 出口管制",
        industry: "宏观 / 政策",
        hypothesis: "出口管制细则反复扰动，会进一步压缩高端 GPU 国内可用量",
        status: "validating",
        statusLabel: "持续验证",
        claimCount: 11,
        conflictCount: 1,
        lastUpdatedAt: "2025-06-22",
      },
      {
        id: "liquid-cooling",
        name: "液冷渗透率",
        industry: "数据中心",
        hypothesis: "2025 液冷渗透率 25%，2026 突破 40%",
        status: "draft",
        statusLabel: "草稿",
        claimCount: 5,
        conflictCount: 0,
        lastUpdatedAt: "2025-04-15",
      },
    ],
    totals: {
      themes: 6,
      validating: 3,
      frozen: 1,
      conflictPairs: 8,
    },
    filters: {
      industries: [
        "半导体 / 信息技术",
        "半导体",
        "汽车",
        "汽车 / 智能驾驶",
        "宏观 / 政策",
        "数据中心",
      ],
      statuses: ["monitoring", "validating", "frozen", "draft"],
    },
  };
}

export function buildThemeWorkbenchView(
  themeId: string,
): ThemeWorkbenchView {
  if (themeId === "city-noa" || themeId === "RC-AIC-2025-01") {
    // 兼容旧路由 RC-AIC-2025-01 走城市 NOA demo
    return cityNoaTheme();
  }
  return aiComputeTheme();
}

function aiComputeTheme(): ThemeWorkbenchView {
  return {
    id: "ai-compute",
    name: "AI 算力链",
    industry: "半导体 / 信息技术",
    hypothesis: "AI 算力需求未来 3 年 CAGR ≥ 45%，上游高景气可验证",
    cutoff: "2025-06-30",
    snapshotId: "RS-2025-06-30-v3",
    status: "validating",
    statusLabel: "持续验证",
    hypothesisLinks: [
      {
        hypothesis: {
          id: "h-1",
          label: "需求侧：CAGR ≥ 45%",
          supportCount: 8,
          contradictCount: 1,
          status: "validated",
        },
        claims: [],
      },
      {
        hypothesis: {
          id: "h-2",
          label: "上游供给：高景气可承接",
          supportCount: 6,
          contradictCount: 3,
          status: "contested",
        },
        claims: [],
      },
      {
        hypothesis: {
          id: "h-3",
          label: "估值：当前未透支",
          supportCount: 2,
          contradictCount: 5,
          status: "unverified",
        },
        claims: [],
      },
    ],
    claims: [
      {
        id: "c-001",
        content: "我们预计未来三年算力需求 CAGR 达 45%",
        sentiment: "positive",
        confidence: 0.9,
        sourceLabel: "中信证券",
        documentTitle: "算力基础设施 2025 年中策略",
        documentType: "研报",
        publishedAt: "2025-07-01",
        snippet: "我们预计未来三年算力需求 CAGR 达 45%，结构性向推理端倾斜。",
        span: "正文第 3 段，字符 12-31",
        conflictsWith: ["c-007"],
        hypothesisIds: ["h-1"],
        isAiProposed: false,
      },
      {
        id: "c-007",
        content: "估值已透支，未来 12 个月收益空间有限",
        sentiment: "negative",
        confidence: 0.72,
        sourceLabel: "国信证券",
        documentTitle: "AI 算力链估值审视",
        documentType: "研报",
        publishedAt: "2025-07-05",
        snippet: "经 DCF 与可比 PE 双口径测算，未来 12 个月预期收益率 ≤ 5%。",
        span: "正文第 4 段，字符 8-32",
        conflictsWith: ["c-001"],
        hypothesisIds: ["h-3"],
        isAiProposed: false,
      },
      {
        id: "c-014",
        content: "数据中心业务收入同比增长 46%",
        sentiment: "positive",
        confidence: 0.95,
        sourceLabel: "NVIDIA",
        documentTitle: "FY2026 Q1 业绩说明会记录",
        documentType: "财报",
        publishedAt: "2025-05-28",
        snippet: "AI revenue, which came in at $4.4 billion, accelerated to 46% growth year-on-year.",
        span: "prepared remarks, pp. 4-5",
        hypothesisIds: ["h-1", "h-2"],
        isAiProposed: false,
      },
      {
        id: "c-022",
        content: "CoWoS 产能指引上调 30%，主要被锁定",
        sentiment: "positive",
        confidence: 0.85,
        sourceLabel: "工商时报",
        documentTitle: "台积电法说会要点",
        documentType: "新闻",
        publishedAt: "2025-05-23",
        snippet: "台积电上调 2024 年 CoWoS 产能指引，全年产能同比增长约 30%。",
        span: "头条",
        hypothesisIds: ["h-2"],
        isAiProposed: false,
      },
      {
        id: "c-030",
        content: "高端 GPU 出口许可积压",
        sentiment: "negative",
        confidence: 0.78,
        sourceLabel: "海关总署",
        documentTitle: "高端 GPU 出口许可清单",
        documentType: "公告",
        publishedAt: "2025-05-22",
        snippet: "申请出现积压，部分订单交付节奏被推迟。",
        span: "第 2 节",
        conflictsWith: ["c-022"],
        hypothesisIds: ["h-2", "h-3"],
        isAiProposed: true,
      },
    ],
    stocks: [
      {
        code: "688256.SH",
        name: "寒武纪",
        industry: "AI 芯片",
        pe: 312.5,
        pb: 14.2,
        roe: 4.5,
        marketCap: "¥ 2850 亿",
        valuationUpdatedAt: "2025-06-30",
        exposure: 0.92,
      },
      {
        code: "601138.SH",
        name: "工业富联",
        industry: "服务器代工",
        pe: 25.8,
        pb: 3.4,
        roe: 18.6,
        marketCap: "¥ 5120 亿",
        valuationUpdatedAt: "2025-06-30",
        exposure: 0.78,
      },
      {
        code: "TSM.US",
        name: "台积电",
        industry: "晶圆代工",
        pe: 28.6,
        pb: 6.2,
        roe: 24.8,
        marketCap: "US$ 9200 亿",
        valuationUpdatedAt: "2025-06-30",
        exposure: 0.71,
      },
      {
        code: "NVDA.US",
        name: "英伟达",
        industry: "AI 芯片",
        pe: 64.2,
        pb: 52.6,
        roe: 92.3,
        marketCap: "US$ 3.2 万亿",
        valuationUpdatedAt: "2025-06-30",
        exposure: 0.95,
      },
    ],
    funds: [
      {
        code: "F-AI-001",
        name: "某 AI 主题精选基金",
        scale: "¥ 86 亿",
        themeExposure: 0.082,
        topHoldings: [
          { code: "688256.SH", name: "寒武纪", weight: 0.092 },
          { code: "601138.SH", name: "工业富联", weight: 0.078 },
          { code: "TSM.US", name: "台积电", weight: 0.061 },
        ],
      },
      {
        code: "F-GROWTH-005",
        name: "某成长混合基金",
        scale: "¥ 240 亿",
        themeExposure: 0.051,
        topHoldings: [
          { code: "NVDA.US", name: "英伟达", weight: 0.044 },
          { code: "TSM.US", name: "台积电", weight: 0.029 },
        ],
      },
      {
        code: "F-INDEX-002",
        name: "中证 AI 指数 ETF",
        scale: "¥ 32 亿",
        themeExposure: 0.95,
        topHoldings: [
          { code: "688256.SH", name: "寒武纪", weight: 0.12 },
          { code: "300033.SH", name: "同花顺", weight: 0.085 },
        ],
      },
    ],
    chain: [
      { code: "TSM.US", name: "台积电", relation: "supplies", side: "upstream" },
      { code: "601138.SH", name: "工业富联", relation: "supplies", side: "upstream" },
      { code: "NVDA.US", name: "英伟达", relation: "competes", side: "competitor" },
      { code: "002230.SZ", name: "科大讯飞", relation: "customer", side: "downstream" },
      { code: "000977.SZ", name: "浪潮信息", relation: "customer", side: "downstream" },
    ],
    counterResearch: [
      {
        id: "counter-ai-capex",
        thesis_id: "h-2",
        thesis_statement: "上游供给：高景气可承接",
        assessment_id: null,
        objective: "验证 CoWoS 扩产是否足以覆盖需求",
        status: "已有反方证据",
        contradicts_count: 3,
        next_action: "补充主要厂商产能与交付周期数据",
      },
    ],
    conflictCount: 5,
  };
}

function cityNoaTheme(): ThemeWorkbenchView {
  return {
    id: "city-noa",
    name: "城市 NOA 商业化落地",
    industry: "汽车 / 智能驾驶",
    hypothesis: "2025 是城市 NOA 商业化关键拐点，2026 进入规模化交付期",
    cutoff: "2025-06-30",
    snapshotId: "RS-2025-06-30-v3",
    status: "validating",
    statusLabel: "持续验证",
    hypothesisLinks: [
      {
        hypothesis: {
          id: "h-1",
          label: "政策与路权已合规",
          supportCount: 6,
          contradictCount: 0,
          status: "validated",
        },
        claims: [],
      },
      {
        hypothesis: {
          id: "h-2",
          label: "技术方案收敛到纯视觉 + 端到端",
          supportCount: 4,
          contradictCount: 2,
          status: "contested",
        },
        claims: [],
      },
      {
        hypothesis: {
          id: "h-3",
          label: "成本结构能支撑商业化",
          supportCount: 3,
          contradictCount: 2,
          status: "unverified",
        },
        claims: [],
      },
    ],
    claims: [
      {
        id: "cn-001",
        content: "工信部：开展智能网联汽车准入试点",
        sentiment: "positive",
        confidence: 0.92,
        sourceLabel: "工信部",
        documentTitle: "智能网联汽车准入试点通知",
        documentType: "公告",
        publishedAt: "2025-04-12",
        snippet: "支持 L3 级及以上自动驾驶功能的智能网联汽车产品开展准入试点。",
        span: "三、工作要求 第 2 段",
        hypothesisIds: ["h-1"],
        isAiProposed: false,
      },
      {
        id: "cn-002",
        content: "北京新增智能网联汽车开放道路",
        sentiment: "positive",
        confidence: 0.87,
        sourceLabel: "北京交管局",
        documentTitle: "新增开放道路通告",
        documentType: "公告",
        publishedAt: "2025-04-08",
        snippet: "新增开放道路 6000 公里，覆盖亦庄、通州等区域。",
        span: "通告正文",
        hypothesisIds: ["h-1"],
        isAiProposed: false,
      },
      {
        id: "cn-003",
        content: "小鹏 XOS 5.2.0 全国推送城市 NOA",
        sentiment: "positive",
        confidence: 0.85,
        sourceLabel: "小鹏汽车",
        documentTitle: "XOS 5.2.0 发布说明",
        documentType: "新闻",
        publishedAt: "2025-04-30",
        snippet: "基于端到端大模型，已实现不限城市高阶智驾的城市 NOA 全国范围开放。",
        span: "技术白皮书",
        hypothesisIds: ["h-2"],
        isAiProposed: false,
      },
      {
        id: "cn-004",
        content: "特斯拉 FSD 入华未获批",
        sentiment: "negative",
        confidence: 0.78,
        sourceLabel: "路透社",
        documentTitle: "FSD 入华进展追踪",
        documentType: "新闻",
        publishedAt: "2025-05-10",
        snippet: "FSD 仍处于审批流程中，预计 6 个月内不会商业化。",
        span: "正文",
        conflictsWith: ["cn-001"],
        hypothesisIds: ["h-1"],
        isAiProposed: true,
      },
    ],
    stocks: [
      {
        code: "9868.HK",
        name: "小鹏汽车",
        industry: "整车",
        pe: -42.3,
        pb: 3.8,
        roe: -12.5,
        marketCap: "HK$ 1280 亿",
        valuationUpdatedAt: "2025-06-30",
        exposure: 0.88,
      },
      {
        code: "002230.SZ",
        name: "科大讯飞",
        industry: "语音 / 智驾",
        pe: 156.2,
        pb: 7.5,
        roe: 5.2,
        marketCap: "¥ 1280 亿",
        valuationUpdatedAt: "2025-06-30",
        exposure: 0.62,
      },
    ],
    funds: [
      {
        code: "F-NEW-008",
        name: "新能源车主题精选",
        scale: "¥ 48 亿",
        themeExposure: 0.34,
        topHoldings: [
          { code: "9868.HK", name: "小鹏汽车", weight: 0.085 },
          { code: "002230.SZ", name: "科大讯飞", weight: 0.052 },
        ],
      },
    ],
    chain: [
      { code: "002230.SZ", name: "科大讯飞", relation: "supplies", side: "upstream" },
      { code: "9868.HK", name: "小鹏汽车", relation: "customer", side: "downstream" },
      { code: "601633.SH", name: "长城汽车", relation: "competes", side: "competitor" },
    ],
    counterResearch: [
      {
        id: "counter-noa-capex",
        thesis_id: "h-2",
        thesis_statement: "技术方案收敛到纯视觉 + 端到端",
        assessment_id: null,
        objective: "验证端到端方案在复杂城市路况的失效率",
        status: "待发起",
        contradicts_count: 0,
        next_action: "寻找独立道路测试与事故样本",
      },
    ],
    conflictCount: 3,
  };
}
