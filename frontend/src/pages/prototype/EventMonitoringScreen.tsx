import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventResearchListItem, EventWorkbench } from "../../domain/eventResearch";

export function EventMonitoringScreen() {
  const [params, setParams] = useSearchParams();
  const [events, setEvents] = useState<EventResearchListItem[]>([]);
  const [caseId, setCaseId] = useState(params.get("caseId") ?? "");
  const [view, setView] = useState<EventWorkbench | null>(null);
  useEffect(() => { void researchClient.listEventResearch().then((items) => { setEvents(items); setCaseId((current) => current && items.some((item) => item.id === current) ? current : items[0]?.id ?? ""); }); }, []);
  useEffect(() => { if (caseId) void researchClient.getEventWorkbench(caseId).then(setView); }, [caseId]);
  const change = (id: string) => { setCaseId(id); setParams({ caseId: id }); };
  return <main className="prototype-screen event-monitoring-screen"><header className="event-page-header"><div><p className="section-kicker">事件监测</p><h1>研究状态与后续变化</h1><p>监测的是某一条事件研究的证据、缺口和结论状态，而不是无边界的主题信息流。</p></div>{caseId ? <Link className="prototype-button" to={`/events/${caseId}`}>返回研究工作台</Link> : null}</header><section className="prototype-paper"><label htmlFor="event-monitor-case">监测事件</label><select id="event-monitor-case" value={caseId} onChange={(event) => change(event.target.value)}>{events.map((event) => <option key={event.id} value={event.id}>{event.eventTitle}</option>)}</select></section>{view ? <section className="event-workbench-grid"><div className="prototype-paper"><p className="section-kicker">当前监测状态</p><h2>{view.lifecycle.summary}</h2><p>{view.lifecycle.currentGap ? `当前缺口：${view.lifecycle.currentGap}` : "暂未发现需要人工补充的范围缺口。"}</p><dl><div><dt>已完成自动轮次</dt><dd>{view.lifecycle.currentRound}</dd></div><div><dt>当前结论</dt><dd>{view.conclusion.state === "published" ? "已发布" : view.conclusion.state === "ai_draft" ? "待复核草案" : "尚无结论"}</dd></div></dl></div><aside className="prototype-paper"><p className="section-kicker">下一次需要你</p><h2>{view.nextAction.label}</h2><p>系统会先自动扩展与核验；只有关键证据、研究范围或结论需要判断时才停下来。</p>{view.nextAction.kind === "review_evidence" ? <Link className="prototype-button primary" to={`/events/${caseId}/review`}>处理关键证据</Link> : view.nextAction.kind === "review_conclusion" ? <Link className="prototype-button primary" to={`/events/${caseId}/conclusion`}>复核结论草案</Link> : null}</aside></section> : <p>正在加载监测状态…</p>}</main>;
}
