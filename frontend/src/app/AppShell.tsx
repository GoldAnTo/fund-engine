import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { researchClient } from "../data/researchClient";
import type { EventResearchListItem } from "../domain/eventResearch";
import { researchOsApi, type ActiveResearchRun } from "./researchOsApi";

function pageLabel(pathname: string) { if (pathname === "/network") return "研究网络"; if (pathname === "/monitoring") return "监控与版本"; if (pathname.endsWith("/wiki")) return "Case Wiki 图谱"; if (pathname.endsWith("/market")) return "市场与表达"; if (pathname.includes("/monitor")) return "监控与版本"; if (pathname.endsWith("/review")) return "待我审核"; if (pathname.endsWith("/new")) return "从事件开始"; return "研究调度"; }

export function AppShell() {
  const location = useLocation(); const [events, setEvents] = useState<EventResearchListItem[]>([]);
  const [activeRuns, setActiveRuns] = useState<ActiveResearchRun[]>([]);
  const [runLoadError, setRunLoadError] = useState(false);
  useEffect(() => { const load = () => researchClient.listEventResearch().then(setEvents).catch(() => setEvents([])); load(); const refresh = window.setInterval(load, 20_000); return () => window.clearInterval(refresh); }, []);
  useEffect(() => { let live = true; const load = () => researchOsApi.activeRuns().then((response) => { if (live) { setActiveRuns(response.items); setRunLoadError(false); } }).catch(() => { if (live) setRunLoadError(true); }); load(); const refresh = window.setInterval(load, 15_000); return () => { live = false; window.clearInterval(refresh); }; }, []);
  const needsReview = events.filter((event) => event.nextHumanAction).length; const active = activeRuns[0];
  return <div className="ros-shell"><aside className="ros-sidebar" aria-label="研究工作台导航"><Link to="/events" className="ros-brand"><span className="ros-mark">F</span><span>Fund Engine</span></Link><span className="ros-nav-label">研究工作台</span><nav className="ros-nav"><NavLink end to="/events">事件研究 <span>⌂</span></NavLink><NavLink to="/events?attention=1">待我审核 {needsReview > 0 && <b>{needsReview}</b>}</NavLink><Link to="/events/new">资料收件箱</Link></nav><span className="ros-nav-label">研究资产</span><nav className="ros-nav"><NavLink to="/network">研究网络</NavLink><NavLink to="/monitoring">监控与版本</NavLink></nav><div className="ros-profile"><b>研究员</b><span>投研团队 · 证据与审核</span></div></aside><div className="ros-main"><header className="ros-topbar"><span>事件研究 / <b>{pageLabel(location.pathname)}</b></span><label className="ros-search"><span aria-hidden>⌕</span><input aria-label="搜索事件、公司、命题或证据" placeholder="搜索事件、公司、命题或证据" /></label><Link className="ros-topbar__create" to="/events/new">＋ 从事件开始</Link></header>{runLoadError && <section className="ros-run-strip ros-run-strip--error" aria-label="运行状态不可用"><div className="ros-run-strip__left"><div><strong>运行状态暂不可确认</strong><span>无法读取统一运行记录；系统不会用旧 Case 状态替代真实运行详情。</span></div></div></section>}{active && <section className="ros-run-strip" aria-label="系统正在运行"><div className="ros-run-strip__left"><i className="ros-run-strip__pulse" /><div><strong>系统正在运行 · {active.stage}</strong><span>{(active.scope.allowed_source_types ?? []).join("、") || "未记录允许来源"} · {active.case_title} · 已处理 {active.processed_count}{activeRuns.length > 1 ? ` · 另有 ${activeRuns.length - 1} 个运行` : ""}</span></div></div><Link to={`/events/${active.case_id}/monitor`}>{active.next_action} →</Link></section>}<Outlet /></div></div>;
}
