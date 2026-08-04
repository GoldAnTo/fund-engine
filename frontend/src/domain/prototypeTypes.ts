// Prototype view models: shapes returned by the mock adapter that back each
// of the prototype screens. Kept separate from domain/types.ts so that the
// existing pages keep working unchanged while we add the new screens.
//
// All data is deterministic — backed by the prototype/ui/data.js fixture
// (RC-AIC-2025-01, snapshot RS-2025-06-30-v3, cutoff 2025-06-30).

// ── Overview screen (research workspace, mirrors prototype 设计原型1) ────
//
// This is a different case than the AIC research case. The fixture mirrors
// `mockResearchAdapter.OVERVIEW` and the prototype/ui/app.js screen which
// renders "AI 算力链" with research-framework tabs, key changes, evidence
// changes and activity streams.

export interface OverviewTab {
  id: string;
  label: string;
  count: number;
  active?: boolean;
}

export interface OverviewKeyChange {
  id: string;
  tag: "新增" | "更新" | "风险";
  text: string;
  detail: string;
  occurredAt: string;
  sourceLabel: string;
}

export interface OverviewFrameworkNode {
  id: string;
  sequence: string;
  title: string;
  description: string;
  expanded: boolean;
  children: { id: string; sequence: string; title: string }[];
}

export interface OverviewTask {
  id: string;
  category: "待审核" | "进行中" | "等待中" | "主要阻塞";
  title: string;
  source: string;
  updatedAt: string;
  assignee: string;
}

export interface OverviewEvidenceChange {
  id: string;
  caseTitle: string;
  description: string;
  source: string;
  kind: string;
  updatedAt: string;
}

export interface OverviewActivity {
  id: string;
  actor: string;
  verb: string;
  target: string;
  occurredAt: string;
  group: "今天" | "昨天" | "更早";
}

export interface OverviewTotals {
  evidenceTotal: number;
  reliablePct: number | null;
  pendingReview: number;
  majorBlockers: number;
}

export interface WorkspaceOverviewScreen {
  caseId: string;
  caseTitle: string;
  caseTopic: string;
  caseTopicTags: string[];
  lastUpdatedAt: string;
  caseCountLabel: string;
  tabs: OverviewTab[];
  bullets: string[];
  keyChanges: OverviewKeyChange[];
  framework: OverviewFrameworkNode[];
  totals: OverviewTotals;
  taskQueue: OverviewTask[];
  evidenceChanges: OverviewEvidenceChange[];
  activity: OverviewActivity[];
}

export interface OverviewQueueItem {
  id: string;
  caseId: string;
  caseTitle: string;
  caseQuestion: string;
  cutoff: string;
  snapshotId: string;
  caseState: string;
  caseStateLabel: string;
  aiLabel: string;
  provisionalAssessment: string;
  primaryBlocker: {
    id: string;
    label: string;
    task: string;
    targetId: string;
    sourceId: string;
    sourceVersion: string;
    reviewStatusLabel: string;
    actionLabel: string;
    actionRoute: string;
  };
}

export interface OverviewContradiction {
  id: string;
  label: string;
  stateLabel: string;
}

export interface OverviewMetric {
  id: string;
  displayName: string;
  value: string;
  period: string;
  sourceVersion: string;
  gapLabel: string;
}

export interface OverviewProvider {
  id: string;
  displayName: string;
  outcomeLabel: string;
  detailLabel: string;
}

export interface OverviewSnapshot {
  id: string;
  label: string;
  cutoff: string;
  frozenAt: string;
}

export interface WorkspaceOverviewView {
  case: {
    id: string;
    title: string;
    question: string;
    cutoff: string;
    snapshotId: string;
    state: string;
    stateLabel: string;
    aiLabel: string;
    provisionalAssessment: string;
  };
  workItem: OverviewQueueItem["primaryBlocker"];
  contradiction: OverviewContradiction;
  metric: OverviewMetric;
  providers: OverviewProvider[];
  recentSnapshot: OverviewSnapshot;
}

// ── New-research draft state ──────────────────────────────────────────────

export const DRAFT_FIELDS = [
  "title",
  "statement",
  "observationStart",
  "observationEnd",
  "nextValidationEvent",
  "supportCondition",
  "falsifier",
] as const;

export type DraftField = (typeof DRAFT_FIELDS)[number];

export const FIELD_LIMITS: Record<DraftField, number> = {
  title: 120,
  statement: 2000,
  nextValidationEvent: 2000,
  supportCondition: 2000,
  falsifier: 2000,
  observationStart: 10,
  observationEnd: 10,
};

export type DraftOrigin = "ai" | "human";
export type LastEditedBy = "ai" | "human" | "system";
export type EvidenceReviewState =
  | "reviewed_links_present"
  | "pending_relationship_review"
  | "no_evidence_links";

export interface ThesisDraft {
  id: string;
  origin: DraftOrigin;
  title: string;
  statement: string;
  observationStart: string;
  observationEnd: string;
  supportCondition: string;
  falsifier: string;
  nextValidationEvent: string;
  lastEditedBy?: LastEditedBy;
}

