import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { researchClient } from "../data/researchClient";
import type { EventResearchListItem } from "../domain/eventResearch";

const nav = [{ to: "/events", label: "事件研究", mark: "◆" }, { to: "/events?attention=1", label: "待我处理", mark: "✓" }];

export function AppShell() {
  const location = useLocation();
  const [events, setEvents] = useState<EventResearchListItem[]>([]);
  useEffect(() => { researchClient.listEventResearch().then(setEvents).catch(() => setEvents([])); const refresh = window.setInterval(() => researchClient.listEventResearch().then(setEvents).catch(() => undefined), 20_000); return () => window.clearInterval(refresh); }, []);
  const active = useMemo(() => events.filter((event) => ["extracting", "researching", "continuing"].includes(event.status)), [events]);
  const hasAttention = events.some((event) => event.nextHumanAction);
  return <div className="ros-shell"><aside className="ros-sidebar"><Link to="/events" className="ros-brand"><span>◐</span><div><strong>研究 OS</strong><small>事件驱动投研</small></div></Link><nav>{nav.map((item) => <NavLink key={item.to} to={item.to} className={({ isActive }) => isActive ? "is-active" : ""}><span>{item.mark}</span>{item.label}{item.label === "待我处理" && hasAttention && <em>!</em>}</NavLink>)}</nav><div className="ros-sidebar__foot"><span>研究网络</span><small>证据 · 审核 · 表达</small></div></aside><div className="ros-main"><header className="ros-topbar"><div className="ros-breadcrumb"><span>投研工作台</span><b>/</b><strong>{location.pathname.startsWith("/events/new") ? "新建事件" : location.pathname.includes("/wiki") ? "Case Wiki" : location.pathname.includes("/market") ? "市场与表达" : location.pathname.includes("/monitor") ? "监测与运行" : "事件研究"}</strong></div><Link className="ros-topbar__create" to="/events/new">+ 新建事件</Link></header>{active.length > 0 && <section className="ros-run-strip" aria-label="系统正在运行"><span className="ros-run-strip__pulse" /><div><strong>系统正在运行 {active.length} 个研究任务</strong><small>{active[0].eventTitle}：{active[0].statusSummary}</small></div><Link to={`/events/${active[0].id}/monitor`}>查看运行明细 →</Link></section>}<Outlet /></div></div>;
}
