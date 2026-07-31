// Frontend domain types. These are the stable contract between UI and
// the researchClient. Adapters (mock / http) must produce these types;
// pages must not import backend response shapes directly.

export type Conclusion =
  | "supported"
  | "contradicted"
  | "insufficient_evidence";

export type EdgeKind =
  | "evidence"
  | "causal"
  | "theme_role"
  | "holding"
  | "contains_thesis"
  | "company_stock"
  | "contains_step"
  | "valuation";

export type NodeKind =
  | "case"
  | "thesis"
  | "statement"
  | "step"
  | "company"
  | "stock"
  | "fund"
  | "valuation";

export type EvidenceRole = "supports" | "contradicts" | "contextualizes";

export type ReviewState = "machine_generated" | "reviewed" | "rejected";

export type StatementKind =
  | "disclosed_fact"
  | "management_attribution"
  | "forecast"
  | "research_opinion";

export type ReviewOutcome = "confirmed" | "modified" | "rejected";

// ── Status markers ────────────────────────────────────────────────────────
export type ObjectStatus =
  | "ai_pending_review"
  | "human_confirmed"
  | "human_modified"
  | "human_rejected"
  | "conflict"
  | "stale"
  | "parse_failed"
  | "permission_denied"
  | "backend_unavailable";

// ── Workspace overview (Prototype 1) ──────────────────────────────────────
export interface ResearchSummaryBlock {
  title: string;
  body: string;
}

export interface KeyChangeItem {
  id: string;
  tag: "新增" | "更新" | "风险" | "缺口";
  text: string;
  detail: string;
  occurred_at: string;
  source_label: string;
}

export interface ResearchFrameworkNode {
  id: string;
  sequence: string;
  title: string;
  children: { id: string; sequence: string; title: string }[];
}

export interface TaskQueueItem {
  id: string;
  category: "待审核" | "进行中" | "等待" | "主要阻塞";
  title: string;
  source: string;
  updated_at: string;
  assignee: string;
}

export interface EvidenceChangeItem {
  id: string;
  case_title: string;
  description: string;
  source: string;
  kind: string;
  updated_at: string;
}

export interface ActivityEvent {
  id: string;
  actor: string;
  verb: string;
  target: string;
  occurred_at: string;
  group: "今天" | "昨天" | "更早";
}

export interface WorkspaceOverview {
  case_id: string;
  case_title: string;
  case_topic: string;
  last_updated_at: string;
  case_count_label: string;
  case_topic_tags: string[];
  bullets: string[];
  key_changes: KeyChangeItem[];
  framework: ResearchFrameworkNode[];
  totals: {
    evidence_total: number;
    reliable_pct: number | null;
    pending_review: number;
    major_blockers: number;
  };
  task_queue: TaskQueueItem[];
  evidence_changes: EvidenceChangeItem[];
  activity: ActivityEvent[];
}

// ── Case dossier (Prototype 2) ────────────────────────────────────────────
export interface ResearchCaseSummary {
  id: string;
  title: string;
  topic: string;
  author: string;
  created_at: string;
  updated_at: string;
  has_markdown: boolean;
}

export interface ThesisAssessment {
  id: string;
  thesis_id: string;
  conclusion: Conclusion;
  rationale: string;
  bullets: string[];
  gaps: string[];
  provisional: boolean;
  review: Record<string, unknown> | null;
  major_gap: string | null;
  status_label: string;
  supply_chain_level: string;
  updated_at: string;
  confidence_label: string;
  focus_axes: string[];
}

export interface CausalStepView {
  id: string;
  sequence: number;
  title: string;
  description: string;
  status: ObjectStatus | null;
}

export interface EvidenceRecord {
  link_id: string;
  statement_id: string;
  statement_text: string | null;
  statement_kind: StatementKind | null;
  span_id: string | null;
  verbatim_text: string | null;
  locator: Record<string, unknown> | null;
  reason: string;
  role: EvidenceRole;
  scope: Record<string, unknown>;
  period: string | null;
  available_at: string;
  review_state: ReviewState;
  source_label: string | null;
  reliability: number | null; // null = no backend reliability in this delivery
  chip_label?: string;
  preview?: string;
  source_meta?: string;
}

export interface ResearchCaseDossier {
  case: ResearchCaseSummary;
  theses: ResearchCaseSummary[];
  focus_thesis_id: string;
  tabs: string[];
  assessment: ThesisAssessment | null;
  causal_chain: CausalStepView[];
  evidence: {
    supports: EvidenceRecord[];
    contradicts: EvidenceRecord[];
    contextualizes: EvidenceRecord[];
  };
  competitive_explanations: string[];
  gaps: string[];
  log: { id: string; at: string; text: string }[];
}

// ── Relationship canvas (Prototype 3) ─────────────────────────────────────
export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  group?: "evidence" | "proposition" | "causal" | "company" | "fund";
  sequence?: number;
  description?: string;
  status?: ObjectStatus;
  // evidence / proposition meta
  chip?: string;
  publisher?: string;
  publish_date?: string;
  reliability_bar?: number; // 0..1
  // causal meta
  chapter?: string;
  // company meta
  code?: string;
  sector?: string;
  relevance?: number; // 0..1
  // fund meta
  weight?: string;
  report_period?: string;
  relevance_score?: number;
}

