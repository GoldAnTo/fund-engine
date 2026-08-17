import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import {
  automaticResearchPollDelay,
  automaticResearchStatusLabel,
  automaticStageStatusLabel,
  formatAutomaticDuration,
  formatAutomaticTimestamp,
  normalizeAutomaticResearchView,
  stableRepeatedTextEntries,
  type AutomaticResearchSource,
  type AutomaticResearchView,
} from "../../domain/automaticResearch";

export const AUTOMATIC_RESEARCH_PROCESS_COLORS = {
  machineLabelBackground: { lightness: .94, chroma: .024, hue: 245 },
  machineLabelText: { lightness: .34, chroma: .055, hue: 245 },
} as const;

const automaticResearchProcessStyle: CSSProperties & {
  "--automatic-process-machine-label-background": string;
  "--automatic-process-machine-label-text": string;
} = {
  "--automatic-process-machine-label-background": toCssOklch(
    AUTOMATIC_RESEARCH_PROCESS_COLORS.machineLabelBackground,
  ),
  "--automatic-process-machine-label-text": toCssOklch(
    AUTOMATIC_RESEARCH_PROCESS_COLORS.machineLabelText,
  ),
};

function toCssOklch(color: { lightness: number; chroma: number; hue: number }): string {
  return `oklch(${color.lightness} ${color.chroma} ${color.hue})`;
}

class InvalidAutomaticResearchProjectionError extends Error {}

function isActive(view: AutomaticResearchView): boolean {
  return view.status === "queued" || view.status === "running";
}

function safeSourceUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function sourceRoleLabel(role: string): string {
  if (role === "supports" || role === "support") return "支持证据";
  if (role === "contradicts" || role === "counter_evidence") return "反证";
  if (role === "contextualizes" || role === "context") return "背景资料";
  return "研究资料";
}

function currentStage(view: AutomaticResearchView) {
  return view.stages.find((stage) => stage.status === "running")
    || view.stages.find((stage) => stage.status === "failed")
    || (view.status === "completed"
      ? view.stages[view.stages.length - 1]
      : view.stages.find((stage) => stage.status === "pending"));
}

function queuedRetryView(
  previous: AutomaticResearchView,
  runId: string,
): AutomaticResearchView {
  return {
    ...previous,
    runId,
    status: "queued",
    stages: previous.stages.map((stage) => ({
      ...stage,
      status: "pending",
      summary: `等待${stage.label}`,
      startedAt: null,
      completedAt: null,
    })),
    stats: { sourceCount: 0, admittedEvidenceCount: 0, skippedCount: 0, durationSeconds: 0 },
    recentActivity: ["新的自动研究已排队"],
    exceptions: [],
    failureReason: null,
    result: null,
  };
}

function SourceItem({ source }: { source: AutomaticResearchSource }) {
  const title = source.title?.trim() || "未命名来源";
  const href = safeSourceUrl(source.url);
  return (
    <li>
      <span className="automatic-research-process__source-role">
        {sourceRoleLabel(source.role)}
      </span>
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer">{title}</a>
      ) : (
        <span>{title}</span>
      )}
      <small>自动纳入</small>
    </li>
  );
}