export interface ConfirmedDraftRecord {
  schemaVersion: 2;
  caseId: string;
  snapshotId: string;
  cutoff: string;
  researchPlanRevision: string;
  confirmationState: "confirmed";
  theses: ThesisDraft[];
}

export interface NewResearchAssetSummary {
  documentCount: number;
  statementCount: number;
  metricCount: number;
  reviewedLinkCount: number;
  relatedCaseIds: string[];
}

export interface PlannedProviderQuery {
  id: string;
  provider: string;
  providerLabel: string;
  capability: string;
  capabilityLabel: string;
  mode: string;
  modeLabel: string;
  status: string;
  statusLabel: string;
  purpose: string;
  dateScope: { start: string; end: string };
  cutoff: string;
  intendedArtifact: string;
}

export interface PlannedMetric {
  id: string;
  name: string;
  value: string;
  period: string;
}

export interface PlannedGapFactor {
  id: string;
  label: string;
}

export interface NewResearchView {
  caseId: string;
  caseTitle: string;
  caseQuestion: string;
  researchObject: string;
  phenomenon: string;
  researchPeriod: { start: string; end: string };
  studyRange: string;
  cutoff: string;
  snapshotId: string;
  theses: ThesisDraft[];
  confirmedTheses: ThesisDraft[];
  activeStep: 2 | 3;
  stageStatus: string;
  assets: NewResearchAssetSummary;
  plan: {
    providerQueries: PlannedProviderQuery[];
    positiveEvidenceSearches: { id: string; label: string; scope: string }[];
    negativeEvidenceSearches: { id: string; label: string; scope: string }[];
    resultMetrics: PlannedMetric[];
    gaps: PlannedGapFactor[];
  };
}

// ── Research plan view ───────────────────────────────────────────────────

export interface PlanAsset {
  id: string;
  kind: "document" | "statement" | "metric" | "evidence_link";
  label: string;
  metricName?: string;
  metricValue?: string;
  metricPeriod?: string;
  sourceVersion: string;
  sourceSpan: string;
  reviewState: "reviewed" | "pending_review";
  reviewCount: number;
  selected: boolean;
}

export interface PlanProviderQuery {
  id: string;
  provider: string;
  capability: string;
  purpose: string;
  dateScope: { start: string; end: string };
  cutoff: string;
  intendedArtifact: string;
  status: string;
  exposureStatus: string;
}

export interface PlanCollection {
  reused: { id: string; label: string; cutoff: string }[];
  awaitingProbe: { id: string; label: string; cutoff: string }[];
  blocked: { id: string; label: string; cutoff: string }[];
  running: { id: string; label: string; cutoff: string }[];
}

export interface PlanPendingResult {
  id: string;
  targetLabel: string;
  task: string;
  sourceId: string;
  sourceVersion: string;
  reviewLabel: string;
}

export interface PlanGap {
  id: string;
  label: string;
  scope: string;
  type: "factor" | "positive" | "negative";
}

export interface PlanProviderRun {
  id: string;
  provider: string;
  outcome: string;
  observedAt: string;
  detail: string;
  sourceVersion?: string;
}

export interface ResearchPlanView {
  case: {
    id: string;
    researchPeriod: string;
    cutoff: string;
    revision: string;
  };
  existingAssets: PlanAsset[];
  orderedAssets: PlanAsset[];
  assetPageSize: number;
  providerQueries: PlanProviderQuery[];
  collection: PlanCollection;
  pendingResults: PlanPendingResult[];
  gaps: PlanGap[];
  resultMetrics: PlannedMetric[];
  failures: PlanProviderRun[];
  permissionGaps: PlanProviderRun[];
  manualUploads: PlanProviderRun[];
}

// ── Case workbench view ──────────────────────────────────────────────────

export interface CaseWorkbenchFormalJudgment {
  text: string;
  rationale: string;
  reviewState: string;
  snapshotId: string;
  reviewedAt: string;
  /** 对应的 AIAssessment id；仅 HTTP 模式可用，用于评估复核写入。 */
  assessmentId?: string;
}

export interface CaseWorkbenchThesisRow {
  id: string;
  title: string;
  supportCondition: string;
  evidenceState: string;
  relationLabels: string;
  scope: string;
  falsifier: string;
  reviewState: string;
  evidenceReviewState: string;
  frozenEligibility: "reviewed" | "excluded";
  selected: boolean;
}

export interface CaseWorkbenchRebuttal {
  id: string;
  statement: string;
  documentId: string;
  documentTitle: string;
  sourceVersion: string;
  publishedDate: string;
  sourceSpan: string;
  reviewLabel: string;
  reviewState: string;
  relation: string;
  snapshotMembership: string;
  frozenEligibility: string;
}

export interface CaseWorkbenchFactorRow {
  factorId: string;
  groupLabel: string;
  roleLabel: string;
  statusLabel: string;
  label: string;
  timeOrder: string;
  mechanism: string;
  directEvidence: string;
  alternatives: string;
  differenceExplanation: string;
  scope: string;
  falsifier: string;
  counterexample: string;
  impactObject: string;
}

