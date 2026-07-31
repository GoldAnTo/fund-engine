interface Props {
  score: number; // 0..1
  tone?: "moss" | "ochre" | "clay" | "neutral" | "mineral" | "iris";
  label?: string;
  showValue?: boolean;
}

const TONE_VAR: Record<NonNullable<Props["tone"]>, string> = {
  moss: "var(--moss)",
  ochre: "var(--ochre)",
  clay: "var(--clay)",
  neutral: "var(--ink-muted)",
  mineral: "var(--mineral)",
  iris: "var(--iris)",
};

export function ScoreBar({
  score,
  tone = "moss",
  label,
  showValue = true,
}: Props) {
  const pct = Math.max(0, Math.min(1, score));
  return (
    <span className="score-bar" aria-label={label ?? `score ${pct.toFixed(2)}`}>
      <span className="score-bar__track" aria-hidden>
        <span
          className="score-bar__fill"
          style={{ width: `${pct * 100}%`, background: TONE_VAR[tone] }}
        />
      </span>
      {showValue && <span className="score-bar__value">{pct.toFixed(2)}</span>}
      {label && <span className="score-bar__label">{label}</span>}
    </span>
  );
}