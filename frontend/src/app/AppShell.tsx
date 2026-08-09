import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { researchClient } from "../data/researchClient";
import type { EventResearchListItem } from "../domain/eventResearch";
import type { SearchHit } from "../domain/types";
import {
  researchOsApi,
  type ActiveResearchRun,
  type ResearchSession,
} from "./researchOsApi";

function pageLabel(pathname: string) {
  if (pathname === "/network") return "研究网络";
  if (pathname === "/monitoring") return "监控与版本";
  if (pathname.endsWith("/wiki")) return "Case Wiki 图谱";
  if (pathname.endsWith("/market")) return "市场与表达";
  if (pathname.includes("/monitor")) return "监控与版本";
  if (pathname.endsWith("/review")) return "待我审核";
  if (pathname.endsWith("/new")) return "从事件开始";
  return "研究调度";
}
const runStageLabels: Record<string, string> = {
  retrieve: "采集资料",
  parse: "解析原文",
  verify: "验证因素",
  review: "等待审核",
  planning: "准备范围",
  claim_review: "等待原子陈述审核",
  stopped: "已停止",
  failed: "运行失败",
};

type RunStageEvent = {
  seq: number;
  stage: string | null;
  status: string | null;
  message: string | null;
  details: Record<string, unknown>;
  created_at: string;
};

