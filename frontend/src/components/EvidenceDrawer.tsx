import type { EvidenceDrawerRecord, EvidenceRole } from "../types";

function locatorText(locator: Record<string, unknown> | null): string {
  if (!locator) return "未知位置";
  const parts: string[] = [];
  if (locator.page != null) parts.push(`第 ${locator.page} 页`);
  if (locator.paragraph != null) parts.push(`第 ${locator.paragraph} 段`);
  if (locator.table != null) parts.push(`表 ${locator.table}`);
  if (locator.row != null) parts.push(`第 ${locator.row} 行`);
  return parts.length ? parts.join("，") : JSON.stringify(locator);
}

const ROLE_LABEL: Record<EvidenceRole, string> = {
  supports: "支持",
  contradicts: "反对",
  contextualizes: "背景",
};

interface Props {
  record: EvidenceDrawerRecord;
  allRecords?: EvidenceDrawerRecord[];
}

export function EvidenceDrawer({ record, allRecords }: Props) {
  // 同一 statement 的相关证据，按 role 分组，呈现"信息有左有右"
  const related = (allRecords ?? []).filter(
    (r) => r.statement_id === record.statement_id
  );
  const hasContradicts = related.some((r) => r.role === "contradicts");
  const grouped: Record<EvidenceRole, EvidenceDrawerRecord[]> = {
    supports: [],
    contradicts: [],
    contextualizes: [],
  };
  related.forEach((r) => {
    grouped[r.role].push(r);
  });

  return (
    <aside className="evidence-drawer">
      <h3>证据详情</h3>
      <p className="role">角色：{ROLE_LABEL[record.role]}（{record.role}）</p>
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

      {hasContradicts && related.length > 1 && (
        <div className="contradiction-compare" data-testid="contradiction-compare">
          <h4>矛盾对比</h4>
          <p className="compare-hint">同一陈述的证据有左有右，请综合判断：</p>
          {(["supports", "contradicts", "contextualizes"] as EvidenceRole[]).map(
            (role) => {
              const items = grouped[role];
              if (items.length === 0) return null;
              return (
                <div key={role} className={`compare-group compare-${role}`}>
                  <p className={`compare-role role-${role}`}>
                    {ROLE_LABEL[role]}（{items.length}）
                  </p>
                  <ul>
                    {items.map((r) => (
                      <li
                        key={r.link_id}
                        className={r.link_id === record.link_id ? "current" : ""}
                      >
                        {r.statement_text ?? r.reason}
                        {r.verbatim_text && (
                          <span className="compare-verbatim">「{r.verbatim_text}」</span>
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            }
          )}
        </div>
      )}
    </aside>
  );
}
