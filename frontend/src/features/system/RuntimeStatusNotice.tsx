import type { CaseRuntimeStatus } from "../../domain/eventWorkflow";


const SERVICE_LABEL = {
  "research-worker": "证据归并服务",
  "acquisition-worker": "资料获取服务",
  scheduler: "流程调度服务",
} as const;

const STATE_LABEL: Record<string, string> = {
  intake: "建立事件",
  awaiting_scope_confirmation: "等待确认研究范围",
  planning_acquisition: "生成资料获取计划",
  acquiring: "主动获取资料",
  freezing_sources: "冻结来源资料",
  assessing_coverage: "评估证据覆盖",
  synthesizing_evidence: "归并证据",
  adjudicating_thesis: "判定研究命题",
  generating_report: "生成研究报告",
  monitoring: "持续监测",
  retry_wait: "等待自动重试",
  recovering: "从检查点恢复",
  needs_scope_decision: "等待研究范围决定",
  exhausted: "检索已充分",
  cancelled: "流程已取消",
  failed: "流程已记录失败",
};

const TRANSITION_LABEL: Record<string, string> = {
  scope_confirmed: "确认研究范围",
  acquisition_planned: "冻结资料获取计划",
  acquisition_started: "开始主动补证",
  acquisition_completed: "完成资料获取",
  recovery_started: "开始自动恢复",
  recovery_completed: "完成自动恢复",
};

function dateTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(parsed);
}

type FallbackCheckpoint = {
  version: number;
  savedAt: string;
};

export function RuntimeStatusNotice({
  status,
  error,
  retrying,
  fallbackCheckpoint,
  onRetry,
}: {
  status: CaseRuntimeStatus | null;
  error: string | null;
  retrying: boolean;
  fallbackCheckpoint?: FallbackCheckpoint;
  onRetry: () => void;
}) {
  if (!status && !error) {
    return (
      <aside className="runtime-status-notice is-loading" role="status" aria-live="polite">
        正在独立检查负责当前阶段的运行服务…
      </aside>
    );
  }

  if (!status) {
    return (
      <aside className="runtime-status-notice is-unavailable" role="status" aria-live="polite">
        <div>
          <strong>运行健康暂不可确认</strong>
          <p>{error}</p>
          {fallbackCheckpoint && (
            <p>
              已保存流程记录：v{fallbackCheckpoint.version} · {dateTime(fallbackCheckpoint.savedAt)}
            </p>
          )}
          <small>Case 进度仍以已保存的流程记录为准，不会用旧状态或健康猜测替代。</small>
        </div>
        <button type="button" disabled={retrying} onClick={onRetry}>
          {retrying ? "正在重新检查…" : "重新检查运行状态"}
        </button>
      </aside>
    );
  }

  const serviceLabel = status.requiredServices.length > 0
    ? status.requiredServices.map((item) => SERVICE_LABEL[item.name]).join("、")
    : "当前阶段";
  const heartbeatSummary = status.requiredServices.length > 0
    ? status.requiredServices
      .map((item) => (
        item.lastSeenAt
          ? `${SERVICE_LABEL[item.name]} ${dateTime(item.lastSeenAt)}`
          : `${SERVICE_LABEL[item.name]}尚未上报`
      ))
      .join(" · ")
    : "当前阶段无需后台服务推进";
  if (status.runtimeStatus === "healthy") {
    return (
      <aside className="runtime-status-notice is-healthy" role="status" aria-live="polite">
        <strong>{serviceLabel}运行正常</strong>
        <span>{heartbeatSummary}</span>
      </aside>
    );
  }

  const checkpoint = status.durableCheckpoint;
  const affectedServices = status.requiredServices
    .filter((item) => item.status !== "healthy")
    .map((item) => SERVICE_LABEL[item.name])
    .join("、") || serviceLabel;
  const transition = checkpoint.lastTransition
    ? TRANSITION_LABEL[checkpoint.lastTransition] ?? checkpoint.lastTransition
    : STATE_LABEL[checkpoint.workflowState] ?? checkpoint.workflowState;
  return (
    <aside className={`runtime-status-notice is-${status.runtimeStatus}`} role="status" aria-live="polite">
      <div>
        <strong>
          {affectedServices}{status.runtimeStatus === "unavailable" ? "暂不可用" : "状态异常"}
        </strong>
        <p>最后耐久检查点：{transition} · v{checkpoint.version} · {dateTime(checkpoint.savedAt)}</p>
        <p>自动恢复：{status.recovery.automatic ? status.recovery.message : "不会自动继续，需要按页面下一步处理。"}</p>
        <small>{status.message}</small>
      </div>
      <button type="button" disabled={retrying} onClick={onRetry}>
        {retrying ? "正在重新检查…" : "重新检查运行状态"}
      </button>
    </aside>
  );
}