export interface CaseWorkbenchSourceRow {
  id: string;
  relation: string;
  relationLabel: string;
  statement: string;
  documentId: string;
  sourceVersion: string;
  publishedDate: string;
  sourceSpan: string;
  reviewState: string;
  reviewLabel: string;
  snapshotMembership: string;
  frozenEligibility: "reviewed" | "excluded";
}

export interface CaseWorkbenchView {
  case: {
    id: string;
    title: string;
    question: string;
    researchObject: string;
    researchPeriod: string;
    cutoff: string;
    snapshotId: string;
    aiState: string;
    humanReviewState: string;
  };
  tabs: string[];
  formalJudgment: CaseWorkbenchFormalJudgment;
  aiDraft: string;
  contradiction: { id: string; label: string };
  gap: { id: string; label: string; explanation: string };
  nextValidation: { thesisId: string; event: string };
  thesisRows: CaseWorkbenchThesisRow[];
  rebuttal: CaseWorkbenchRebuttal;
  factorRows: CaseWorkbenchFactorRow[];
  selectedFactor: CaseWorkbenchFactorRow;
  sources: CaseWorkbenchSourceRow[];
}

// ── Relationship graph view ──────────────────────────────────────────────

export interface GraphNodeView {
  id: string;
  layer: string;
  title: string;
  meta: string;
  kind: string;
  kindLabel: string;
  relation: string;
  review: string;
  sourceName: string;
  sourceSpan: string;
  sourceHref: string;
  attachment: string;
  publicationDate: string;
  asOf: string;
  scope: string;
  citations: string[];
  note?: string;
  actions?: boolean;
}

export interface GraphLayer {
  key: "evidence" | "thesis" | "causal" | "company" | "fund";
  label: string;
  nodes: GraphNodeView[];
}

export interface GraphEdgeView {
  id: string;
  /** 两端节点 id（方向以后端语义为准，展示层按层序左右排布）。 */
  source: string;
  target: string;
  /** 后端 semantic_kind：evidence / causal / contains_step / holding / … */
  kind: string;
  /** 关系中文标签，如 支持 / 反驳 / 因果 / 持仓。 */
  label: string;
  /** evidence 边的 properties.role：supports / contradicts / contextualizes。 */
  role?: string;
  /** 后端 review_state（如 reviewed）；空表示未经人工复核。 */
  reviewState?: string;
}

export interface RelationshipGraphView {
  case: { id: string; title: string; question: string; cutoff: string; snapshotId: string };
  layers: GraphLayer[];
  nodes: GraphNodeView[];
  edges: GraphEdgeView[];
  selectedNodeId: string;
  theses?: { id: string; title: string; statement: string }[];
}

// ── Library view ─────────────────────────────────────────────────────────

export interface LibraryDocument {
  id: string;
  title: string;
  sourceName: string;
  sourceVersion: string;
  documentType: string;
  entity: string;
  reuseCount: number;
  reviewState: "reviewed" | "pending_review";
  publishedLabel: string;
  availableLabel: string;
  acquiredLabel: string;
  previousVersion: string;
  linkedCaseIds: string[];
  reuseHistory: { caseId: string; label: string; reusedAt: string }[];
  sourceExcerpt: string;
  exactSpan: string;
  /**
   * 精确区段的人话翻译：HTTP 模式下从 span.locator 派生（如「第 35-39 页 · 资本开支段」）；
   * mock 模式直接等于 exactSpan。给研究员快速判断"这是哪一段"，不必读 JSON 也不必
   * 拼接原文范围。
   */
  humanSpan?: string;
  /** 当前版本已抽取的原文片段数；HTTP 模式来自 /documents，mock 模式由 fixture 注入。 */
  spanCount?: number;
  /** 当前版本已生成的陈述数；用于"抽取陈述"按钮解释段。 */
  statementCount?: number;
  /** 有待抽取片段但尚无陈述（span_count > 0 且 statement_count = 0）。 */
  pendingExtraction?: boolean;
}

export interface LibraryKnowledge {
  statement: { id: string; text: string };
  link: { id: string; role: string; reviewedBy?: string; reviewedAt?: string };
  roleLabel: string;
  thesis: { id: string; title: string } | null;
  factor: { id: string; label: string } | null;
  reviewedBy: string;
  reviewedAt: string;
}

export interface LibraryProposal {
  statement: { id: string; text: string };
  link: { id: string; role: string } | null;
  roleLabel: string;
}

export interface LibraryView {
  cutoff: string;
  snapshotId: string;
  documents: LibraryDocument[];
  selected: LibraryDocument;
  knowledge: LibraryKnowledge | null;
  proposal: LibraryProposal | null;
}

// ── Data center view ─────────────────────────────────────────────────────

export interface DataCatalogItem {
  id: string;
  label: string;
  entity: string;
  cadence: string;
  state: string;
  /** Live-selection keys for /metrics/series. */
  stockId: string;
  metricName: string;
}

