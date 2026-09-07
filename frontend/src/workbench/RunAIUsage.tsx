import { useEffect, useRef, useState } from 'react';
import { request } from '@/data/researchApi';
interface Usage { run_id?: string; case_id?: string; coverage: string; operation_count: number; reported_attempt_count: number; unavailable_attempt_count: number; unavailable_operation_count: number; reported_prompt_tokens: number; reported_completion_tokens: number; reported_total_tokens: number; recorded_total_tokens: number | null }
function AIUsage({ scope, id }: { scope: 'run' | 'case'; id: string }) {
  const title = scope === 'run' ? '模型用量' : '研究累计模型用量';
  const [usage, setUsage] = useState<Usage | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController>();
  useEffect(() => () => pending.current?.abort(), []);
  async function load() {
    if (busy) return;
    const controller = new AbortController(); pending.current = controller; setBusy(true); setUsage(null); setError('');
    try {
      const data = await request<Usage>(`/${scope === 'run' ? 'research-runs' : 'research-cases'}/${encodeURIComponent(id)}/ai-usage`, controller.signal, (v) => {
        if (!v || typeof v !== 'object') return false;
        const d = v as Usage;
        const counts = [d.operation_count, d.reported_attempt_count, d.unavailable_attempt_count, d.unavailable_operation_count, d.reported_prompt_tokens, d.reported_completion_tokens, d.reported_total_tokens];
        return d[scope === 'run' ? 'run_id' : 'case_id'] === id && d.coverage === 'recorded_attributed_operations_only' && counts.every((n) => Number.isSafeInteger(n) && n >= 0) && d.unavailable_operation_count <= d.operation_count && d.reported_prompt_tokens + d.reported_completion_tokens === d.reported_total_tokens && d.recorded_total_tokens === (d.operation_count > 0 && d.unavailable_operation_count === 0 && d.unavailable_attempt_count === 0 ? d.reported_total_tokens : null);
      }, undefined, '/api/v1');
      if (!controller.signal.aborted) setUsage(data);
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : '用量读取失败。'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section aria-label={title}><h4>{title}</h4>
    <p>仅统计已保存且明确关联{scope === 'run' ? '本次运行' : '本研究'}的调用记录，不代表完整账单。任务预算与 token 用量分别计量。</p>
    <button className="live-secondary" disabled={busy} onClick={() => void load()}>{busy ? `正在读取${title}…` : usage ? `刷新${title}` : `查看${title}`}</button>
    {error && <p role="alert">{error}</p>}
    {usage && (usage.operation_count === 0 ? <p>暂无可归属的调用记录，不能据此认定用量为零。</p> : <>
      <dl><div><dt>已记录操作</dt><dd>{usage.operation_count}</dd></div><div><dt>已报告用量的调用尝试</dt><dd>{usage.reported_attempt_count}</dd></div><div><dt>已报告 token 小计</dt><dd>{usage.reported_total_tokens}（输入 {usage.reported_prompt_tokens}，输出 {usage.reported_completion_tokens}）</dd></div></dl>
      {usage.recorded_total_tokens === null ? <p>记录用量不完整：{usage.unavailable_attempt_count} 次调用尝试未返回有效用量，{usage.unavailable_operation_count} 条操作缺少用量记录。上述小计不包含未知部分。</p> : <p>已保存记录的 token 总量：{usage.recorded_total_tokens}。仍不包含未归属或未保存的调用。</p>}
    </>)}
  </section>;
}


export function RunAIUsage({ runId }: { runId: string }) {
  return <AIUsage key={runId} scope="run" id={runId} />;
}
export function CaseAIUsage({ caseId }: { caseId: string }) {
  return <AIUsage key={caseId} scope="case" id={caseId} />;
}
