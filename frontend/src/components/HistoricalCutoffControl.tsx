interface Props {
  cutoff: string | null;
  onChange: (cutoff: string | null) => void;
}

const PRESETS: { label: string; value: string }[] = [
  { label: "今天", value: new Date().toISOString().slice(0, 10) },
  { label: "上月", value: "" },
  { label: "去年", value: "" },
];

export function HistoricalCutoffControl({ cutoff, onChange }: Props) {
  return (
    <div className="cutoff-control" data-testid="cutoff-control">
      <span className="cutoff-control__label">历史截点</span>
      <input
        type="date"
        value={cutoff ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        aria-label="历史截点"
        data-testid="cutoff-input"
      />
      {cutoff && (
        <>
          <span className="cutoff-control__flag" data-testid="cutoff-flag">
            ⏱ 历史回放至 {cutoff}
          </span>
          <button type="button" onClick={() => onChange(null)}>
            回到当前
          </button>
        </>
      )}
      <div className="cutoff-control__presets" role="group" aria-label="截点预设">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            type="button"
            onClick={() => p.value && onChange(p.value)}
            disabled={!p.value}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}