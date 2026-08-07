import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventWorkbench } from "../../domain/eventResearch";
import { EventFactorScopeEditor } from "./EventFactorScopeEditor";

function conclusionTitle(state: EventWorkbench["conclusion"]["state"]) {
  if (state === "cannot_conclude") return "暂不能下结论";
  if (state === "published") return "已确认正式结论";
  return "AI 结论草案";
}

export function EventResearchWorkbenchScreen() {
  const { caseId = "" } = useParams();
  const [view, setView] = useState<EventWorkbench | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isEditingFactors, setIsEditingFactors] = useState(false);
  const [updateStatus, setUpdateStatus] = useState<string | null>(null);
  const loadWorkbench = useCallback(async () => {
    if (!caseId) return;
    try {
      setError(null);
      setView(await researchClient.getEventWorkbench(caseId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "请稍后重试");
    }
  }, [caseId]);
  useEffect(() => { void loadWorkbench(); }, [loadWorkbench]);
  if (error) return <main className="prototype-screen"><p className="form-error">研究加载失败：{error}</p></main>;
  if (!view) return <main className="prototype-screen"><p>正在加载事件研究…</p></main>;
  return <main className="prototype-screen event-workbench">
    <header className="event-page-header"><div><p className="section-kicker">事件研究 · 第 {view.lifecycle.currentRound} 轮</p><h1>{view.event.eventTitle}</h1><p>{view.event.companyName ?? "公司待确认"}{view.event.ticker ? ` · ${view.event.ticker}` : ""}</p></div><Link className="prototype-button" to="/events">返回事件列表</Link></header>
    <section className="prototype-paper event-conclusion"><p className="section-kicker">当前判断</p><h2>{conclusionTitle(view.conclusion.state)}</h2><p>{view.conclusion.text}</p>{view.progress.currentGap ? <p className="event-conclusion__gap">当前缺口：{view.progress.currentGap}</p> : null}</section>
    <section className="event-progress" aria-label="研究进度"><span className="event-progress__chip">已核验 {view.progress.verified}</span><span className="event-progress__chip">待审核 {view.progress.pending}</span><span className="event-progress__chip">无效来源不计结论 {view.progress.invalidSource}</span><span className="event-progress__chip">研究中：{view.lifecycle.summary}</span></section>
    {updateStatus ? <p className="event-update-status" role="status">{updateStatus}</p> : null}
    <section className="event-workbench-grid"><div className="prototype-paper"><p className="section-kicker">竞争性因素</p><h2>系统正在区分什么</h2>{view.factors.map((factor) => <article className="event-factor-card" key={factor.position}><strong>{factor.position}. {factor.statement}</strong><span>支持 {factor.reviewedSupportCount} · 反证 {factor.reviewedContradictionCount}</span>{factor.currentGap ? <small>缺口：{factor.currentGap}</small> : null}</article>)}</div><aside className="prototype-paper event-next-action"><p className="section-kicker">下一步</p><h2>{view.nextAction.label}</h2><p>{view.lifecycle.summary}</p>{view.nextAction.kind === "edit_factors" ? <button className="prototype-button primary" type="button" onClick={() => setIsEditingFactors(true)}>{view.nextAction.label}</button> : view.nextAction.kind === "review_evidence" ? <Link className="prototype-button primary" to={`/events/${caseId}/review`}>{view.nextAction.label}</Link> : view.nextAction.kind === "review_conclusion" ? <Link className="prototype-button primary" to={`/events/${caseId}/conclusion`}>{view.nextAction.label}</Link> : view.nextAction.kind === "view_conclusion_change" ? <Link className="prototype-button primary" to={`/events/${caseId}/conclusion`}>{view.nextAction.label}</Link> : <p className="event-muted">{view.lifecycle.summary}</p>}</aside></section>
    {isEditingFactors ? <EventFactorScopeEditor caseId={caseId} factors={view.scope.factors} onClose={() => setIsEditingFactors(false)} onSaved={async () => { setUpdateStatus("已更新因素；已核验证据会保留并重新归类，系统将继续自动研究"); await loadWorkbench(); }} /> : null}
  </main>;
}
