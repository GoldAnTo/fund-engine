import {
  automaticStageStatusLabel,
  formatAutomaticDuration,
  formatAutomaticTimestamp,
  stableRepeatedTextEntries,
  type AutomaticResearchSource,
  type AutomaticResearchView,
} from "../../domain/automaticResearch";

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
      <small>系统自动纳入，未经人工审核</small>
    </li>
  );
}

export function AutomaticResearchSections({ view }: { view: AutomaticResearchView }) {
  const duration = formatAutomaticDuration(view.stats.durationSeconds);
  const activityEntries = stableRepeatedTextEntries(
    view.recentActivity,
    `${view.runId}-activity`,
  );

  return (
    <>
      {view.result && (
        <section className="automatic-research-process__result" aria-labelledby="automatic-research-conclusion">
          <p className="automatic-research-process__machine-label">{view.result.label}</p>
          <h2 id="automatic-research-conclusion">结论</h2>
          <p className="automatic-research-process__conclusion">{view.result.conclusion}</p>
          {view.result.keyFindings.length > 0 && (
            <div className="automatic-research-process__findings">
              <h3>关键发现</h3>
              <ul>{view.result.keyFindings.map((finding, index) => (
                <li key={`${view.runId}-finding-${index}`}>{finding}</li>
              ))}</ul>
            </div>
          )}
        </section>
      )}

      <section className="automatic-research-process__stages" aria-labelledby="automatic-research-stages">
        <h2 id="automatic-research-stages">处理阶段</h2>
        <ol>{view.stages.map((stage, index) => {
          const startedAt = formatAutomaticTimestamp(stage.startedAt);
          const completedAt = formatAutomaticTimestamp(stage.completedAt);
          return (
            <li
              key={stage.key}
              className={`automatic-research-process__stage automatic-research-process__stage--${stage.status}`}
              aria-label={`${stage.label}阶段，${automaticStageStatusLabel(stage.status)}，${stage.summary}`}
            >
              <span className="automatic-research-process__stage-number" aria-hidden="true">{index + 1}</span>
              <div>
                <div className="automatic-research-process__stage-heading">
                  <h3>{stage.label}</h3>
                  <span className="automatic-research-process__stage-status">{automaticStageStatusLabel(stage.status)}</span>
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
        })}</ol>
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
            <section><h3>最近活动</h3><ul>{activityEntries.map((activity) => (
              <li key={activity.key}>{activity.text}</li>
            ))}</ul></section>
          )}
          {view.exceptions.length > 0 && (
            <section><h3>跳过与异常</h3><ul>{view.exceptions.map((exception, index) => (
              <li key={`${view.runId}-exception-${exception.stage}-${index}`}>
                {exception.reason}（{exception.stage}，{exception.count} 项）
              </li>
            ))}</ul></section>
          )}
          {view.result?.counterEvidence.length ? (
            <section><h3>反证</h3><ul>{view.result.counterEvidence.map((item, index) => (
              <li key={`${view.runId}-counter-${index}`}>{item}</li>
            ))}</ul></section>
          ) : null}
          {view.result?.limitations.length ? (
            <section><h3>限制</h3><ul>{view.result.limitations.map((item, index) => (
              <li key={`${view.runId}-limitation-${index}`}>{item}</li>
            ))}</ul></section>
          ) : null}
          {view.result?.sources.length ? (
            <section><h3>资料来源</h3><ul className="automatic-research-process__sources">
              {view.result.sources.map((source, index) => (
                <SourceItem key={`${source.url || "source"}-${index}`} source={source} />
              ))}
            </ul></section>
          ) : null}
          {!view.recentActivity.length
            && !view.exceptions.length
            && !view.result?.counterEvidence.length
            && !view.result?.limitations.length
            && !view.result?.sources.length && <p>尚无可展示的过程记录。</p>}
        </div>
      </details>
    </>
  );
}
