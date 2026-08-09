import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type ActiveResearchRun } from "../../app/researchOsApi";

const stageLabels: Record<string, string> = {
  retrieve: "采集资料",
  parse: "解析原文",
  verify: "验证因素",
  review: "等待审核",
};

const triggerLabels: Record<string, string> = {
  manual: "立即补充",
  schedule: "定时任务",
};

export function GlobalMonitoringPage() {
  const [runs, setRuns] = useState<ActiveResearchRun[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    researchOsApi.activeRuns().then((response) => setRuns(response.items)).catch(() => {
      setError("无法读取实际运行记录；系统不会以推测的运行状态替代真实记录。");
    });
  }, []);

  return <main className="ros-page ros-global-monitoring">
    <header className="ros-page-head">
      <div><p className="ros-eyebrow">研究资产 · Global Monitoring</p><h1>全局运行与监控</h1><p>系统正在做什么、为什么做、用什么范围做，都以每次运行冻结的记录展示；历史运行不由当前配置回填。</p></div>
      <Link className="ros-button ros-button--primary" to="/events/new">＋ 新增事件</Link>
    </header>
    {error && <p className="ros-error" role="alert">{error}</p>}
    {!runs ? !error && <div className="ros-empty">正在读取统一运行记录…</div> : runs.length === 0 ? <div className="ros-empty">当前没有进行中的 ResearchRun。历史运行与可调整的监控配置保留在各自的 Case 内。</div> : <section className="ros-global-run-list" aria-label="进行中的研究运行">
      {runs.map((run) => <RunCard key={run.run_id} run={run} />)}
    </section>}
  </main>;
}

function RunCard({ run }: { run: ActiveResearchRun }) {
  const scope = run.scope;
  const factorIds = scope.factor_ids ?? [];
  const allowedSourceTypes = scope.allowed_source_types ?? [];
  return <article className="ros-global-run">
    <header><div><p className="ros-eyebrow">{run.status} · {stageLabels[run.stage] ?? run.stage}</p><h2><Link to={`/events/${run.case_id}`}>{run.case_title}</Link></h2></div><span className="ros-pill ros-pill--running">已处理 {run.processed_count}</span></header>
    <dl className="ros-global-run__scope">
      <div><dt>触发方式</dt><dd>{scope.trigger ? (triggerLabels[scope.trigger] ?? scope.trigger) : "本次未记录"}</dd></div>
      <div><dt>监控版本</dt><dd>{scope.monitor_version_id ?? "本次未记录"}</dd></div>
      <div><dt>关键因素</dt><dd>{factorIds.length ? factorIds.join(" · ") : "本次未冻结关键因素"}</dd></div>
      <div><dt>允许来源</dt><dd>{allowedSourceTypes.length ? allowedSourceTypes.join(" · ") : "本次未冻结来源类型"}</dd></div>
      <div><dt>资料预算</dt><dd>{scope.budget ?? "本次未记录"}</dd></div>
      <div><dt>更新时间</dt><dd>{new Date(run.updated_at).toLocaleString("zh-CN")}</dd></div>
    </dl>
    <footer><p>范围来自本次运行冻结记录；如需调整后续运行，请在 Case 内更新并生成新的监控版本。</p><Link className="ros-button ros-button--secondary" to={`/events/${run.case_id}/monitor`}>{run.next_action}</Link></footer>
  </article>;
}