export interface DataSeriesPoint {
  period: string;
  value: string;
  numericValue: number;
  acquiredAt: string;
  cutoffUsable: boolean;
  status: string;
}

export interface DataRevisionComparison {
  oldValue: string;
  oldSource: string;
  oldCutoffMeaning: string;
  newValue: string;
  newSource: string;
  newCutoffMeaning: string;
  whyItMatters: string;
}

export interface DataHistoricalRun {
  id: string;
  providerLabel: string;
  outcome: "success" | "quota_failure" | "permission_gap" | "manual_upload";
  outcomeLabel: string;
  detailLabel: string;
  observedAt: string;
}

export interface DataPlannedAttempt {
  id: string;
  label: string;
  state: string;
  meaning: string;
}

// ── Research-ops KPI block (GET /api/v1/research-ops/kpis) ────────────────

export interface ResearchOpsKpisView {
  asOf: string;
  throughput: {
    linkReviewsTotal: number;
    linkReviewsLast7d: number;
    assessmentReviewsTotal: number;
    assessmentReviewsLast7d: number;
    reviewsByReviewer: { reviewer: string; count: number }[];
    pendingLinkReviews: number;
    pendingAssessmentReviews: number;
  };
  agreement: {
    assessmentAgreementRate: number | null;
    assessmentOutcomes: { outcome: string; count: number }[];
    conclusionChanged: number;
    linkAgreementRate: number | null;
    linkModified: number;
    linkOutcomes: { outcome: string; count: number }[];
  };
  latency: {
    evidenceToAssessmentAvgDays: number | null;
    evidenceToAssessmentMaxDays: number | null;
    assessmentToReviewAvgDays: number | null;
    assessmentToReviewMaxDays: number | null;
  };
}

export interface DataCenterView {
  cutoff: string;
  snapshotId: string;
  researchOps: ResearchOpsKpisView;
  catalog: DataCatalogItem[];
  selectedMetricId: string;
  selectedMetric: {
    id: string;
    name: string;
    entity: string;
    value: string;
    unit: string;
    period: string;
    asOf: string;
    publishedAt: string;
    availableAt: string;
    acquiredAt: string;
    source: string;
    methodology: string;
    revision: string;
    providerRunId: string;
    failureMeaning: string;
  };
  series: DataSeriesPoint[];
  revisionComparison: DataRevisionComparison;
  plannedAttempt: DataPlannedAttempt;
  historicalRuns: DataHistoricalRun[];
}

// ── Versions view ────────────────────────────────────────────────────────

export interface VersionRecordRow {
  id: string;
  label: string;
  role?: string;
  reviewState?: string;
  state?: string;
  version?: string;
  kind?: string;
}

export interface VersionColumnContent {
  formalConclusion: { state: string; text: string };
  inputs: VersionRecordRow[];
  relationships: VersionRecordRow[];
  factors: VersionRecordRow[];
  gaps: VersionRecordRow[];
}

export interface VersionChangeRail {
  inputSummary: string;
  relationshipSummary: string;
  factorSummary: string;
  conclusionSummary: string;
  gapSummary: string;
  rationale: string;
  reviewedBy: string;
  reviewedAt: string;
}

export interface VersionsView {
  case: { id: string; title: string };
  focusThesisId: string;
  beforeSnapshot: { id: string; cutoff: string; freezeTime: string };
  afterSnapshot: { id: string; cutoff: string; freezeTime: string };
  before: VersionColumnContent;
  after: VersionColumnContent;
  changeRail: VersionChangeRail;
  /** 每条命题的 before→after 变化总览：结论、缺口、关系数。供版本页
   *  "多命题概览" 表格使用，避免只看到 focus 命题而忽略其他命题的进展。 */
  perThesisChanges: ThesisVersionChange[];
  /** 案例可用的全部快照 cutoff（按时间升序），供 base/compare 下拉使用。 */
  availableCutoffs: string[];
  /** 案例的完整快照元数据（按时间升序），供时间轴渲染使用。
   *  linkCount 是该快照点处已建立的证据关系数，节点大小可据此变化。 */
  snapshotPoints: SnapshotPoint[];
  aiProposal: {
    runId: string;
    observedAt: string;
    label: string;
    text: string;
    boundary: string;
  };
}

export interface ThesisVersionChange {
  thesisId: string;
  statement: string;
  conclusionBefore: string | null;
  conclusionAfter: string | null;
  gapsBeforeCount: number;
  gapsAfterCount: number;
  addedLinks: number;
  removedLinks: number;
}

export interface SnapshotPoint {
  /** 快照 ID（短） */
  id: string;
  /** ISO 时间戳；与 VersionsView.before/afterSnapshot.cutoff 一致 */
  cutoff: string;
  /** 该快照点处该案例下所有证据关系（已审核 + AI 提议）的合计。 */
  linkCount: number;
  /** 相对前一个 cutoff 的事件概要（仅在第一个 cutoff 上为 null）。 */
  eventSummary: PlaybackEvent | null;
}

