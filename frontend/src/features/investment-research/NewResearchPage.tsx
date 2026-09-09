import { useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  investmentResearchApi,
  type CompanyResearchPreview,
  type IndustryCompanyBrowse,
  type IndustryCompanyBrowseItem,
  type ProductObjectSearchItem,
} from "../../data/investmentResearchApi";

type PageState = "selecting_company" | "reviewing_default" | "initializing";

type ResearchCompany = Pick<IndustryCompanyBrowseItem, "object_id" | "canonical_name">;
type ResearchSecurity = Pick<IndustryCompanyBrowseItem, "object_id" | "symbol" | "exchange" | "share_class" | "trading_currency">;
type IndustryCompanyGroup = { company: ResearchCompany; securities: ResearchSecurity[] };

function industryCompanyGroups(browse: IndustryCompanyBrowse | null): IndustryCompanyGroup[] {
  if (!browse) return [];
  const groups: IndustryCompanyGroup[] = [];
  let current: IndustryCompanyGroup | null = null;
  for (const item of browse.items) {
    if (item.kind === "company") {
      current = { company: item, securities: [] };
      groups.push(current);
    } else if (current) {
      current.securities.push(item);
    }
  }
  return groups;
}

function initialSeed(state: unknown): ProductObjectSearchItem | null {
  if (typeof state !== "object" || state === null || !("seedObject" in state)) return null;
  const value = state.seedObject;
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (item.schema_version !== "underwriting.v1" || item.kind !== "company"
    || typeof item.object_id !== "string" || typeof item.identity_version_id !== "string"
    || typeof item.external_key !== "string" || typeof item.canonical_name !== "string") return null;
  return {
    schema_version: "underwriting.v1",
    object_id: item.object_id,
    identity_version_id: item.identity_version_id,
    kind: "company",
    external_key: item.external_key,
    canonical_name: item.canonical_name,
    symbol: null,
    exchange: null,
    share_class: null,
    trading_currency: null,
  };
}

function initialSearchQuery(state: unknown): string {
  if (typeof state !== "object" || state === null || !("searchQuery" in state)) return "";
  return typeof state.searchQuery === "string" ? state.searchQuery : "";
}

function companyActionName(company: Pick<ResearchCompany, "canonical_name">): string {
  return company.canonical_name === "Alphabet Inc." ? "Alphabet" : company.canonical_name;
}