export function AppShell() {
  const location = useLocation();
  const [events, setEvents] = useState<EventResearchListItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [globalSearchMatches, setGlobalSearchMatches] = useState<SearchHit[]>(
    [],
  );
  const [globalSearchError, setGlobalSearchError] = useState(false);
  const [activeRuns, setActiveRuns] = useState<ActiveResearchRun[]>([]);
  const [activeRunEvents, setActiveRunEvents] = useState<
    Record<string, RunStageEvent[]>
  >({});
  const [activeRunEventErrors, setActiveRunEventErrors] = useState<
    Record<string, boolean>
  >({});
  const [runLoadError, setRunLoadError] = useState(false);
  const [drawerRun, setDrawerRun] = useState<ActiveResearchRun | null>(null);
  const [drawerEvents, setDrawerEvents] = useState<Array<{
    seq: number;
    stage: string | null;
    status: string | null;
    message: string | null;
    details: Record<string, unknown>;
    created_at: string;
  }> | null>(null);
  const [drawerError, setDrawerError] = useState<string | null>(null);
  const [session, setSession] = useState<ResearchSession | null>(null);
  useEffect(() => {
    const load = () =>
      researchClient
        .listEventResearch()
        .then(setEvents)
        .catch(() => setEvents([]));
    load();
    const refresh = window.setInterval(load, 20_000);
    return () => window.clearInterval(refresh);
  }, []);
  useEffect(() => {
    const query = searchQuery.trim();
    if (query.length < 2) {
      setGlobalSearchMatches([]);
      setGlobalSearchError(false);
      return;
    }
    let live = true;
    const timer = window.setTimeout(() => {
      researchClient
        .search(query)
        .then((hits) => {
          if (live) {
            setGlobalSearchMatches(hits);
            setGlobalSearchError(false);
          }
        })
        .catch(() => {
          if (live) {
            setGlobalSearchMatches([]);
            setGlobalSearchError(true);
          }
        });
    }, 120);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [searchQuery]);
  useEffect(() => {
    let live = true;
    const load = () =>
      researchOsApi
        .activeRuns()
        .then((response) => {
          if (live) {
            setActiveRuns(response.items);
            setRunLoadError(false);
          }
        })
        .catch(() => {
          if (live) {
            setActiveRuns([]);
            setActiveRunEvents({});
            setActiveRunEventErrors({});
            setRunLoadError(true);
          }
        });
    load();
    const refresh = window.setInterval(load, 15_000);
    return () => {
      live = false;
      window.clearInterval(refresh);
    };
  }, []);
  useEffect(() => {
    let live = true;
    researchOsApi
      .session()
      .then((value) => {
        if (live) setSession(value);
      })
      .catch(() => {
        if (live) setSession(null);
      });
    return () => {
      live = false;
    };
  }, []);
  const activeRunIds = activeRuns.map((run) => run.run_id).join(",");
  useEffect(() => {
    let live = true;
    if (!activeRunIds) {
      setActiveRunEvents({});
      setActiveRunEventErrors({});
      return;
    }
    const load = async () => {
      const results = await Promise.all(
        activeRuns.map(async (run) => {
          try {
            const response = await researchOsApi.runEvents(run.run_id);
            return {
              runId: run.run_id,
              events: response.items.map((event) => ({
                seq: event.seq,
                stage: event.stage ?? null,
                status: event.status ?? null,
                message: event.message ?? null,
                details: event.details ?? {},
                created_at: event.created_at,
              })),
              failed: false,
            };
          } catch {
            return { runId: run.run_id, events: [], failed: true };
          }
        }),
      );
      if (!live) return;
      setActiveRunEvents(
        Object.fromEntries(results.map((result) => [result.runId, result.events])),
      );
      setActiveRunEventErrors(
        Object.fromEntries(results.map((result) => [result.runId, result.failed])),
      );
    };
    load();
    const refresh = window.setInterval(load, 15_000);
    return () => {
      live = false;
      window.clearInterval(refresh);
    };
  }, [activeRunIds]);
  const needsReview = events.filter((event) => event.nextHumanAction).length;
  const searchMatches = useMemo(() => {
    const query = searchQuery.trim().toLocaleLowerCase("zh-CN");
    if (!query) return [];
    return events
      .filter((event) =>
        [event.eventTitle, event.companyName, event.ticker]
          .filter((value): value is string => Boolean(value))
          .some((value) => value.toLocaleLowerCase("zh-CN").includes(query)),
      )
      .slice(0, 8);
  }, [events, searchQuery]);
  function openDrawer(run: ActiveResearchRun) {
    setDrawerRun(run);
    setDrawerEvents(null);
    setDrawerError(null);
    researchOsApi
      .runEvents(run.run_id)
      .then((response) =>
        setDrawerEvents(
          response.items.map((event) => ({
            seq: event.seq,
            stage: event.stage ?? null,
            status: event.status ?? null,
            message: event.message ?? null,
            details: event.details ?? {},
            created_at: event.created_at,
          })),
        ),
      )
      .catch(() =>
        setDrawerError(
          "无法读取这次运行的事件链；系统不会以当前配置补写历史记录。",
        ),
      );
  }
  const canManageLegacyCases =
    session?.roles?.includes("case_administrator") ?? false;
  return (
    <div className="ros-shell">
      <aside className="ros-sidebar" aria-label="研究工作台导航">
        <Link to="/events" className="ros-brand">
          <span className="ros-mark">F</span>
          <span>Fund Engine</span>
        </Link>
        <span className="ros-nav-label">研究工作台</span>
        <nav className="ros-nav">
          <NavLink end to="/events">
            事件研究 <span>⌂</span>
          </NavLink>
          <NavLink to="/events?attention=1">
            待我审核 {needsReview > 0 && <b>{needsReview}</b>}
          </NavLink>
          <Link to="/events/new">资料收件箱</Link>
        </nav>
        <span className="ros-nav-label">研究资产</span>
        <nav className="ros-nav">
          <NavLink to="/network">研究网络</NavLink>
          <NavLink to="/monitoring">监控与版本</NavLink>
        </nav>
        {canManageLegacyCases && (
          <>
            <span className="ros-nav-label">运营治理</span>
            <nav className="ros-nav">
              <NavLink to="/governance/case-admissions">历史 Case 准入</NavLink>
            </nav>
          </>
        )}
        <div className="ros-profile">
          <b>{canManageLegacyCases ? "Case 管理员" : "研究员"}</b>
          <span>
            {session ? `${session.tenant_id} · ` : "投研团队 · "}证据与审核
          </span>
        </div>
      </aside>
      <div className="ros-main">
        <header className="ros-topbar">
          <span>
            事件研究 / <b>{pageLabel(location.pathname)}</b>
          </span>
          <div className="ros-topbar-search">
            <label className="ros-search">
              <span aria-hidden>⌕</span>
              <input
                aria-label="搜索事件、公司、命题或证据"
                placeholder="搜索事件、公司、命题或证据"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
              />
            </label>
            {searchQuery.trim() && (
              <section
                className="ros-search-results"
                aria-label="Case 搜索结果"
              >
                <p>
                  {globalSearchMatches.length
                    ? `全局已准入研究资产找到 ${globalSearchMatches.length} 条匹配`
                    : searchMatches.length
                      ? `当前 Case 清单中找到 ${searchMatches.length} 条匹配`
                      : globalSearchError
                        ? "全局检索暂不可读取；仅保留当前 Case 清单结果。"
                        : "未找到当前团队已准入的可展示记录。"}
                </p>
                {globalSearchMatches.length || searchMatches.length ? (
                  <ul>
                    {globalSearchMatches.map((hit) => (
                      <li key={`${hit.group}:${hit.id}`}>
                        <Link
                          to={hit.navigate_to}
                          onClick={() => setSearchQuery("")}
                        >
                          <strong>
                            <small>{hit.group}</small> {hit.title}
                          </strong>
                          <span>{hit.hint}</span>
                        </Link>
                      </li>
                    ))}
                    {searchMatches
                      .filter(
                        (event) =>
                          !globalSearchMatches.some(
                            (hit) => hit.navigate_to === `/events/${event.id}`,
                          ),
                      )
                      .map((event) => (
                        <li key={event.id}>
                          <Link
                            to={`/events/${event.id}`}
                            onClick={() => setSearchQuery("")}
                          >
                            <strong>
                              <small>Case</small> {event.eventTitle}
                            </strong>
                            <span>
                              {[event.companyName, event.ticker]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          </Link>
                        </li>
                      ))}
                  </ul>
                ) : (
                  <small>
                    搜索不会猜测当前团队未准入或不可展示的记录；可从“从事件开始”录入新材料。
                  </small>
                )}
              </section>
            )}
          </div>
          <Link className="ros-topbar__create" to="/events/new">
            ＋ 从事件开始
          </Link>
        </header>
        {runLoadError && (
          <section
            className="ros-run-strip ros-run-strip--error"
            aria-label="运行状态不可用"
          >
            <div className="ros-run-strip__left">
              <div>
                <strong>运行状态暂不可确认</strong>
                <span>
                  无法读取统一运行记录；系统不会用旧 Case 状态替代真实运行详情。
                </span>
              </div>
            </div>
          </section>
        )}
        {activeRuns.map((run) => {
          const runEvents = activeRunEvents[run.run_id];
          const latestActiveEvent = runEvents?.[runEvents.length - 1];
          const activeRunEventError = activeRunEventErrors[run.run_id];
          return (
            <section
              className="ros-run-strip"
              aria-label="系统正在运行"
              key={run.run_id}
            >
              <div className="ros-run-strip__left">
                <i className="ros-run-strip__pulse" />
                <div>
                  <strong>
                    系统正在运行 · {run.stage}
                    {runStageLabels[run.stage]
                      ? ` · ${runStageLabels[run.stage]}`
                      : ""}
                  </strong>
                  <span>
                    {(run.scope.allowed_source_types ?? []).join("、") ||
                      "未记录允许来源"}{" "}
                    · {run.case_title} · 已处理 {run.processed_count}
                    {latestActiveEvent
                      ? ` · 最近记录 · ${runStageLabels[latestActiveEvent.stage ?? ""] ?? latestActiveEvent.stage ?? "阶段"} · ${latestActiveEvent.message || "已记录阶段事件"}`
                      : activeRunEventError
                        ? " · 最近运行记录暂不可读取"
                        : " · 正在读取最近阶段记录"}
                  </span>
                </div>
              </div>
              <div className="ros-run-strip__actions">
                <button type="button" onClick={() => openDrawer(run)}>
                  展开运行详情
                </button>
                <Link to={`/events/${run.case_id}/monitor`}>
                  {run.next_action} →
                </Link>
              </div>
            </section>
          );
        })}
        <Outlet />
      </div>
      {drawerRun && (
        <GlobalRunDrawer
          run={drawerRun}
          events={drawerEvents}
          error={drawerError}
          onClose={() => setDrawerRun(null)}
        />
      )}
    </div>
  );
}

function GlobalRunDrawer({
  run,
  events,
  error,
  onClose,
}: {
  run: ActiveResearchRun;
  events: Array<{
    seq: number;
    stage: string | null;
    status: string | null;
    message: string | null;
    details: Record<string, unknown>;
    created_at: string;
  }> | null;
  error: string | null;
  onClose: () => void;
}) {
  const scope = run.scope;
  const scopeRows = [
    ["触发方式", scope.trigger],
    ["配置版本", scope.monitor_version_id],
    [
      "关键因素",
      (scope.factor_statements?.length
        ? scope.factor_statements
        : (scope.factor_ids ?? [])
      ).join("、"),
    ],
    ["允许来源", (scope.allowed_source_types ?? []).join("、")],
    ["资料预算", scope.budget],
  ];
  return (
    <aside className="ros-run-drawer" aria-label="全局运行详情">
      <header>
        <div>
          <p className="ros-eyebrow">ResearchRun · {run.run_id}</p>
          <h2>{run.case_title}</h2>
          <p>以下范围与事件均来自本次运行记录，不由当前 Case 配置回填。</p>
        </div>
        <button aria-label="关闭全局运行详情" type="button" onClick={onClose}>
          ×
        </button>
      </header>
      <div className="ros-drawer-body">
        <section className="ros-run-scope">
          <p className="ros-eyebrow">冻结范围</p>
          <dl>
            {scopeRows.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value || "未记录"}</dd>
              </div>
            ))}
          </dl>
        </section>
        {error ? (
          <p className="ros-error" role="alert">
            {error}
          </p>
        ) : !events ? (
          <div className="ros-empty ros-empty--compact">
            正在读取本次运行的事件链…
          </div>
        ) : (
          <ol className="ros-run-log">
            {events.length ? (
              events.map((event) => (
                <li key={event.seq}>
                  <span>{event.seq}</span>
                  <div>
                    <strong>
                      {event.stage || "阶段"} · {event.status || "已记录"}
                    </strong>
                    <p>{event.message || "无文字摘要"}</p>
                    <small>
                      {new Date(event.created_at).toLocaleString("zh-CN")} ·{" "}
                      {Object.entries(event.details || {})
                        .map(
                          ([key, value]) =>
                            `${key}: ${Array.isArray(value) ? value.join("、") : String(value)}`,
                        )
                        .join(" · ") || "无额外字段"}
                    </small>
                  </div>
                </li>
              ))
            ) : (
              <li>
                <span>—</span>
                <div>
                  <strong>未返回事件</strong>
                  <p>这次运行尚未记录可展示的阶段事件；不会以推测内容替代。</p>
                </div>
              </li>
            )}
          </ol>
        )}
      </div>
    </aside>
  );
}