export interface PlaybackEvent {
  /** 相对前一个 cutoff 新增的证据关系数。 */
  linkDelta: number;
  /** 相对前一个 cutoff 移除的证据关系数。 */
  removedLinkDelta: number;
  /** 结论在该 cutoff 发生变化的命题列表。 */
  conclusionFlips: Array<{
    thesisId: string;
    from: string;
    to: string;
    statement: string;
  }>;
  /** 命题缺口数变化（key=thesisId, value=delta；负=缺口收敛）。 */
  gapsDelta: Record<string, number>;
  /** 该 cutoff 新增的人工复核记录数。 */
  reviewedDelta: number;
}

// ── Theme (主题) ─ 一等公民 ───────────────────────────────────────────
//
// 设计文档 §3 数据模型：Theme 是用户「我相信的这件事」的锚点，所有证据
// （Claim）/ 穿透（Stock / Fund）都挂在主题下。本节视图模型用于驱动
// 主题列表 / 主题详情 / 主题工作台，与旧的 ResearchCaseDossierPage 解耦。

export type ThemeStatus = "monitoring" | "validating" | "frozen" | "draft";

export interface ThemeHypothesis {
  id: string;
  label: string;
  supportCount: number;
  contradictCount: number;
  status: "validated" | "contested" | "unverified";
}

export interface ThemeClaim {
  id: string;
  content: string;
  sentiment: "positive" | "negative" | "neutral";
  confidence: number;
  sourceLabel: string;
  documentTitle: string;
  documentType: "公告" | "研报" | "财报" | "政策" | "新闻";
  publishedAt: string;
  snippet: string;
  span: string;
  conflictsWith?: string[];
  hypothesisIds: string[];
  isAiProposed: boolean;
}

export interface ThemeStock {
  code: string;
  name: string;
  industry: string;
  pe: number;
  pb: number;
  roe: number;
  marketCap: string;
  valuationUpdatedAt: string;
  exposure: number;
}

export interface ThemeFund {
  code: string;
  name: string;
  scale: string;
  themeExposure: number;
  topHoldings: { code: string; name: string; weight: number }[];
}

export interface ThemeChainLink {
  code: string;
  name: string;
  relation: "supplies" | "competes" | "customer";
  side: "upstream" | "downstream" | "competitor";
}

export interface ThemeHypothesisLink {
  hypothesis: ThemeHypothesis;
  claims: ThemeClaim[];
}

export interface ThemeIndexEntry {
  id: string;
  name: string;
  industry: string;
  hypothesis: string;
  status: ThemeStatus;
  statusLabel: string;
  claimCount: number;
  conflictCount: number;
  lastUpdatedAt: string;
}

export interface ThemeIndexView {
  themes: ThemeIndexEntry[];
  totals: {
    themes: number;
    validating: number;
    frozen: number;
    conflictPairs: number;
  };
  filters: {
    industries: string[];
    statuses: ThemeStatus[];
  };
}

export interface ThemeWorkbenchView {
  id: string;
  name: string;
  industry: string;
  hypothesis: string;
  cutoff: string;
  snapshotId: string;
  status: ThemeStatus;
  statusLabel: string;
  hypothesisLinks: ThemeHypothesisLink[];
  claims: ThemeClaim[];
  stocks: ThemeStock[];
  funds: ThemeFund[];
  chain: ThemeChainLink[];
  conflictCount: number;
}

// ── Adapter extensions ───────────────────────────────────────────────────

import type { ResearchClient as BaseResearchClient } from "./types";

/** Payload for POST /research-cases (screen 2 · 新建研究 submit). */
export interface CreateCaseInput {
  title: string;
  industryTopic: string;
  createdBy: string;
  researchObject?: string;
  phenomenon?: string;
  coreQuestion?: string;
  periodStart?: string;
  periodEnd?: string;
  theses: {
    statement: string;
    title?: string;
    observationStart?: string;
    observationEnd?: string;
    supportCondition?: string;
    falsificationCondition?: string;
    nextVerificationEvent?: string;
    creatorType: "human" | "ai";
  }[];
}

export interface CreateCaseResult {
  caseId: string;
  thesisIds: string[];
}

/** Lightweight case list row (screen 4 · 案例工作台 sidebar). */
export interface CaseSummaryItem {
  id: string;
  title: string;
  topic: string;
  updatedAt: string;
}

// ── 「结论与关键因素」页面 (屏幕 11 · 设计原型11) ────────────────────

export interface ConclusionHeader {
  researchCaseId: string;
  caseTitle: string;
  industryTopic: string;
  evidenceCutoff: string;
  conclusionText: string;
  conclusionStatus: "supported" | "contradicted" | "insufficient_evidence" | null;
  rationale: string;
  reviewState: string;
  reviewer: string | null;
  reviewedAt: string | null;
  snapshotId: string | null;
  aiProvisional: boolean;
}

export interface ConclusionKeyFactor {
  factorId: string;
  thesisId: string;
  thesisTitle: string;
  thesisStatement: string;
  statusLabel: string;
  roleLabel: string;
  factorLabel: string;
  timeOrder: string;
  mechanism: string;
  directEvidence: string;
  alternatives: string;
  differenceExplanation: string;
  scopeWarning: string | null;
  falsifier: string;
  impactObject: string;
}

