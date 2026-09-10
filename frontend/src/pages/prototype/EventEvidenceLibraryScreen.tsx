import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type { EventResearchListItem, EventWorkbench } from "../../domain/eventResearch";

export function EventEvidenceLibraryScreen() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [events, setEvents] = useState<EventResearchListItem[]>([]);
  const [caseId, setCaseId] = useState(searchParams.get("caseId") ?? "");
  const [view, setView] = useState<EventWorkbench | null>(null);

  useEffect(() => {
    void researchClient.listEventResearch().then((items) => {
      setEvents(items);
      setCaseId((current) => current && items.some((item) => item.id === current) ? current : items[0]?.id ?? "");
    });
  }, []);
  useEffect(() => { if (caseId) void researchClient.getEventWorkbench(caseId).then(setView); }, [caseId]);
  const switchCase = (id: string) => {
    setCaseId(id);
    setSearchParams({ caseId: id });
  };

  return <main className="prototype-screen event-library-screen">
    <header className="event-page-header"><div><p className="section-kicker">事件上下文</p><h1>事件证据库</h1><p>只显示当前新闻事件的证据；切换事件后，不会混入上一项研究的材料。</p></div>{caseId ? <Link className="prototype-button" to={`/events/${caseId}`}>返回研究工作台</Link> : null}</header>
    <section className="prototype-paper"><label htmlFor="event-library-case">当前事件</label><select id="event-library-case" value={caseId} onChange={(event) => switchCase(event.target.value)}>{events.map((event) => <option key={event.id} value={event.id}>{event.eventTitle}</option>)}</select></section>
    {!view ? <p>正在加载该事件的证据…</p> : <section className="event-workbench-grid"><div className="prototype-paper"><p className="section-kicker">可追溯原始材料</p><h2>{view.evidence.length} 条事件内证据</h2>{view.evidence.length ? <ul className="event-citation-list">{view.evidence.map((item, index) => <li key={`${item.sourceUrl}-${index}`}><strong>{item.factorStatement} · {item.role}</strong><span>{item.excerpt}</span><small>{item.sourceTitle ?? item.sourceUrl ?? "来源待补充"} · 定位 {JSON.stringify(item.locator)} · 可用 {new Date(item.availableAt).toLocaleString("zh-CN")}</small></li>)}</ul> : <p>系统仍在搜证；只有进入当前事件的材料才会显示在这里。</p>}</div><aside className="prototype-paper"><p className="section-kicker">使用边界</p><h2>证据先于结论</h2><p>“已审核”材料才可进入正式结论；未审核材料只作为待核验候选，不会静默支持结论。</p><p className="event-muted">研究问题：{view.event.eventTitle}</p></aside></section>}
  </main>;
}
