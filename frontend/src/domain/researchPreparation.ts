/**
 * Stable frontend vocabulary for the review-gated research preparation flow.
 * No preparation action implies provider execution; authorization is the only
 * command that can materialize its already-reviewed plan into a research run.
 */

export interface ResearchPreparationStep {
  state: string;
  reviewState: string | null;
  artifactSequence: number | null;
}

export interface ResearchPreparationArtifact {
  sequence: number;
  state: string;
  payload: Record<string, unknown>;
  contextFingerprint: string | null;
}

export interface ResearchPreparation {
  caseId: string;
  revision: number;
  status: string;
  researchRunId: string | null;
  system: Record<string, ResearchPreparationStep>;
  review: Record<string, ResearchPreparationStep>;
  nextAttemptAt: string | null;
  lastErrorMessage: string | null;
  artifacts: Record<string, ResearchPreparationArtifact | null>;
  authorizedEvidencePlan: Record<string, unknown> | null;
}

export interface ResearchPreparationEvent {
  seq: number;
  type: string;
  step: string | null;
  message: string | null;
  detail: Record<string, unknown> | null;
  createdAt: string;
}

export interface ResearchPreparationEventsPage {
  items: ResearchPreparationEvent[];
  nextAfterSeq: number | null;
}

export interface ConfirmResearchPreparationClaimsInput {
  caseId: string;
  revision: number;
  actor: string;
  decisions: Array<{
    candidateId: string;
    outcome: "confirmed" | "modified" | "rejected";
    reason: string;
    normalizedText?: string | null;
  }>;
}

export interface ConfirmResearchPreparationProtocolInput {
  caseId: string;
  revision: number;
  actor: string;
  draftSequence: number;
  edits?: Record<string, unknown>;
}

export interface RetryResearchPreparationInput {
  caseId: string;
  revision: number;
  actor: string;
}

export interface AuthorizeResearchPreparationInput {
  caseId: string;
  revision: number;
  actor: string;
  planSequence: number;
  idempotencyKey: string;
}

/** A revision/idempotency conflict that UI can handle without parsing text. */
export class ConflictError extends Error {
  readonly status = 409;
  constructor(message = "当前准备版本已变化，请刷新后重试。") {
    super(message);
    this.name = "ConflictError";
  }
}
