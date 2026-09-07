import { useEffect, useRef, useState } from 'react';
import { evidenceReviewApi, type EvidenceDecision, type EvidenceOutcome, type EvidenceProposal, type EvidenceQueue } from '@/data/evidenceReviewApi';
import { researchTime } from './researchPresentation';
const roles: Record<string, string> = { supports: '支持', contradicts: '反驳', contextualizes: '背景' };
const sources: Record<string, string> = { accessible: '可访问', pasted_unverified: '粘贴来源，尚未验证', restricted: '访问受限', invalid: '来源无效' };
const fields: Record<string, string> = { page: '页码', paragraph: '段落', period: '期间', description: '适用范围', company: '公司', region: '地区', unit: '单位' };
function describe(value: unknown): string {
  if (value === null) return '未提供';
  if (Array.isArray(value)) return value.map(describe).join('、');
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>).map(([key, entry]) => `${fields[key] || key}：${describe(entry)}`).join('；') || '未提供';
  return String(value);
}
function sourceUrl(value: string | null): string | undefined { try { const url = new URL(value ?? ''); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; } }
function EvidenceRow({ item, actor, onSaved }: { item: EvidenceProposal; actor: string; onSaved: () => void }) {
  const [outcome, setOutcome] = useState<EvidenceOutcome | ''>('');
  const [reason, setReason] = useState(''); const [role, setRole] = useState(''); const [scope, setScope] = useState('');
  const [busy, setBusy] = useState(false); const [error, setError] = useState(''); const [saved, setSaved] = useState(false);
  const mutation = useRef<AbortController>(); const keys = useRef(new Map<string, string>());
  useEffect(() => () => mutation.current?.abort(), []);
  const canAccept = item.can_accept && !item.display_withheld;
  const valid = Boolean(actor.trim() && outcome && reason.trim() && (outcome === 'rejected' || canAccept) && (outcome !== 'modified' || (role && scope.trim() && item.statement_id)));
  async function submit() {
    if (!valid || busy || saved || !outcome) return;
    const body: EvidenceDecision = { outcome, reason: reason.trim(), reviewer_id: actor.trim(), expected_version: item.proposal_version,
      ...(outcome === 'modified' ? { replacement_payload: { source_statement_id: item.statement_id!, role, reason: reason.trim(), scope: { description: scope.trim() } } } : {}) };
    const identity = JSON.stringify([item.case_id, item.proposal_id, body]);
    let key = keys.current.get(identity); if (!key) { key = crypto.randomUUID(); keys.current.set(identity, key); }
    const controller = new AbortController(); mutation.current = controller; setBusy(true); setError('');
    try { await evidenceReviewApi.decide(item.proposal_id, body, key, controller.signal); if (!controller.signal.aborted) { setSaved(true); onSaved(); } }
    catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '审核提交失败，请重试。'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  const link = sourceUrl(item.document_source_url);
  return <article aria-label={item.source_title || '来源信息不可用'} className="live-evidence-review-row">
    <h4>{item.source_title || '来源信息不可用'}</h4>
    <p>来源状态：{sources[item.source_status]} · {item.source_status_reason}</p>
    {item.display_withheld ? <p>来源内容暂不可展示。当前仅可拒绝提议。</p> : <>
      {link && <p><a href={link} target="_blank" rel="noreferrer">查看来源页面</a></p>}
      <p>发布时间：{item.document_published_at ? researchTime(item.document_published_at) : '未提供'} · 可用时间：{item.available_at ? researchTime(item.available_at) : '未提供'}</p>
      <blockquote>{item.verbatim_text || '原文暂不可用'}</blockquote><p>原文位置：{describe(item.locator)}</p>
      <p>原子陈述：{item.statement_text || '未提供'}</p>
    </>}
    <p>候选因素：{item.thesis_statement || '因素信息不可用'}</p>
    <h5>AI 提议 · 待人工审核</h5>
    <p>建议角色：{roles[item.ai_role] || item.ai_role || '未提供'}</p>
    {!item.display_withheld && <><p>AI 理由：{item.ai_reason || '未提供'}</p><p>AI 适用范围：{describe(item.ai_scope)}</p></>}
    {!canAccept && <p>此来源不可接受为正式证据。</p>}
    {saved ? <p role="status">审核决定已保存。</p> : <form className="live-create" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <fieldset disabled={busy}><legend>人工审核决定</legend>
        <label>审核结论<select value={outcome} onChange={(event) => setOutcome(event.target.value as EvidenceOutcome | '')}><option value="">请选择审核结论</option><option value="confirmed" disabled={!canAccept}>接受 AI 提议</option><option value="rejected">拒绝提议</option><option value="modified" disabled={!canAccept}>修改后接受</option></select></label>
        <label>审核理由<textarea required rows={3} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        {outcome === 'modified' && <><label>人工证据角色<select value={role} onChange={(event) => setRole(event.target.value)}><option value="">请选择证据角色</option>{Object.entries(roles).map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label><label>适用范围<textarea required rows={2} value={scope} onChange={(event) => setScope(event.target.value)} /></label><p>将以此范围和审核理由替换 AI 提议，原文和候选因素保持关联。</p></>}
      </fieldset>
      {!actor.trim() && <p>请先填写上方操作人。</p>}
      <p>提交审核决定后，研究流程可能继续执行后续步骤。</p>
      {error && <p role="alert">{error} 审核理由已保留，可核对当前记录后重试。</p>}
      <button className="live-primary" disabled={!valid || busy}>{busy ? '正在提交审核…' : '提交审核决定'}</button>
    </form>}
  </article>;
}
function CaseEvidenceReviewPanel({ caseId, actor, onRefresh }: { caseId: string; actor: string; onRefresh: () => void }) {
  const [open, setOpen] = useState(false); const [queue, setQueue] = useState<EvidenceQueue | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(false); const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController(); setLoading(true); setError('');
    evidenceReviewApi.queue(caseId, controller.signal).then((data) => { if (!controller.signal.aborted) setQueue(data); }).catch((err) => { if (!controller.signal.aborted) { setQueue(null); setError(err instanceof Error ? err.message : '读取审核队列失败。'); } }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [caseId, open, revision]);
  return <section aria-label="待审核证据"><h3>待审核证据</h3>
    {!open ? <button className="live-secondary" onClick={() => setOpen(true)}>查看待审核证据</button> : <>
      {loading && <p role="status">正在读取待审核证据…</p>}{error && <p role="alert">{error}</p>}
      {queue && <><p>已审核 {queue.summary.reviewed} · 可审核 {queue.summary.pending} · 来源无效 {queue.summary.invalid_source}</p>{!queue.items.length && <p>当前没有待审核证据。新研究提议到达后可在此核对原文并作出决定。</p>}
        {queue.items.map((item) => <EvidenceRow key={`${caseId}:${item.proposal_id}:${item.proposal_version}`} item={item} actor={actor} onSaved={() => { setRevision((value) => value + 1); onRefresh(); }} />)}</>}
      <button className="live-secondary" disabled={loading} onClick={() => setRevision((value) => value + 1)}>刷新审核队列</button>
    </>}
  </section>;
}

export function EvidenceReviewPanel(props: { caseId: string; actor: string; onRefresh: () => void }) {
  return <CaseEvidenceReviewPanel key={props.caseId} {...props} />;
}
