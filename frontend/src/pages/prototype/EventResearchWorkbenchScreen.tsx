import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventWorkbench } from "../../domain/eventResearch";

export function EventResearchWorkbenchScreen() {
  const { caseId = "" } = useParams();
  const [view, setView] = useState<EventWorkbench | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (caseId) researchClient.getEventWorkbench(caseId).then(setView).catch((err: Error) => setError(err.message)); }, [caseId]);
  if (error) return <main className="prototype-screen"><p className="form-error">研究加载失败：{error}</p></main>;
  if (!view) return <main className="prototype-screen"><p>正在加载事件研究…</p></main>;
  return <main className="prototype-screen event-workbench">
    <header className="event-page-header"><div><p className="section-kicker">事件研究 · 第 {view.lifecycle.currentRound} 轮</p><h1>{view.event.eventTitle}</h1><p>{view.event.companyName ?? "公司待确认"}{view.event.ticker ? ` · ${view.event.ticker}` : ""}</p></div><Link className="prototype-button" to="/events">返回事件列表</Link></header>
    <section className="prototype-paper event-conclusion"><p className="section-kicker">当前结论</p><h2>{view.conclusion.state === "cannot_conclude" ? "尚不能下结论" : view.conclusion.state === "published" ? "已确认正式结论" : "AI 结论草案"}</h2><p>{view.conclusion.text}</p></section>
    <section className="event-workbench-grid"><div className="prototype-paper"><p className="section-kicker">竞争性因素</p><h2>系统正在区分什么</h2>{view.factors.map((factor) => <article className="event-factor-card" key={factor.position}><strong>{factor.position}. {factor.statement}</strong><span>已审核支持 {factor.reviewedSupportCount} · 反证 {factor.reviewedContradictionCount}</span>{factor.currentGap ? <small>缺口：{factor.currentGap}</small> : null}</article>)}</div><aside className="prototype-paper event-next-action"><p className="section-kicker">下一步</p><h2>{view.nextAction.label}</h2><p>{view.lifecycle.summary}</p>{view.nextAction.kind === "review_evidence" ? <Link className="prototype-button primary" to={`/events/${caseId}/review`}>{view.nextAction.label}</Link> : view.nextAction.kind === "review_conclusion" ? <Link className="prototype-button primary" to={`/events/${caseId}/conclusion`}>{view.nextAction.label}</Link> : <span className="event-muted">无需手动推进，系统会在有需要时通知你。</span>}</aside></section>
  </main>;
}
