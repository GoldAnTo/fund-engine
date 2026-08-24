import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  investmentResearchApi,
  type ProductObjectSearchItem,
  type ProductProject,
} from "../../data/investmentResearchApi";

const kindLabels: Record<ProductObjectSearchItem["kind"], string> = {
  company: "Company",
  security: "Security",
  industry: "Industry",
};

function identityLine(item: ProductObjectSearchItem): string {
  if (item.kind === "security") {
    return [item.symbol, item.exchange, item.share_class, item.trading_currency]
      .filter(Boolean)
      .join(" · ");
  }
  return item.external_key;
}

export default function ResearchHomePage() {
  const [projects, setProjects] = useState<ProductProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductObjectSearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchCompleted, setSearchCompleted] = useState(false);

  useEffect(() => {
    let active = true;
    investmentResearchApi.projects(20)
      .then((response) => {
        if (active) setProjects(response.items);
      })
      .catch((error: Error) => {
        if (active) setLoadError(error.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const recentProjects = projects
    .slice()
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim();
    if (!normalized) return;
    setSearching(true);
    setSearchCompleted(false);
    setSearchError(null);
    try {
      const response = await investmentResearchApi.searchObjects(normalized);
      setResults(response.items);
      setSearchCompleted(true);
    } catch (error) {
      setResults([]);
      setSearchCompleted(true);
      setSearchError(error instanceof Error ? error.message : "对象搜索失败");
    } finally {
      setSearching(false);
    }
  }

  return (
    <main className="ir-page ir-home">
      <header className="ir-page-head">
        <div>
          <p className="ir-eyebrow">Research register</p>
          <h1>独立投资研究</h1>
          <p>从明确的 Company 与 Security 身份出发，维护可冻结、可复核的长期研究版本。</p>
        </div>
        <Link className="ir-button ir-button--primary" to="/research/new">建立研究项目</Link>
      </header>

      <section className="ir-search-section" aria-labelledby="ir-object-search-title">
        <div>
          <p className="ir-eyebrow">Object search</p>
          <h2 id="ir-object-search-title">查找研究对象</h2>
        </div>
        <form className="ir-search-form" onSubmit={search}>
          <label>
            <span>搜索 Company、Security 或 Industry</span>
            <input autoComplete="off" name="research_object_query" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <button className="ir-button" disabled={searching || !query.trim()} type="submit">
            {searching ? "搜索中" : "搜索对象"}
          </button>
        </form>
        {searchError ? <p className="ir-alert" role="alert">{searchError}</p> : null}
        {results.length > 0 ? (
          <ul className="ir-object-list" aria-label="对象搜索结果">
            {results.map((item) => (
              <li key={`${item.kind}:${item.object_id}`}>
                <span className={`ir-kind ir-kind--${item.kind}`}>{kindLabels[item.kind]}</span>
                <div>
                  <strong>{item.canonical_name}</strong>
                  <small>{identityLine(item)}</small>
                </div>
                <span>{item.kind === "industry" ? "仅浏览" : "可用于建项"}</span>
              </li>
            ))}
          </ul>
        ) : null}
        {searchCompleted && !searching && !searchError && results.length === 0 ? (
          <p className="ir-empty" role="status">没有找到匹配的 Company、Security 或 Industry。请检查名称、代码或身份标识。</p>
        ) : null}
      </section>

      <section className="ir-projects" aria-labelledby="ir-recent-title">
        <div className="ir-section-head">
          <div>
            <p className="ir-eyebrow">Recent projects</p>
            <h2 id="ir-recent-title">最近项目</h2>
          </div>
          <span>最近建立优先</span>
        </div>
        {loading ? (
          <div className="ir-skeleton" aria-busy="true" aria-label="项目加载中">
            <span /><span /><span />
          </div>
        ) : null}
        {loadError ? <p className="ir-alert" role="alert">{loadError}</p> : null}
        {!loading && !loadError && recentProjects.length === 0 ? (
          <div className="ir-empty">
            <strong>尚无独立研究项目</strong>
            <p>先确认一家公司与至少一只相关证券，再建立第一份研究边界。</p>
            <Link to="/research/new">建立研究项目</Link>
          </div>
        ) : null}
        {recentProjects.length > 0 ? (
          <ol className="ir-project-list">
            {recentProjects.map((project) => (
              <li key={project.id}>
                <Link to={`/research/projects/${encodeURIComponent(project.id)}`}>
                  <div>
                    <strong>{project.company_identity.canonical_name}</strong>
                    <small>{project.security_identities.map((security) => `${security.symbol} · ${security.share_class}`).join("；")} · 项目 {project.id}</small>
                  </div>
                  <span>{project.target_security_ids.length} 只 Security</span>
                  <time dateTime={project.created_at}>{new Date(project.created_at).toLocaleString("zh-CN")}</time>
                </Link>
              </li>
            ))}
          </ol>
        ) : null}
      </section>
    </main>
  );
}
