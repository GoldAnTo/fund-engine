import type { EvidenceDrawerRecord } from "../types";

function locatorText(locator: Record<string, unknown> | null): string {
  if (!locator) return "未知位置";
  const parts: string[] = [];
  if (locator.page != null) parts.push(`第 ${locator.page} 页`);
  if (locator.paragraph != null) parts.push(`第 ${locator.paragraph} 段`);
  if (locator.table != null) parts.push(`表 ${locator.table}`);
  if (locator.row != null) parts.push(`第 ${locator.row} 行`);
  return parts.length ? parts.join("，") : JSON.stringify(locator);
}

export function EvidenceDrawer({ record }: { record: EvidenceDrawerRecord }) {
  return (
    <aside className="evidence-drawer">
      <h3>证据详情</h3>
      <p className="role">角色：{record.role}</p>
      <p className="reason">理由：{record.reason}</p>
      <p className="scope">范围：{JSON.stringify(record.scope)}</p>
      {record.period && <p className="period">期间：{record.period}</p>}
      <p className="review-state">审核：{record.review_state}</p>
      <div className="source">
        <p className="locator">原文位置：{locatorText(record.locator)}</p>
        {record.verbatim_text && (
          <blockquote className="verbatim">{record.verbatim_text}</blockquote>
        )}
      </div>
    </aside>
  );
}
