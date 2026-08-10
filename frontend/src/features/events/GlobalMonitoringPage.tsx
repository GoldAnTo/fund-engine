import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type ResearchRunArchive, type RunEvent } from "../../app/researchOsApi";
import {
  formatRunEventDetails,
  runFrequencyLabel,
  runStageLabel,
  runStatusLabel,
  runStopReasonLabel,
  runTriggerLabel,
} from "../../domain/runPresentation";
import { sourceTypeListLabel } from "../../domain/sourcePresentation";

export function GlobalMonitoringPage() {
  const [runs, setRuns] = useState<ResearchRunArchive[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<ResearchRunArchive | null>(null);
  const [runEvents, setRunEvents] = useState<RunEvent[] | null>(null);
  const [runEventsError, setRunEventsError] = useState<string | null>(null);
  const [openingRunId, setOpeningRunId] = useState<string | null>(null);
  const [lastReadAt, setLastReadAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function loadRuns() {
    setRefreshing(true);
    try {
      const response = await researchOsApi.runs();
      setRuns(response.items); setError(null); setLastReadAt(new Date());
    } catch {
      setError("无法读取实际运行记录；系统不会以推测的运行状态替代真实记录。");
    } finally { setRefreshing(false); }
  }
  useEffect(() => { void loadRuns(); const refresh = window.setInterval(() => void loadRuns(), 15_000); return () => window.clearInterval(refresh); }, []);

  async function openRun(run: ResearchRunArchive) {
    setSelectedRun(run);
    setRunEvents(null);
    setRunEventsError(null);
    setOpeningRunId(run.run_id);
    try {
      const response = await researchOsApi.runEvents(run.run_id);
      setRunEvents(response.items);
    } catch {
      setRunEventsError("无法读取本次运行的事件链；不会以当前配置或推测内容替代。 ");
    } finally {
      setOpeningRunId((current) => current === run.run_id ? null : current);
    }
  }

  return <main className="ros-page ros-global-monitoring">
    <header className="ros-page-head">
      <div><p className="ros-eyebrow">研究资产 · Global Monitoring</p><h1>全局运行与监控</h1><p>进行中、等待审核、失败和完成的运行均保留在同一档案中；范围只读取本次运行冻结记录，绝不由当前配置回填。</p><p className="ros-muted" aria-live="polite">每 15 秒自动刷新{lastReadAt ? ` · 最近读取 ${lastReadAt.toLocaleTimeString("zh-CN")}` : " · 正在读取实际运行档案"}</p></div>
      <div className="ros-header-actions"><button className="ros-button ros-button--secondary" type="button" disabled={refreshing} onClick={() => void loadRuns()}>{refreshing ? "正在刷新运行档案…" : "刷新运行档案"}</button><Link className="ros-button ros-button--primary" to="/events/new">＋ 新增事件</Link></div>
    </header>
    {error && <p className="ros-error" role="alert">{error}</p>}
    {!runs ? !error && <GlobalRunArchiveSkeleton /> : runs.length === 0 ? <div className="ros-empty">尚无 ResearchRun。创建事件或在 Case 内启动一次受控补证后，范围和每一步都会保留在这里。</div> : <section className="ros-global-run-list" aria-label="全局研究运行档案">
      {runs.map((run) => <RunCard key={run.run_id} run={run} onOpen={() => void openRun(run)} />)}
    </section>}
    {selectedRun && <RunArchiveDrawer run={selectedRun} events={runEvents} error={runEventsError} loading={openingRunId === selectedRun.run_id} onRetry={() => void openRun(selectedRun)} onClose={() => setSelectedRun(null)} />}
  </main>;
}

function GlobalRunArchiveSkeleton() {
  return <section className="ros-global-run-list ros-global-run-list--loading" aria-label="全局运行档案加载中" aria-busy="true">
    {[0, 1].map((item) => <article className="ros-global-run" data-testid="global-run-skeleton" key={item}>
      <header><div><i /><b /></div><span /></header>
      <div className="ros-global-run-skeleton__scope">{[0, 1, 2, 3, 4, 5].map((field) => <span key={field} />)}</div>
      <footer><i /><b /></footer>
    </article>)}
  </section>;
}

function RunCard({ run, onOpen }: { run: ResearchRunArchive; onOpen: () => void }) {
  const scope = run.scope;
  const factorIds = scope.factor_ids ?? [];
  const factorStatements = scope.factor_statements ?? [];
  const allowedSourceTypes = scope.allowed_source_types ?? [];
  return <article className="ros-global-run">
    <header><div><p className="ros-eyebrow">{runStatusLabel(run.status)} · {runStageLabel(run.stage)}</p><h2><Link to={`/events/${run.case_id}`}>{run.case_title}</Link></h2></div><span className="ros-pill ros-pill--running">已处理 {run.processed_count}</span></header>
    <dl className="ros-global-run__scope">
      <div><dt>触发方式</dt><dd>{scope.trigger ? runTriggerLabel(scope.trigger) : "本次未记录"}</dd></div>
      <div><dt>监控版本</dt><dd>{scope.monitor_version_id ?? "本次未记录"}</dd></div>
      <div><dt>关键因素</dt><dd>{factorStatements.length ? factorStatements.join(" · ") : factorIds.length ? factorIds.join(" · ") : "本次未冻结关键因素"}</dd></div>
      <div><dt>允许来源</dt><dd>{allowedSourceTypes.length ? sourceTypeListLabel(allowedSourceTypes) : "本次未冻结来源类型"}</dd></div>
      <div><dt>资料预算</dt><dd>{scope.budget ?? "本次未记录"}</dd></div>
      <div><dt>执行频率</dt><dd>{scope.frequency ? runFrequencyLabel(scope.frequency) : "本次未记录"}</dd></div>
      <div><dt>下一验证事件</dt><dd>{scope.next_verification_event ?? "本次未记录"}</dd></div>
      <div><dt>配置人</dt><dd>{scope.configured_by ?? "本次未记录"}</dd></div>
      <div><dt>本次配置依据</dt><dd>{scope.configuration_change_reason ?? "本次未记录"}</dd></div>
      <div><dt>启动时间</dt><dd>{new Date(run.created_at).toLocaleString("zh-CN")}</dd></div>
      <div><dt>更新时间</dt><dd>{new Date(run.updated_at).toLocaleString("zh-CN")}</dd></div>
      {run.stop_reason && <div><dt>停止原因</dt><dd>{runStopReasonLabel(run.stop_reason)}</dd></div>}
    </dl>
    <footer><p>范围来自本次运行冻结记录；如需调整后续运行，请在 Case 内更新并生成新的监控版本。</p><div className="ros-header-actions"><button className="ros-button ros-button--secondary" type="button" onClick={onOpen}>展开本次运行记录</button><Link className="ros-button ros-button--secondary" to={`/events/${run.case_id}/monitor`}>{run.next_action}</Link></div></footer>
  </article>;
}

function RunArchiveDrawer({ run, events, error, loading, onRetry, onClose }: { run: ResearchRunArchive; events: RunEvent[] | null; error: string | null; loading: boolean; onRetry: () => void; onClose: () => void }) {
  return <aside className="ros-run-drawer" aria-label="全局运行记录"><header><div><p className="ros-eyebrow">ResearchRun · {run.run_id}</p><h2>{run.case_title}</h2><p>每个阶段都来自本次运行的追加记录，当前 CaseMonitor 不会覆盖这里的范围或结果。</p></div><button aria-label="关闭全局运行记录" type="button" onClick={onClose}>×</button></header><div className="ros-drawer-body"><section className="ros-run-scope"><p className="ros-eyebrow">冻结范围</p><dl><div><dt>触发方式</dt><dd>{runTriggerLabel(run.scope.trigger)}</dd></div><div><dt>监控版本</dt><dd>{run.scope.monitor_version_id || "未记录"}</dd></div><div><dt>关键因素</dt><dd>{run.scope.factor_statements?.join("、") || run.scope.factor_ids?.join("、") || "未记录"}</dd></div><div><dt>允许来源</dt><dd>{sourceTypeListLabel(run.scope.allowed_source_types)}</dd></div><div><dt>资料预算</dt><dd>{run.scope.budget ?? "未记录"}</dd></div><div><dt>执行频率</dt><dd>{runFrequencyLabel(run.scope.frequency)}</dd></div><div><dt>下一验证事件</dt><dd>{run.scope.next_verification_event || "未记录"}</dd></div><div><dt>配置人</dt><dd>{run.scope.configured_by || "未记录"}</dd></div><div><dt>本次配置依据</dt><dd>{run.scope.configuration_change_reason || "未记录"}</dd></div><div><dt>停止原因</dt><dd>{runStopReasonLabel(run.stop_reason)}</dd></div></dl></section>{error ? <section className="ros-empty ros-empty--compact" role="alert"><strong>本次运行事件暂不可读取</strong><p>冻结范围仍可查看；但阶段、排除理由和候选输出暂无法确认，不会用当前配置替代。</p><button className="ros-button ros-button--secondary" type="button" disabled={loading} onClick={onRetry}>{loading ? "正在重新读取…" : "重新读取本次运行事件"}</button></section> : events === null ? <div className="ros-empty ros-empty--compact">正在读取本次运行的事件链…</div> : <ol className="ros-run-log">{events.length ? events.map((event) => <li key={event.seq}><span>{event.seq}</span><div><strong>{runStageLabel(event.stage)} · {runStatusLabel(event.status)}</strong><p>{event.message || "无文字摘要"}</p><small>{new Date(event.created_at).toLocaleString("zh-CN")} · {formatRunEventDetails(event.details || {}) || "无额外字段"}</small></div></li>) : <li><span>—</span><div><strong>未返回阶段事件</strong><p>这次运行没有可展示的阶段记录，系统不会用推测内容填充。</p></div></li>}</ol>}</div></aside>;
}
