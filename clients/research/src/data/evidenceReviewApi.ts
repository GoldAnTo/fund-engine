import { request } from './researchApi';
export interface EvidenceProposal {
  case_id: string; proposal_id: string; proposal_version: number; status: 'pending'; proposed_at: string;
  thesis_id: string | null; thesis_statement: string | null; statement_id: string | null; statement_text: string | null;
  span_id: string | null; document_version_id: string | null; verbatim_text: string | null; locator: Record<string, unknown>;
  source_title: string | null; source_status: string; source_status_reason: string; can_accept: boolean; display_withheld?: boolean;
  document_source_url: string | null; document_published_at: string | null; available_at: string | null;
  ai_role: string; ai_reason: string; ai_scope: Record<string, unknown>;
}
export interface EvidenceQueue { items: EvidenceProposal[]; summary: { total: number; reviewed: number; pending: number; invalid_source: number; current_round: number; next_action: string | null } }
export type EvidenceOutcome = 'confirmed' | 'rejected' | 'modified';
export interface EvidenceDecision { outcome: EvidenceOutcome; reason: string; reviewer_id: string; expected_version: number; replacement_payload?: { source_statement_id: string; role: string; reason: string; scope: { description: string } } }
const record = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === 'object' && !Array.isArray(x);
const uuid = (x: unknown): x is string => typeof x === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(x);
const nullableString = (x: unknown) => x === null || typeof x === 'string';
const timestamp = (x: unknown) => typeof x === 'string' && Number.isFinite(Date.parse(x));
export function validEvidenceQueue(x: unknown, caseId: string): boolean {
  if (!record(x) || !Array.isArray(x.items) || !record(x.summary)) return false;
  const ids = new Set<string>();
  return ['total', 'reviewed', 'pending', 'invalid_source', 'current_round'].every((key) => Number.isInteger((x.summary as Record<string, unknown>)[key]) && Number((x.summary as Record<string, unknown>)[key]) >= 0) && nullableString(x.summary.next_action) && x.items.every((item) => {
    if (!record(item) || item.case_id !== caseId || !uuid(item.proposal_id) || ids.has(item.proposal_id)) return false;
    ids.add(item.proposal_id);
    return item.status === 'pending' && Number.isInteger(item.proposal_version) && Number(item.proposal_version) > 0 && timestamp(item.proposed_at)
      && ['thesis_id', 'statement_id', 'span_id', 'document_version_id'].every((key) => item[key] === null || uuid(item[key]))
      && ['thesis_statement', 'statement_text', 'verbatim_text', 'source_title', 'document_source_url'].every((key) => nullableString(item[key]))
      && ['document_published_at', 'available_at'].every((key) => item[key] === null || timestamp(item[key]))
      && ['ai_role', 'ai_reason', 'source_status_reason'].every((key) => typeof item[key] === 'string')
      && ['accessible', 'pasted_unverified', 'restricted', 'invalid'].includes(String(item.source_status))
      && typeof item.can_accept === 'boolean' && (item.display_withheld === undefined || typeof item.display_withheld === 'boolean') && record(item.locator) && record(item.ai_scope)
      && (!item.can_accept || (item.display_withheld !== true && item.source_status === 'accessible' && uuid(item.statement_id) && uuid(item.thesis_id) && uuid(item.document_version_id) && uuid(item.span_id) && typeof item.verbatim_text === 'string'));
  });
}
export const evidenceReviewApi = {
  queue: (caseId: string, signal: AbortSignal) => request<EvidenceQueue>(`/${encodeURIComponent(caseId)}/review-queue`, signal, (x) => validEvidenceQueue(x, caseId)),
  decide: (id: string, body: EvidenceDecision, key: string, signal: AbortSignal) => request(`/review-proposals/${encodeURIComponent(id)}/decisions`, signal,
    (x) => record(x) && uuid(x.id) && x.proposal_id === id && x.outcome === body.outcome && x.reason === body.reason && x.reviewer_id === body.reviewer_id && x.expected_proposal_version === body.expected_version && timestamp(x.decided_at) && (body.outcome === 'rejected' ? x.published_entity_id === null : uuid(x.published_entity_id)), body, '/api/v1', key),
};
