import { RunAIUsage } from "./RunAIUsage";
import { useEffect, useId, useRef, useState } from 'react';
import type { RunData } from '@/data/researchActionsApi';
import { researchRunApi, type RunDetail, type RunEvent } from '@/data/researchRunApi';
import { researchLabel, researchTime } from './researchPresentation';
const cancellable = new Set(['queued', 'running', 'waiting_for_sources', 'waiting_for_review']);

// The keyed child makes case/run changes discard drafts and abort in-flight reads and writes.
export function ResearchRunPanel(props: { caseId: string; run: RunData; actor: string; onRefresh: () => void }) {
  return <RunPanel key={`${props.caseId}:${props.run.id}`} {...props} />;
}
function RunPanel({ caseId, run, actor, onRefresh }: { caseId: string; run: RunData; actor: string; onRefresh: () => void }) {
  const id = useId();
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [more, setMore] = useState(false);
  const [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [reason, setReason] = useState('');
  const operation = useRef<AbortController>();
  const current = detail ?? run;
  useEffect(() => () => operation.current?.abort(), []);
  async function read(signal: AbortSignal) {
    const [fresh, page] = await Promise.all([researchRunApi.detail(caseId, run.id, signal), researchRunApi.events(run.id, signal)]);
    if (!signal.aborted) { setDetail(fresh); setVerified(true); setEvents(page.items); setMore(page.has_more); }
    return fresh;
  }
  async function perform(kind: 'read' | 'more' | 'cancel') {
    if (operation.current && !operation.current.signal.aborted) return;
    if (kind === 'cancel' && (!verified || !detail || !cancellable.has(detail.status) || !actor.trim() || !reason.trim())) return;
    const controller = new AbortController(); operation.current = controller;
    setBusy(true); setError(''); setNotice('');
    if (kind !== 'more') setVerified(false);
    try {
      if (kind === 'more') {
        const page = await researchRunApi.events(run.id, controller.signal, events.at(-1)?.seq ?? 0);
        if (!controller.signal.aborted) { setEvents((previous) => [...previous, ...page.items]); setMore(page.has_more); }
      } else {
        if (kind === 'cancel') {
          await researchRunApi.cancel(run.id, { actor: actor.trim(), change_reason: reason.trim() }, controller.signal);
          // Invalidate cancellation eligibility even when the following authoritative refresh fails.
          if (!controller.signal.aborted) setDetail(null);
        }
        const fresh = await read(controller.signal);
        if (kind === 'cancel' && !controller.signal.aborted) {
          if (fresh.status === 'cancelled') { setNotice('本次运行已取消。'); setReason(''); }
          onRefresh();
        }
      }
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '读取运行记录失败，请重试。'); }
    finally { if (!controller.signal.aborted) { setBusy(false); operation.current = undefined; } }
  }
  return <li className="live-run-row">
    <strong>{researchLabel(current.status)}</strong><span>{researchLabel(current.stage)}</span>
    <p>轮次 {current.round} / {current.max_rounds} · 已用预算 {current.budget_used} / {current.budget}</p>
    <p>{current.next_action}</p><small>运行编号：{run.id}</small>
    <div className="live-run-toolbar"><button className="live-secondary" aria-expanded={expanded} aria-controls={id} onClick={() => { if (!expanded) { setExpanded(true); void perform('read'); } else { operation.current?.abort(); operation.current = undefined; setBusy(false); setExpanded(false); } }}>{expanded ? '收起运行详情' : '查看运行详情'}</button></div>
    {expanded && <section id={id} className="live-run-details" aria-label={`运行 ${run.id} 详情`} aria-busy={busy}>
      <button className="live-secondary" disabled={busy} onClick={() => void perform('read')}>刷新运行详情</button>
      {busy && <p role="status">正在更新运行记录…</p>}
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      {detail?.stop_reason && <p>停止原因：{researchLabel(detail.stop_reason)}</p>}
      {!!detail?.gaps.length && <><h4>待补证据</h4><ul>{detail.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul></>}
      <RunAIUsage key={run.id} runId={run.id} />
      <h4>阶段事件</h4>
      {detail && !events.length && <p>暂无阶段事件。</p>}
      <ol className="live-run-events">{events.map((event) => <li key={event.seq}>
        <div><strong>#{event.seq} {event.stage ? researchLabel(event.stage) : '运行事件'}</strong>{event.status && <span>{researchLabel(event.status)}</span>}</div>
        <time dateTime={event.created_at}>{researchTime(event.created_at)}</time>{event.message && <p>{event.message}</p>}
        {Object.keys(event.details).length > 0 && <details><summary>查看事件记录</summary><dl>{Object.entries(event.details).map(([key, value]) => <div key={key}><dt>{({ actor: '操作人', change_reason: '变更原因', stop_reason: '停止原因' } as Record<string, string>)[key] ?? researchLabel(key)}</dt><dd>{typeof value === 'string' ? value : JSON.stringify(value)}</dd></div>)}</dl></details>}
      </li>)}</ol>
      {more && <button className="live-secondary" disabled={busy} onClick={() => void perform('more')}>加载更多事件</button>}
      <form className="live-create live-run-cancel" onSubmit={(event) => { event.preventDefault(); void perform('cancel'); }}>
        <h4>取消本次运行</h4><p>取消后停止本次研究，保留已有进度与事件记录。</p>
        {!cancellable.has(current.status) && <p>当前运行状态不支持取消。</p>}
        <label>取消原因<textarea rows={2} maxLength={2000} required disabled={busy || !verified || !detail || !cancellable.has(detail.status)} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        {cancellable.has(current.status) && !actor.trim() && <p>请先填写上方操作人。</p>}
        <button className="live-secondary" disabled={busy || !verified || !detail || !cancellable.has(detail.status) || !actor.trim() || actor.trim().length > 200 || !reason.trim()}>确认取消本次运行</button>
      </form>
    </section>}
  </li>;
}
