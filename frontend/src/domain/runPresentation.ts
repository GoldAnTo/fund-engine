import { sourceTypeListLabel } from "./sourcePresentation";

const RUN_STAGE_LABELS: Record<string, string> = {
  scope: "冻结本次范围",
  queued: "等待执行",
  extract: "提取资料",
  retrieve: "采集资料",
  parse: "解析原文",
  verify: "验证关键因素",
  planning: "准备研究范围",
  review: "等待审核",
  claim_review: "等待原子陈述审核",
  stopped: "运行已停止",
  failed: "运行失败",
  complete: "运行完成",
};

const RUN_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  started: "已开始",
  recorded: "已记录",
  completed: "已完成",
  waiting_for_review: "等待人工审核",
  failed: "失败",
  cancelled: "已停止",
  blocked: "已阻塞",
  continuing: "继续补证",
  exhausted: "已达到上限",
  awaiting_key_review: "等待关键审核",
  awaiting_scope: "等待确认范围",
  draft_ready: "草案待审",
};

const RUN_TRIGGER_LABELS: Record<string, string> = {
  manual: "立即补证",
  schedule: "定时任务",
  factor_manual: "立即补证此因素",
  material_continuation: "新增材料重新复核",
};

const RUN_STOP_REASON_LABELS: Record<string, string> = {
  task_failed: "任务执行失败",
  budget_exhausted: "达到资料预算上限",
  max_rounds_reached: "达到最大补证轮次",
  no_new_evidence: "未发现可纳入的新资料",
  cancelled_by_human: "研究员手动停止",
};

const RUN_FREQUENCY_LABELS: Record<string, string> = {
  weekday_08_30: "工作日 08:30",
  weekday_12_30: "工作日 12:30",
  daily_20_00: "每日 20:00",
};

const RUN_EVENT_DETAIL_LABELS: Record<string, string> = {
  accepted: "已纳入资料",
  excluded: "已排除资料",
  exclusion_reason: "排除原因",
  pending_review: "待人工审核",
  failed: "失败项",
  failure_reason: "失败原因",
  candidates: "候选数",
  frozen: "已冻结资料",
  trigger: "触发方式",
  monitor_version_id: "配置版本",
  factor_ids: "范围因素",
  factor_statements: "范围因素说明",
  allowed_source_types: "允许来源",
  budget: "资料预算",
  stop_reason: "停止原因",
};

export function runStageLabel(value: string | null | undefined): string {
  if (!value) return "未记录阶段";
  return RUN_STAGE_LABELS[value] ?? value;
}

export function runStatusLabel(value: string | null | undefined): string {
  if (!value) return "未记录状态";
  return RUN_STATUS_LABELS[value] ?? value;
}

export function runTriggerLabel(value: string | null | undefined): string {
  if (!value) return "未记录";
  return RUN_TRIGGER_LABELS[value] ?? value;
}

export function runStopReasonLabel(value: string | null | undefined): string {
  if (!value) return "未记录";
  return RUN_STOP_REASON_LABELS[value] ?? value;
}

export function runFrequencyLabel(value: string | null | undefined): string {
  if (!value) return "未记录";
  return RUN_FREQUENCY_LABELS[value] ?? value;
}

export function formatRunEventDetails(details: Record<string, unknown>): string {
  return Object.entries(details)
    .map(([key, value]) => {
      const rendered =
        key === "allowed_source_types" && Array.isArray(value)
          ? sourceTypeListLabel(value.map(String))
          : key === "trigger"
            ? runTriggerLabel(typeof value === "string" ? value : null)
            : key === "stop_reason"
              ? runStopReasonLabel(typeof value === "string" ? value : null)
              : Array.isArray(value)
                ? value.join("、")
                : typeof value === "object" && value !== null
                  ? JSON.stringify(value)
                  : String(value);
      return `${RUN_EVENT_DETAIL_LABELS[key] || key}：${rendered}`;
    })
    .join(" · ");
}
