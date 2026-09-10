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

function companyActionName(item: ProductObjectSearchItem): string {
  return item.canonical_name === "Alphabet Inc." ? "Alphabet" : item.canonical_name;
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
  const [loadAttempt, setLoadAttempt] = useState(0);

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
  }, [loadAttempt]);

  const recentProjects = projects
    .slice()
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));

  async function runSearch() {
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

  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void runSearch();
  }

  return (
    <main className="ir-page ir-home">
      <header className="ir-page-head">
        <div>
          <p className="ir-eyebrow">Research register</p>
          <h1>AI 公司研究</h1>
          <p>输入公司或证券，生成有来源、有假设、有反证并可保存的研究初稿。</p>
        </div>
        <Link className="ir-button ir-button--primary" to="/research/new">开始 AI 研究</Link>
      </header>

      <section className="ir-search-section" aria-busy={searching} aria-labelledby="ir-object-search-title">
        <div>
          <p className="ir-eyebrow">Object search</p>
          <h2 id="ir-object-search-title">查找研究对象</h2>
        </div>
        <form className="ir-search-form" onSubmit={search}>
          <label>
            <span>搜索公司或证券</span>
            <input autoComplete="off" name="company_or_security" value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <button className="ir-button" disabled={searching || !query.trim()} type="submit">
            {searching ? "搜索中" : "搜索对象"}
          </button>
        </form>
        {searching ? <p className="ir-search-status" role="status">正在搜索研究对象…</p> : null}
        {searchCompleted && !searching && !searchError && results.length > 0 ? <p className="ir-search-status" role="status">已找到 {results.length} 个相关对象。</p> : null}
        {searchError ? <div className="ir-alert" role="alert"><p>{searchError}</p><button className="ir-button" disabled={searching} onClick={() => void runSearch()} type="button">重试对象搜索</button></div> : null}
        {results.length > 0 ? (
          <ul className="ir-object-list" aria-label="对象搜索结果">
            {results.map((item) => (
              <li key={`${item.kind}:${item.object_id}`}>
                <span className={`ir-kind ir-kind--${item.kind}`}>{kindLabels[item.kind]}</span>
                <div>
                  <strong>{item.canonical_name}</strong>
                  <small>{identityLine(item)}</small>
                </div>
                {item.kind === "industry"
                  ? <Link aria-label={`查看相关公司 ${item.canonical_name}`} state={{ searchQuery: item.canonical_name }} to="/research/new">查看相关公司</Link>
                  : item.kind === "company"
                    ? <Link aria-label={`研究 ${companyActionName(item)}`} state={{ seedObject: item }} to="/research/new">研究 {companyActionName(item)}</Link>
                    : <Link aria-label={`查看 ${item.canonical_name} 的关联公司`} state={{ searchQuery: item.symbol ?? item.canonical_name }} to="/research/new">查看关联公司</Link>}
              </li>
            ))}
          </ul>
        ) : null}
        {searchCompleted && !searching && !searchError && results.length === 0 ? (
          <p className="ir-empty" role="status">没有找到匹配的 Company、Security 或 Industry。请检查名称、代码或身份标识。</p>
        ) : null}
      </section>

      <section className="ir-projects" aria-busy={loading} aria-labelledby="ir-recent-title" aria-live="polite">
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
        {loadError ? <div className="ir-alert" role="alert"><p>{loadError}</p><button className="ir-button" onClick={() => { setLoading(true); setLoadError(null); setLoadAttempt((value) => value + 1); }} type="button">重试读取项目目录</button></div> : null}
        {!loading && !loadError && recentProjects.length === 0 ? (
          <div className="ir-empty">
            <strong>尚无独立研究项目</strong>
            <p>先确认一家公司与至少一只相关证券，再建立第一份研究边界。</p>
            <Link to="/research/new">开始 AI 研究</Link>
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

      <details className="ir-advanced-tools">
        <summary>高级工具</summary>
        <nav aria-label="高级研究工具">
          <Link to="/events">事件研究与基金披露</Link>
          <Link to="/underwriting/research">历史档案</Link>
          <Link to="/monitoring">运行管理</Link>
        </nav>
      </details>
    </main>
  );
}