export interface ConclusionComparisonCell {
  factorId: string;
  factorLabel: string;
  columnId: string;
  columnLabel: string;
  text: string;
}

export interface ConclusionComparisonRow {
  factorId: string;
  factorLabel: string;
  cells: ConclusionComparisonCell[];
}

export interface ConclusionComparisonTable {
  columns: string[];
  rows: ConclusionComparisonRow[];
}

export interface ConclusionSourceCitation {
  label: string;
  relation: "supports" | "contradicts" | "contextualizes";
  documentTitle: string;
  publisher: string | null;
  citation: string;
  locator: string;
}

export interface ConclusionSourceGroup {
  sectionLabel: string;
  relations: ConclusionSourceCitation[];
}

export interface ConclusionManifest {
  currentSelectionLabel: string;
  currentSelectionState: string;
  formalJudgment: string;
  researchSnapshot: string;
  documentVersion: string;
  publisherRecord: string;
  availableAt: string;
  reproducer: string;
  factorCompareVersion: string;
  recheckManifest: string;
}

export interface ConclusionCausalStep {
  sequence: number;
  description: string;
}

export interface ConclusionGapExplanation {
  factorId: string;
  factorLabel: string;
  why: string;
  applicableScope: string;
  category: string;
  dataPattern: string;
  categoryAlt: string;
  rationale: string;
}

export interface ConclusionView {
  basis: {
    cutoff: string;
    isHistorical: boolean;
    ledgerHighWatermark: string | null;
    projectionBuiltAt: string | null;
    projectionSchemaVersion: string | null;
  };
  header: ConclusionHeader;
  keyFactors: ConclusionKeyFactor[];
  comparison: ConclusionComparisonTable;
  sourceGroups: ConclusionSourceGroup[];
  reproductionManifest: ConclusionManifest;
  causalPath: ConclusionCausalStep[];
  gapExplanation: ConclusionGapExplanation;
}

export interface PrototypeClient {
  getWorkspaceOverviewView(): Promise<WorkspaceOverviewView>;
  getWorkspaceOverviewScreen(): Promise<WorkspaceOverviewScreen>;
  getNewResearchView(): Promise<NewResearchView>;
  createCase(input: CreateCaseInput): Promise<CreateCaseResult>;
  listCaseSummaries(): Promise<CaseSummaryItem[]>;
  getResearchPlanView(caseId?: string): Promise<ResearchPlanView>;
  getCaseWorkbenchView(
    caseId: string,
    options?: { thesisId?: string },
  ): Promise<CaseWorkbenchView>;
  getRelationshipGraphView(
    caseId: string,
    thesisId?: string,
  ): Promise<RelationshipGraphView>;
  getLibraryView(): Promise<LibraryView>;
  getDataCenterView(): Promise<DataCenterView>;
  getVersionsView(
    caseId?: string,
    options?: { base?: string; compare?: string },
  ): Promise<VersionsView>;
  getThemeIndexView(): Promise<ThemeIndexView>;
  getThemeWorkbenchView(themeId: string): Promise<ThemeWorkbenchView>;
  getConclusionView(
    caseId: string,
    options?: { cutoff?: string },
  ): Promise<ConclusionView>;
}

export type ResearchClient = BaseResearchClient &
  PrototypeClient &
  ReviewQueueClient &
  VersionsClient &
  EngineClient &
  DataCenterClient &
  CompanyThemeClient;
// ── Review queue (screen 6 · live API slice) ─────────────────────────────

/** One pending link-level review, mapped from ReviewQueueItemDTO. */
export interface ReviewQueueViewItem {
  linkId: string;
  thesisId: string;
  caseId: string;
  thesisStatement: string;
  /** AI-proposed relation: supports / contradicts / contextualizes. */
  aiRole: string;
  aiReason: string;
  aiScope: Record<string, unknown>;
  statementId: string;
  statementText: string;
  statementKind: string;
  verbatimText: string;
  documentVersionId: string;
  documentSourceUrl: string;
  documentPublishedAt: string | null;
  availableAt: string;
}

export interface ReviewQueueView {
  items: ReviewQueueViewItem[];
}

/** 四要素关系级审核 payload (LinkReviewRequest). */
export interface LinkReviewPayload {
  outcome: "confirmed" | "rejected" | "needs_more_evidence";
  relation: "supports" | "contradicts" | "contextualizes" | "evidence_gap" | null;
  factor_role: string;
  scope_boundary: string;
  reason: string;
  reviewer: string;
}

export interface ReviewQueueClient {
  getReviewQueueView(caseId?: string): Promise<ReviewQueueView>;
  submitLinkReview(linkId: string, payload: LinkReviewPayload): Promise<void>;
  reviewAssessment(
    assessmentId: string,
    payload: AssessmentReviewPayload,
  ): Promise<AssessmentReviewResult>;
}

