import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type ResearchRunArchive } from "../../app/researchOsApi";

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

  useEffect(() => {
    researchOsApi.runs().then((response) => setRuns(response.items)).catch(() => {
      setError("无法读取实际运行记录；系统不会以推测的运行状态替代真实记录。");
    });
  }, []);

  return <main className="ros-page ros-global-monitoring">
    <header className="ros-page-head">
      <div><p className="ros-eyebrow">研究资产 · Global Monitoring</p><h1>全局运行与监控</h1><p>进行中、等待审核、失败和完成的运行均保留在同一档案中；范围只读取本次运行冻结记录，绝不由当前配置回填。</p></div>
      <Link className="ros-button ros-button--primary" to="/events/new">＋ 新增事件</Link>
    </header>
    {error && <p className="ros-error" role="alert">{error}</p>}
    {!runs ? !error && <div className="ros-empty">正在读取统一运行记录…</div> : runs.length === 0 ? <div className="ros-empty">尚无 ResearchRun。创建事件或在 Case 内启动一次受控补证后，范围和每一步都会保留在这里。</div> : <section className="ros-global-run-list" aria-label="全局研究运行档案">
      {runs.map((run) => <RunCard key={run.run_id} run={run} />)}
    </section>}
  </main>;
}

function RunCard({ run }: { run: ResearchRunArchive }) {
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
      <div><dt>启动时间</dt><dd>{new Date(run.created_at).toLocaleString("zh-CN")}</dd></div>
      <div><dt>更新时间</dt><dd>{new Date(run.updated_at).toLocaleString("zh-CN")}</dd></div>
      {run.stop_reason && <div><dt>停止原因</dt><dd>{run.stop_reason}</dd></div>}
    </dl>
    <footer><p>范围来自本次运行冻结记录；如需调整后续运行，请在 Case 内更新并生成新的监控版本。</p><Link className="ros-button ros-button--secondary" to={`/events/${run.case_id}/monitor`}>{run.next_action}</Link></footer>
  </article>;
}
