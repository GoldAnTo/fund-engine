import { useState } from "react";
import type { CausalStepView, ObjectStatus } from "../domain/types";

interface Props {
  steps: CausalStepView[];
  onSelect?: (id: string) => void;
}

// CausalChain — single column of testable causal steps. Click a step to focus
// it: the parent can dim other content via `data-causal-focused` and the
// related supports/contradicts in EvidenceComparison will highlight. Click
// again (or press Escape) to clear focus. Keyboard: ArrowUp/Down to navigate
// steps; Enter / Space to toggle focus.
export function CausalChain({ steps, onSelect }: Props) {
  const [focusedId, setFocusedId] = useState<string | null>(null);

  function pick(id: string) {
    const next = focusedId === id ? null : id;
    setFocusedId(next);
    onSelect?.(next ?? "");
  }

  function onKeyDown(e: React.KeyboardEvent, idx: number) {
    if (e.key === "ArrowDown" && idx < steps.length - 1) {
      e.preventDefault();
      const next = steps[idx + 1];
      setFocusedId(next.id);
      onSelect?.(next.id);
    } else if (e.key === "ArrowUp" && idx > 0) {
      e.preventDefault();
      const prev = steps[idx - 1];
      setFocusedId(prev.id);
      onSelect?.(prev.id);
    } else if (e.key === "Escape" && focusedId) {
      setFocusedId(null);
      onSelect?.("");
    }
  }

  return (
    <ol
      className={`causal-chain${focusedId ? " has-focus" : ""}`}
      data-testid="causal-chain"
      data-focused-step={focusedId ?? ""}
    >
      {steps.map((step, idx) => (
        <li
          key={step.id}
          className={`causal-step${focusedId === step.id ? " is-focused" : ""}`}
          aria-current={focusedId === step.id ? "true" : undefined}
          data-status={step.status}
        >
          <button
            type="button"
            className="causal-step__btn"
            onClick={() => pick(step.id)}
            onKeyDown={(e) => onKeyDown(e, idx)}
            aria-label={`${step.sequence}. ${step.title}`}
            aria-pressed={focusedId === step.id}
          >
            <span className="causal-step__seq">{step.sequence}</span>
            <span className="causal-step__title">{step.title}</span>
            <span className="causal-step__desc">{step.description}</span>
            <span className={`causal-step__status status-${statusTone(step.status)}`}>
              {step.status === "ai_pending_review" ? "AI 提议" : "已审核"}
            </span>
          </button>
          {idx < steps.length - 1 && (
            <span className="causal-chain__connector" aria-hidden />
          )}
        </li>
      ))}
    </ol>
  );
}

function statusTone(s: ObjectStatus): string {
  if (s === "ai_pending_review") return "amber";
  if (s === "human_confirmed" || s === "human_modified") return "deep-green";
  if (s === "human_rejected") return "clay";
  return "neutral";
}