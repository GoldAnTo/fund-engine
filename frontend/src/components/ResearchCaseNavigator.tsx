import type { ResearchCaseSummary } from "../domain/types";

interface Props {
  cases: ResearchCaseSummary[];
  selectedCaseId?: string;
  onSelect: (id: string) => void;
}

export function ResearchCaseNavigator({
  cases,
  selectedCaseId,
  onSelect,
}: Props) {
  return (
    <aside className="case-navigator" aria-label="案例导航">
      <header className="case-navigator__header">
        <h2>行业案例</h2>
        <input
          type="search"
          placeholder="搜索案例或关键词"
          aria-label="搜索案例"
        />
      </header>
      <nav>
        <ul className="case-navigator__tabs" role="tablist">
          <li>
            <button type="button" role="tab" aria-selected="true">
              全部 {cases.length}
            </button>
          </li>
          <li>
            <button type="button">已创建的 {cases.length}</button>
          </li>
          <li>
            <button type="button">已复盘 {Math.max(0, cases.length - 1)}</button>
          </li>
        </ul>
        <ul className="case-navigator__list">
          {cases.map((c) => (
            <li
              key={c.id}
              className={`case-navigator__item${
                selectedCaseId === c.id ? " is-active" : ""
              }`}
            >
              <button
                type="button"
                onClick={() => onSelect(c.id)}
                aria-current={selectedCaseId === c.id ? "true" : undefined}
              >
                <span className="case-navigator__title">{c.title}</span>
                <span className="case-navigator__topic">{c.topic}</span>
                <span className="case-navigator__date">{c.updated_at.slice(0, 10)}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
}