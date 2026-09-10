import type { CompanyResearchRun } from "../../data/investmentResearchApi";
import { researchRunStageView } from "./companyResearchView";

const STATUS_LABELS: Readonly<Record<CompanyResearchRun["status"], string>> = {
  queued: "等待开始",
  collecting_sources: "收集资料",
  analyzing_company: "分析公司",
  building_forecast: "建立预测",
  generating_report: "生成报告",
  completed: "AI 初稿已完成",
  needs_input: "需要补充",
  failed: "运行失败",
};

const STAGE_STATE_LABELS: Readonly<Record<CompanyResearchRun["stages"][number]["status"], string>> = {
  pending: "待开始",
  active: "进行中",
  completed: "已完成",
  needs_input: "需要补充",
  failed: "失败",
};

export type FrozenVerificationState = "draft" | "verifying" | "verified" | "failed";

function visibleStatus(run: CompanyResearchRun, frozenVerification: FrozenVerificationState): string {
  if (frozenVerification === "verified") return "已冻结版本";
  if (frozenVerification === "verifying") return "正在验证冻结版本";
  if (frozenVerification === "failed") return "冻结版本验证失败";
  if (run.status === "failed" && run.recent_process.some((entry) => entry.retry?.retryable === true)) return "可恢复失败";
  return STATUS_LABELS[run.status];
}

function retryStageLabel(run: CompanyResearchRun): string {
  const failed = researchRunStageView(run).find((stage) => stage.status === "failed");
  return failed?.label ?? "研究运行";
}

export interface CompanyResearchProgressProps {
  run: CompanyResearchRun;
  frozenVerification: FrozenVerificationState;
}

export interface CompanyResearchProcessProps {
  run: CompanyResearchRun;
  processExpanded: boolean;
  processLoading: boolean;
  retrying: boolean;
  onProcessToggle: () => void;
  onRetry: () => void;
}

export default function CompanyResearchProgress({
  run,
  frozenVerification,
}: CompanyResearchProgressProps) {
  const stages = researchRunStageView(run);
  const current = stages.find((stage) => stage.status === "active" || stage.status === "needs_input" || stage.status === "failed")
    ?? stages.at(-1);
  const markerStatus = frozenVerification === "failed" ? "failed" : frozenVerification === "verifying" ? "generating_report" : run.status;
  return <>
    <section className="ir-run-status" aria-live="polite">
      <div>
        <span className={`ir-run-status__mark ir-run-status__mark--${markerStatus}`} aria-hidden="true" />
        <strong>{visibleStatus(run, frozenVerification)}</strong>
        <span>{run.progress}%</span>
      </div>
      <progress aria-label="研究准备进度" aria-valuemax={100} aria-valuemin={0} aria-valuenow={run.progress} max="100" value={run.progress}>{run.progress}%</progress>
      <p className="ir-sr-only">当前阶段：{current?.label ?? "等待开始"}，{current ? STAGE_STATE_LABELS[current.status] : "待开始"}</p>
    </section>
    <section className="ir-stage-rail" aria-label="研究阶段">
      <ol>
        {stages.map((stage, index) => <li aria-current={stage === current ? "step" : undefined} className={`is-${stage.status}`} key={stage.key}>
          <span aria-hidden="true">{index + 1}</span>
          <div><strong>{stage.label}</strong><small>{STAGE_STATE_LABELS[stage.status]}</small></div>
        </li>)}
      </ol>
    </section>
  </>;
}

export function CompanyResearchProcess({
  run,
  processExpanded,
  processLoading,
  retrying,
  onProcessToggle,
  onRetry,
}: CompanyResearchProcessProps) {
  const recentProcess = processExpanded ? run.recent_process : run.recent_process.slice(-3);
  const retryable = run.status === "failed" && run.recent_process.some((entry) => entry.retry?.retryable === true);
  return <section className="ir-process" aria-labelledby="company-research-process-title">
      <div className="ir-process__head">
        <div><p className="ir-eyebrow">Process</p><h2 id="company-research-process-title">研究过程</h2></div>
        <button aria-controls="company-research-process-history" aria-expanded={processExpanded} className="ir-button" disabled={processLoading} onClick={onProcessToggle} type="button">
          {processLoading ? "正在读取过程…" : processExpanded ? "收起完整过程" : "查看完整过程"}
        </button>
      </div>
      <div id="company-research-process-history">
        {recentProcess.length > 0 ? <ol className="ir-process__list">
          {recentProcess.map((entry) => <li key={`${entry.occurred_at}-${entry.code}`}>
            <time dateTime={entry.occurred_at}>{new Date(entry.occurred_at).toLocaleString("zh-CN")}</time>
            <p>{entry.message}</p>
            {entry.retry ? <small>{entry.retry.retryable ? "可以安全重试" : "需要人工处理"}{entry.retry.next_attempt_at ? `，下次尝试 ${new Date(entry.retry.next_attempt_at).toLocaleString("zh-CN")}` : ""}</small> : null}
          </li>)}
        </ol> : <p className="ir-process__empty">过程记录尚未建立。</p>}
      </div>
      {retryable ? <button className="ir-button" disabled={retrying} onClick={onRetry} type="button">{retrying ? `正在重试${retryStageLabel(run)}…` : `重试${retryStageLabel(run)}`}</button> : null}
    </section>;
}
