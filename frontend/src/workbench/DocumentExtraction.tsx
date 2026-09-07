import { useEffect, useRef, useState } from 'react';
import { request } from '@/data/researchApi';
interface Result { document_version_id: string; candidate_count: number; reason: string | null; candidates: { id: string; claim_type: string; normalized_text: string; quote: string; quote_start: number; quote_end: number; review_state: string }[] }
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
export function DocumentExtraction({ caseId, documentId, allowed, extractionState }: { caseId: string; documentId: string; allowed: boolean; extractionState: string }) {
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState('');
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  const eligible = allowed && ['not_attempted', 'failed'].includes(extractionState);
  async function extract() {
    if (!eligible || !consent || attempted || busy) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setAttempted(true); setError('');
    try {
      const data = await request<Result>(`/documents/${encodeURIComponent(documentId)}/extract?case_id=${encodeURIComponent(caseId)}`, controller.signal, (v) => record(v) && v.document_version_id === documentId && Number.isSafeInteger(v.candidate_count) && Number(v.candidate_count) >= 0 && (v.reason === null || typeof v.reason === 'string') && Array.isArray(v.candidates) && v.candidates.length === v.candidate_count && v.candidates.every((c) => record(c) && ['id', 'claim_type', 'normalized_text', 'quote'].every((key) => typeof c[key] === 'string') && c.review_state === 'awaiting_review' && Number.isSafeInteger(c.quote_start) && Number(c.quote_start) >= 0 && Number.isSafeInteger(c.quote_end) && Number(c.quote_end) > Number(c.quote_start)), {}, '/api/v1');
      if (!controller.signal.aborted) setResult(data);
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '候选抽取失败。'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="live-create live-monitor-config">
    <h4>提取待审核候选</h4>
    <p>此操作可能调用已配置模型。候选需逐条核对冻结原文并人工审核，不能直接作为已确认事实。</p>
    {!allowed && <p>来源权限或可见原文不足，当前不可抽取。</p>}
    {['extracted', 'extracted_empty'].includes(extractionState) && <p>本资料已有成功抽取记录，请先查看既有审核记录。</p>}
    <label><input type="checkbox" checked={consent} disabled={!eligible || attempted || busy} onChange={(e) => setConsent(e.target.checked)} />我已核对来源，确认调用模型提取待审核候选</label>
    <button className="live-secondary" disabled={!eligible || !consent || attempted || busy} onClick={() => void extract()}>{busy ? '正在提取候选…' : '从冻结资料提取候选'}</button>
    {error && <p role="alert">{error} 请刷新原始资料核对抽取状态，避免重复调用。</p>}
    {result && <><p role="status">已生成 {result.candidate_count} 条待审核候选，尚未发布正式陈述。</p>{result.reason && <p>{result.reason}</p>}<ul className="live-factors">{result.candidates.map((c) => <li key={c.id}><h4>{c.normalized_text}</h4><blockquote tabIndex={0}>{c.quote}</blockquote><p>原文字符范围：{c.quote_start}–{c.quote_end} · 待人工审核</p></li>)}</ul></>}
  </section>;
}
