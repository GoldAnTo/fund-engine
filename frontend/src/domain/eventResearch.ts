export type EventLifecycleStatus =
  | "extracting"
  | "researching"
  | "awaiting_key_review"
  | "continuing"
  | "awaiting_scope"
  | "draft_ready"
  | "published"
  | "exhausted";

export interface EventExtractionInput {
  rawInput: string;
  sourceUrl?: string;
}

export interface EventExtraction {
  eventTitle: string | null;
  companyName: string | null;
  ticker: string | null;
  eventAt: string | null;
  marketReaction: string | null;
  summary: string | null;
  researchQuestion: string;
  candidateFactors: string[];
  confirmationRequired: boolean;
}

export interface CreateEventResearchInput extends EventExtraction {
  rawInput: string;
  sourceUrl?: string;
  eventTitle: string;
  createdBy: string;
}

export interface EventLifecycle {
  status: EventLifecycleStatus;
  activeRunId: string | null;
  currentRound: number;
  summary: string;
  currentGap: string | null;
  nextHumanAction: string | null;
}

export interface EventResearchListItem {
  id: string;
  eventTitle: string;
  companyName: string | null;
  ticker: string | null;
  eventAt: string | null;
  status: EventLifecycleStatus;
  statusSummary: string;
  nextHumanAction: string | null;
  updatedAt: string;
}

export interface EventFactor {
  statement: string;
  description?: string | null;
  position: number;
  reviewedSupportCount: number;
  reviewedContradictionCount: number;
  pendingProposalCount: number;
  currentGap: string | null;
}

export interface WorkbenchProgress {
  verified: number;
  pending: number;
  invalidSource: number;
  currentGap: string | null;
}

export interface EventResearchScope {
  version: number;
  factors: EventResearchScopeFactor[];
  unmappedEvidenceCount: number;
}

export interface EventImpactTrace {
  scopeVersion: number;
  asOf: string | null;
  factors: EventImpactFactor[];
  alternatives: EventImpactFactor[];
  progress: Record<string, number>;
}

export interface EventImpactFactor {
  hypothesisId: string;
  statement: string;
  rank: number;
  classification: string;
  scoreComponents: Record<string, number>;
  explanation: string;
  relations: EventImpactRelation[];
  funds: EventImpactFund[];
}
export interface EventImpactRelation {
  relationId: string; companyId: string; companyName: string; companyType: string;
  relationKind: string; direction: string; mechanism: string; status: string; effectiveStatus: string;
  isHighImpact: boolean; isReviewable: boolean;
  sourceStatementId: string | null; stocks: Array<{ stockId: string; code: string; name: string; market: string }>;
  review: EventImpactRelationReview | null;
  observations: EventImpactObservation[]; fundExposure: EventImpactFund[];
}
export interface EventImpactRelationReview { outcome: string; reason: string; reviewer: string; createdAt: string; }
export interface EventImpactObservation { kind: string; status: string; sourceStatementId: string | null; valuationSnapshotId: string | null; summary: string; asOfDate: string | null; }
export interface EventImpactFund {
  fundId: string;
  fundCode: string;
  fundName: string;
  reportPeriod: string;
  publishedAt: string;
  source: string;
  coverageRatio: number;
  coverageStatus: string;
  computable: boolean;
  exposure: string | number | null;
}

export interface EventResearchScopeFactor {
  statement: string;
  description?: string | null;
}

export type EventResearchScopeFactorInput = EventResearchScopeFactor | string;

export interface EventEvidenceCitation {
  caseId: string;
  factorStatement: string;
  role: string;
  reviewState: string;
  sourceTitle: string | null;
  sourceUrl: string | null;
  excerpt: string;
  locator: Record<string, unknown>;
  availableAt: string;
}

export interface EventReviewQueueSummary {
  total: number;
  reviewed: number;
  pending: number;
  invalidSource: number;
  currentRound: number;
  nextAction: string | null;
}

export type EventSourceStatus =
  | "accessible"
  | "pasted_unverified"
  | "invalid";

/** A proposal and its frozen-source context for event evidence review. */
export interface EventReviewQueueItem {
  proposalId: string;
  status: string;
  proposedAt: string;
  linkId: string;
  thesisId: string | null;
  caseId: string;
  thesisStatement: string | null;
  aiRole: string | null;
  aiReason: string | null;
  aiScope: Record<string, unknown>;
  statementId: string | null;
  statementText: string | null;
  statementKind: string | null;
  spanId: string | null;
  verbatimText: string | null;
  locator: Record<string, unknown>;
  documentVersionId: string | null;
  documentSourceUrl: string | null;
  documentPublishedAt: string | null;
  availableAt: string | null;
  sourceTitle: string | null;
  sourceStatus: EventSourceStatus;
  sourceStatusReason: string;
  canAccept: boolean;
  proposalReason: string;
  position: number | null;
}

export interface EventReviewQueue {
  summary: EventReviewQueueSummary;
  items: EventReviewQueueItem[];
}

export interface EventWorkbench {
  event: EventResearchListItem;
  lifecycle: EventLifecycle;
  conclusion: { state: "cannot_conclude" | "ai_draft" | "published"; text: string; confidence: "low" | "medium" | "high"; citations: EventEvidenceCitation[] };
  factors: EventFactor[];
  evidence: EventEvidenceCitation[];
  progress: WorkbenchProgress;
  scope: EventResearchScope;
  nextAction: { kind: "wait" | "review_evidence" | "review_conclusion" | "edit_factors" | "view_conclusion_change"; label: string; count?: number };
}

export interface EventResearchClient {
  extractEventResearch(input: EventExtractionInput): Promise<EventExtraction>;
  createEventResearch(input: CreateEventResearchInput): Promise<{ caseId: string; briefId: string; lifecycle: EventLifecycle }>;
  listEventResearch(status?: EventLifecycleStatus): Promise<EventResearchListItem[]>;
  getEventWorkbench(caseId: string): Promise<EventWorkbench>;
  getEventImpactTrace?(caseId: string): Promise<EventImpactTrace>;
  reviewEventImpactRelation?(input: { relationId: string; outcome: "accepted" | "rejected" | "needs_more"; reason: string; reviewer: string }): Promise<{ reviewId: string; relationId: string; outcome: string }>;
  updateEventResearchScope(input: { caseId: string; factors: EventResearchScopeFactorInput[]; changedBy: string }): Promise<EventResearchScope & { reclassifiedEvidenceCount: number }>;
  getEventReviewQueue(caseId: string): Promise<EventReviewQueue>;
  publishEventConclusion(input: { caseId: string; text: string; reviewer: string }): Promise<{ conclusionId: string; state: "published" }>;
}