export function AutomaticResearchPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const [view, setView] = useState<AutomaticResearchView | null>(null);
  const [loading, setLoading] = useState(Boolean(caseId));
  const [loadError, setLoadError] = useState<"unavailable" | "invalid" | null>(null);
  const [transientError, setTransientError] = useState(false);
  const [retryError, setRetryError] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [reading, setReading] = useState(false);
  const [pollVersion, setPollVersion] = useState(0);
  const mountedRef = useRef(false);
  const requestTokenRef = useRef(0);
  const flowTokenRef = useRef(0);
  const seedViewRef = useRef<AutomaticResearchView | null>(null);
  const activeReadRef = useRef<Promise<AutomaticResearchView> | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestTokenRef.current += 1;
      flowTokenRef.current += 1;
    };
  }, []);

  useEffect(() => {
    const flowToken = ++flowTokenRef.current;
    let timer: number | null = null;
    const seed = seedViewRef.current?.caseId === caseId ? seedViewRef.current : null;
    seedViewRef.current = null;
    let lastView = seed;
    let consecutiveFailures = 0;
    setView(seed);
    setLoadError(null);
    setTransientError(false);
    setRetryError(false);
    setRetrying(false);
    setLoading(Boolean(caseId) && !seed);

    if (!caseId) return undefined;

    const load = async () => {
      const existingRead = activeReadRef.current;
      if (existingRead) {
        try {
          await existingRead;
        } catch {
          // The owning flow presents the failure. A newer flow waits so GETs stay serialized.
        }
        if (!mountedRef.current || flowToken !== flowTokenRef.current) return;
      }
      const requestToken = ++requestTokenRef.current;
      const request = researchClient.getAutomaticResearch(caseId);
      activeReadRef.current = request;
      setReading(true);
      try {
        const response = await request;
        const nextView = normalizeAutomaticResearchView(response);
        if (!nextView) throw new InvalidAutomaticResearchProjectionError();
        if (
          !mountedRef.current
          || flowToken !== flowTokenRef.current
          || requestToken !== requestTokenRef.current
        ) return;
        lastView = nextView;
        consecutiveFailures = 0;
        setView(nextView);
        setLoadError(null);
        setTransientError(false);
        setLoading(false);
        if (isActive(nextView)) {
          timer = window.setTimeout(
            () => { void load(); },
            automaticResearchPollDelay(0),
          );
        }
      } catch (error) {
        if (
          !mountedRef.current
          || flowToken !== flowTokenRef.current
          || requestToken !== requestTokenRef.current
        ) return;
        setLoading(false);
        if (error instanceof InvalidAutomaticResearchProjectionError) {
          setView(null);
          setLoadError("invalid");
          setTransientError(false);
          return;
        }
        if (lastView && isActive(lastView)) {
          consecutiveFailures += 1;
          setView(lastView);
          setLoadError(null);
          setTransientError(true);
          timer = window.setTimeout(
            () => { void load(); },
            automaticResearchPollDelay(consecutiveFailures),
          );
          return;
        }
        setView(null);
        setLoadError("unavailable");
        setTransientError(false);
      } finally {
        if (activeReadRef.current === request) activeReadRef.current = null;
        if (
          mountedRef.current
          && flowToken === flowTokenRef.current
          && requestToken === requestTokenRef.current
        ) setReading(false);
      }
    };

    void load();
    return () => {
      flowTokenRef.current += 1;
      requestTokenRef.current += 1;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [caseId, pollVersion]);

  function readProgressNow() {
    if (!caseId || !view || reading || !transientError || !isActive(view)) return;
    seedViewRef.current = view;
    setPollVersion((version) => version + 1);
  }

  function readEmptyError() {
    if (!caseId || reading || !loadError) return;
    setPollVersion((version) => version + 1);
  }

  async function retry() {
    if (!caseId || retrying || view?.status !== "failed") return;
    const requestToken = ++requestTokenRef.current;
    setRetrying(true);
    setRetryError(false);
    try {
      const started = await researchClient.retryAutomaticResearch(caseId);
      if (!mountedRef.current || requestToken !== requestTokenRef.current) return;
      const queued = queuedRetryView(view, started.runId);
      seedViewRef.current = queued;
      setView(queued);
      setRetrying(false);
      setPollVersion((version) => version + 1);
    } catch {
      if (!mountedRef.current || requestToken !== requestTokenRef.current) return;
      setRetryError(true);
      setRetrying(false);
    }
  }

  if (!caseId) {
    return (
      <main className="ros-page automatic-research-process">
        <div className="ros-error" role="alert">
          <strong>缺少自动研究标识</strong>
          <p>请从研究调度页重新打开这项研究。</p>
        </div>
      </main>
    );
  }

  if (loading) {
    return (
      <main className="ros-page automatic-research-process">
        <div className="automatic-research-process__loading" role="status">
          <strong>正在读取自动研究过程</strong>
          <span aria-hidden="true" />
          <span aria-hidden="true" />
          <span aria-hidden="true" />
        </div>
      </main>
    );
  }

  if (loadError || !view) {
    return (
      <main className="ros-page automatic-research-process">
        <div className="ros-error" role="alert">
          <strong>
            {loadError === "invalid"
              ? "自动研究过程记录不完整"
              : "暂时无法读取这项自动研究"}
          </strong>
          <p>
            {loadError === "invalid"
              ? "系统收到的阶段记录不符合固定的五阶段结构，已停止展示以避免误导。"
              : "它可能不存在，或研究服务暂时不可用。请稍后从研究调度页重试。"}
          </p>
          <button
            type="button"
            className="ros-button ros-button--secondary"
            disabled={reading}
            onClick={readEmptyError}
          >
            重新读取
          </button>
        </div>
      </main>
    );
  }

  const duration = formatAutomaticDuration(view.stats.durationSeconds);
  const liveStage = currentStage(view);
  const activityEntries = stableRepeatedTextEntries(
    view.recentActivity,
    `${view.runId}-activity`,
  );

  return (
    <main
      className="ros-page automatic-research-process"
      style={automaticResearchProcessStyle}
    >
      <header className="automatic-research-process__header">
        <div>
          <p className="ros-eyebrow">自动研究过程</p>
          <h1>{view.title}</h1>
        </div>
        <p
          className={`automatic-research-process__overall-status automatic-research-process__overall-status--${view.status}`}
        >
          <span aria-hidden="true" />
          {automaticResearchStatusLabel(view.status)}
        </p>
      </header>

      {liveStage && (
        <p
          className="automatic-research-process__current-stage"
          aria-live="polite"
          aria-atomic="true"
        >
          当前阶段：{liveStage.label}，{automaticStageStatusLabel(liveStage.status)}。
          {liveStage.summary}
        </p>
      )}

      {transientError && isActive(view) && (
        <section
          className="automatic-research-process__transient-error"
          role="status"
          aria-label="进度暂时无法更新"
        >
          <p>进度暂时无法更新，系统会继续自动读取。</p>
          <button type="button" disabled={reading} onClick={readProgressNow}>
            立即重新读取
          </button>
        </section>
      )}

      {view.status === "failed" && (
        <section className="automatic-research-process__failure" aria-labelledby="automatic-research-failure">
          <div role="alert">
            <h2 id="automatic-research-failure">本次研究未完成</h2>
            <p>{view.failureReason || "处理过程中遇到暂时性问题。"}</p>
          </div>
          <button
            type="button"
            className="ros-button ros-button--primary"
            disabled={retrying}
            onClick={() => { void retry(); }}
          >
            {retrying ? "正在重新运行…" : "重新运行"}
          </button>
          {retryError && (
            <p className="ros-error" role="alert" aria-label="重新运行失败">
              重新运行未能启动，请稍后再试。
            </p>
          )}
        </section>
      )}

      {view.result && (
        <section className="automatic-research-process__result" aria-labelledby="automatic-research-conclusion">
          <p className="automatic-research-process__machine-label">
            {view.result.label}
          </p>
          <h2 id="automatic-research-conclusion">结论</h2>
          <p className="automatic-research-process__conclusion">{view.result.conclusion}</p>
          {view.result.keyFindings.length > 0 && (
            <div className="automatic-research-process__findings">
              <h3>关键发现</h3>
              <ul>
                {view.result.keyFindings.map((finding, index) => (
                  <li key={`${view.runId}-finding-${index}`}>{finding}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      <section className="automatic-research-process__stages" aria-labelledby="automatic-research-stages">
        <h2 id="automatic-research-stages">处理阶段</h2>
        <ol>
          {view.stages.map((stage, index) => {
            const startedAt = formatAutomaticTimestamp(stage.startedAt);
            const completedAt = formatAutomaticTimestamp(stage.completedAt);
            return (
              <li
                key={stage.key}
                className={`automatic-research-process__stage automatic-research-process__stage--${stage.status}`}
                aria-label={`${stage.label}阶段，${automaticStageStatusLabel(stage.status)}，${stage.summary}`}
              >
                <span className="automatic-research-process__stage-number" aria-hidden="true">
                  {index + 1}
                </span>
                <div>
                  <div className="automatic-research-process__stage-heading">
                    <h3>{stage.label}</h3>
                    <span className="automatic-research-process__stage-status">
                      {automaticStageStatusLabel(stage.status)}
                    </span>
                  </div>
                  <p>{stage.summary}</p>
                  {(startedAt || completedAt) && (
                    <p className="automatic-research-process__stage-time">
                      {startedAt && <>开始 <time dateTime={stage.startedAt || undefined}>{startedAt}</time></>}
                      {startedAt && completedAt && <span aria-hidden="true">；</span>}
                      {completedAt && <>完成 <time dateTime={stage.completedAt || undefined}>{completedAt}</time></>}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </section>

      <section className="automatic-research-process__stats" aria-labelledby="automatic-research-stats">
        <h2 id="automatic-research-stats">本次处理</h2>
        <dl>
          <div><dt>资料来源</dt><dd>{view.stats.sourceCount}</dd></div>
          <div><dt>纳入证据</dt><dd>{view.stats.admittedEvidenceCount}</dd></div>
          <div><dt>跳过资料</dt><dd>{view.stats.skippedCount}</dd></div>
          {duration && <div><dt>处理时长</dt><dd>{duration}</dd></div>}
        </dl>
      </section>

      <details className="automatic-research-process__details">
        <summary>查看过程</summary>
        <div className="automatic-research-process__detail-content">
          {view.recentActivity.length > 0 && (
            <section>
              <h3>最近活动</h3>
              <ul>{activityEntries.map((activity) => (
                <li key={activity.key}>{activity.text}</li>
              ))}</ul>
            </section>
          )}
          {view.exceptions.length > 0 && (
            <section>
              <h3>跳过与异常</h3>
              <ul>
                {view.exceptions.map((exception, index) => (
                  <li key={`${view.runId}-exception-${exception.stage}-${index}`}>
                    {exception.reason}（{exception.stage}，{exception.count} 项）
                  </li>
                ))}
              </ul>
            </section>
          )}
          {view.result?.counterEvidence.length ? (
            <section>
              <h3>反证</h3>
              <ul>{view.result.counterEvidence.map((item, index) => (
                <li key={`${view.runId}-counter-${index}`}>{item}</li>
              ))}</ul>
            </section>
          ) : null}
          {view.result?.limitations.length ? (
            <section>
              <h3>限制</h3>
              <ul>{view.result.limitations.map((item, index) => (
                <li key={`${view.runId}-limitation-${index}`}>{item}</li>
              ))}</ul>
            </section>
          ) : null}
          {view.result?.sources.length ? (
            <section>
              <h3>资料来源</h3>
              <ul className="automatic-research-process__sources">
                {view.result.sources.map((source, index) => (
                  <SourceItem key={`${source.url || "source"}-${index}`} source={source} />
                ))}
              </ul>
            </section>
          ) : null}
          {!view.recentActivity.length
            && !view.exceptions.length
            && !view.result?.counterEvidence.length
            && !view.result?.limitations.length
            && !view.result?.sources.length && <p>尚无可展示的过程记录。</p>}
        </div>
      </details>

      <Link className="automatic-research-process__case-link" to={`/events/${encodeURIComponent(caseId)}`}>
        查看 Case 详情
      </Link>
    </main>
  );
}
