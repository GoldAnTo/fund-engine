import type { ObjectStatus } from "../domain/types";

const LABEL: Record<ObjectStatus, string> = {
  ai_pending_review: "AI 临时判断，未经人工复核",
  human_confirmed: "已人工确认",
  human_modified: "已人工修改",
  human_rejected: "已人工驳回",
  conflict: "证据冲突",
  stale: "数据滞后",
  parse_failed: "解析失败",
  permission_denied: "权限不足",
  backend_unavailable: "后端不可用",
};

const TONE: Record<ObjectStatus, string> = {
  ai_pending_review: "status-mark--amber",
  human_confirmed: "status-mark--deep-green",
  human_modified: "status-mark--deep-green",
  human_rejected: "status-mark--clay",
  conflict: "status-mark--clay",
  stale: "status-mark--neutral",
  parse_failed: "status-mark--clay",
  permission_denied: "status-mark--neutral",
  backend_unavailable: "status-mark--neutral",
};

export function StatusMark({ status }: { status: ObjectStatus }) {
  return (
    <span
      className={`status-mark ${TONE[status]}`}
      data-status={status}
      aria-label={LABEL[status]}
    >
      <span className="status-mark__dot" aria-hidden />
      <span className="status-mark__text">{LABEL[status]}</span>
    </span>
  );
}

export function statusLabel(status: ObjectStatus): string {
  return LABEL[status];
}