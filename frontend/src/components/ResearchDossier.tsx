interface Props {
  rationale: string;
  competitiveExplanations: string[];
  gaps: string[];
  log: { id: string; at: string; text: string }[];
}

export function ResearchDossier({
  rationale,
  competitiveExplanations,
  gaps,
  log,
}: Props) {
  return (
    <section
      className="research-dossier"
      aria-labelledby="dossier-heading"
      data-testid="research-dossier"
    >
      <h3 id="dossier-heading">当前判断</h3>
      <p className="dossier__rationale">{rationale}</p>

      <h3>竞争解释</h3>
      <ul>
        {competitiveExplanations.map((line, idx) => (
          <li key={idx}>{line}</li>
        ))}
      </ul>

      <h3>研究缺口</h3>
      <ul className="dossier__gaps">
        {gaps.map((g) => (
          <li key={g}>{g}</li>
        ))}
      </ul>

      <h3>研究日志</h3>
      <ol className="dossier__log">
        {log.map((entry) => (
          <li key={entry.id}>
            <time>{entry.at}</time>
            <span>{entry.text}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}