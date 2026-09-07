import { MonitorReadiness } from "./MonitorReadiness";
import { MonitorConfiguration } from "./MonitorConfiguration";
import { useEffect, useRef, useState } from 'react';
import { request } from '@/data/researchApi';
interface Monitor { id: string; version: number; status: 'active' | 'paused'; frequency: string; budget: number; next_verification_event: string; changed_by: string; change_reason: string }
interface Detail { monitor: Monitor | null; next_scheduled_at: string | null }
const frequencyLabels: Record<string, string> = { weekday_08_30: "工作日 08:30", weekday_12_30: "工作日 12:30", daily_20_00: "每天 20:00" };
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const validMonitor = (value: unknown) => record(value) && ['id', 'frequency', 'next_verification_event', 'changed_by', 'change_reason'].every((key) => typeof value[key] === 'string') && (value.status === 'active' || value.status === 'paused') && ['version', 'budget'].every((key) => Number.isSafeInteger(value[key]) && Number(value[key]) > 0);
const validDetail = (value: unknown) => record(value) && (value.monitor === null || validMonitor(value.monitor)) && (value.next_scheduled_at === null || (typeof value.next_scheduled_at === 'string' && Number.isFinite(Date.parse(value.next_scheduled_at))));

/** Parent keys this panel by Case; abort guards also cover standalone Case changes. */
export function MonitorPanel({ caseId, actor, onStarted = () => {} }: { caseId: string; actor: string; onStarted?: () => void }) {
  const [opened, setOpened] = useState(false);
  const [data, setData] = useState<Detail | null>(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(false);
  const [error, setError] = useState('');
  const pending = useRef<AbortController>();
  useEffect(() => {
    setOpened(false); setData(null); setReason(''); setVerified(false); setError(''); setBusy(false);
    return () => pending.current?.abort();
  }, [caseId]);
  async function load(target?: 'active' | 'paused') {
    if (pending.current && !pending.current.signal.aborted) return;
    const controller = new AbortController(); pending.current = controller;
    setOpened(true); setBusy(true); setVerified(false); setError('');
    const path = `/research-cases/${encodeURIComponent(caseId)}/monitor`;
    try {
      if (target) await request<Monitor>(`${path}/${target}`, controller.signal, validMonitor, { actor: actor.trim(), change_reason: reason.trim(), expected_version: data?.monitor?.version }, '/api/v1');
      const detail = await request<Detail>(path, controller.signal, validDetail, undefined, '/api/v1');
      if (!controller.signal.aborted) { setData(detail); setVerified(true); if (target) setReason(''); }
    } catch (err) {
      if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '监控状态读取失败，请刷新后重试。');
    } finally {
      if (!controller.signal.aborted) { setBusy(false); pending.current = undefined; }
    }
  }
  const monitor = data?.monitor;
  const supportedFrequency = Boolean(monitor && Object.hasOwn(frequencyLabels, monitor.frequency));
  return <section>
    <h3>监控计划</h3>
    <p>暂停仅影响未来调度；正在执行的研究需在运行详情中单独取消。恢复后可能使用已配置的数据与模型服务。</p>
    <button className="live-secondary" disabled={busy} onClick={() => void load()}>{opened ? '刷新监控计划' : '查看监控计划'}</button>
    {busy && <p role="status">正在核对监控状态…</p>}
    {error && <p role="alert">{error} 当前状态尚未核实，请刷新监控计划。</p>}
    {opened && data && !monitor && <p>尚未配置监控计划。</p>}
    <MonitorReadiness key={`scope:${caseId}`} caseId={caseId} actor={actor} onStarted={onStarted} />
    <MonitorConfiguration key={caseId} caseId={caseId} actor={actor} onSaved={() => void load()} />
    {monitor && <>
      <dl className="live-progress"><div><dt>状态{!verified ? '（待核实）' : ''}</dt><dd>{monitor.status === 'active' ? (supportedFrequency ? '调度中' : '配置无效') : '已暂停'}</dd></div><div><dt>版本</dt><dd>{monitor.version}</dd></div><div><dt>频率</dt><dd>{frequencyLabels[monitor.frequency] ?? monitor.frequency}（北京时间）</dd></div><div><dt>单次任务预算</dt><dd>{monitor.budget}</dd></div></dl>
      {!supportedFrequency && <p role="alert">此计划的频率不受支持，无法自动调度。请先保存受支持的监控配置，再恢复计划。</p>}
      <p>{monitor.next_verification_event}</p>
      <p>最近变更：{monitor.changed_by} · {monitor.change_reason}</p>
      {verified && data?.next_scheduled_at && <p>下一次计划时间：{new Date(data.next_scheduled_at).toLocaleString('zh-CN')}</p>}
      <div className="live-create"><label>监控变更原因<textarea value={reason} disabled={busy} onChange={(event) => setReason(event.target.value)} rows={2} /></label></div>
      <button className="live-secondary" disabled={busy || !verified || !actor.trim() || !reason.trim() || (monitor.status === "paused" && !supportedFrequency)} onClick={() => void load(monitor.status === 'active' ? 'paused' : 'active')}>{monitor.status === 'active' ? '暂停未来调度' : '恢复未来调度'}</button>
    </>}
  </section>;
}
