import type { EvidenceRecord, EvidenceRole } from "../domain/types";
import { EvidenceCard } from "./EvidenceCard";
import { locatorText } from "../domain/locator";

interface Props {
  evidence: {
    supports: EvidenceRecord[];
    contradicts: EvidenceRecord[];
    contextualizes: EvidenceRecord[];
  };
  focusedLinkId?: string | null;
  focusedStepId?: string | null;
  onSelect?: (linkId: string) => void;
}

const ROLE_LABEL: Record<EvidenceRole, string> = {
  supports: "支持证据",
  contradicts: "反证",
  contextualizes: "背景",
};

// EvidenceComparison — Prototype 2 中支持证据 / 反证 两栏对照。
// contextualizes（背景证据）以 chip 形式塞在两栏底部，保留旧场景兼容。
// 当父组件传递 focusedStepId 时，相关证据高亮，其它证据降低不透明度（不隐藏）。
export function EvidenceComparison({
  evidence,
  focusedLinkId,
  focusedStepId,
  onSelect,
}: Props) {
  const totalCount =
    evidence.supports.length +
    evidence.contradicts.length +
    evidence.contextualizes.length;

  // 主对照：左 supports / 右 contradicts。
  const primaryGroups: { role: EvidenceRole; items: EvidenceRecord[] }[] = [
    { role: "supports", items: evidence.supports },
    { role: "contradicts", items: evidence.contradicts },
  ];

  // 相关性：focusedStep 命中时 supports/contradicts 都高亮，contextualizes 降级。
  const isFocused = (record: EvidenceRecord): boolean => {
    if (focusedLinkId && record.link_id === focusedLinkId) return true;
    if (!focusedStepId) return false;
    return record.role !== "contextualizes";
  };

  return (
    <div
      className={`evidence-comparison${focusedStepId ? " has-focus" : ""}`}
      data-testid="evidence-comparison"
      data-focused-step={focusedStepId ?? ""}
    >
      <div className="evidence-comparison__columns">
        {primaryGroups.map((g) => (
          <section
            key={g.role}
            className={`evidence-comparison__group role-${g.role}`}
            aria-labelledby={`grp-${g.role}`}
            data-testid={`evidence-group-${g.role}`}
          >
            <header>
              <h3 id={`grp-${g.role}`}>
                {ROLE_LABEL[g.role]}
                <span className="evidence-comparison__count">
                  （{g.items.length}）
                </span>
              </h3>
              <span className="evidence-comparison__filters">
                相关性 ⌄ · 全部来源 ⌄
              </span>
            </header>
            <ul>
              {g.items.map((e) => (
                <li key={e.link_id}>
                  <EvidenceCard
                    record={e}
                    role={e.role}
                    focused={focusedLinkId === e.link_id}
                    dimmed={Boolean(focusedStepId) && !isFocused(e)}
                    onClick={() => onSelect?.(e.link_id)}
                  />
                </li>
              ))}
              {g.items.length === 0 && (
                <li
                  className="evidence-comparison__empty muted"
                  data-testid={`evidence-empty-${g.role}`}
                >
                  {g.role === "supports"
                    ? "当前论点尚无支持证据 — 这可能是论证薄弱信号。"
                    : "当前论点尚无反证。"}
                </li>
              )}
            </ul>
            {g.items.length > 0 && (
              <a
                className="evidence-comparison__more"
                href={`#all-${g.role}`}
                aria-label={`查看全部${ROLE_LABEL[g.role]}`}
              >
                查看全部{ROLE_LABEL[g.role]}（{g.items.length}）
              </a>
            )}
          </section>
        ))}
      </div>

      {evidence.contextualizes.length > 0 && (
        <details className="evidence-comparison__contextualizes">
          <summary>
            背景证据（{evidence.contextualizes.length}） — 不参与结论对照
          </summary>
          <ul>
            {evidence.contextualizes.map((e) => (
              <li key={e.link_id}>
                <EvidenceCard
                  record={e}
                  role={e.role}
                  focused={focusedLinkId === e.link_id}
                  dimmed={Boolean(focusedStepId)}
                  onClick={() => onSelect?.(e.link_id)}
                />
              </li>
            ))}
          </ul>
        </details>
      )}

      {totalCount === 0 && (
        <p className="evidence-empty" data-testid="evidence-empty">
          当前证据为空。
          {focusedStepId && (
            <span> · 当前聚焦的因果环节尚无关联证据。</span>
          )}
        </p>
      )}
    </div>
  );
}

export { locatorText };