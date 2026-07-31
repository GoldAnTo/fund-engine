export type Conclusion =
  | "supported"
  | "contradicted"
  | "insufficient_evidence";

export type EdgeKind = "evidence" | "causal" | "theme_role" | "holding";

export type NodeKind =
  | "case"
  | "thesis"
  | "statement"
  | "step"
  | "company"
  | "stock"
  | "fund";

export type EvidenceRole = "supports" | "contradicts" | "contextualizes";

export type ReviewState = "machine_generated" | "reviewed" | "rejected";

export type StatementKind =
  | "disclosed_fact"
  | "management_attribution"
  | "forecast"
  | "research_opinion";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  label: string;
  [key: string]: unknown;
}

export interface GraphEdge {
  id: string;
  kind: EdgeKind;
  source: string;
  target: string;
  role?: EvidenceRole;
  reason?: string;
  review_state?: ReviewState;
  rationale?: string;
  scope?: Record<string, unknown>;
  weight?: string;
  report_period?: string;
}

export interface EvidenceDrawerRecord {
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
  review_state: ReviewState;
}

export interface ValuationSnapshot {
  stock_id: string;
  stock_code: string | null;
  stock_name: string | null;
  as_of_date: string | null;
  metric_name: string;
  metric_value: string | null;
  source: string;
  definition: string;
}

export interface HoldingDisclosure {
  disclosure_id: string;
  fund_id: string;
  fund_code: string | null;
  fund_name: string | null;
  stock_id: string;
  stock_code: string | null;
  stock_name: string | null;
  weight: string | null;
  report_period: string | null;
  published_at: string | null;
  source: string;
}

export interface WorkbenchResponse {
  case: { id: string; title: string; industry_topic: string };
  focus_thesis: { id: string; statement: string } | null;
  assessment: {
    id: string;
    conclusion: Conclusion;
    rationale: string;
    gaps: string[];
    provisional: boolean;
  } | null;
  review: {
    outcome: string;
    conclusion: string | null;
    reason: string;
  } | null;
  major_gap: string | null;
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  evidence_drawer_records: EvidenceDrawerRecord[];
  stock_valuation_snapshots: ValuationSnapshot[];
  fund_holding_disclosures: HoldingDisclosure[];
}
