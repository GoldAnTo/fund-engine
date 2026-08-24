import { useState, type FormEvent, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import {
  InvestmentResearchRequestError,
  investmentResearchApi,
  type ProductDraft,
  type ProductObjectSearchItem,
  type ProductProject,
} from "../../data/investmentResearchApi";

const schemaVersion = "underwriting.v1" as const;
const hashPattern = "[0-9a-f]{64}";

function field(form: FormData, name: string): string {
  const value = form.get(name);
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`请填写 ${name}`);
  }
  return value.trim();
}

function optionalField(form: FormData, name: string): string | null {
  const value = form.get(name);
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function lines(value: string): string[] {
  return value.split(/\n|，|,/).map((item) => item.trim()).filter(Boolean);
}

function aware(value: string): string {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) throw new Error("时间格式无法识别");
  return timestamp.toISOString();
}

function FormField({ label, name, children, ...props }: {
  label: string;
  name: string;
  children?: ReactNode;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "name">) {
  return (
    <label className="ir-field">
      <span>{label}</span>
      {children ?? <input name={name} {...props} />}
    </label>
  );
}

function identityDetail(item: ProductObjectSearchItem): string {
  if (item.kind === "security") {
    return [item.symbol, item.exchange, item.share_class, item.trading_currency]
      .filter(Boolean)
      .join(" · ");
  }
  return item.external_key;
}

export default function NewResearchPage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductObjectSearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [companyId, setCompanyId] = useState<string | null>(null);
  const [securityIds, setSecurityIds] = useState<string[]>([]);
  const [industryIds, setIndustryIds] = useState<string[]>([]);
  const [project, setProject] = useState<ProductProject | null>(null);
  const [draft, setDraft] = useState<ProductDraft | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [identityError, setIdentityError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [baseCurrency, setBaseCurrency] = useState<"CNY" | "USD">("CNY");

  const selectedCompany = results.find((item) => item.kind === "company" && item.object_id === companyId) ?? null;
  const selectedSecurities = securityIds.flatMap((id) => {
    const item = results.find((candidate) => candidate.kind === "security" && candidate.object_id === id);
    return item ? [item] : [];
  });
  const selectedIndustries = industryIds.flatMap((id) => {
    const item = results.find((candidate) => candidate.kind === "industry" && candidate.object_id === id);
    return item ? [item] : [];
  });
  const foreignCurrencies = [...new Set(selectedSecurities
    .map((item) => item.trading_currency)
    .filter((currency): currency is "CNY" | "USD" => Boolean(currency) && currency !== baseCurrency))];
  const canConfirm = Boolean(selectedCompany) && selectedSecurities.length > 0 && !project;

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalized = query.trim();
    if (!normalized) return;
    setSearching(true);
    setSearchError(null);
    try {
      const response = await investmentResearchApi.searchObjects(normalized);
      setResults(response.items);
      setCompanyId(null);
      setSecurityIds([]);
      setIndustryIds([]);
      setProject(null);
      setDraft(null);
    } catch (error) {
      setResults([]);
      setSearchError(error instanceof Error ? error.message : "对象搜索失败");
    } finally {
      setSearching(false);
    }
  }

  function toggleSecurity(id: string) {
    setSecurityIds((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id]);
  }

  function toggleIndustry(id: string) {
    setIndustryIds((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : [...current, id]);
  }

  async function confirmIdentity() {
    if (!selectedCompany || selectedSecurities.length === 0) return;
    setConfirming(true);
    setIdentityError(null);
    try {
      const confirmedProject = await investmentResearchApi.createProject({
        schema_version: schemaVersion,
        primary_company_id: selectedCompany.object_id,
        target_security_ids: selectedSecurities.map((item) => item.object_id),
      });
      const initialDraft = await investmentResearchApi.draft(confirmedProject.id);
      setProject(confirmedProject);
      setDraft(initialDraft);
    } catch (error) {
      setIdentityError(error instanceof Error ? error.message : "身份关系校验失败");
    } finally {
      setConfirming(false);
    }
  }

  async function establishBoundary(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!project || !draft || !selectedCompany) return;
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setSetupError(null);
    try {
      const effectiveAt = aware(field(form, "effective_at"));
      const commonMarketAt = (name: string) => aware(field(form, name));
      const mandatePromise = investmentResearchApi.createMandate(project.id, {
        schema_version: schemaVersion,
        horizon_years: Number(field(form, "horizon_years")),
        base_currency: baseCurrency,
        required_return: field(form, "required_return"),
        permanent_loss_limit: field(form, "permanent_loss_limit"),
        comparison_set: lines(field(form, "comparison_set")),
        benchmark_key: optionalField(form, "benchmark_key"),
        required_excess_return: optionalField(form, "required_excess_return"),
        effective_at: effectiveAt,
        expires_at: optionalField(form, "expires_at")
          ? aware(field(form, "expires_at"))
          : null,
        expected_parent_id: null,
      });
      const focus = field(form, "user_focus");
      const scopePromise = investmentResearchApi.createScope(project.id, {
        schema_version: schemaVersion,
        primary_company_id: selectedCompany.object_id,
        target_security_ids: selectedSecurities.map((item) => item.object_id),
        industry_ids: selectedIndustries.map((item) => item.object_id),
        covered_segments: lines(field(form, "covered_segments")),
        user_focus: focus,
        exclusions: lines(field(form, "exclusions")),
        expected_parent_id: null,
      });
      const basisPromise = investmentResearchApi.createHistoricalBasis({
        schema_version: schemaVersion,
        cutoff_at: aware(field(form, "cutoff_at")),
        source_manifest_hash: field(form, "source_manifest_hash"),
        definition_bundle_hash: field(form, "definition_bundle_hash"),
        parser_bundle_hash: field(form, "parser_bundle_hash"),
      });
      const pricePromise = Promise.all(selectedSecurities.map((security) => {
        const key = security.object_id;
        if (!security.trading_currency) throw new Error(`${security.symbol ?? security.external_key} 缺少交易货币身份`);
        return investmentResearchApi.createPriceSnapshot({
          schema_version: schemaVersion,
          security_identity_id: security.identity_version_id,
          price: field(form, `price_${key}`),
          currency: security.trading_currency,
          price_type: field(form, `price_type_${key}`),
          adjustment_basis: field(form, `adjustment_basis_${key}`),
          market_at: commonMarketAt(`price_market_at_${key}`),
          available_at: commonMarketAt(`price_available_at_${key}`),
          source_id: field(form, `price_source_${key}`),
          raw_hash: field(form, `price_raw_hash_${key}`),
        });
      }));
      const fxPromise = Promise.all(foreignCurrencies.map((currency) => investmentResearchApi.createFxSnapshot({
        schema_version: schemaVersion,
        base_currency: baseCurrency,
        quote_currency: currency,
        rate: field(form, `fx_rate_${currency}`),
        quote_direction: "quote_per_base",
        market_at: commonMarketAt(`fx_market_at_${currency}`),
        available_at: commonMarketAt(`fx_available_at_${currency}`),
        source_id: field(form, `fx_source_${currency}`),
        raw_hash: field(form, `fx_raw_hash_${currency}`),
      })));
      const capitalPromise = investmentResearchApi.createCapitalStructure({
        schema_version: schemaVersion,
        company_id: selectedCompany.object_id,
        currency: baseCurrency,
        cash: field(form, "cash"),
        debt: field(form, "debt"),
        minority_interest: field(form, "minority_interest"),
        investments: field(form, "investments"),
        pension_liabilities: field(form, "pension_liabilities"),
        other_adjustments: field(form, "other_adjustments"),
        basic_shares: field(form, "basic_shares"),
        diluted_shares: field(form, "diluted_shares"),
        potential_dilution_descriptors: lines(optionalField(form, "potential_dilution_descriptors") ?? ""),
        report_period_start: aware(field(form, "report_period_start")),
        report_period_end: aware(field(form, "report_period_end")),
        market_at: aware(field(form, "capital_market_at")),
        available_at: aware(field(form, "capital_available_at")),
        source_id: field(form, "capital_source_id"),
        raw_hash: field(form, "capital_raw_hash"),
      });
      const rightsPromise = Promise.all(selectedSecurities.map((security) => {
        const key = security.object_id;
        return investmentResearchApi.createSecurityRights({
          schema_version: schemaVersion,
          security_identity_id: security.identity_version_id,
          economic_units: field(form, `economic_units_${key}`),
          votes_per_unit: field(form, `votes_per_unit_${key}`),
          conversion_ratio: field(form, `conversion_ratio_${key}`),
          adr_ratio: field(form, `adr_ratio_${key}`),
          dividend_rights_per_unit: field(form, `dividend_rights_${key}`),
          effective_from: aware(field(form, `rights_effective_from_${key}`)),
          effective_to: optionalField(form, `rights_effective_to_${key}`)
            ? aware(field(form, `rights_effective_to_${key}`))
            : null,
          source_id: field(form, `rights_source_${key}`),
          raw_hash: field(form, `rights_raw_hash_${key}`),
          expected_parent_id: null,
        });
      }));

      const [mandate, scope, basis, prices, fxRates, capital, rights] = await Promise.all([
        mandatePromise,
        scopePromise,
        basisPromise,
        pricePromise,
        fxPromise,
        capitalPromise,
        rightsPromise,
      ]);
      const agenda = await investmentResearchApi.createAgenda(project.id, {
        schema_version: schemaVersion,
        scope_id: scope.id,
        items: lines(field(form, "agenda_items")),
        generator: {
          schema_version: schemaVersion,
          method: "deterministic_template",
          template_key: field(form, "template_key"),
          template_version: field(form, "template_version"),
          model_name: null,
          prompt_template_version: null,
          input_summary_hash: optionalField(form, "agenda_input_hash"),
          output_hash: field(form, "agenda_output_hash"),
        },
        expected_parent_id: null,
      });
      const savedDraft = await investmentResearchApi.saveDraft(project.id, {
        schema_version: schemaVersion,
        expected_lock_version: draft.lock_version,
        mandate_id: mandate.id,
        scope_id: scope.id,
        agenda_id: agenda.id,
        historical_basis_id: basis.id,
        price_snapshot_ids: prices.map((item) => item.id),
        fx_snapshot_ids: fxRates.map((item) => item.id),
        capital_structure_snapshot_id: capital.id,
        security_rights_ids: rights.map((item) => item.id),
        user_focus: focus,
      });
      await investmentResearchApi.preview(project.id, {
        schema_version: schemaVersion,
        expected_lock_version: savedDraft.lock_version,
      });
      navigate(`/research/projects/${encodeURIComponent(project.id)}`);
    } catch (error) {
      const message = error instanceof InvestmentResearchRequestError || error instanceof Error
        ? error.message
        : "版本边界建立失败";
      setSetupError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="ir-page ir-setup-page">
      <header className="ir-page-head">
        <div>
          <p className="ir-eyebrow">Project foundation</p>
          <h1>建立研究项目</h1>
          <p>先固定研究对象与投资约束，再记录可复核的版本边界。这里不会自动补全研究结论。</p>
        </div>
      </header>

      <section className="ir-step" aria-labelledby="identity-step-title">
        <header>
          <span>01</span>
          <div>
            <h2 id="identity-step-title">确认 Company 与 Security</h2>
            <p>Industry 可加入浏览范围，但不能单独建立项目。</p>
          </div>
        </header>
        <form className="ir-search-form" onSubmit={search}>
          <label>
            <span>搜索公司、证券或行业</span>
            <input disabled={Boolean(project)} value={query} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <button className="ir-button" disabled={searching || Boolean(project) || !query.trim()} type="submit">
            {searching ? "搜索中" : "搜索对象"}
          </button>
        </form>
        {searchError ? <p className="ir-alert" role="alert">{searchError}</p> : null}
        {results.length > 0 ? (
          <div className="ir-identity-results" role="region" aria-label="对象搜索结果">
            {results.map((item) => (
              <label className={`ir-identity-option ir-identity-option--${item.kind}`} key={`${item.kind}:${item.object_id}`}>
                <input
                  checked={item.kind === "company" ? companyId === item.object_id : item.kind === "security" ? securityIds.includes(item.object_id) : industryIds.includes(item.object_id)}
                  disabled={Boolean(project)}
                  name={item.kind === "company" ? "company" : undefined}
                  onChange={() => item.kind === "company" ? setCompanyId(item.object_id) : item.kind === "security" ? toggleSecurity(item.object_id) : toggleIndustry(item.object_id)}
                  type={item.kind === "company" ? "radio" : "checkbox"}
                />
                <span className={`ir-kind ir-kind--${item.kind}`}>{item.kind === "company" ? "Company" : item.kind === "security" ? "Security" : "Industry"}</span>
                <span>
                  <strong>{item.canonical_name}</strong>
                  <small>{identityDetail(item)}</small>
                  {item.kind === "industry" ? <em>行业仅可加入浏览范围</em> : null}
                </span>
              </label>
            ))}
          </div>
        ) : null}
        {!project ? (
          <div className="ir-step-action">
            <p>{canConfirm ? "提交后由身份账本校验关系；提交前不声称 Company 与 Security 已关联。" : "必须选择一家公司和至少一只证券；Industry 不能替代其中任一身份。"}</p>
            <button className="ir-button ir-button--primary" disabled={!canConfirm || confirming} onClick={confirmIdentity} type="button">
              {confirming ? "校验中" : "提交身份账本校验"}
            </button>
          </div>
        ) : (
          <p className="ir-confirmed" role="status">关系已确认：身份账本已接受所选 Company 与 Security 组合。</p>
        )}
        {identityError ? <p className="ir-alert" role="alert">{identityError}</p> : null}
      </section>

      {project && draft ? (
        <form onSubmit={establishBoundary}>
          <section className="ir-step" aria-labelledby="mandate-step-title">
            <header><span>02</span><div><h2 id="mandate-step-title">研究任务与边界</h2><p>记录 InvestmentMandate 与 ResearchScope，不要求预先写出研究问题。</p></div></header>
            <div className="ir-form-grid">
              <FormField label="研究期限（年）" name="horizon_years" min="3" max="5" required type="number" />
              <label className="ir-field"><span>基础货币</span><select name="base_currency" value={baseCurrency} onChange={(event) => setBaseCurrency(event.target.value === "USD" ? "USD" : "CNY")}><option value="CNY">CNY</option><option value="USD">USD</option></select></label>
              <FormField label="必要回报率" name="required_return" required inputMode="decimal" />
              <FormField label="永久损失上限" name="permanent_loss_limit" required inputMode="decimal" />
              <FormField label="比较集合" name="comparison_set" required />
              <FormField label="生效时间" name="effective_at" required type="datetime-local" />
              <FormField label="基准标识（可选）" name="benchmark_key" />
              <FormField label="必要超额回报（与基准同时填写）" name="required_excess_return" inputMode="decimal" />
              <FormField label="到期时间（可选）" name="expires_at" type="datetime-local" />
              <FormField label="覆盖业务分部" name="covered_segments" required />
              <FormField label="研究焦点" name="user_focus" required />
              <FormField label="排除范围" name="exclusions" required />
            </div>
          </section>

          <section className="ir-step" aria-labelledby="agenda-step-title">
            <header><span>03</span><div><h2 id="agenda-step-title">研究议程</h2><p>议程是待核验事项，不代表系统会自动完成。</p></div></header>
            <div className="ir-form-grid">
              <label className="ir-field ir-field--wide"><span>议程事项</span><textarea name="agenda_items" required /></label>
              <FormField label="模板标识" name="template_key" required />
              <FormField label="模板版本" name="template_version" required />
              <FormField label="议程输入哈希（可选）" name="agenda_input_hash" pattern={hashPattern} />
              <FormField label="议程输出哈希" name="agenda_output_hash" pattern={hashPattern} required />
            </div>
          </section>

          <section className="ir-step" aria-labelledby="boundary-step-title">
            <header><span>04</span><div><h2 id="boundary-step-title">RevisionBoundary</h2><p>只记录用户提供的历史、市场、资本结构与证券权利快照，不推断缺失值。</p></div></header>
            <fieldset className="ir-fieldset">
              <legend>历史口径</legend>
              <div className="ir-form-grid">
                <FormField label="历史截止时间" name="cutoff_at" required type="datetime-local" />
                <FormField label="来源清单哈希" name="source_manifest_hash" pattern={hashPattern} required />
                <FormField label="定义包哈希" name="definition_bundle_hash" pattern={hashPattern} required />
                <FormField label="解析器包哈希" name="parser_bundle_hash" pattern={hashPattern} required />
              </div>
            </fieldset>
            {selectedSecurities.map((security) => {
              const key = security.object_id;
              const label = security.symbol ?? security.external_key;
              return (
                <fieldset className="ir-fieldset" key={`price:${key}`}>
                  <legend>{label} 市场价格</legend>
                  <div className="ir-form-grid">
                    <FormField label={`${label} 价格`} name={`price_${key}`} required inputMode="decimal" />
                    <label className="ir-field"><span>{label} 价格类型</span><select name={`price_type_${key}`}><option value="close">收盘价</option><option value="official_close">官方收盘价</option></select></label>
                    <label className="ir-field"><span>{label} 调整口径</span><select name={`adjustment_basis_${key}`}><option value="unadjusted">未复权</option><option value="split_adjusted">拆分调整</option></select></label>
                    <FormField label={`${label} 市场时间`} name={`price_market_at_${key}`} required type="datetime-local" />
                    <FormField label={`${label} 可用时间`} name={`price_available_at_${key}`} required type="datetime-local" />
                    <FormField label={`${label} 价格来源`} name={`price_source_${key}`} required />
                    <FormField label={`${label} 价格原文哈希`} name={`price_raw_hash_${key}`} pattern={hashPattern} required />
                  </div>
                </fieldset>
              );
            })}
            {foreignCurrencies.map((currency) => (
              <fieldset className="ir-fieldset" key={`fx:${currency}`}>
                <legend>{baseCurrency}/{currency} 汇率</legend>
                <div className="ir-form-grid">
                  <FormField label={`${baseCurrency}/${currency} 汇率`} name={`fx_rate_${currency}`} required inputMode="decimal" />
                  <FormField label={`${baseCurrency}/${currency} 市场时间`} name={`fx_market_at_${currency}`} required type="datetime-local" />
                  <FormField label={`${baseCurrency}/${currency} 可用时间`} name={`fx_available_at_${currency}`} required type="datetime-local" />
                  <FormField label={`${baseCurrency}/${currency} 来源`} name={`fx_source_${currency}`} required />
                  <FormField label={`${baseCurrency}/${currency} 原文哈希`} name={`fx_raw_hash_${currency}`} pattern={hashPattern} required />
                </div>
              </fieldset>
            ))}
            <fieldset className="ir-fieldset">
              <legend>资本结构（{baseCurrency}）</legend>
              <div className="ir-form-grid">
                <FormField label="现金" name="cash" required inputMode="decimal" />
                <FormField label="债务" name="debt" required inputMode="decimal" />
                <FormField label="少数股东权益" name="minority_interest" required inputMode="decimal" />
                <FormField label="投资资产" name="investments" required inputMode="decimal" />
                <FormField label="养老金负债" name="pension_liabilities" required inputMode="decimal" />
                <FormField label="其他调整" name="other_adjustments" required inputMode="decimal" />
                <FormField label="基本股数" name="basic_shares" required inputMode="decimal" />
                <FormField label="稀释股数" name="diluted_shares" required inputMode="decimal" />
                <FormField label="潜在稀释说明（可选）" name="potential_dilution_descriptors" />
                <FormField label="报告期开始" name="report_period_start" required type="datetime-local" />
                <FormField label="报告期结束" name="report_period_end" required type="datetime-local" />
                <FormField label="资本结构市场时间" name="capital_market_at" required type="datetime-local" />
                <FormField label="资本结构可用时间" name="capital_available_at" required type="datetime-local" />
                <FormField label="资本结构来源" name="capital_source_id" required />
                <FormField label="资本结构原文哈希" name="capital_raw_hash" pattern={hashPattern} required />
              </div>
            </fieldset>
            {selectedSecurities.map((security) => {
              const key = security.object_id;
              const label = security.symbol ?? security.external_key;
              return (
                <fieldset className="ir-fieldset" key={`rights:${key}`}>
                  <legend>{label} 证券权利</legend>
                  <div className="ir-form-grid">
                    <FormField label={`${label} 经济单位`} name={`economic_units_${key}`} required inputMode="decimal" />
                    <FormField label={`${label} 每单位投票权`} name={`votes_per_unit_${key}`} required inputMode="decimal" />
                    <FormField label={`${label} 转换比例`} name={`conversion_ratio_${key}`} required inputMode="decimal" />
                    <FormField label={`${label} ADR 比例`} name={`adr_ratio_${key}`} required inputMode="decimal" />
                    <FormField label={`${label} 每单位分红权`} name={`dividend_rights_${key}`} required inputMode="decimal" />
                    <FormField label={`${label} 权利生效时间`} name={`rights_effective_from_${key}`} required type="datetime-local" />
                    <FormField label={`${label} 权利结束时间（可选）`} name={`rights_effective_to_${key}`} type="datetime-local" />
                    <FormField label={`${label} 权利来源`} name={`rights_source_${key}`} required />
                    <FormField label={`${label} 权利原文哈希`} name={`rights_raw_hash_${key}`} pattern={hashPattern} required />
                  </div>
                </fieldset>
              );
            })}
            {setupError ? <p className="ir-alert" role="alert">{setupError}</p> : null}
            <div className="ir-boundary-submit">
              <p>提交后建立草稿边界并生成 publication preview；未满足证据门槛时会明确保持不可回答。</p>
              <button className="ir-button ir-button--primary" disabled={submitting} type="submit">
                {submitting ? "正在建立边界" : "建立版本边界并进入工作台"}
              </button>
            </div>
          </section>
        </form>
      ) : null}
    </main>
  );
}
