import { useEffect, useRef, useState } from 'react';
import { request, ResearchApiError } from '@/data/researchApi';
type Outcome = 'confirmed' | 'modified' | 'rejected';
type Review = { id: string; outcome: Outcome; reviewer: string; reason: string; published_source_statement: { id: string; normalized_text: string } | null };
type Claim = { id: string; document_version_id: string; source_span_id: string; document_source_url: string; locator: Record<string, unknown>; quote: string; quote_start: number; quote_end: number; normalized_text: string; claim_type: string; authority_level: string; review_state: Outcome | 'awaiting_review'; review_history: Review[]; published_source_statement: Review['published_source_statement'] };
type Page = { items: Claim[]; has_more: boolean; next_cursor: string | null };
type Decision = { outcome: Outcome; reviewer: string; reason: string; idempotency_key: string; normalized_text?: string };
const labels = { awaiting_review: '待审核', confirmed: '已确认', modified: '修订后确认', rejected: '已驳回' };
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const strings = (v: unknown, keys: string[]) => record(v) && keys.every((key) => typeof v[key] === 'string');
const validStatement = (v: unknown) => v === null || strings(v, ['id', 'normalized_text']);
const validReview = (v: unknown) => record(v) && strings(v, ['id', 'reviewer', 'reason']) && ['confirmed', 'modified', 'rejected'].includes(String(v.outcome)) && validStatement(v.published_source_statement);
const validClaim = (v: unknown) => record(v) && strings(v, ['id', 'document_version_id', 'source_span_id', 'document_source_url', 'quote', 'normalized_text', 'claim_type', 'authority_level']) && record(v.locator) && Object.hasOwn(labels, String(v.review_state)) && Number.isSafeInteger(v.quote_start) && Number(v.quote_start) >= 0 && Number.isSafeInteger(v.quote_end) && Number(v.quote_end) > Number(v.quote_start) && Array.isArray(v.review_history) && v.review_history.every(validReview) && validStatement(v.published_source_statement);
function sourceLink(value: string) { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; } }
function ClaimRow({ item, actor, locked, onLock, onRefresh }: { item: Claim; actor: string; locked: boolean; onLock: (id: string | null) => void; onRefresh: () => void }) {
  const [outcome, setOutcome] = useState<Outcome | ''>(''); const [reason, setReason] = useState(''); const [text, setText] = useState(item.normalized_text);
  const [busy, setBusy] = useState(false); const [uncertain, setUncertain] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState<Review | null>(null);
  const pending = useRef<AbortController>(); const submitted = useRef<Decision>();
  useEffect(() => () => pending.current?.abort(), []);
  async function submit() {
    if (busy || locked || saved || item.review_state !== 'awaiting_review') return;
    if (!uncertain && (!actor.trim() || !outcome || !reason.trim() || (outcome === 'modified' && !text.trim()))) return;
    const body = uncertain ? submitted.current! : { outcome: outcome as Outcome, reviewer: actor.trim(), reason: reason.trim(), idempotency_key: crypto.randomUUID(), ...(outcome === 'modified' ? { normalized_text: text.trim() } : {}) };
    submitted.current = body;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setError(''); onLock(item.id);
    try {
      const review = await request<Review>(`/atomic-claims/${encodeURIComponent(item.id)}/reviews`, controller.signal, (v) => validReview(v) && record(v) && v.outcome === body.outcome && v.reviewer === body.reviewer && v.reason === body.reason && (body.outcome === 'rejected' ? v.published_source_statement === null : record(v.published_source_statement) && v.published_source_statement.normalized_text === (body.normalized_text ?? item.normalized_text).trim()), body, '/api/v1');
      if (!controller.signal.aborted) { setSaved(review); setUncertain(false); onLock(null); onRefresh(); }
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof Error ? err.message : '审核失败。');
        const unknown = !(err instanceof ResearchApiError && err.status >= 400 && err.status < 500);
        setUncertain(unknown); if (!unknown) onLock(null);
      }
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }
  const link = sourceLink(item.document_source_url);
  return <article className="live-evidence-review-row" aria-label={item.normalized_text}>
    <h4>{item.normalized_text}</h4><p>审核状态：{labels[item.review_state]} · 类型：{item.claim_type} · 来源权威：{item.authority_level}</p>
    {link && <a href={link} target="_blank" rel="noreferrer">查看候选来源网页</a>}
    <blockquote tabIndex={0}>{item.quote}</blockquote><p>原文字符范围：{item.quote_start}–{item.quote_end}</p>
    <details><summary>来源定位与审核记录</summary><p>文档版本：{item.document_version_id} · 原文片段：{item.source_span_id}</p><pre>{JSON.stringify(item.locator, null, 2)}</pre>{item.review_history.map((review) => <p key={review.id}>{labels[review.outcome]} · {review.reviewer}：{review.reason}</p>)}</details>
    {item.published_source_statement && <p>正式陈述：{item.published_source_statement.normalized_text}</p>}
    {saved ? <><p role="status">审核已保存：{labels[saved.outcome]}</p>{saved.published_source_statement && <p>正式陈述：{saved.published_source_statement.normalized_text}</p>}</> : item.review_state === 'awaiting_review' && <form className="live-create" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <fieldset disabled={busy || uncertain || locked}><legend>核对原文后作出决定</legend>
        <label>候选审核决定<select value={outcome} onChange={(e) => setOutcome(e.target.value as Outcome | '')}><option value="">请选择</option><option value="confirmed">确认并发布陈述</option><option value="modified">修订后发布陈述</option><option value="rejected">驳回候选</option></select></label>
        {outcome === 'modified' && <label>修订后的陈述<textarea value={text} onChange={(e) => setText(e.target.value)} required /></label>}
        <label>候选审核理由<textarea value={reason} onChange={(e) => setReason(e.target.value)} required /></label>
      </fieldset>
      {uncertain && <p>提交结果尚未确认。重试将沿用原操作人、决定和请求编号，请先完成本次核对。</p>}
      {error && <p role="alert">{error}</p>}
      <button className="live-primary" disabled={busy || locked || (!uncertain && (!actor.trim() || !outcome || !reason.trim() || (outcome === 'modified' && !text.trim())))}>{busy ? '正在保存候选审核…' : uncertain ? '重试原候选审核' : '保存候选审核'}</button>
    </form>}
  </article>;
}
export function AtomicClaimQueue({ caseId, actor, onRefresh }: { caseId: string; actor: string; onRefresh: () => void }) {
  const [items, setItems] = useState<Claim[] | null>(null); const [cursor, setCursor] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [lock, setLock] = useState<string | null>(null);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  async function load(more = false) {
    if (busy || lock) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setError('');
    if (!more) { setItems(null); setCursor(null); }
    const query = new URLSearchParams({ limit: '20' }); if (more && cursor) query.set('cursor', cursor);
    try {
      const page = await request<Page>(`/research-cases/${encodeURIComponent(caseId)}/atomic-claims?${query}`, controller.signal, (v) => record(v) && Array.isArray(v.items) && v.items.every(validClaim) && typeof v.has_more === 'boolean' && (v.next_cursor === null || typeof v.next_cursor === 'string') && (!v.has_more || (v.items.length > 0 && typeof v.next_cursor === 'string' && v.next_cursor.length > 0 && v.next_cursor !== (more ? cursor : null))), undefined, '/api/v1');
      if (!controller.signal.aborted) { setItems((old) => more ? [...(old ?? []), ...page.items.filter((item) => !old?.some((previous) => previous.id === item.id))] : page.items); setCursor(page.has_more ? page.next_cursor : null); }
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '候选读取失败。'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="live-documents" aria-label="原子候选审核"><h3>原子候选审核</h3><p>逐条核对冻结原文。确认或修订会发布正式陈述，并可能恢复等待审核的研究运行。</p>
    <button className="live-secondary" disabled={busy || Boolean(lock)} onClick={() => void load()}>{items ? '刷新原子候选审核' : '查看原子候选审核'}</button>
    {busy && <p role="status">正在读取候选…</p>}{error && <p role="alert">{error}</p>}
    {items && !items.length && <p>当前没有可见的原子候选。</p>}
    {items?.map((item) => <ClaimRow key={item.id} item={item} actor={actor} locked={busy || (lock !== null && lock !== item.id)} onLock={setLock} onRefresh={onRefresh} />)}
    {cursor && <button className="live-secondary" disabled={busy || Boolean(lock)} onClick={() => void load(true)}>加载更多候选</button>}
  </section>;
}