export interface GraphEdge {
  id: string;
  kind: EdgeKind;
  source: string;
  target: string;
  role?: EvidenceRole;
  reason?: string;
  review_state?: ReviewState;
  weight?: string;
  report_period?: string;
}

export interface RelationshipGraph {
  case: ResearchCaseSummary;
  nodes: GraphNode[];
  edges: GraphEdge[];
  legend: { id: string; label: string; group: GraphNode["group"] }[];
}

// ── Documents ─────────────────────────────────────────────────────────────
export interface CitationEntry {
  id: string;
  theme: string;
  date: string;
  description: string;
}

export interface SourceDocumentView {
  id: string;
  title: string | null;
  publisher: string | null;
  document_type: string | null;
  publish_date: string | null;
  available_at: string;
  acquired_at: string;
  parser_version: string;
  parse_quality: "ok" | "partial" | "failed";
  parse_failure_stage?: string;
  linked_cases: { id: string; title: string }[];
  span_count: number;
  statement_count: number;
  version_label: string | null; // e.g. v3 · 2024-05-12
}

export interface DocumentSpan {
  id: string;
  document_id: string;
  locator: Record<string, unknown>;
  verbatim_text: string;
  cited_by: { evidence_id: string; thesis_id: string; role: EvidenceRole }[];
}

// ── Review queue ──────────────────────────────────────────────────────────
export interface ReviewQueueItem {
  id: string;
  kind: "evidence_link" | "causal_edge" | "statement" | "entity_alignment";
  case_id: string;
  case_title: string;
  thesis_id: string;
  thesis_title: string;
  proposed_by: "ai";
  proposed_at: string;
  preview: string;
  reason: string;
  scope: Record<string, unknown>;
  available_at: string;
  status: "pending" | "skipped";
}

// ── Securities & funds (used by ExposurePanel and inspectors) ─────────────
export interface CompanyExposure {
  company_id: string;
  company_name: string;
  role: string;
  scope: string;
  stocks: { stock_id: string; code: string; name: string; market: string }[];
}

export interface FundDisclosure {
  disclosure_id: string;
  fund_id: string;
  fund_code: string;
  fund_name: string;
  stock_id: string;
  stock_code: string;
  stock_name: string;
  weight: string;
  report_period: string;
  published_at: string;
  acquired_at: string;
  source: string;
}

export interface ValuationSnapshot {
  stock_id: string;
  stock_code: string;
  stock_name: string;
  as_of_date: string;
  metric_name: string;
  metric_value: string;
  source: string;
  definition: string;
}

// ── Historical snapshot ───────────────────────────────────────────────────
export interface HistoricalSnapshotContext {
  cutoff: string;
  is_historical: true;
  note: string;
}

// ── Client surface ────────────────────────────────────────────────────────
export interface OverviewQuery {
  cutoff?: string;
}

export interface DossierQuery {
  thesisId?: string;
  cutoff?: string;
}

export interface RelationshipQuery {
  cutoff?: string;
}

export interface DocumentsQuery {
  query?: string;
  cutoff?: string;
}

export interface SearchHit {
  group: "案例" | "命题" | "证据" | "公司" | "股票" | "基金";
  id: string;
  title: string;
  hint: string;
  navigate_to: string;
}

export interface ResearchClient {
  getOverview(query?: OverviewQuery): Promise<WorkspaceOverview>;
  getCaseDossier(caseId: string, query?: DossierQuery): Promise<ResearchCaseDossier>;
  getRelationshipGraph(
    caseId: string,
    query?: RelationshipQuery
  ): Promise<RelationshipGraph>;
  getDocuments(query?: DocumentsQuery): Promise<SourceDocumentView[]>;
  getDocumentDetail(documentId: string): Promise<{
    document: SourceDocumentView;
    spans: DocumentSpan[];
  }>;
  getReviewQueue(): Promise<ReviewQueueItem[]>;
  search(query: string): Promise<SearchHit[]>;
  getCaseSummaries(): Promise<ResearchCaseSummary[]>;
  submitReviewDecision(
    itemId: string,
    decision: {
      outcome: ReviewOutcome;
      conclusion: Conclusion | null;
      reason: string;
    }
  ): Promise<void>;
}

// ── Mock scenarios ───────────────────────────────────────────────────────
//
// Tests and Storyboards pick a scenario by instantiating
// MockResearchAdapter({ scenario: ... }). Pages themselves only depend on
// ResearchClient; switching scenarios does not change component code.

export type MockScenario =
  | "typical"
  | "empty"
  | "conflict"
  | "insufficient"
  | "parse_failed"
  | "historical"
  | "large"
  | "offline"
  | "permission"
  | "stale";

// ── Error surface ────────────────────────────────────────────────────────
//
// Adapters throw PageStateError with a `kind` matching one of these values.
// Pages translate the kind into a page state (offline banner, permission
// notice, etc.) without parsing message strings.

export type PageStateErrorKind =
  | "backend_unavailable"
  | "permission_denied"
  | "parse_failed"
  | "stale";

export class PageStateError extends Error {
  readonly kind: PageStateErrorKind;
  constructor(kind: PageStateErrorKind, message?: string) {
    super(message ?? kind);
    this.kind = kind;
    this.name = "PageStateError";
  }
}