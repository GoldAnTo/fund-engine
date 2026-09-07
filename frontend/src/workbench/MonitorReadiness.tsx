import { ManualMonitorStart } from "./ManualMonitorStart";
import { useEffect, useRef, useState } from 'react';
import { request } from '@/data/researchApi';
interface Factor { id: string; statement: string }
interface Scope { monitor: { version: number; budget: number; allowed_source_types: string[]; factor_ids: string[] } | null; confirmed_factors: Factor[] }
interface Readiness { status: 'not_applicable' | 'blocked' | 'single_metric_monitoring' | 'ready'; reason_codes: string[]; next_action: string }
const labels = { not_applicable: '无需研究协议', blocked: '协议尚未通过', single_metric_monitoring: '可进行单指标监控', ready: '协议已就绪' };
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const validScope = (v: unknown) => record(v) && (v.monitor === null || (record(v.monitor) && Number.isSafeInteger(v.monitor.version) && Number(v.monitor.version) > 0 && Number.isSafeInteger(v.monitor.budget) && Number(v.monitor.budget) > 0 && Array.isArray(v.monitor.allowed_source_types) && v.monitor.allowed_source_types.every((source) => typeof source === "string") && Array.isArray(v.monitor.factor_ids) && v.monitor.factor_ids.every((id) => typeof id === 'string'))) && Array.isArray(v.confirmed_factors) && v.confirmed_factors.every((f) => record(f) && typeof f.id === 'string' && typeof f.statement === 'string');
const validReadiness = (v: unknown) => record(v) && typeof v.status === 'string' && Object.hasOwn(labels, v.status) && typeof v.next_action === 'string' && Array.isArray(v.reason_codes) && v.reason_codes.every((code) => typeof code === 'string');
export function MonitorReadiness({ caseId, actor = "", onStarted = () => {} }: { caseId: string; actor?: string; onStarted?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [scope, setScope] = useState<Scope['monitor']>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [rows, setRows] = useState<(Factor & Readiness)[] | null>(null);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), [caseId]);
  async function load() {
    if (pending.current && !pending.current.signal.aborted) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError(''); setRows(null); setVersion(null); setScope(null);
    try {
      const scope = await request<Scope>(`/research-cases/${encodeURIComponent(caseId)}/monitor`, controller.signal, validScope, undefined, '/api/v1');
      if (!scope.monitor) { if (!controller.signal.aborted) setRows([]); return; }
      const selected = scope.monitor.factor_ids.map((id) => scope.confirmed_factors.find((f) => f.id === id));
      if (!selected.length || selected.some((f) => !f)) throw new Error('当前计划的已确认因素不完整，请重新核对监控配置。');
      const result: (Factor & Readiness)[] = [];
      // Bound parallel protocol reads even for unusually large legacy scopes.
      for (let offset = 0; offset < selected.length; offset += 4) {
        if (controller.signal.aborted) return;
        result.push(...await Promise.all(selected.slice(offset, offset + 4).map(async (factor) => ({ ...factor!, ...await request<Readiness>(`/theses/${encodeURIComponent(factor!.id)}/researchability`, controller.signal, validReadiness, undefined, '/api/v1') }))));
      }
      if (!controller.signal.aborted) { setRows(result); setVersion(scope.monitor.version); setScope(scope.monitor); }
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '研究协议状态读取失败。'); }
    finally { if (!controller.signal.aborted) { setBusy(false); pending.current = undefined; } }
  }
  return <div>
    <button className="live-secondary" disabled={busy} onClick={() => void load()}>核对监控范围与协议</button>
    {busy && <p role="status">正在读取研究范围与协议…</p>}
    {error && <p role="alert">{error}</p>}
    {rows && !rows.length && <p>尚无监控计划，请先配置研究范围。</p>}
    {rows && rows.length > 0 && <>
      <p>监控版本 {version} 的协议快照；配置或审核变化后请重新核对，实际启动仍由服务端检查。</p>
      <ul className="live-factors">{rows.map((row) => <li key={row.id}><h4>{row.statement}</h4><p>{labels[row.status]}</p><p>{row.next_action}</p></li>)}</ul>
      {scope && <ManualMonitorStart key={`${caseId}:${scope.version}`} caseId={caseId} actor={actor} version={scope.version} budget={scope.budget} allowedSources={scope.allowed_source_types} ready={rows.every((row) => row.status !== "blocked")} onStarted={onStarted} />}
    </>}
  </div>;
}
