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
  position: number;
  reviewedSupportCount: number;
  reviewedContradictionCount: number;
  currentGap: string | null;
}

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
  sourceStatus: string;
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
  conclusion: { state: "cannot_conclude" | "ai_draft" | "published"; text: string; citations: EventEvidenceCitation[] };
  factors: EventFactor[];
  evidence: EventEvidenceCitation[];
  nextAction: { kind: "wait" | "review_evidence" | "review_conclusion" | "supply_scope"; label: string; count?: number };
}

export interface EventResearchClient {
  extractEventResearch(input: EventExtractionInput): Promise<EventExtraction>;
  createEventResearch(input: CreateEventResearchInput): Promise<{ caseId: string; briefId: string; lifecycle: EventLifecycle }>;
  listEventResearch(status?: EventLifecycleStatus): Promise<EventResearchListItem[]>;
  getEventWorkbench(caseId: string): Promise<EventWorkbench>;
  getEventReviewQueue(caseId: string): Promise<EventReviewQueue>;
  publishEventConclusion(input: { caseId: string; text: string; reviewer: string }): Promise<{ conclusionId: string; state: "published" }>;
}