/** Payload for POST /assessments/{id}/reviews (评估复核 · 人工决策). */
export interface AssessmentReviewPayload {
  outcome: "confirmed" | "modified" | "rejected";
  conclusion?: "supported" | "contradicted" | "insufficient_evidence";
  reason: string;
  reviewer: string;
}

export interface AssessmentReviewResult {
  id: string;
  outcome: string;
  reviewer: string;
  createdAt: string;
}

// ── Versions (screen 9 · live API slice) ─────────────────────────────────

/** Result of POST /theses/{id}/rerun (AI RERUN 监测与更新). */
export interface ThesisRerunResult {
  thesisId: string;
  mode: string;
  assessmentId: string;
  snapshotId: string;
  conclusion: string;
  rationale: string;
  gaps: string[];
  createdAt: string;
}

export interface VersionsClient {
  rerunThesis(thesisId: string): Promise<ThesisRerunResult>;
}

// ── Engine commands (propose · AI 提议证据关系) ──────────────────────────

/** Result of POST /theses/{id}/propose: proposed links enter the review queue. */
export interface ProposeEvidenceResult {
  thesisId: string;
  mode: string;
  linkCount: number;
}

export interface EngineClient {
  proposeEvidence(thesisId: string): Promise<ProposeEvidenceResult>;
  ingestDocuments(
    caseId?: string,
    extra?: { macroQueries?: string[] },
  ): Promise<IngestRunResult>;
  extractStatements(documentVersionId: string): Promise<ExtractStatementsResult>;
}

/** Result of POST /documents/{id}/extract (陈述抽取 · append-only). */
export interface ExtractStatementsResult {
  documentVersionId: string;
  mode: string;
  statementCount: number;
  /** 抽取为 0 时的如实原因（无片段 / LLM 未返回 / 合规受限等）。 */
  reason: string | null;
}

/** Result of POST /documents/ingest (数据接入 · Gildata). */
export interface IngestRunResult {
  researchReports: number;
  announcements: number;
  news: number;
  macroSeries: number;
  spans: number;
  valuationsWritten: number;
  valuationsSkipped: number;
  caseId: string | null;
}

// ── Data center (screen 8 · live API slice) ──────────────────────────────

/** Detail + series for one catalog metric, loaded on selection change. */
export interface DataMetricSelection {
  selectedMetric: DataCenterView["selectedMetric"];
  series: DataSeriesPoint[];
}

export interface DataCenterClient {
  getDataCenterMetric(
    stockId: string,
    metricName: string,
  ): Promise<DataMetricSelection>;
}

// ── 公司研究（/companies · live API slice）─────────────────────────────────
//
// CompanyDossierView 是后端 CompanyDossierResponse 的领域映射：以公司
// 为入口的逆向读视图。AI 判断与人工复核分离承载（aiConclusion/aiProvisional
// vs review*），页面用与案例页相同的双编码语义渲染，不合成公司级结论。

export interface CompanyListItem {
  id: string;
  code: string;
  name: string;
  type: string;
  stockCount: number;
  themeRoleCount: number;
  latestReportPeriod: string | null;
}

export interface CompanyListView {
  items: CompanyListItem[];
  hasMore: boolean;
  nextCursor: string | null;
}

export interface CompanyStockView {
  id: string;
  code: string;
  name: string;
  market: string;
}

export interface CompanyThemeRoleView {
  id: string;
  caseId: string | null;
  caseTitle: string | null;
  role: string;
  scope: Record<string, unknown>;
  applicableFrom: string | null;
  applicableTo: string | null;
  statementId: string | null;
  statementText: string | null;
  spanId: string | null;
  documentVersionId: string | null;
  // 设计图 10 主题角色卡片的"传导描述"（如"资本开支向设备交付的传导"）
  transmission?: string;
  // 设计图 10 关联命题表的"来源案例"行（如"RC-CAPEX-2025-02"）
  sourceCaseId?: string;
  // 状态文案（"已复核" / "待补证据" / "AI 提议·待复核"）
  statusLabel?: string;
  statusVariant?: "reviewed" | "warning" | "ai" | "support" | "contradict" | "draft";
}

export interface CompanyThesisJudgment {
  thesisId: string;
  caseId: string;
  caseTitle: string;
  statement: string;
  title: string | null;
  aiConclusion: string | null;
  aiProvisional: boolean;
  assessedAt: string | null;
  reviewOutcome: string | null;
  reviewConclusion: string | null;
  reviewReason: string | null;
  reviewer: string | null;
  reviewedAt: string | null;
}

export interface CompanyValuationView {
  stockId: string;
  stockCode: string;
  metricName: string;
  metricValue: number;
  asOfDate: string;
  source: string;
  definition: string;
}

export interface CompanyFundHolderView {
  fundId: string;
  fundCode: string;
  fundName: string;
  stockId: string;
  stockCode: string;
  weight: number;
  reportPeriod: string;
  publishedAt: string | null;
  acquiredAt: string | null;
  source: string;
}

