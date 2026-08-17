import { useEffect, useMemo, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { researchClient } from "../data/researchClient";
import type { EventResearchListItem } from "../domain/eventResearch";
import {
  formatRunEventDetails,
  runFrequencyLabel,
  runStageLabel,
  runStatusLabel,
  runStopReasonLabel,
  runTriggerLabel,
} from "../domain/runPresentation";
import { sourceTypeListLabel } from "../domain/sourcePresentation";
import type { SearchHit } from "../domain/types";
import {
  researchOsApi,
  type ActiveFundDisclosureSyncRun,
  type ActiveResearchRun,
  type ResearchSession,
  type ResearchWorkerStatus,
} from "./researchOsApi";

function pageLabel(pathname: string) {
  if (pathname === "/network") return "研究网络";
  if (pathname === "/monitoring") return "监控与版本";
  if (pathname.endsWith("/wiki")) return "Case Wiki 图谱";
  if (pathname.endsWith("/market")) return "市场与表达";
  if (pathname.includes("/monitor")) return "监控与版本";
  if (pathname.endsWith("/review")) return "待我审核";
  if (pathname.endsWith("/automatic-research")) return "自动研究";
  if (pathname.endsWith("/new")) return "自动研究";
  return "研究调度";
}

function isAutomaticResearchProcessPath(pathname: string): boolean {
  return /^\/events\/[^/]+\/automatic-research\/?$/.test(pathname);
}
type RunStageEvent = {
  seq: number;
  stage: string | null;
  status: string | null;
  message: string | null;
  details: Record<string, unknown>;
  created_at: string;
};

function queuedRunExecutionState(
  workerStatus: ResearchWorkerStatus | null,
  unreadable: boolean,
): { ariaLabel: string; heading: string; detail: string } | null {
  if (workerStatus?.status === "unavailable") {
    return {
      ariaLabel: "已排队但执行器未启动",
      heading: "排队中 · 执行器未启动",
      detail: "本次范围已冻结，尚未执行；待执行器恢复后才会领取",
    };
  }
  if (workerStatus?.status === "stale") {
    return {
      ariaLabel: "已排队但执行器心跳失联",
      heading: "排队中 · 执行器心跳已失联",
      detail: "本次范围已冻结，尚未执行；请恢复执行器后确认新的阶段记录",
    };
  }
  if (unreadable) {
    return {
      ariaLabel: "已排队但执行器状态不可确认",
      heading: "排队中 · 执行器状态暂不可确认",
      detail: "本次范围已冻结，无法确认是否会被领取，不会把排队显示为执行中",
    };
  }
  return null;
}

export function AppShell() {
  const location = useLocation();
  const suppressGlobalRunStrips = isAutomaticResearchProcessPath(location.pathname);
  const [events, setEvents] = useState<EventResearchListItem[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [globalSearchMatches, setGlobalSearchMatches] = useState<SearchHit[]>(
    [],
  );
  const [globalSearchError, setGlobalSearchError] = useState(false);
  const [activeRuns, setActiveRuns] = useState<ActiveResearchRun[]>([]);
  const [activeFundRuns, setActiveFundRuns] = useState<ActiveFundDisclosureSyncRun[]>([]);
  const [activeRunEvents, setActiveRunEvents] = useState<
    Record<string, RunStageEvent[]>
  >({});
  const [activeRunEventErrors, setActiveRunEventErrors] = useState<
    Record<string, boolean>
  >({});
  const [runLoadError, setRunLoadError] = useState(false);
  const [workerStatus, setWorkerStatus] = useState<ResearchWorkerStatus | null>(null);
  const [workerStatusError, setWorkerStatusError] = useState(false);
  const [fundRunLoadError, setFundRunLoadError] = useState(false);
  const [runReload, setRunReload] = useState(0);
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
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [session, setSession] = useState<ResearchSession | null>(null);
  useEffect(() => {
    let live = true;
    let latestRequest = 0;
    const load = () => {
      const request = ++latestRequest;
      return researchClient
        .listEventResearch()
        .then((items) => {
          if (live && request === latestRequest) setEvents(items);
        })
        .catch(() => {
          if (live && request === latestRequest) setEvents([]);
        });
    };
    load();
    const refresh = window.setInterval(load, 20_000);
    const refreshAfterWorkflow = () => {
      void load();
    };
    window.addEventListener(
      "research-os-workflow-refresh",
      refreshAfterWorkflow,
    );
    return () => {
      live = false;
      window.clearInterval(refresh);
      window.removeEventListener(
        "research-os-workflow-refresh",
        refreshAfterWorkflow,
      );
    };
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
    let latestRequest = 0;
    const load = () => {
      const request = ++latestRequest;
      return researchOsApi
        .activeRuns()
        .then((response) => {
          if (live && request === latestRequest) {
            setActiveRuns(response.items);
            setRunLoadError(false);
          }
        })
        .catch(() => {
          if (live && request === latestRequest) {
            setActiveRuns([]);
            setActiveRunEvents({});
            setActiveRunEventErrors({});
            setRunLoadError(true);
          }
        });
    };
    load();
    const refresh = window.setInterval(load, 15_000);
    const refreshAfterRunStart = () => {
      void load();
      // The route commits the frozen scope before it calls the provider. A
      // short second read crosses that transaction boundary, rather than
      // making researchers wait for the normal background poll.
      window.setTimeout(load, 350);
    };
    const refreshAfterWorkflow = () => {
      void load();
    };
    window.addEventListener("research-os-run-refresh", refreshAfterRunStart);
    window.addEventListener(
      "research-os-workflow-refresh",
      refreshAfterWorkflow,
    );
    return () => {
      live = false;
      window.clearInterval(refresh);
      window.removeEventListener("research-os-run-refresh", refreshAfterRunStart);
      window.removeEventListener(
        "research-os-workflow-refresh",
        refreshAfterWorkflow,
      );
    };
  }, [runReload]);
  useEffect(() => {
    let live = true;
    const load = () => researchOsApi
      .workerStatus()
      .then((status) => {
        if (!live) return;
        setWorkerStatus(status);
        setWorkerStatusError(false);
      })
      .catch(() => {
        if (!live) return;
        setWorkerStatus(null);
        setWorkerStatusError(true);
      });
    void load();
    const refresh = window.setInterval(load, 15_000);
    return () => {
      live = false;
      window.clearInterval(refresh);
    };
  }, [runReload]);
  useEffect(() => {
    let live = true;
    const load = () =>
      researchOsApi
        .activeFundDisclosureSyncRuns()
        .then((response) => {
          if (!live) return;
          // Older fixtures can answer every endpoint with the ResearchRun
          // shape. Do not misrepresent one of those responses as a fund run.
          setActiveFundRuns(
            response.items.filter(
              (item) => Array.isArray(item.fund_codes) && Array.isArray(item.stock_codes),
            ),
          );
          setFundRunLoadError(false);
        })
        .catch(() => {
          if (live) {
            setActiveFundRuns([]);
            setFundRunLoadError(true);
          }
        });
    load();
    const refresh = window.setInterval(load, 15_000);
    const refreshAfterFundRunChange = () => {
      void load();
      // The request commits its frozen scope before reaching the provider.
      // Re-read once just after that boundary instead of waiting 15 seconds.
      window.setTimeout(load, 350);
    };
    window.addEventListener("research-os-run-refresh", refreshAfterFundRunChange);
    return () => {
      live = false;
      window.clearInterval(refresh);
      window.removeEventListener("research-os-run-refresh", refreshAfterFundRunChange);
    };
  }, [runReload]);
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
    let latestRequest = 0;
    if (!activeRunIds) {
      setActiveRunEvents({});
      setActiveRunEventErrors({});
      return;
    }
    const load = async () => {
      const request = ++latestRequest;
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
      if (!live || request !== latestRequest) return;
      setActiveRunEvents(
        Object.fromEntries(results.map((result) => [result.runId, result.events])),
      );
      setActiveRunEventErrors(
        Object.fromEntries(results.map((result) => [result.runId, result.failed])),
      );
    };
    load();
    const refresh = window.setInterval(load, 15_000);
    const refreshAfterWorkflow = () => {
      void load();
    };
    window.addEventListener(
      "research-os-workflow-refresh",
      refreshAfterWorkflow,
    );
    return () => {
      live = false;
      window.clearInterval(refresh);
      window.removeEventListener(
        "research-os-workflow-refresh",
        refreshAfterWorkflow,
      );
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
  async function openDrawer(run: ActiveResearchRun) {
    setDrawerRun(run);
    setDrawerEvents(null);
    setDrawerError(null);
    setDrawerLoading(true);
    try {
      const response = await researchOsApi.runEvents(run.run_id);
      setDrawerEvents(
        response.items.map((event) => ({
            seq: event.seq,
            stage: event.stage ?? null,
            status: event.status ?? null,
            message: event.message ?? null,
            details: event.details ?? {},
            created_at: event.created_at,
          })),
      );
    } catch {
      setDrawerError(
        "无法读取这次运行的事件链；系统不会以当前配置补写历史记录。",
      );
    } finally {
      setDrawerLoading(false);
    }
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
          <Link to="/events/new">自动研究</Link>
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
                    搜索不会猜测当前团队未准入或不可展示的记录；可从“自动研究”入口提交主题或材料。
                  </small>
                )}
              </section>
            )}
          </div>
          <Link className="ros-topbar__create" to="/events/new">
            ＋ 自动研究
          </Link>
        </header>
        {!suppressGlobalRunStrips && runLoadError && (
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
            <div className="ros-run-strip__actions">
              <button type="button" onClick={() => setRunReload((value) => value + 1)}>
                重新读取统一运行记录
              </button>
            </div>
          </section>
        )}
        {!suppressGlobalRunStrips && fundRunLoadError && (
          <section
            className="ros-run-strip ros-run-strip--error"
            aria-label="基金披露运行状态不可用"
          >
            <div className="ros-run-strip__left">
              <div>
                <strong>基金披露运行状态暂不可确认</strong>
                <span>
                  无法读取基金披露补充的实时阶段；不会用旧披露或当前配置替代运行记录。
                </span>
              </div>
            </div>
            <div className="ros-run-strip__actions">
              <button type="button" onClick={() => setRunReload((value) => value + 1)}>
                重新读取基金披露运行
              </button>
            </div>
          </section>
        )}
        {!suppressGlobalRunStrips && activeRuns.map((run) => {
          const runEvents = activeRunEvents[run.run_id];
          const latestActiveEvent = runEvents?.[runEvents.length - 1];
          const activeRunEventError = activeRunEventErrors[run.run_id];
          const queuedExecution = run.status === "queued"
            ? queuedRunExecutionState(workerStatus, workerStatusError)
            : null;
          return (
            <section
              className={`ros-run-strip${queuedExecution ? " ros-run-strip--error" : ""}`}
              aria-label={queuedExecution?.ariaLabel ?? "系统正在运行"}
              key={run.run_id}
            >
              <div className="ros-run-strip__left">
                {!queuedExecution && <i className="ros-run-strip__pulse" />}
                <div>
                  <strong>
                    {queuedExecution?.heading ?? `${runStatusLabel(run.status)} · ${runStageLabel(run.stage)}`}
                  </strong>
                  <span>
                    {sourceTypeListLabel(run.scope.allowed_source_types)}{" "}
                    · {run.case_title} · 已处理 {run.processed_count}
                    {queuedExecution
                      ? ` · ${queuedExecution.detail}`
                      : latestActiveEvent
                      ? ` · 最近记录 · ${runStageLabel(latestActiveEvent.stage)} · ${latestActiveEvent.message || "已记录阶段事件"}`
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
        {!suppressGlobalRunStrips && activeFundRuns.map((run) => (
          <section
            className="ros-run-strip"
            aria-label="系统正在运行"
            key={`fund-disclosure-${run.run_id}`}
          >
            <div className="ros-run-strip__left">
              <i className="ros-run-strip__pulse" />
              <div>
                <strong>
                  基金披露补充 · {runTriggerLabel(run.trigger)}
                </strong>
                <span>
                  基金 {run.fund_codes.join("、")} · 股票 {run.stock_codes.join("、") || "未绑定"}
                  {` · ${run.case_title} · ${runStageLabel(run.stage)} · ${run.message}`}
                </span>
              </div>
            </div>
            <div className="ros-run-strip__actions">
              <Link to={`/events/${run.case_id}/market`}>
                查看基金披露运行 →
              </Link>
            </div>
          </section>
        ))}
        <Outlet />
      </div>
      {!suppressGlobalRunStrips && drawerRun && (
        <GlobalRunDrawer
          run={drawerRun}
          events={drawerEvents}
          error={drawerError}
          loading={drawerLoading}
          onRetry={() => void openDrawer(drawerRun)}
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
  loading,
  onRetry,
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
  loading: boolean;
  onRetry: () => void;
  onClose: () => void;
}) {
  const scope = run.scope;
  const scopeRows = [
    ["触发方式", runTriggerLabel(scope.trigger)],
    ["配置版本", scope.monitor_version_id],
    [
      "关键因素",
      (scope.factor_statements?.length
        ? scope.factor_statements
        : (scope.factor_ids ?? [])
      ).join("、"),
    ],
    ["允许来源", sourceTypeListLabel(scope.allowed_source_types)],
    ["资料预算", scope.budget],
    ["执行频率", runFrequencyLabel(scope.frequency)],
    ["下一验证事件", scope.next_verification_event],
    ["配置人", scope.configured_by],
    ["本次配置依据", scope.configuration_change_reason],
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
          <section className="ros-empty ros-empty--compact" role="alert">
            <strong>本次运行事件暂不可读取</strong>
            <p>
              冻结范围仍可查看；但阶段、排除理由和候选输出暂无法确认，
              不会以当前配置补写历史记录。
            </p>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              disabled={loading}
              onClick={onRetry}
            >
              {loading ? "正在重新读取…" : "重新读取本次运行事件"}
            </button>
          </section>
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
                      {runStageLabel(event.stage)} · {runStatusLabel(event.status)}
                    </strong>
                    <p>{event.message || "无文字摘要"}</p>
                    <small>
                      {new Date(event.created_at).toLocaleString("zh-CN")} ·{" "}
                      {formatRunEventDetails(event.details || {}) || "无额外字段"}
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
