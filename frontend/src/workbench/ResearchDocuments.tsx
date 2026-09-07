import { DocumentExtraction } from "./DocumentExtraction";
import { useEffect, useRef, useState } from 'react';
import { request } from '@/data/researchApi';
import { researchTime } from './researchPresentation';
interface Document { extraction_state?: string; source_contract?: { status: string; permissions: { ai_processing: boolean } } | null; id: string; title: string | null; parse_state: string; span_count: number; available_at: string; source_url: string | null }
interface Page { items: Document[]; page: { has_more: boolean; next_cursor: string | null } }
interface Detail { document: Document; spans: { id: string; document_version_id: string; verbatim_text: string; locator: Record<string, unknown> }[] }
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const validDocument = (v: unknown) => record(v) && typeof v.id === 'string' && (v.title === null || typeof v.title === 'string') && (v.source_url === null || typeof v.source_url === 'string') && typeof v.available_at === 'string' && Number.isFinite(Date.parse(v.available_at)) && Number.isSafeInteger(v.span_count) && Number(v.span_count) >= 0 && typeof v.parse_state === 'string' && ['parsed', 'partial', 'failed', 'unparsed'].includes(v.parse_state);
const parseLabels: Record<string, string> = { parsed: '已解析', partial: '部分解析', failed: '解析失败', unparsed: '尚未解析' };
function sourceLink(value: string | null) { try { const url = new URL(value ?? ''); return ['https:', 'http:'].includes(url.protocol) ? url.href : null; } catch { return null; } }
export function ResearchDocuments({ caseId }: { caseId: string }) {
  const [items, setItems] = useState<Document[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController>();
  const detailRef = useRef<HTMLElement>(null);
  useEffect(() => { if (detail) detailRef.current?.focus(); }, [detail]);
  useEffect(() => () => pending.current?.abort(), [caseId]);
  const path = `/${encodeURIComponent(caseId)}/documents`;
  async function load(documentId?: string, more = false) {
    if (pending.current && !pending.current.signal.aborted) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError(''); setDetail(null);
    if (!documentId && !more) { setItems(null); setCursor(null); }
    try {
      if (documentId) {
        const data = await request<Detail>(`${path}/${encodeURIComponent(documentId)}`, controller.signal, (v) => record(v) && validDocument(v.document) && record(v.document) && v.document.id === documentId && Array.isArray(v.spans) && v.spans.every((span) => record(span) && typeof span.id === 'string' && span.document_version_id === documentId && typeof span.verbatim_text === 'string' && record(span.locator)));
        if (!controller.signal.aborted) setDetail(data);
      } else {
        const query = new URLSearchParams({ limit: '20' });
        if (more && cursor) query.set('cursor', cursor);
        const page = await request<Page>(`${path}?${query}`, controller.signal, (v) => record(v) && Array.isArray(v.items) && v.items.every(validDocument) && record(v.page) && typeof v.page.has_more === 'boolean' && (v.page.next_cursor === null || typeof v.page.next_cursor === 'string') && (!v.page.has_more || (v.items.length > 0 && typeof v.page.next_cursor === 'string' && v.page.next_cursor.length > 0 && v.page.next_cursor !== (more ? cursor : null))));
        if (!controller.signal.aborted) {
          setItems((previous) => more ? [...(previous ?? []), ...page.items.filter((entry) => !previous?.some((old) => old.id === entry.id))] : page.items);
          setCursor(page.page.has_more ? page.page.next_cursor : null);
        }
      }
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '原始资料读取失败。'); }
    finally { if (!controller.signal.aborted) { setBusy(false); pending.current = undefined; } }
  }
  return <section className="live-documents">
    <h3>原始资料</h3>
    <button className="live-secondary" disabled={busy} onClick={() => void load()}>{items ? '刷新原始资料' : '查看原始资料'}</button>
    {busy && <p role="status">正在读取原始资料…</p>}
    {error && <p role="alert">{error}</p>}
    {items && !items.length && <p>本研究尚无可见资料。</p>}
    {items && <ul className="live-factors">{items.map((item) => <li key={item.id}><h4>{item.title || '未命名资料'}</h4><p>{parseLabels[item.parse_state]} · {item.span_count} 段可见原文 · 可用时间 {researchTime(item.available_at)}</p><button className="live-secondary" disabled={busy} onClick={() => void load(item.id)}>查看冻结原文</button></li>)}</ul>}
    {cursor && <button className="live-secondary" disabled={busy} onClick={() => void load(undefined, true)}>加载更多资料</button>}
    {detail && <article ref={detailRef} tabIndex={-1} aria-label="冻结资料详情"><h4>{detail.document.title || '未命名资料'}</h4><p>资料版本：{detail.document.id}</p>
      {sourceLink(detail.document.source_url) && <a href={sourceLink(detail.document.source_url)!} target="_blank" rel="noreferrer">查看来源网页 ↗</a>}
      {!detail.spans.length && <p>当前没有可展示的原文段落，请核对解析状态与来源权限。</p>}
      <DocumentExtraction key={detail.document.id} caseId={caseId} documentId={detail.document.id} extractionState={detail.document.extraction_state ?? "unknown"} allowed={detail.spans.length > 0 && (detail.document.source_contract == null || (detail.document.source_contract.status === "admitted" && detail.document.source_contract.permissions?.ai_processing === true))} />
      {detail.spans.map((span) => <div key={span.id}><blockquote tabIndex={0}>{span.verbatim_text}</blockquote><details><summary>原文定位</summary><pre>{JSON.stringify(span.locator, null, 2)}</pre></details></div>)}
    </article>}
  </section>;
}
