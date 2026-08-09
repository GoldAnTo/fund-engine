import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { researchClient } from "../data/researchClient";
import type { EventResearchListItem } from "../domain/eventResearch";

function pageLabel(pathname: string) { if (pathname === "/network") return "研究网络"; if (pathname === "/monitoring") return "监控与版本"; if (pathname.endsWith("/wiki")) return "Case Wiki 图谱"; if (pathname.endsWith("/market")) return "市场与表达"; if (pathname.includes("/monitor")) return "监控与版本"; if (pathname.endsWith("/review")) return "待我审核"; if (pathname.endsWith("/new")) return "从事件开始"; return "研究调度"; }

export function AppShell() {
  const location = useLocation(); const [events, setEvents] = useState<EventResearchListItem[]>([]);
  useEffect(() => { const load = () => researchClient.listEventResearch().then(setEvents).catch(() => setEvents([])); load(); const refresh = window.setInterval(load, 20_000); return () => window.clearInterval(refresh); }, []);
  const active = useMemo(() => events.filter((event) => ["extracting", "researching", "continuing"].includes(event.status)), [events]); const needsReview = events.filter((event) => event.nextHumanAction).length;
  return <div className="ros-shell"><aside className="ros-sidebar" aria-label="研究工作台导航"><Link to="/events" className="ros-brand"><span className="ros-mark">F</span><span>Fund Engine</span></Link><span className="ros-nav-label">研究工作台</span><nav className="ros-nav"><NavLink end to="/events">事件研究 <span>⌂</span></NavLink><NavLink to="/events?attention=1">待我审核 {needsReview > 0 && <b>{needsReview}</b>}</NavLink><Link to="/events/new">资料收件箱</Link></nav><span className="ros-nav-label">研究资产</span><nav className="ros-nav"><NavLink to="/network">研究网络</NavLink><NavLink to="/monitoring">监控与版本</NavLink></nav><div className="ros-profile"><b>研究员</b><span>投研团队 · 证据与审核</span></div></aside><div className="ros-main"><header className="ros-topbar"><span>事件研究 / <b>{pageLabel(location.pathname)}</b></span><label className="ros-search"><span aria-hidden>⌕</span><input aria-label="搜索事件、公司、命题或证据" placeholder="搜索事件、公司、命题或证据" /></label><Link className="ros-topbar__create" to="/events/new">＋ 从事件开始</Link></header>{active.length > 0 && <section className="ros-run-strip" aria-label="系统正在运行"><div className="ros-run-strip__left"><i className="ros-run-strip__pulse" /><div><strong>系统正在补证</strong><span>{active[0].eventTitle} · {active[0].statusSummary}</span></div></div><Link to={`/events/${active[0].id}/monitor`}>查看运行详情 →</Link></section>}<Outlet /></div></div>;
}
