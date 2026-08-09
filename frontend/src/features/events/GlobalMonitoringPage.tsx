import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type ResearchRunArchive, type RunEvent } from "../../app/researchOsApi";

const stageLabels: Record<string, string> = {
  retrieve: "采集资料",
  parse: "解析原文",
  verify: "验证因素",
  review: "等待审核",
  planning: "准备范围",
  claim_review: "等待原子陈述审核",
  stopped: "已停止",
  failed: "运行失败",
};

const triggerLabels: Record<string, string> = {
  manual: "立即补充",
  schedule: "定时任务",
};

export function GlobalMonitoringPage() {
  const [runs, setRuns] = useState<ResearchRunArchive[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<ResearchRunArchive | null>(null);
  const [runEvents, setRunEvents] = useState<RunEvent[] | null>(null);
  const [runEventsError, setRunEventsError] = useState<string | null>(null);
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
    try {
      const response = await researchOsApi.runEvents(run.run_id);
      setRunEvents(response.items);
    } catch {
      setRunEventsError("无法读取本次运行的事件链；不会以当前配置或推测内容替代。 ");
    }
  }

  return <main className="ros-page ros-global-monitoring">
    <header className="ros-page-head">
      <div><p className="ros-eyebrow">研究资产 · Global Monitoring</p><h1>全局运行与监控</h1><p>进行中、等待审核、失败和完成的运行均保留在同一档案中；范围只读取本次运行冻结记录，绝不由当前配置回填。</p><p className="ros-muted" aria-live="polite">每 15 秒自动刷新{lastReadAt ? ` · 最近读取 ${lastReadAt.toLocaleTimeString("zh-CN")}` : " · 正在读取实际运行档案"}</p></div>
      <div className="ros-header-actions"><button className="ros-button ros-button--secondary" type="button" disabled={refreshing} onClick={() => void loadRuns()}>{refreshing ? "正在刷新运行档案…" : "刷新运行档案"}</button><Link className="ros-button ros-button--primary" to="/events/new">＋ 新增事件</Link></div>
    </header>
    {error && <p className="ros-error" role="alert">{error}</p>}
    {!runs ? !error && <div className="ros-empty">正在读取统一运行记录…</div> : runs.length === 0 ? <div className="ros-empty">尚无 ResearchRun。创建事件或在 Case 内启动一次受控补证后，范围和每一步都会保留在这里。</div> : <section className="ros-global-run-list" aria-label="全局研究运行档案">
      {runs.map((run) => <RunCard key={run.run_id} run={run} onOpen={() => void openRun(run)} />)}
    </section>}
    {selectedRun && <RunArchiveDrawer run={selectedRun} events={runEvents} error={runEventsError} onClose={() => setSelectedRun(null)} />}
  </main>;
}

function RunCard({ run, onOpen }: { run: ResearchRunArchive; onOpen: () => void }) {
  const scope = run.scope;
  const factorIds = scope.factor_ids ?? [];
  const factorStatements = scope.factor_statements ?? [];
  const allowedSourceTypes = scope.allowed_source_types ?? [];
  return <article className="ros-global-run">
    <header><div><p className="ros-eyebrow">{run.status} · {stageLabels[run.stage] ?? run.stage}</p><h2><Link to={`/events/${run.case_id}`}>{run.case_title}</Link></h2></div><span className="ros-pill ros-pill--running">已处理 {run.processed_count}</span></header>
    <dl className="ros-global-run__scope">
      <div><dt>触发方式</dt><dd>{scope.trigger ? (triggerLabels[scope.trigger] ?? scope.trigger) : "本次未记录"}</dd></div>
      <div><dt>监控版本</dt><dd>{scope.monitor_version_id ?? "本次未记录"}</dd></div>
      <div><dt>关键因素</dt><dd>{factorStatements.length ? factorStatements.join(" · ") : factorIds.length ? factorIds.join(" · ") : "本次未冻结关键因素"}</dd></div>
      <div><dt>允许来源</dt><dd>{allowedSourceTypes.length ? allowedSourceTypes.join(" · ") : "本次未冻结来源类型"}</dd></div>
      <div><dt>资料预算</dt><dd>{scope.budget ?? "本次未记录"}</dd></div>
      <div><dt>启动时间</dt><dd>{new Date(run.created_at).toLocaleString("zh-CN")}</dd></div>
      <div><dt>更新时间</dt><dd>{new Date(run.updated_at).toLocaleString("zh-CN")}</dd></div>
      {run.stop_reason && <div><dt>停止原因</dt><dd>{run.stop_reason}</dd></div>}
    </dl>
    <footer><p>范围来自本次运行冻结记录；如需调整后续运行，请在 Case 内更新并生成新的监控版本。</p><div className="ros-header-actions"><button className="ros-button ros-button--secondary" type="button" onClick={onOpen}>展开本次运行记录</button><Link className="ros-button ros-button--secondary" to={`/events/${run.case_id}/monitor`}>{run.next_action}</Link></div></footer>
  </article>;
}

function RunArchiveDrawer({ run, events, error, onClose }: { run: ResearchRunArchive; events: RunEvent[] | null; error: string | null; onClose: () => void }) {
  return <aside className="ros-run-drawer" aria-label="全局运行记录"><header><div><p className="ros-eyebrow">ResearchRun · {run.run_id}</p><h2>{run.case_title}</h2><p>每个阶段都来自本次运行的追加记录，当前 CaseMonitor 不会覆盖这里的范围或结果。</p></div><button aria-label="关闭全局运行记录" type="button" onClick={onClose}>×</button></header><div className="ros-drawer-body"><section className="ros-run-scope"><p className="ros-eyebrow">冻结范围</p><dl><div><dt>触发方式</dt><dd>{run.scope.trigger || "未记录"}</dd></div><div><dt>监控版本</dt><dd>{run.scope.monitor_version_id || "未记录"}</dd></div><div><dt>关键因素</dt><dd>{run.scope.factor_statements?.join("、") || run.scope.factor_ids?.join("、") || "未记录"}</dd></div><div><dt>允许来源</dt><dd>{run.scope.allowed_source_types?.join("、") || "未记录"}</dd></div><div><dt>停止原因</dt><dd>{run.stop_reason || "未记录"}</dd></div></dl></section>{error ? <p className="ros-error" role="alert">{error}</p> : events === null ? <div className="ros-empty ros-empty--compact">正在读取本次运行的事件链…</div> : <ol className="ros-run-log">{events.length ? events.map((event) => <li key={event.seq}><span>{event.seq}</span><div><strong>{event.stage || "阶段"} · {event.status || "已记录"}</strong><p>{event.message || "无文字摘要"}</p><small>{new Date(event.created_at).toLocaleString("zh-CN")} · {Object.entries(event.details || {}).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join("、") : String(value)}`).join(" · ") || "无额外字段"}</small></div></li>) : <li><span>—</span><div><strong>未返回阶段事件</strong><p>这次运行没有可展示的阶段记录，系统不会用推测内容填充。</p></div></li>}</ol>}</div></aside>;
}
