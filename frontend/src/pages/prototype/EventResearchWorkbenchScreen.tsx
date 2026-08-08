import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventWorkbench } from "../../domain/eventResearch";
import { EventFactorScopeEditor } from "./EventFactorScopeEditor";

function conclusionTitle(state: EventWorkbench["conclusion"]["state"]) {
  if (state === "cannot_conclude") return "暂不能下结论";
  if (state === "published") return "已确认正式结论";
  return "AI 结论草案";
}

function factorConfidence(support: number, contradiction: number, pending: number) {
  if (support === 0 && contradiction === 0) return pending > 0 ? "待核验" : "证据不足";
  if (support > contradiction) return "支持更强";
  if (contradiction > support) return "反证更强";
  return "支持与反证相当";
}

function nextResearchAttempt(view: EventWorkbench) {
  if (view.lifecycle.status === "awaiting_scope") return "确认研究因素后，系统会按更新后的范围继续检索。";
  if (view.progress.currentGap) return `围绕“${view.progress.currentGap}”继续寻找能区分不同解释的材料。`;
  return "继续核验当前范围内的候选证据，并在出现需要人工判断时通知你。";
}

export function EventResearchWorkbenchScreen() {
  const { caseId = "" } = useParams();
  const [view, setView] = useState<EventWorkbench | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isEditingFactors, setIsEditingFactors] = useState(false);
  const [updateStatus, setUpdateStatus] = useState<string | null>(null);
  const factorEditTrigger = useRef<HTMLButtonElement>(null);
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
  return <main className="prototype-screen event-workbench" data-layout="conclusion-first">
    <header className="event-page-header"><div><p className="section-kicker">事件研究 · 第 {view.lifecycle.currentRound} 轮</p><h1>{view.event.eventTitle}</h1><p>{view.event.companyName ?? "公司待确认"}{view.event.ticker ? ` · ${view.event.ticker}` : ""}</p></div><Link className="prototype-button" to="/events">返回事件列表</Link></header>
    <section className="prototype-paper event-conclusion"><p className="section-kicker">当前判断</p><h2>{conclusionTitle(view.conclusion.state)}</h2><p>{view.conclusion.text}</p><p className="event-conclusion__confidence">结论置信度：{view.conclusion.confidence === "high" ? "高" : view.conclusion.confidence === "medium" ? "中" : "低"}</p>{view.progress.currentGap ? <p className="event-conclusion__gap">当前缺口：{view.progress.currentGap}</p> : null}<Link className="event-conclusion__impact-link" to={`/events/${caseId}/impact`}>查看影响传导与披露边界</Link></section>
    <section className="event-progress" aria-label="研究进度"><span className="event-progress__chip">已核验 {view.progress.verified}</span><span className="event-progress__chip">待审核 {view.progress.pending}</span><span className="event-progress__chip">无效来源不计结论 {view.progress.invalidSource}</span><span className="event-progress__chip">当前缺口：{view.progress.currentGap ?? "当前未发现范围缺口"}</span></section>
    {updateStatus ? <p className="event-update-status" role="status">{updateStatus}</p> : null}
    <section className="event-workbench-grid"><div className="prototype-paper event-factors"><p className="section-kicker">竞争性因素</p><h2>系统正在区分什么</h2>{view.factors.map((factor) => { const evidence = view.evidence.filter((item) => item.factorStatement === factor.statement && item.reviewState === "reviewed"); const confidence = factorConfidence(factor.reviewedSupportCount, factor.reviewedContradictionCount, factor.pendingProposalCount); return <article className="event-factor-card" key={factor.position}><strong>{factor.position}. {factor.statement}</strong>{factor.description ? <p className="event-factor-card__description">{factor.description}</p> : null}<span>支持 {factor.reviewedSupportCount} · 反证 {factor.reviewedContradictionCount} · 待审核 {factor.pendingProposalCount}</span><small>判断：{confidence}</small>{factor.currentGap ? <small>缺口：{factor.currentGap}</small> : null}{view.conclusion.state === "ai_draft" ? <div className="event-factor-card__evidence"><b>因素置信度：{confidence}</b><p>支持证据：{evidence.filter((item) => item.role === "supports").map((item) => item.excerpt).slice(0, 2).join("；") || "暂无已审核的支持证据"}</p><p>反驳证据：{evidence.filter((item) => item.role === "contradicts").map((item) => item.excerpt).slice(0, 2).join("；") || "暂无已审核的反驳证据"}</p></div> : null}</article>; })}{view.factors.length === 0 || view.lifecycle.status === "exhausted" ? <section className="event-evidence-boundary" aria-label="证据边界"><h3>当前证据边界</h3><p>当前缺什么：{view.progress.currentGap ?? "尚无可确认的范围缺口。"}</p><p>已检查：已核验 {view.progress.verified} 条证据；已排除 {view.progress.invalidSource} 条无效来源，它们不计入结论。</p><p>下一次系统会尝试：{nextResearchAttempt(view)}</p></section> : null}</div><aside className="prototype-paper event-next-action"><p className="section-kicker">下一步</p><h2>{view.nextAction.kind === "wait" ? "系统自动研究中" : view.nextAction.label}</h2><p>{view.lifecycle.summary}</p>{view.nextAction.kind === "wait" ? <p className="event-next-action__support">系统正在自动核验，无需手动推进。 <Link className="event-next-action__basis" to={`/events/${caseId}/basis`}>查看研究依据</Link></p> : view.nextAction.kind === "edit_factors" ? <button ref={factorEditTrigger} className="prototype-button primary" type="button" onClick={() => setIsEditingFactors(true)}>{view.nextAction.label}</button> : view.nextAction.kind === "review_evidence" ? <Link className="prototype-button primary" to={`/events/${caseId}/review`}>{view.nextAction.label}</Link> : view.nextAction.kind === "review_conclusion" ? <Link className="prototype-button primary" to={`/events/${caseId}/conclusion`}>{view.nextAction.label}</Link> : <Link className="prototype-button primary" to={`/events/${caseId}/conclusion`}>{view.nextAction.label}</Link>}</aside></section>
    {isEditingFactors ? <EventFactorScopeEditor caseId={caseId} factors={view.scope.factors} onClose={() => { setIsEditingFactors(false); window.setTimeout(() => factorEditTrigger.current?.focus(), 0); }} onSaved={async () => { setUpdateStatus("已更新因素；已核验证据会保留并重新归类，系统将继续自动研究"); await loadWorkbench(); }} /> : null}
  </main>;
}
