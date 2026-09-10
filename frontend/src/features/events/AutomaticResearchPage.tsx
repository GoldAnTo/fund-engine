import type { CSSProperties } from "react";
import { Link, useParams } from "react-router-dom";

import {
  automaticResearchStatusLabel,
  automaticStageStatusLabel,
  type AutomaticResearchView,
} from "../../domain/automaticResearch";
import { AutomaticResearchSections } from "./AutomaticResearchSections";
import { useAutomaticResearchProgress } from "./useAutomaticResearchProgress";

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

function isActive(view: AutomaticResearchView): boolean {
  return view.status === "queued" || view.status === "running";
}

function currentStage(view: AutomaticResearchView) {
  return view.stages.find((stage) => stage.status === "running")
    || view.stages.find((stage) => stage.status === "failed")
    || (view.status === "completed"
      ? view.stages[view.stages.length - 1]
      : view.stages.find((stage) => stage.status === "pending"));
}

export function AutomaticResearchPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const progress = useAutomaticResearchProgress(caseId);
  const { state } = progress;

  if (state.kind === "missing-case") {
    return (
      <main className="ros-page automatic-research-process">
        <div className="ros-error" role="alert">
          <strong>缺少自动研究标识</strong>
          <p>请从研究调度页重新打开这项研究。</p>
        </div>
      </main>
    );
  }

  if (state.kind === "loading") {
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

  if (state.kind === "load-error") {
    return (
      <main className="ros-page automatic-research-process">
        <div className="ros-error" role="alert">
          <strong>
            {state.reason === "invalid"
              ? "自动研究过程记录不完整"
              : "暂时无法读取这项自动研究"}
          </strong>
          <p>
            {state.reason === "invalid"
              ? "系统收到的阶段记录不符合固定的五阶段结构，已停止展示以避免误导。"
              : "它可能不存在，或研究服务暂时不可用。请稍后从研究调度页重试。"}
          </p>
          <button
            type="button"
            className="ros-button ros-button--secondary"
            disabled={state.reading}
            onClick={progress.reread}
          >
            重新读取
          </button>
        </div>
      </main>
    );
  }

  const { caseId: resolvedCaseId, view, reading, transientError, retryStatus } = state;
  const liveStage = currentStage(view);

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
          <button type="button" disabled={reading} onClick={progress.reread}>
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
            disabled={retryStatus === "running"}
            onClick={() => { void progress.retry(); }}
          >
            {retryStatus === "running" ? "正在重新运行…" : "重新运行"}
          </button>
          {retryStatus === "error" && (
            <p className="ros-error" role="alert" aria-label="重新运行失败">
              重新运行未能启动，请稍后再试。
            </p>
          )}
        </section>
      )}

      <AutomaticResearchSections view={view} />

      <Link className="automatic-research-process__case-link" to={`/events/${encodeURIComponent(resolvedCaseId)}`}>
        查看 Case 详情
      </Link>
    </main>
  );
}
