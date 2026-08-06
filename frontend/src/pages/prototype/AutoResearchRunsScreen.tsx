import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { ResearchRunDetail, ResearchRunSummary } from "../../domain/prototypeTypes";
import { PageHeader } from "../../components/prototype/PageHeader";

const fields = ["status", "stage", "round", "stop_reason", "created_at", "next_action"] as const;

export function AutoResearchRunsScreen() {
  const { runId: routeRunId } = useParams<{ runId?: string }>();
  const location = useLocation();
  const runId = routeRunId ?? location.pathname.match(/\/auto-research\/runs\/([^/]+)/)?.[1];
  const [runs, setRuns] = useState<ResearchRunSummary[]>([]);
  const [detail, setDetail] = useState<ResearchRunDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (runId) return;
    researchClient
      .listCaseSummaries()
      .then((cases) => {
        const caseId = cases[0]?.id;
        if (!caseId) return [];
        return researchClient.listResearchRuns(caseId);
      })
      .then(setRuns)
      .catch((err: Error) => setError(err.message));
  }, [runId]);
  useEffect(() => {
    if (!runId) return;
    researchClient.getResearchRun(runId).then(setDetail).catch((err: Error) => setError(err.message));
  }, [runId]);

  if (error) return <div className="prototype-screen"><div className="form-error">自动研究运行加载失败：{error}</div></div>;
  return (
    <div className="prototype-screen" data-testid="auto-research-runs">
      <PageHeader title={runId ? "自动研究运行详情" : "自动研究运行"} eyebrow="自动研究 · Runs" lede="查看自动研究的阶段、轮次、证据产物与待处理动作。" actions={<Link to="/workspace" className="prototype-button">返回研究总览</Link>} />
      {!runId ? (
        <section className="prototype-paper workspace-block">
          <h2>运行列表</h2>
          {runs.map((run) => <Link className="auto-research-run" data-testid="run-list-item" key={run.id} to={`/auto-research/runs/${run.id}`}>
            {fields.map((field) => <span key={field}><strong>{field}</strong>{String(run[field] ?? "—")}</span>)}
          </Link>)}
          {runs.length === 0 && <p>暂无自动研究运行。</p>}
        </section>
      ) : detail ? <RunDetail detail={detail} /> : <p>正在加载运行详情…</p>}
    </div>
  );
}

function RunDetail({ detail }: { detail: ResearchRunDetail }) {
  return <section className="prototype-paper workspace-block" data-testid="run-detail">
    <Link to="/auto-research/runs">← 返回运行列表</Link>
    <h2>{detail.id}</h2>
    <p>状态：{detail.status} · 阶段：{detail.stage} · 第 {detail.round} 轮</p>
    <p>下一动作：{detail.next_action}</p>
    {(["progress", "evidence", "pending_proposals", "review_tasks", "gap_tasks", "failed_tasks"] as const).map((field) => <article key={field} className="auto-research-detail-block">
      <h3>{field}</h3><pre>{JSON.stringify(detail[field], null, 2)}</pre>
    </article>)}
  </section>;
}
