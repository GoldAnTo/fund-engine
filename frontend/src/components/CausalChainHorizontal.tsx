import type { CausalStepView, ObjectStatus } from "../domain/types";

interface Props {
  steps: CausalStepView[];
  focusedStepId?: string | null;
  onSelect?: (id: string) => void;
}

// CausalChainHorizontal — Prototype 2 中 5 卡片横向因果链。卡片之间有箭头 →。
// 键盘：ArrowLeft / ArrowRight 在卡片间切换，Enter / Space 切换聚焦。
export function CausalChainHorizontal({
  steps,
  focusedStepId,
  onSelect,
}: Props) {
  return (
    <ol
      className={`causal-chain-h${focusedStepId ? " has-focus" : ""}`}
      data-testid="causal-chain"
      data-focused-step={focusedStepId ?? ""}
    >
      {steps.map((step, idx) => (
        <li
          key={step.id}
          className={`causal-step-h${focusedStepId === step.id ? " is-focused" : ""}`}
          data-status={step.status}
          aria-current={focusedStepId === step.id ? "true" : undefined}
        >
          <button
            type="button"
            className="causal-step-h__btn"
            onClick={() =>
              onSelect?.(focusedStepId === step.id ? "" : step.id)
            }
            aria-pressed={focusedStepId === step.id}
            aria-label={`${step.sequence}. ${step.title}`}
          >
            <header className="causal-step-h__head">
              <span className="causal-step-h__seq">
                {String(step.sequence).padStart(2, "0")}
              </span>
              <span className={`causal-step-h__status status-${statusTone(step.status)}`}>
                {step.status === null
                ? "无审核标记"
                : step.status === "ai_pending_review"
                  ? "AI 提议"
                  : "已审核"}
              </span>
            </header>
            <h4 className="causal-step-h__title">{step.title}</h4>
            <p className="causal-step-h__desc">{step.description}</p>
          </button>
          {idx < steps.length - 1 && (
            <span className="causal-step-h__arrow" aria-hidden>
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

function statusTone(s: ObjectStatus | null): string {
  if (s === null) return "neutral";
  if (s === "ai_pending_review") return "amber";
  if (s === "human_confirmed" || s === "human_modified") return "deep-green";
  if (s === "human_rejected") return "clay";
  return "neutral";
}