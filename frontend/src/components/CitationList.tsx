import type { CitationEntry } from "../domain/types";

interface Props {
  citations: CitationEntry[];
}

export function CitationList({ citations }: Props) {
  // Group by theme for the Prototype 3 "引用记录" block.
  const groups = new Map<string, CitationEntry[]>();
  for (const c of citations) {
    if (!groups.has(c.theme)) groups.set(c.theme, []);
    groups.get(c.theme)!.push(c);
  }

  return (
    <section className="citation-list" data-testid="citation-list">
      <h4>引用记录（{citations.length} 条）</h4>
      {Array.from(groups.entries()).map(([theme, items]) => (
        <div key={theme} className="citation-list__group">
          <h5>{theme}</h5>
          <ul>
            {items.map((c) => (
              <li key={c.id} className="citation-list__item">
                <span className="citation-list__desc">{c.description}</span>
                <span className="citation-list__date muted">{c.date}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {citations.length === 0 && (
        <p className="muted">暂无引用记录。</p>
      )}
    </section>
  );
}