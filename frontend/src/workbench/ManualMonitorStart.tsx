import { useEffect, useRef, useState } from 'react';
import { request, ResearchApiError } from '@/data/researchApi';
import { validRun } from '@/data/researchRunApi';
import type { RunData } from '@/data/researchActionsApi';
import { researchLabel } from './researchPresentation';
export function ManualMonitorStart({ caseId, actor, version, budget, allowedSources, ready, onStarted }: { caseId: string; actor: string; version: number; budget: number; allowedSources: string[]; ready: boolean; onStarted: () => void }) {
  const [reason, setReason] = useState('');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const submitted = useRef<{ actor: string; change_reason: string; expected_version: number }>();
  const [run, setRun] = useState<RunData | null>(null);
  const pending = useRef<AbortController>();
  const identity = useRef<{ fingerprint: string; key: string }>();
  useEffect(() => () => pending.current?.abort(), []);
  const canStart = ready && (uncertain ? submitted.current?.actor : actor.trim()) && reason.trim() && consent && !busy && !run;
  async function start() {
    if (!canStart || (pending.current && !pending.current.signal.aborted)) return;
    const body = uncertain && submitted.current ? submitted.current : { actor: actor.trim(), change_reason: reason.trim(), expected_version: version };
    submitted.current = body;
    const fingerprint = JSON.stringify(body);
    const storageKey = `fundclaw:manual-monitor:${caseId}:${version}`;
    if (!identity.current) {
      try {
        const cached = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
        if (cached && typeof cached.fingerprint === 'string' && typeof cached.key === 'string') identity.current = cached;
      } catch { /* In-memory identity still protects retries in this view. */ }
    }
    if (identity.current?.fingerprint !== fingerprint) identity.current = { fingerprint, key: crypto.randomUUID() };
    try { sessionStorage.setItem(storageKey, JSON.stringify(identity.current)); } catch { /* Session storage can be unavailable. */ }
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError('');
    try {
      const result = await request<RunData>(`/research-cases/${encodeURIComponent(caseId)}/monitor/runs`, controller.signal, validRun,
        { ...body, idempotency_key: identity.current!.key }, '/api/v1');
      if (!controller.signal.aborted) { setUncertain(false); setRun(result); onStarted(); }
    } catch (err) {
      if (!controller.signal.aborted) {
        setUncertain(!(err instanceof ResearchApiError && err.status >= 400 && err.status < 500));
        setError(err instanceof Error ? err.message : '启动研究失败。');
      }
    }
    finally { if (!controller.signal.aborted) { setBusy(false); pending.current = undefined; } }
  }
  return <div className="live-create live-monitor-config">
    <p>本次使用监控版本 {version}，任务额度 {budget}，来源：{allowedSources.map(researchLabel).join('、')}。可能调用已配置的数据与模型服务。暂停未来调度仍允许手动启动一次。</p>
    {!ready && <p>研究协议尚未通过，不能启动研究。</p>}
    <label>单次研究启动原因<textarea maxLength={2000} value={reason} disabled={busy || uncertain || Boolean(run)} onChange={(e) => { setReason(e.target.value); setConsent(false); }} /></label>
    <label><input type="checkbox" checked={consent} disabled={busy || uncertain || Boolean(run)} onChange={(e) => setConsent(e.target.checked)} />我已核对上述范围和额度，确认启动一次研究</label>
    <button className="live-primary" disabled={!canStart} onClick={() => void start()}>{busy ? '正在提交研究…' : '按此计划启动一次研究'}</button>
    {uncertain && <p role="status">正在核实首次提交，重试沿用操作人 {submitted.current?.actor}、原原因和同一请求标识。</p>}
    {error && <p role="alert">{error} 结果不确定时请保持内容重试；计划变化时请重新核对范围。</p>}
    {run && <p role="status">{run.status === 'queued' ? '研究已排队' : `研究状态：${researchLabel(run.status)}`}，运行编号：{run.id}</p>}
  </div>;
}
