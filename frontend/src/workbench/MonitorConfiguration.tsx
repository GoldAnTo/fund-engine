import { useEffect, useRef, useState } from 'react';
import { request } from '@/data/researchApi';
const sources: Record<string, string> = { licensed_provider: '授权数据服务', company_disclosure: '公司披露', uploaded_file: '上传文件', pasted_snapshot: '粘贴快照' };
interface Configuration { id: string; version: number; status: string; frequency: string; factor_ids: string[]; allowed_source_types: string[]; next_verification_event: string; budget: number }
interface Detail { monitor: Configuration | null; available_confirmed_factors: { id: string; statement: string }[] }
const record = (v: unknown): v is Record<string, unknown> => Boolean(v) && typeof v === 'object' && !Array.isArray(v);
const strings = (v: unknown) => Array.isArray(v) && v.every((s) => typeof s === 'string');
const validConfiguration = (v: unknown) => record(v) && ['id', 'status', 'frequency', 'next_verification_event'].every((key) => typeof v[key] === 'string') && strings(v.factor_ids) && strings(v.allowed_source_types) && Number.isSafeInteger(v.version) && Number(v.version) > 0 && Number.isSafeInteger(v.budget) && Number(v.budget) > 0;
const validDetail = (v: unknown) => record(v) && (v.monitor === null || validConfiguration(v.monitor)) && Array.isArray(v.available_confirmed_factors) && v.available_confirmed_factors.every((f) => record(f) && typeof f.id === 'string' && typeof f.statement === 'string');
export function MonitorConfiguration({ caseId, actor, onSaved }: { caseId: string; actor: string; onSaved: () => void }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  const [verified, setVerified] = useState(false);
  const [frequency, setFrequency] = useState('daily_20_00');
  const [factors, setFactors] = useState<string[]>([]);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [event, setEvent] = useState('');
  const [budget, setBudget] = useState('20');
  const [reason, setReason] = useState('');
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), [caseId]);
  const path = `/research-cases/${encodeURIComponent(caseId)}/monitor`;
  function hydrate(data: Detail) {
    setDetail(data); setFrequency(data.monitor?.frequency ?? 'daily_20_00');
    setFactors((data.monitor?.factor_ids ?? []).filter((id) => data.available_confirmed_factors.some((f) => f.id === id)));
    setAllowed((data.monitor?.allowed_source_types ?? []).filter((s) => Object.hasOwn(sources, s)));
    setEvent(data.monitor?.next_verification_event ?? ''); setBudget(String(data.monitor?.budget ?? 20));
    setReason(''); setConsent(false); setVerified(true);
  }
  async function perform(save = false) {
    if (pending.current && !pending.current.signal.aborted) return;
    const controller = new AbortController(); pending.current = controller;
    setBusy(true); setError(''); setNotice(''); setVerified(false);
    try {
      if (save) await request<Configuration>(path, controller.signal, (v) => validConfiguration(v) && record(v) && v.status === 'active', {
        actor: actor.trim(), expected_version: detail?.monitor?.version ?? 0, frequency, factor_ids: factors, allowed_source_types: allowed,
        next_verification_event: event.trim(), budget: Number(budget), change_reason: reason.trim(),
      }, '/api/v1', undefined, 'PUT');
      const data = await request<Detail>(path, controller.signal, validDetail, undefined, '/api/v1');
      if (save && !data.monitor) throw new Error('保存后的监控配置无法核实。');
      if (!controller.signal.aborted) {
        hydrate(data);
        if (save) { setNotice(`监控配置已保存，当前版本 ${data.monitor?.version ?? '未知'}。`); onSaved(); }
      }
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '监控配置读取失败。'); }
    finally { if (!controller.signal.aborted) { setBusy(false); pending.current = undefined; } }
  }
  const valid = verified && actor.trim() && factors.length > 0 && allowed.length > 0 && ['daily_20_00', 'weekday_08_30', 'weekday_12_30'].includes(frequency) && event.trim() && reason.trim() && Number.isSafeInteger(Number(budget)) && Number(budget) > 0 && consent;
  const toggle = (items: string[], item: string, selected: boolean) => selected ? [...items, item] : items.filter((value) => value !== item);
  return <div className="live-create live-monitor-config">
    <button type="button" className="live-secondary" disabled={busy} onClick={() => void perform()}>{detail ? '重新读取配置（舍弃草稿）' : '配置监控计划'}</button>
    {busy && <p role="status">正在核对监控配置…</p>}
    {error && <p role="alert">{error} 请重新读取配置后核对，避免重复提交。</p>}
    {notice && <p role="status">{notice}</p>}
    {detail && <form onSubmit={(e) => { e.preventDefault(); if (valid && !busy) void perform(true); }} onChange={(e) => { if ((e.target as HTMLInputElement).name !== 'monitor-consent') setConsent(false); }}>
      <p>保存会追加版本并启用未来调度，包括当前已暂停的计划；可能使用已配置的数据与模型服务。预算为任务执行额度。</p>
      <fieldset disabled={busy || !verified}><legend>监控范围与频率</legend>
        {!detail.available_confirmed_factors.length && <p>暂无可用的已确认因素，请先完成因素审核。</p>}
        {detail.available_confirmed_factors.map((f) => <label key={f.id}><input type="checkbox" checked={factors.includes(f.id)} onChange={(e) => setFactors(toggle(factors, f.id, e.target.checked))} />{f.statement}</label>)}
        <label>监控频率<select aria-label="监控频率" value={frequency} onChange={(e) => setFrequency(e.target.value)}><option value="">请选择受支持频率</option><option value="daily_20_00">每天 20:00（北京时间）</option><option value="weekday_08_30">工作日 08:30（北京时间）</option><option value="weekday_12_30">工作日 12:30（北京时间）</option></select></label>
        {Object.entries(sources).map(([key, label]) => <label key={key}><input type="checkbox" checked={allowed.includes(key)} onChange={(e) => setAllowed(toggle(allowed, key, e.target.checked))} />{label}</label>)}
        <label>单次任务额度<input type="number" min="1" step="1" required value={budget} onChange={(e) => setBudget(e.target.value)} /></label>
        <label>下一核验事件<input required value={event} onChange={(e) => setEvent(e.target.value)} /></label>
        <label>配置变更原因<textarea required value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <label><input type="checkbox" name="monitor-consent" checked={consent} onChange={(e) => setConsent(e.target.checked)} />我已核对范围与预算，同意启用未来调度</label>
      </fieldset>
      <button className="live-primary" disabled={busy || !valid}>保存并启用监控</button>
    </form>}
  </div>;
}
