import { request } from './researchApi';
import type { RunData } from './researchActionsApi';
export interface RunDetail extends RunData {
  case_id: string; stop_reason: string | null; gaps: string[];
  progress: Record<string, number>; evidence: Record<string, number>;
}
export interface RunEvent { seq: number; status?: string | null; stage?: string | null; message?: string | null; details: Record<string, unknown>; created_at: string }
export interface RunEvents { run_id: string; items: RunEvent[]; has_more: boolean }
export interface RunPage { items: RunData[]; has_more: boolean; next_cursor: string | null }
const record = (x: unknown): x is Record<string, unknown> => Boolean(x) && typeof x === 'object' && !Array.isArray(x);
const nullableString = (x: unknown) => x == null || typeof x === 'string';
const counts = (x: unknown) => record(x) && Object.values(x).every((v) => Number.isInteger(v) && Number(v) >= 0);
export const validRun = (x: unknown) => record(x) && ['id', 'status', 'stage', 'next_action'].every((key) => typeof x[key] === 'string') && ['round', 'max_rounds', 'budget', 'budget_used'].every((key) => Number.isInteger(x[key]) && Number(x[key]) >= 0);
const path = (id: string) => `/research-runs/${encodeURIComponent(id)}`;
export const researchRunApi = {
  detail: (caseId: string, id: string, signal: AbortSignal) => request<RunDetail>(path(id), signal, (x) => validRun(x) && record(x) && x.id === id && x.case_id === caseId && nullableString(x.stop_reason) && Array.isArray(x.gaps) && x.gaps.every((gap) => typeof gap === 'string') && counts(x.progress) && counts(x.evidence), undefined, '/api/v1'),
  events: (id: string, signal: AbortSignal, afterSeq = 0) => request<RunEvents>(`${path(id)}/events?limit=50&after_seq=${afterSeq}`, signal, (x) => record(x) && x.run_id === id && typeof x.has_more === 'boolean' && Array.isArray(x.items) && (!x.has_more || x.items.length > 0) && x.items.every((v, index, items) => record(v) && Number.isInteger(v.seq) && Number(v.seq) > (index === 0 ? afterSeq : Number(items[index - 1].seq)) && ['status', 'stage', 'message'].every((key) => nullableString(v[key])) && typeof v.created_at === 'string' && record(v.details)), undefined, '/api/v1'),
  cancel: (id: string, body: { actor: string; change_reason: string }, signal: AbortSignal) => request<RunData>(`${path(id)}/cancel`, signal, (x) => validRun(x) && record(x) && x.id === id && x.status === 'cancelled', body, '/api/v1'),
  list: (caseId: string, signal: AbortSignal, cursor?: string) => {
    const query = new URLSearchParams({ limit: '20' });
    if (cursor) { const separator = cursor.lastIndexOf('|'); query.set('after_created_at', cursor.slice(0, separator)); query.set('after_id', cursor.slice(separator + 1)); }
    return request<RunPage>(`/research-cases/${encodeURIComponent(caseId)}/runs?${query}`, signal, (x) => record(x) && Array.isArray(x.items) && x.items.every(validRun) && typeof x.has_more === 'boolean' && (x.next_cursor === null || typeof x.next_cursor === 'string') && (!x.has_more || (x.items.length > 0 && typeof x.next_cursor === 'string' && x.next_cursor.includes('|') && x.next_cursor !== cursor)), undefined, '/api/v1');
  },
};
