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

export interface RelationshipGraphView {
  case: { id: string; title: string; question: string; cutoff: string; snapshotId: string };
  layers: GraphLayer[];
  nodes: GraphNodeView[];
  selectedNodeId: string;
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

export interface DataCenterView {
  cutoff: string;
  snapshotId: string;
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
  aiProposal: {
    runId: string;
    observedAt: string;
    label: string;
    text: string;
    boundary: string;
  };
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

export interface PrototypeClient {
  getWorkspaceOverviewView(): Promise<WorkspaceOverviewView>;
  getWorkspaceOverviewScreen(): Promise<WorkspaceOverviewScreen>;
  getNewResearchView(): Promise<NewResearchView>;
  getResearchPlanView(): Promise<ResearchPlanView>;
  getCaseWorkbenchView(caseId: string): Promise<CaseWorkbenchView>;
  getRelationshipGraphView(caseId: string): Promise<RelationshipGraphView>;
  getLibraryView(): Promise<LibraryView>;
  getDataCenterView(): Promise<DataCenterView>;
  getVersionsView(): Promise<VersionsView>;
  getThemeIndexView(): Promise<ThemeIndexView>;
  getThemeWorkbenchView(themeId: string): Promise<ThemeWorkbenchView>;
}

export type ResearchClient = BaseResearchClient &
  PrototypeClient &
  ReviewQueueClient &
  VersionsClient;
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
  getReviewQueueView(): Promise<ReviewQueueView>;
  submitLinkReview(linkId: string, payload: LinkReviewPayload): Promise<void>;
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