function percent(value: string): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${number * 100}%` : value;
}

function shanghaiTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function idempotencyKey(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6]! & 0x0f) | 0x40;
  bytes[8] = (bytes[8]! & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export default function NewResearchPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const seed = initialSeed(location.state);
  const initialLookupRef = useRef(seed?.external_key ?? initialSearchQuery(location.state).trim());
  const retryLookupRef = useRef(initialLookupRef.current);
  const mountedRef = useRef(true);
  const searchEpochRef = useRef(0);
  const industryBrowseEpochRef = useRef(0);
  const previewEpochRef = useRef(0);
  const initializationEpochRef = useRef(0);
  const initializationLockRef = useRef(false);
  const intentKeyRef = useRef<string | null>(null);
  const reviewHeadingRef = useRef<HTMLHeadingElement>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const [state, setState] = useState<PageState>("selecting_company");
  const [query, setQuery] = useState(() => initialSearchQuery(location.state));
  const [userFocus, setUserFocus] = useState("");
  const [results, setResults] = useState<ProductObjectSearchItem[]>([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [industryBrowse, setIndustryBrowse] = useState<IndustryCompanyBrowse | null>(null);
  const [industryBrowseIndustry, setIndustryBrowseIndustry] = useState<ProductObjectSearchItem | null>(null);
  const [browsingIndustryId, setBrowsingIndustryId] = useState<string | null>(null);
  const [industryBrowseError, setIndustryBrowseError] = useState<string | null>(null);
  const [previewingCompanyId, setPreviewingCompanyId] = useState<string | null>(null);
  const [selectedCompany, setSelectedCompany] = useState<ResearchCompany | null>(null);
  const [selectedSecurities, setSelectedSecurities] = useState<ResearchSecurity[]>([]);
  const [preview, setPreview] = useState<CompanyResearchPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [initializationError, setInitializationError] = useState<string | null>(null);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      searchEpochRef.current += 1;
      industryBrowseEpochRef.current += 1;
      previewEpochRef.current += 1;
      initializationEpochRef.current += 1;
    };
  }, []);

  useEffect(() => {
    if (state === "reviewing_default") reviewHeadingRef.current?.focus();
  }, [state]);

  useEffect(() => {
    if (searchError || industryBrowseError || previewError || initializationError) alertRef.current?.focus();
  }, [searchError, industryBrowseError, previewError, initializationError]);

  const companies = results.filter((item) => item.kind === "company");
  const securities = results.filter((item) => item.kind === "security");
  const industries = results.filter((item) => item.kind === "industry");
  const relatedCompanyGroups = industryCompanyGroups(industryBrowse);

  function invalidateDefaultPlan() {
    previewEpochRef.current += 1;
    initializationEpochRef.current += 1;
    initializationLockRef.current = false;
    intentKeyRef.current = null;
    setSelectedCompany(null);
    setSelectedSecurities([]);
    setPreviewingCompanyId(null);
    setPreview(null);
    setPreviewError(null);
    setInitializationError(null);
    setState("selecting_company");
  }

  function invalidateIndustryBrowse() {
    industryBrowseEpochRef.current += 1;
    setIndustryBrowse(null);
    setIndustryBrowseIndustry(null);
    setBrowsingIndustryId(null);
    setIndustryBrowseError(null);
  }

  async function searchFor(rawTerm: string) {
    const term = rawTerm.trim();
    if (!term) return;
    retryLookupRef.current = term;
    // A result for the old company must never take the user back to its plan
    // after they have explicitly begun looking for another research object.
    invalidateDefaultPlan();
    invalidateIndustryBrowse();
    const epoch = ++searchEpochRef.current;
    setSearching(true);
    setSearched(false);
    setSearchError(null);
    try {
      const response = await investmentResearchApi.searchObjects(term);
      if (!mountedRef.current || searchEpochRef.current !== epoch) return;
      setResults(response.items);
      setSearched(true);
    } catch (error) {
      if (!mountedRef.current || searchEpochRef.current !== epoch) return;
      setResults([]);
      setSearched(true);
      setSearchError(error instanceof Error ? error.message : "对象搜索失败");
    } finally {
      if (mountedRef.current && searchEpochRef.current === epoch) setSearching(false);
    }
  }

  useEffect(() => {
    if (initialLookupRef.current) void searchFor(initialLookupRef.current);
  }, []);

  async function runSearch(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const term = query.trim();
    if (!term || searching) return;
    await searchFor(term);
  }

  function retrySearch() {
    if (!searching) void searchFor(retryLookupRef.current);
  }

  async function browseIndustry(industry: ProductObjectSearchItem) {
    if (browsingIndustryId !== null) return;
    const epoch = ++industryBrowseEpochRef.current;
    setIndustryBrowse(null);
    setIndustryBrowseIndustry(industry);
    setBrowsingIndustryId(industry.object_id);
    setIndustryBrowseError(null);
    try {
      const response = await investmentResearchApi.industryCompanies(industry.object_id, { asOf: new Date().toISOString() });
      if (!mountedRef.current || industryBrowseEpochRef.current !== epoch) return;
      setIndustryBrowse(response);
    } catch (error) {
      if (!mountedRef.current || industryBrowseEpochRef.current !== epoch) return;
      setIndustryBrowseError(error instanceof Error ? error.message : "相关公司无法加载");
    } finally {
      if (mountedRef.current && industryBrowseEpochRef.current === epoch) setBrowsingIndustryId(null);
    }
  }

  async function reviewDefault(company: ResearchCompany, companySecurities: ResearchSecurity[]) {
    if (companySecurities.length === 0 || previewingCompanyId !== null || state === "initializing") return;
    const epoch = ++previewEpochRef.current;
    setSelectedCompany(company);
    setSelectedSecurities(companySecurities);
    setPreviewingCompanyId(company.object_id);
    setPreviewError(null);
    setInitializationError(null);
    setPreview(null);
    intentKeyRef.current = null;
    try {
      const response = await investmentResearchApi.previewCompanyResearch({
        schema_version: "underwriting.v1",
        company_id: company.object_id,
        cutoff_at: new Date().toISOString(),
        ...(userFocus.normalize("NFC").trim() ? { user_focus: userFocus.normalize("NFC").trim() } : {}),
      });
      if (!mountedRef.current || previewEpochRef.current !== epoch) return;
      setPreview(response);
      setState("reviewing_default");
    } catch (error) {
      if (!mountedRef.current || previewEpochRef.current !== epoch) return;
      setPreviewError(error instanceof Error ? error.message : "默认研究方案无法准备");
    } finally {
      if (mountedRef.current && previewEpochRef.current === epoch) setPreviewingCompanyId(null);
    }
  }

  async function startResearch() {
    if (!preview || initializationLockRef.current) return;
    initializationLockRef.current = true;
    const epoch = ++initializationEpochRef.current;
    const key = intentKeyRef.current ?? idempotencyKey();
    intentKeyRef.current = key;
    setState("initializing");
    setInitializationError(null);
    try {
      const project = await investmentResearchApi.initializeCompanyResearch({
        schema_version: "underwriting.v1",
        company_id: preview.company.object_id,
        cutoff_at: preview.cutoff_at,
        preview_hash: preview.preview_hash,
        ...(preview.user_focus ? { user_focus: preview.user_focus } : {}),
      }, key);
      if (!mountedRef.current || initializationEpochRef.current !== epoch) return;
      navigate(`/research/projects/${encodeURIComponent(project.project_id)}`, { replace: true });
    } catch (error) {
      if (!mountedRef.current || initializationEpochRef.current !== epoch) return;
      setInitializationError(error instanceof Error ? error.message : "研究启动失败");
      setState("reviewing_default");
    } finally {
      if (initializationEpochRef.current === epoch) initializationLockRef.current = false;
    }
  }

  function returnToCompanySelection() {
    invalidateDefaultPlan();
  }

  if (state === "reviewing_default" || state === "initializing") {
    if (!preview) return null;
    const securitiesText = preview.securities.map((security) => `${security.symbol} ${security.share_class}`).join("；");
    return (
      <main className="ir-page ir-company-entry">
        <header className="ir-page-head">
          <div>
            <p className="ir-eyebrow">Company research</p>
            <h1 ref={reviewHeadingRef} tabIndex={-1}>确认默认研究方案</h1>
            <p>系统会以固定策略建立公司研究并准备可核对的资料；不会把资料准备伪装成已经完成的结论。</p>
          </div>
        </header>
        <section aria-busy={state === "initializing"} aria-labelledby="default-plan-title" className="ir-default-plan" aria-live="polite">
          <h2 id="default-plan-title">{preview.company.canonical_name}</h2>
          <dl>
            <div><dt>研究对象</dt><dd>研究对象：{preview.company.canonical_name}</dd></div>
            <div><dt>关联证券</dt><dd>关联证券：{securitiesText}</dd></div>
            <div><dt>关注问题</dt><dd>{preview.user_focus ?? "全面了解公司"}</dd></div>
            <div><dt>研究期限</dt><dd>研究期限：{preview.horizon_years} 年</dd></div>
            <div><dt>基准币种</dt><dd>基准币种：{preview.base_currency}</dd></div>
            <div><dt>最低要求回报</dt><dd>最低要求回报：{percent(preview.required_return)}</dd></div>
            <div><dt>永久损失边界</dt><dd>永久损失边界：{percent(preview.permanent_loss_limit)}</dd></div>
            <div><dt>资料截止</dt><dd>资料截止：{shanghaiTime(preview.cutoff_at)}（Asia/Shanghai）</dd></div>
          </dl>
          <p className="ir-default-plan__agenda">系统将准备：业务地图、经营驱动、证据与缺口、财务桥、三种情景、DCF/反向 DCF、反证与版本</p>
          {initializationError ? <div className="ir-alert" ref={alertRef} role="alert" tabIndex={-1}><p>{initializationError}</p><button className="ir-button" onClick={() => void startResearch()} type="button">重试开始研究</button></div> : null}
          <div className="ir-step-action">
            <button className="ir-button" disabled={state === "initializing"} onClick={returnToCompanySelection} type="button">返回选择公司</button>
            <button className="ir-button ir-button--primary" disabled={state === "initializing"} onClick={() => void startResearch()} type="button">{state === "initializing" ? "正在开始研究" : "开始研究"}</button>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="ir-page ir-company-entry">
      <header className="ir-page-head">
        <div>
          <p className="ir-eyebrow">Company research</p>
          <h1>选择研究公司</h1>
          <p>输入公司名称或证券代码，确认研究对象后开始。也可以先写下你最关注的问题。</p>
        </div>
      </header>
      <section aria-busy={searching} aria-labelledby="company-search-title" className="ir-search-section" aria-live="polite">
        <h2 id="company-search-title">查找研究对象</h2>
        <form className="ir-search-form" onSubmit={(event) => void runSearch(event)}>
          <label>
            <span>搜索公司、证券或行业</span>
            <input autoComplete="off" name="research_object_query" onChange={(event) => {
              setQuery(event.target.value);
              searchEpochRef.current += 1;
              setSearching(false);
              setSearched(false);
              setSearchError(null);
              setResults([]);
              invalidateDefaultPlan();
              invalidateIndustryBrowse();
            }} value={query} />
          </label>
          <button className="ir-button" disabled={searching || !query.trim()} type="submit">{searching ? "搜索中" : "搜索对象"}</button>
        </form>
        <div className="ir-field ir-focus-field">
          <label htmlFor="research-user-focus">关注问题（可选）</label>
          <textarea
            aria-describedby="research-user-focus-help"
            id="research-user-focus"
            maxLength={2000}
            placeholder="例如：云业务增长能否改善公司的长期现金流？"
            rows={3}
            value={userFocus}
            onChange={(event) => {
              setUserFocus(event.target.value);
              invalidateDefaultPlan();
            }}
          />
          <p className="ir-field-help" id="research-user-focus-help">问题将随本次研究范围保存；留空则使用默认公司研究议程。</p>
        </div>
        {searchError ? <div className="ir-alert" ref={alertRef} role="alert" tabIndex={-1}><p>{searchError}</p><button className="ir-button" disabled={searching} onClick={retrySearch} type="button">重试对象搜索</button></div> : null}
        {previewError ? <div className="ir-alert" ref={alertRef} role="alert" tabIndex={-1}><p>{previewError}</p><button className="ir-button" disabled={previewingCompanyId !== null} onClick={() => { if (selectedCompany) void reviewDefault(selectedCompany, selectedSecurities); }} type="button">重试默认方案</button></div> : null}
        {industryBrowseError && industryBrowseIndustry ? <div className="ir-alert" ref={alertRef} role="alert" tabIndex={-1}><p>{industryBrowseError}</p><button className="ir-button" disabled={browsingIndustryId !== null} onClick={() => void browseIndustry(industryBrowseIndustry)} type="button">重试相关公司</button></div> : null}
        {results.length > 0 ? (
          <div aria-label="对象搜索结果" className="ir-company-results" role="region">
            {companies.map((company) => (
              <article className="ir-company-card" key={company.object_id}>
                <div><span className="ir-kind ir-kind--company">Company</span><h3>{company.canonical_name}</h3><p>{company.external_key}</p></div>
                {securities.length > 0 ? <p className="ir-company-card__securities">关联证券：{securities.map((security) => `${security.symbol} ${security.share_class}`).join("；")}</p> : <p className="ir-company-card__securities">尚未返回关联证券；不能建立研究。</p>}
                <button className="ir-button ir-button--primary" disabled={securities.length === 0 || previewingCompanyId !== null} onClick={() => void reviewDefault(company, securities)} type="button">{previewingCompanyId === company.object_id ? "正在准备默认方案" : `研究 ${companyActionName(company)}`}</button>
              </article>
            ))}
            {securities.length > 0 ? <section aria-label="关联证券" className="ir-related-securities"><h3>关联 Securities</h3><ul>{securities.map((security) => <li key={security.object_id}><span className="ir-kind ir-kind--security">Security</span><strong>{security.symbol} {security.share_class}</strong><small>{security.exchange} · {security.trading_currency}</small></li>)}</ul></section> : null}
            {industries.length > 0 ? <section aria-label="行业浏览" className="ir-industry-browse"><h3>行业上下文</h3>{industries.map((industry) => <div key={industry.object_id}><span className="ir-kind ir-kind--industry">Industry</span><strong>{industry.canonical_name}</strong><button className="ir-button" disabled={browsingIndustryId !== null} onClick={() => void browseIndustry(industry)} type="button">{browsingIndustryId === industry.object_id ? "正在加载相关公司" : "查看相关公司"}</button></div>)}</section> : null}
          </div>
        ) : null}
        {industryBrowse && relatedCompanyGroups.length > 0 ? <section aria-label="行业相关公司" className="ir-company-results" role="region"><h3>{industryBrowseIndustry?.canonical_name} 的相关公司</h3>{relatedCompanyGroups.map(({ company, securities: groupSecurities }) => <article className="ir-company-card" key={company.object_id}><div><span className="ir-kind ir-kind--company">Company</span><h3>{company.canonical_name}</h3></div><p className="ir-company-card__securities">{groupSecurities.length > 0 ? `关联证券：${groupSecurities.map((security) => `${security.symbol} ${security.share_class}`).join("；")}` : "尚无在该时点有效的关联证券；不能建立研究。"}</p><button className="ir-button ir-button--primary" disabled={groupSecurities.length === 0 || previewingCompanyId !== null} onClick={() => void reviewDefault(company, groupSecurities)} type="button">{previewingCompanyId === company.object_id ? "正在准备默认方案" : `研究 ${companyActionName(company)}`}</button></article>)}</section> : null}
        {industryBrowse && relatedCompanyGroups.length === 0 ? <p className="ir-empty" role="status">该行业暂未配置可研究公司。</p> : null}
        {searched && !searching && !searchError && results.length === 0 ? <p className="ir-empty" role="status">没有找到匹配的 Company、Security 或 Industry。请检查名称、代码或身份标识。</p> : null}
      </section>
    </main>
  );
}