export interface CompanyDossierView {
  cutoff: string;
  isHistorical: boolean;
  company: {
    id: string;
    code: string;
    name: string;
    type: string;
    createdAt: string | null;
    // 设计图 10 公司身份卡：市场 / 上市标签 / 最近披露期
    market?: string;
    listedLabel?: string;
    reportPeriod?: string;
    reportNote?: string;
  };
  stocks: CompanyStockView[];
  themeRoles: CompanyThemeRoleView[];
  relatedTheses: CompanyThesisJudgment[];
  valuations: CompanyValuationView[];
  fundHolders: CompanyFundHolderView[];
  // 关系路径：设计图 10 底部 5 节点链（冻结证据→命题→公司角色→股票→基金）
  pathNodes?: TopicPathNode[];
  // 右栏检查器锁定的关联命题
  pinnedThesisId?: string | null;
}

// ── 主题研究（/topics · 横切主题 live API slice）───────────────────────────
//
// TopicView 是后端 ThemeViewResponse 的领域映射：跨案例聚合投影。为避免
// 与案例中心「主题」（ThemeIndexView / ThemeWorkbenchView）混淆，横切
// 主题一律使用 Topic 前缀。thesisCounts 是案例层有效判断的分桶计数，
// 不构成主题级总结论；derivedFrom 携带全部聚合来源引用。

export interface TopicListItem {
  tag: string;
  caseCount: number;
  companyCount: number;
  thesisCount: number;
}

export interface TopicEvidenceSummary {
  linkId: string;
  role: "supports" | "contradicts" | "contextualizes";
  statement: string;
  sourceUrl: string | null;
  locator: Record<string, unknown>;
  reviewState: string;
  scope: Record<string, unknown>;
}

export interface TopicThesisView {
  thesisId: string;
  statement: string;
  title: string | null;
  evidenceCounts?: Record<string, number>;
  evidence?: TopicEvidenceSummary[];
  aiConclusion: string | null;
  aiProvisional: boolean;
  assessedAt: string | null;
  reviewOutcome: string | null;
  reviewConclusion: string | null;
  reviewReason: string | null;
  reviewer: string | null;
  reviewedAt: string | null;
}

export interface TopicCaseView {
  caseId: string;
  caseTitle: string;
  thesisCounts: Record<string, number>;
  theses: TopicThesisView[];
  // 设计图 9 的"ResearchCase 卡片"内容：2-3 句结论 + 主要反证/下一事件 bullet
  summary?: string;
  rebuttalBullet?: string;
  nextEventBullet?: string;
  // 案例级状态徽章文案（设计图 9 卡片标题正下方）
  statusLabel?: string;
  statusVariant?: "support" | "contradict" | "warning" | "ai" | "draft";
}

export interface TopicCompanyRoleView {
  companyId: string;
  companyCode: string;
  companyName: string;
  caseId: string | null;
  caseTitle: string | null;
  role: string;
  scope: Record<string, unknown>;
  applicableFrom: string | null;
  applicableTo: string | null;
  statementId: string | null;
  // 设计图 9/10 表格"关联命题"列的传导描述（如"资本开支→设备交付"）
  transmission?: string;
  // 表格"证据状态"列文案（"已复核支持" / "待补证据" / "AI 提议·待复核"）
  statusLabel?: string;
  statusVariant?: "reviewed" | "warning" | "ai" | "support" | "contradict" | "draft";
  // 表格"适用范围"列（如"2025H1·全球云商" / "CoWoS·2025-2026"）
  applicableScope?: string;
}

export interface TopicExposurePosition {
  fundId: string;
  fundCode: string;
  fundName: string;
  stockId: string;
  stockCode: string;
  stockName: string;
  weight: number;
  reportPeriod: string;
  source: string;
}

// 主题关系路径：5 节点链，固定证据→命题→公司角色→股票映射→基金披露。
// 每个节点都是"可点击回链"——左栏主题目录选中主题后，主区底部展示路径。
export interface TopicPathNode {
  kind: "evidence" | "thesis" | "role" | "stock" | "fund";
  label: string;
  refId: string;
  meta: string;
}

export interface TopicView {
  cutoff: string;
  isHistorical: boolean;
  tag: string;
  cases: TopicCaseView[];
  companyRoles: TopicCompanyRoleView[];
  fundExposure: TopicExposurePosition[];
  // 关系路径（设计图 9 底部 5 节点链），可由 mock 适配器自动派生
  pathNodes?: TopicPathNode[];
  // 当前主题内被右栏检查器锁定的命题（默认第一条 ai_pending / contradicted）
  pinnedThesisId?: string | null;
  derivedFrom: {
    caseIds: string[];
    thesisIds: string[];
    themeRoleIds: string[];
    disclosureIds: string[];
  };
}

export interface CompanyThemeClient {
  listCompanies(query?: string, cursor?: string | null): Promise<CompanyListView>;
  getCompanyDossier(
    companyId: string,
    opts?: { cutoff?: string },
  ): Promise<CompanyDossierView>;
  listThemes(): Promise<TopicListItem[]>;
  getThemeView(tag: string, opts?: { cutoff?: string }): Promise<TopicView>;
}
