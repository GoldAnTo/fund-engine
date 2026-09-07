import { request } from "./researchApi";
export interface EvidencePlanItem { factor: string; evidence_target: string; allowed_source_roles: string[]; priority: string; stop_condition: string; budget: number }
export interface PreparationData {
  case_id: string; status: string; revision: number; research_run_id: string | null;
  system: Record<string, { state: string }>; review: Record<string, { state: string }>;
  progress: { completed_steps: number; total_steps: number };
  artifacts: Record<string, { sequence: number; state: string; display_withheld: boolean; payload: Record<string, unknown>; context_fingerprint?: string | null } | null>;
}
export interface ClaimCandidate {
  id: string; normalized_text: string; quote: string; document_version_id: string;
  source_span_id: string; document_source_url: string; locator: Record<string, unknown>;
  claim_type: string; authority_level: string; assertion_actor: string | null;
  structured_fields: Record<string, unknown>; validation_result: Record<string, unknown>;
  review_state: string; review_history: unknown[];
}
export interface ClaimDecision { candidate_id: string; outcome: "confirmed" | "modified" | "rejected"; reason: string; normalized_text?: string }
export type PreparationReview = { kind: "claims"; decisions: ClaimDecision[] } | { kind: "protocol"; draft_sequence: number; edits: Record<string, unknown> };
export interface RunData { id: string; status: string; stage: string; round: number; max_rounds: number; budget: number; budget_used: number; next_action: string }
const record = (x: unknown): x is Record<string, unknown> => Boolean(x) && typeof x === "object" && !Array.isArray(x);
const steps = (x: unknown) => record(x) && Object.values(x).every((v) => record(v) && typeof v.state === "string");
const validPreparation = (x: unknown) => record(x) && typeof x.case_id === "string" && typeof x.status === "string" && Number.isInteger(x.revision) && steps(x.system) && steps(x.review) && record(x.progress) && typeof x.progress.completed_steps === "number" && typeof x.progress.total_steps === "number" && record(x.artifacts) && Object.values(x.artifacts).every((v) => v === null || (record(v) && typeof v.sequence === "number" && typeof v.state === "string" && typeof v.display_withheld === "boolean" && record(v.payload)));
export function planItems(preparation: PreparationData): EvidencePlanItem[] | null {
  const artifact = preparation.artifacts.plan;
  if (!artifact || artifact.display_withheld || artifact.state !== "current") return null;
  const items = artifact.payload.items;
  if (!Array.isArray(items) || !items.length || !items.every((item) => record(item) && ["factor", "evidence_target", "priority", "stop_condition"].every((key) => typeof item[key] === "string") && typeof item.budget === "number" && item.budget > 0 && Array.isArray(item.allowed_source_roles) && item.allowed_source_roles.every((role) => typeof role === "string"))) return null;
  return items as EvidencePlanItem[];
}
const casePath = (id: string) => `/${encodeURIComponent(id)}`;
export const researchActionsApi = {
  claims: (id: string, signal: AbortSignal) => request<{ items: ClaimCandidate[] }>(`/research-cases/${encodeURIComponent(id)}/atomic-claims?limit=200`, signal, (x) => record(x) && Array.isArray(x.items) && x.items.every((item) => record(item) && ["id", "normalized_text", "quote", "document_version_id", "source_span_id", "document_source_url", "claim_type", "authority_level", "review_state"].every((key) => typeof item[key] === "string") && record(item.locator) && record(item.validation_result) && record(item.structured_fields) && Array.isArray(item.review_history)), undefined, "/api/v1"),
  confirmClaims: (id: string, body: { revision: number; actor: string; decisions: ClaimDecision[] }, signal: AbortSignal) => request<PreparationData>(`${casePath(id)}/preparation/claims/confirm`, signal, validPreparation, body),
  confirmProtocol: (id: string, body: { revision: number; actor: string; draft_sequence: number; edits: Record<string, unknown> }, signal: AbortSignal) => request<PreparationData>(`${casePath(id)}/preparation/protocol/confirm`, signal, validPreparation, body),
  preparation: (id: string, signal: AbortSignal) => request<PreparationData>(`${casePath(id)}/preparation`, signal, (x) => validPreparation(x) && record(x) && x.case_id === id),
  retry: (id: string, body: { revision: number; actor: string }, signal: AbortSignal) => request<PreparationData>(`${casePath(id)}/preparation/retry`, signal, validPreparation, body),
  authorize: (id: string, body: { revision: number; actor: string; plan_sequence: number; idempotency_key: string }, signal: AbortSignal) => request<PreparationData>(`${casePath(id)}/preparation/authorize`, signal, validPreparation, body),
  material: (id: string, body: { raw_input: string; actor: string; source_type: "pasted_snapshot"; source_url?: string }, signal: AbortSignal) => request<{ document_version_id: string; source_type: string }>(`${casePath(id)}/materials`, signal, (x) => record(x) && typeof x.document_version_id === "string" && typeof x.source_type === "string", body),
  runs: (id: string, signal: AbortSignal) => request<{ items: RunData[] }>(`/research-cases/${encodeURIComponent(id)}/runs`, signal, (x) => record(x) && Array.isArray(x.items) && x.items.every((run) => record(run) && ["id", "status", "stage", "next_action"].every((key) => typeof run[key] === "string") && ["round", "max_rounds", "budget", "budget_used"].every((key) => typeof run[key] === "number")), undefined, "/api/v1"),
};
