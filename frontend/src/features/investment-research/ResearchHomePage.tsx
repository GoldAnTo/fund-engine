import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  investmentResearchApi,
  type ProductObjectSearchItem,
  type ProductProject,
} from "../../data/investmentResearchApi";
import { COMPANY_RESEARCH_STATUS_LABELS, type CompanyResearchProgress } from "./companyResearchProgress";

const kindLabels: Record<ProductObjectSearchItem["kind"], string> = {
  company: "公司",
  security: "证券",
  industry: "行业",
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
  const [progressByProject, setProgressByProject] = useState<Record<string, CompanyResearchProgress | null>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductObjectSearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchCompleted, setSearchCompleted] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const searchEpochRef = useRef(0);

  useEffect(() => () => { searchEpochRef.current += 1; }, []);

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

  useEffect(() => {
    let active = true;
    setProgressByProject({});
    // Load compact status responses independently; an unavailable run must not hide its project.
    for (const project of projects) {
      void investmentResearchApi.companyResearchProject(project.id).then((result) => {
        if (!active) return;
        const progress = result.company_id === project.primary_company_id ? result.product_progress ?? null : null;
        setProgressByProject((current) => ({ ...current, [project.id]: progress }));
      }).catch(() => {
        if (active) setProgressByProject((current) => ({ ...current, [project.id]: null }));
      });
    }
    return () => { active = false; };
  }, [projects]);

  const recentProjects = projects
    .slice()
    .sort((left, right) => Date.parse(right.created_at) - Date.parse(left.created_at));

  async function runSearch() {
    const normalized = query.trim();
    if (!normalized || searching) return;
    const epoch = ++searchEpochRef.current;
    setSearching(true);
    setSearchCompleted(false);
    setSearchError(null);
    try {
      const response = await investmentResearchApi.searchObjects(normalized);
      if (searchEpochRef.current !== epoch) return;
      setResults(response.items);
      setSearchCompleted(true);
    } catch (error) {
      if (searchEpochRef.current !== epoch) return;
      setResults([]);
      setSearchCompleted(true);
      setSearchError(error instanceof Error ? error.message : "对象搜索失败");
    } finally {
      if (searchEpochRef.current === epoch) setSearching(false);
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
          <p className="ir-eyebrow">Company research</p>
          <h1>研究库</h1>
          <p>从一家公司开始，理解它如何赚钱、什么决定经营结果，以及还有哪些问题需要验证。</p>
        </div>
        <Link className="ir-button ir-button--primary" to="/research/new">开始公司研究</Link>
      </header>

      <section className="ir-search-section" aria-busy={searching} aria-labelledby="ir-object-search-title" aria-live="polite">
        <div>
          <p className="ir-eyebrow">Object search</p>
          <h2 id="ir-object-search-title">查找研究对象</h2>
        </div>
        <form className="ir-search-form" onSubmit={search}>
          <label>
            <span>公司名称或证券代码</span>
            <input autoComplete="off" name="research_object_query" placeholder="输入公司名称或证券代码" value={query} onChange={(event) => {
              searchEpochRef.current += 1;
              setQuery(event.target.value);
              setResults([]);
              setSearchCompleted(false);
              setSearchError(null);
              setSearching(false);
            }} />
          </label>
          <button className="ir-button" disabled={searching || !query.trim()} type="submit">
            {searching ? "搜索中" : "搜索公司"}
          </button>
        </form>
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
          <p className="ir-empty" role="status">没有找到匹配的公司或证券。请检查名称、代码，或换一个名称搜索。</p>
        ) : null}
      </section>

      <section className="ir-projects" aria-busy={loading} aria-labelledby="ir-recent-title" aria-live="polite">
        <div className="ir-section-head">
          <div>
            <p className="ir-eyebrow">Recent projects</p>
            <h2 id="ir-recent-title">最近研究</h2>
          </div>
          <span>最近建立优先</span>
        </div>
        {loading ? (
          <div className="ir-skeleton" aria-busy="true" aria-label="项目加载中">
            <span /><span /><span />
          </div>
        ) : null}
        {loadError ? <div className="ir-alert" role="alert"><p>{loadError}</p><button className="ir-button" onClick={() => { setLoading(true); setLoadError(null); setLoadAttempt((value) => value + 1); }} type="button">重新加载研究库</button></div> : null}
        {!loading && !loadError && recentProjects.length === 0 ? (
          <div className="ir-empty">
            <strong>开始第一份公司研究</strong>
            <p>选择公司和关联证券，可选填写关注问题。已有研究会保存在这里，方便随时继续。</p>
            <Link to="/research/new">选择研究公司</Link>
          </div>
        ) : null}
        {recentProjects.length > 0 ? (
          <ol className="ir-project-list" aria-label="已有公司研究">
            {recentProjects.map((project) => {
              const progress = progressByProject[project.id];
              return (
              <li key={project.id}>
                <Link to={`/research/projects/${encodeURIComponent(project.id)}`}>
                  <div>
                    <strong>{project.company_identity.canonical_name}</strong>
                    <small>{project.security_identities.map((security) => `${security.symbol} · ${security.share_class}`).join("；")}</small>
                    {progress?.user_focus ? <small>关注问题：{progress.user_focus}</small> : null}
                  </div>
                  <span><span aria-label={`${project.company_identity.canonical_name}的研究状态`}>
                    {progress === undefined ? "读取状态中" : progress === null ? "状态暂不可用" : COMPANY_RESEARCH_STATUS_LABELS[progress.status]}
                    {progress?.status === "needs_input" ? " · 待处理" : ""}
                  </span><small>继续研究 →</small></span>
                  <time dateTime={project.created_at}>{new Date(project.created_at).toLocaleString("zh-CN")}</time>
                </Link>
              </li>
              );
            })}
          </ol>
        ) : null}
      </section>
    </main>
  );
}
