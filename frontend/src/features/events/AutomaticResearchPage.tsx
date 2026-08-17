import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import {
  automaticResearchStatusLabel,
  automaticStageStatusLabel,
  formatAutomaticDuration,
  formatAutomaticTimestamp,
  type AutomaticResearchSource,
  type AutomaticResearchView,
} from "../../domain/automaticResearch";

const POLL_INTERVAL_MS = 2_000;

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
  const [loadError, setLoadError] = useState(false);
  const [retryError, setRetryError] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);
  const mountedRef = useRef(false);
  const requestTokenRef = useRef(0);
  const flowTokenRef = useRef(0);

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
    setView(null);
    setLoadError(false);
    setRetryError(false);
    setRetrying(false);
    setLoading(Boolean(caseId));

    if (!caseId) return undefined;

    const load = async () => {
      const requestToken = ++requestTokenRef.current;
      try {
        const nextView = await researchClient.getAutomaticResearch(caseId);
        if (
          !mountedRef.current
          || flowToken !== flowTokenRef.current
          || requestToken !== requestTokenRef.current
        ) return;
        setView(nextView);
        setLoadError(false);
        setLoading(false);
        if (isActive(nextView)) {
          timer = window.setTimeout(() => { void load(); }, POLL_INTERVAL_MS);
        }
      } catch {
        if (
          !mountedRef.current
          || flowToken !== flowTokenRef.current
          || requestToken !== requestTokenRef.current
        ) return;
        setView(null);
        setLoadError(true);
        setLoading(false);
      }
    };

    void load();
    return () => {
      flowTokenRef.current += 1;
      requestTokenRef.current += 1;
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [caseId, reloadVersion]);

  async function retry() {
    if (!caseId || retrying || view?.status !== "failed") return;
    const requestToken = ++requestTokenRef.current;
    setRetrying(true);
    setRetryError(false);
    try {
      await researchClient.retryAutomaticResearch(caseId);
      if (!mountedRef.current || requestToken !== requestTokenRef.current) return;
      setReloadVersion((version) => version + 1);
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
          <strong>暂时无法读取这项自动研究</strong>
          <p>它可能不存在，或研究服务暂时不可用。请稍后从研究调度页重试。</p>
        </div>
      </main>
    );
  }

  const duration = formatAutomaticDuration(view.stats.durationSeconds);

  return (
    <main className="ros-page automatic-research-process">
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
                {view.result.keyFindings.map((finding) => <li key={finding}>{finding}</li>)}
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
                aria-label={`${stage.label}阶段`}
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
                      {startedAt && <time dateTime={stage.startedAt || undefined}>{startedAt}</time>}
                      {startedAt && completedAt && <span aria-hidden="true"> 至 </span>}
                      {completedAt && <time dateTime={stage.completedAt || undefined}>{completedAt}</time>}
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
              <ul>{view.recentActivity.map((activity) => <li key={activity}>{activity}</li>)}</ul>
            </section>
          )}
          {view.exceptions.length > 0 && (
            <section>
              <h3>跳过与异常</h3>
              <ul>
                {view.exceptions.map((exception) => (
                  <li key={`${exception.stage}-${exception.reason}`}>
                    {exception.reason}（{exception.stage}，{exception.count} 项）
                  </li>
                ))}
              </ul>
            </section>
          )}
          {view.result?.counterEvidence.length ? (
            <section>
              <h3>反证</h3>
              <ul>{view.result.counterEvidence.map((item) => <li key={item}>{item}</li>)}</ul>
            </section>
          ) : null}
          {view.result?.limitations.length ? (
            <section>
              <h3>限制</h3>
              <ul>{view.result.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
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
