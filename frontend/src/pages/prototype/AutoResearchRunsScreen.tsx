import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { CaseSummaryItem, ResearchRunDetail, ResearchRunSummary } from "../../domain/prototypeTypes";
import { PageHeader } from "../../components/prototype/PageHeader";

const fields = ["status", "stage", "round", "stop_reason", "created_at", "next_action"] as const;

export function AutoResearchRunsScreen() {
  const { runId: routeRunId } = useParams<{ runId?: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const runId = routeRunId ?? location.pathname.match(/\/auto-research\/runs\/([^/]+)/)?.[1];
  const [cases, setCases] = useState<CaseSummaryItem[]>([]);
  const [caseId, setCaseId] = useState("");
  const [runs, setRuns] = useState<ResearchRunSummary[]>([]);
  const [detail, setDetail] = useState<ResearchRunDetail | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const loadRuns = (id: string) => researchClient.listResearchRuns(id).then(setRuns);
  const loadDetail = (id: string) => researchClient.getResearchRun(id).then(setDetail);

  useEffect(() => {
    if (runId) return;
    researchClient.listCaseSummaries()
      .then((items) => {
        setCases(items);
        const initial = items[0]?.id ?? "";
        setCaseId(initial);
        return initial ? loadRuns(initial) : undefined;
      })
      .catch((err: Error) => setError(err.message));
  }, [runId]);
  useEffect(() => {
    if (!runId) return;
    loadDetail(runId).catch((err: Error) => setError(err.message));
  }, [runId]);

  const start = () => {
    if (!caseId || submitting) return;
    setSubmitting(true); setError("");
    researchClient.startResearchRun(caseId, { max_rounds: 3, budget: 100, auto_execute: false })
      .then((run) => navigate(`/auto-research/runs/${run.id}`))
      .catch((err: Error) => setError(err.message || "自动研究启动失败"))
      .finally(() => setSubmitting(false));
  };
  const cancel = () => {
    if (!detail || submitting) return;
    setSubmitting(true); setError("");
    researchClient.cancelResearchRun(detail.id)
      .then(() => loadDetail(detail.id))
      .catch((err: Error) => setError(err.message || "取消失败"))
      .finally(() => setSubmitting(false));
  };

  if (error) return <div className="prototype-screen"><div className="form-error">自动研究运行加载失败：{error}</div></div>;
  return (
    <div className="prototype-screen" data-testid="auto-research-runs">
      <PageHeader title={runId ? "自动研究运行详情" : "自动研究运行"} eyebrow="自动研究 · Runs" lede="启动后台研究、跟踪状态，并把 AI 产物送入人工审核。" actions={<Link to="/workspace" className="prototype-button">返回研究总览</Link>} />
      {!runId ? (
        <>
          <section className="prototype-paper workspace-block" data-testid="start-auto-research">
            <h2>启动后台研究</h2>
            <p>请求只会入队；独立 worker 在任务边界检查取消，并把提案和临时评估交给人工发布。</p>
            <label htmlFor="auto-research-case">研究案例</label>{" "}
            <select id="auto-research-case" value={caseId} onChange={(event) => { setCaseId(event.target.value); loadRuns(event.target.value).catch((err: Error) => setError(err.message)); }}>
              {cases.map((item) => <option key={item.id} value={item.id}>{item.title} · {item.topic}</option>)}
            </select>{" "}
            <button type="button" className="prototype-button" onClick={start} disabled={!caseId || submitting}>{submitting ? "正在入队…" : "启动自动研究"}</button>
          </section>
          <section className="prototype-paper workspace-block">
            <h2>运行列表</h2>
            {runs.map((run) => <Link className="auto-research-run" data-testid="run-list-item" key={run.id} to={`/auto-research/runs/${run.id}`}>
              {fields.map((field) => <span key={field}><strong>{field}</strong>{String(run[field] ?? "—")}</span>)}
            </Link>)}
            {runs.length === 0 && <p>当前案例暂无自动研究运行。</p>}
          </section>
        </>
      ) : detail ? <RunDetail detail={detail} submitting={submitting} onCancel={cancel} /> : <p>正在加载运行详情…</p>}
    </div>
  );
}

function RunDetail({ detail, submitting, onCancel }: { detail: ResearchRunDetail; submitting: boolean; onCancel: () => void }) {
  const cancellable = detail.status === "queued" || detail.status === "running";
  return <section className="prototype-paper workspace-block" data-testid="run-detail">
    <Link to="/auto-research/runs">← 返回运行列表</Link>
    <h2>{detail.id}</h2>
    <p>状态：{detail.status} · 阶段：{detail.stage} · 第 {detail.round} 轮</p>
    <p>下一动作：{detail.next_action}</p>
    {cancellable && <button type="button" className="prototype-button" onClick={onCancel} disabled={submitting}>{submitting ? "正在取消…" : "取消后台任务"}</button>}
    <p><Link to={`/review?caseId=${encodeURIComponent(detail.case_id)}`}>审核待发布提案 →</Link>{" · "}<Link to={`/cases/${detail.case_id}`}>确认临时评估 →</Link>{" · "}<Link to={`/conclusion/${detail.case_id}`}>查看正式结论 →</Link></p>
    {(["progress", "evidence", "pending_proposals", "review_tasks", "gap_tasks", "failed_tasks"] as const).map((field) => <article key={field} className="auto-research-detail-block">
      <h3>{field}</h3><pre>{JSON.stringify(detail[field], null, 2)}</pre>
    </article>)}
  </section>;
}
