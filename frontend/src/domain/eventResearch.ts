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
}
