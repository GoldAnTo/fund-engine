import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventWorkbench } from "../../domain/eventResearch";

export function EventResearchBasisScreen() {
  const { caseId = "" } = useParams();
  const [view, setView] = useState<EventWorkbench | null>(null);
  useEffect(() => { if (caseId) void researchClient.getEventWorkbench(caseId).then(setView); }, [caseId]);
  const evidenceByFactor = useMemo(() => new Map((view?.factors ?? []).map((factor) => [factor.statement, (view?.evidence ?? []).filter((item) => item.factorStatement === factor.statement)])), [view]);
  if (!view) return <main className="prototype-screen"><p>正在载入研究依据…</p></main>;
  return <main className="prototype-screen event-basis-screen"><header className="event-page-header"><div><p className="section-kicker">事件研究 · 可复核依据</p><h1>研究依据</h1><p>{view.event.eventTitle}：从因素、证据到结论，逐层查看；图谱不是理解研究的唯一入口。</p></div><Link className="prototype-button" to={`/events/${caseId}`}>返回结论工作台</Link></header><section className="prototype-paper"><p className="section-kicker">当前结论边界</p><h2>{view.conclusion.state === "published" ? "已确认正式结论" : view.conclusion.state === "ai_draft" ? "待审核结论草案" : "尚不能下结论"}</h2><p>{view.conclusion.text}</p></section><section className="prototype-paper"><p className="section-kicker">因素 → 原始证据</p>{view.factors.map((factor) => <article className="event-factor-card" key={factor.position}><strong>{factor.position}. {factor.statement}</strong><span>支持 {factor.reviewedSupportCount} · 反证 {factor.reviewedContradictionCount}</span>{(evidenceByFactor.get(factor.statement) ?? []).map((item, index) => <p key={`${item.sourceUrl}-${index}`} className="event-basis-evidence"><b>{item.reviewState === "reviewed" ? "已审核" : "待审核"}</b> · {item.excerpt}<small>{item.sourceTitle ?? item.sourceUrl ?? "来源待补充"} · 定位 {JSON.stringify(item.locator)}</small></p>)}{!(evidenceByFactor.get(factor.statement)?.length) ? <small>该因素尚未找到事件内证据。</small> : null}</article>)}</section><section className="event-workbench-grid"><div className="prototype-paper"><p className="section-kicker">原始资料</p><p>需要核对全文、定位或审核状态时，在事件证据库中查看。</p><Link className="prototype-button" to={`/library?caseId=${caseId}`}>查看事件证据库</Link></div><div className="prototype-paper"><p className="section-kicker">后续变化</p><p>监测页会说明新材料是否改变当前研究的结论或是否需要再研究。</p><Link className="prototype-button" to={`/versions?caseId=${caseId}`}>查看事件监测</Link></div></section></main>;
}
