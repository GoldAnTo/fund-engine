import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  InvestmentResearchRequestError,
  investmentResearchApi,
  type ProductDraft,
  type ProductAgenda,
  type ProductCapitalStructure,
  type ProductFxSnapshot,
  type ProductHistoricalBasis,
  type ProductMandate,
  type ProductObjectSearchItem,
  type ProductPriceSnapshot,
  type ProductPreview,
  type ProductProject,
  type ProductScope,
  type ProductSecurityRights,
  type CreateMandateRequest,
  type CreateScopeRequest,
  type CreateHistoricalBasisRequest,
  type CreatePriceSnapshotRequest,
  type CreateFxSnapshotRequest,
  type CreateCapitalStructureRequest,
  type CreateSecurityRightsRequest,
} from "../../data/investmentResearchApi";
import {
  generateFoundationAgenda,
  toFrozenIso,
  type FrozenTimezone,
  type GeneratedFoundationAgenda,
} from "./researchFoundation";

const schemaVersion = "underwriting.v1" as const;
const hashPattern = "[0-9a-f]{64}";
const fractionPattern = "0(?:\\.\\d+)?";
const positiveDecimalPattern = "(?:0\\.\\d*[1-9]\\d*|[1-9]\\d*(?:\\.\\d+)?)";
const nonNegativeDecimalPattern = "(?:0|[1-9]\\d*)(?:\\.\\d+)?";
const hashHint = "64 位小写十六进制 SHA-256";

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

function FormField({ label, name, children, hint, ...props }: {
  label: string;
  name: string;
  children?: ReactNode;
  hint?: string;
} & Omit<React.InputHTMLAttributes<HTMLInputElement>, "name">) {
  const hintId = `${name.replace(/[^a-zA-Z0-9_-]/g, "-")}-hint`;
  return (
    <label className="ir-field">
      <span>{label}</span>
      {children ?? <input aria-describedby={hint ? hintId : undefined} aria-label={label} name={name} {...props} />}
      {hint ? <small className="ir-field-help" id={hintId}>{hint}</small> : null}
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

function seededObject(state: unknown): ProductObjectSearchItem | null {
  if (typeof state !== "object" || state === null || !("seedObject" in state)) return null;
  const value = state.seedObject;
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const nullableString = (key: string) => record[key] === null || typeof record[key] === "string";
  if (record.schema_version !== "underwriting.v1"
    || !["company", "security", "industry"].includes(String(record.kind))
    || typeof record.object_id !== "string"
    || typeof record.identity_version_id !== "string"
    || typeof record.external_key !== "string"
    || typeof record.canonical_name !== "string"
    || !nullableString("symbol") || !nullableString("exchange")
    || !nullableString("share_class") || !nullableString("trading_currency")) return null;
  if (record.trading_currency !== null && record.trading_currency !== "CNY" && record.trading_currency !== "USD") return null;
  return {
    schema_version: "underwriting.v1",
    object_id: record.object_id,
    identity_version_id: record.identity_version_id,
    kind: record.kind as ProductObjectSearchItem["kind"],
    external_key: record.external_key,
    canonical_name: record.canonical_name,
    symbol: record.symbol as string | null,
    exchange: record.exchange as string | null,
    share_class: record.share_class as string | null,
    trading_currency: record.trading_currency as "CNY" | "USD" | null,
  };
}

export default function NewResearchPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const seed = seededObject(location.state);
  const boundaryFormRef = useRef<HTMLFormElement>(null);
  const mountedRef = useRef(true);
  const identityLockRef = useRef(false);
  const submitLockRef = useRef(false);
  const operationEpochRef = useRef(0);
  const agendaEpochRef = useRef(0);
  const identityAlertRef = useRef<HTMLParagraphElement>(null);
  const setupAlertRef = useRef<HTMLParagraphElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ProductObjectSearchItem[]>(seed ? [seed] : []);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchCompleted, setSearchCompleted] = useState(Boolean(seed));
  const [companyId, setCompanyId] = useState<string | null>(seed?.kind === "company" ? seed.object_id : null);
  const [securityIds, setSecurityIds] = useState<string[]>(seed?.kind === "security" ? [seed.object_id] : []);
  const [industryIds, setIndustryIds] = useState<string[]>(seed?.kind === "industry" ? [seed.object_id] : []);
  const [project, setProject] = useState<ProductProject | null>(null);
  const [draft, setDraft] = useState<ProductDraft | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [identityError, setIdentityError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [baseCurrency, setBaseCurrency] = useState<"CNY" | "USD">("CNY");
  const [timezone, setTimezone] = useState<FrozenTimezone>("+08:00");
  const [agendaPreview, setAgendaPreview] = useState<GeneratedFoundationAgenda | null>(null);
  const [confirmationSummary, setConfirmationSummary] = useState<{
    company: string;
    securities: string;
    mandate: string;
    timezone: FrozenTimezone;
  } | null>(null);
  const [foundation, setFoundation] = useState<{
    mandate?: ProductMandate;
    scope?: ProductScope;
    agenda?: ProductAgenda;
    basis?: ProductHistoricalBasis;
    prices: Record<string, ProductPriceSnapshot>;
    fxRates: Record<string, ProductFxSnapshot>;
    capital?: ProductCapitalStructure;
    rights: Record<string, ProductSecurityRights>;
    savedDraft?: ProductDraft;
    publicationPreview?: ProductPreview;
    inputs: {
      mandate?: CreateMandateRequest;
      scope?: CreateScopeRequest;
      basis?: CreateHistoricalBasisRequest;
      prices: Record<string, CreatePriceSnapshotRequest>;
      fxRates: Record<string, CreateFxSnapshotRequest>;
      capital?: CreateCapitalStructureRequest;
      rights: Record<string, CreateSecurityRightsRequest>;
    };
  }>({ prices: {}, fxRates: {}, rights: {}, inputs: { prices: {}, fxRates: {}, rights: {} } });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      operationEpochRef.current += 1;
      agendaEpochRef.current += 1;
    };
  }, []);

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
  const foundationStarted = Boolean(foundation.mandate || foundation.scope || foundation.agenda || foundation.basis
    || foundation.capital || foundation.savedDraft || foundation.publicationPreview
    || Object.keys(foundation.prices).length > 0 || Object.keys(foundation.fxRates).length > 0
    || Object.keys(foundation.rights).length > 0);

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
      setCompanyId(null);
      setSecurityIds([]);
      setIndustryIds([]);
      setProject(null);
      setDraft(null);
    } catch (error) {
      setResults([]);
      setSearchCompleted(true);
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
    if (identityLockRef.current || ((!selectedCompany || selectedSecurities.length === 0) && !project)) return;
    identityLockRef.current = true;
    setConfirming(true);
    setIdentityError(null);
    try {
      const confirmedProject = project ?? await investmentResearchApi.createProject({
        schema_version: schemaVersion,
        primary_company_id: selectedCompany!.object_id,
        target_security_ids: selectedSecurities.map((item) => item.object_id),
      });
      setProject(confirmedProject);
      const initialDraft = await investmentResearchApi.draft(confirmedProject.id);
      setDraft(initialDraft);
    } catch (error) {
      setIdentityError(error instanceof Error ? error.message : "身份关系校验失败");
      requestAnimationFrame(() => {
        identityAlertRef.current?.focus();
        if (typeof identityAlertRef.current?.scrollIntoView === "function") {
          identityAlertRef.current.scrollIntoView({ block: "center" });
        }
      });
    } finally {
      identityLockRef.current = false;
      if (mountedRef.current) setConfirming(false);
    }
  }

  function agendaInput(form: FormData) {
    if (!selectedCompany) throw new Error("缺少 Company 身份");
    const savedMandate = foundation.inputs.mandate;
    const savedScope = foundation.inputs.scope;
    return {
      company: {
        objectId: selectedCompany.object_id,
        canonicalName: selectedCompany.canonical_name,
        externalKey: selectedCompany.external_key,
      },
      securities: selectedSecurities.map((security) => ({
        objectId: security.object_id,
        canonicalName: security.canonical_name,
        externalKey: security.external_key,
        symbol: security.symbol,
      })),
      mandate: {
        horizonYears: savedMandate?.horizon_years ?? Number(field(form, "horizon_years")),
        baseCurrency: savedMandate?.base_currency ?? baseCurrency,
        requiredReturn: String(savedMandate?.required_return ?? field(form, "required_return")),
        permanentLossLimit: String(savedMandate?.permanent_loss_limit ?? field(form, "permanent_loss_limit")),
        comparisonSet: savedMandate?.comparison_set ?? lines(field(form, "comparison_set")),
        benchmarkKey: savedMandate ? (savedMandate.benchmark_key ?? null) : optionalField(form, "benchmark_key"),
        requiredExcessReturn: savedMandate ? (savedMandate.required_excess_return == null ? null : String(savedMandate.required_excess_return)) : optionalField(form, "required_excess_return"),
      },
      scope: {
        industryNames: selectedIndustries.map((industry) => industry.canonical_name),
        coveredSegments: savedScope ? (savedScope.covered_segments ?? []) : lines(optionalField(form, "covered_segments") ?? ""),
        userFocus: savedScope ? (savedScope.user_focus ?? null) : optionalField(form, "user_focus"),
        exclusions: savedScope ? (savedScope.exclusions ?? []) : lines(optionalField(form, "exclusions") ?? ""),
      },
    };
  }

  function focusSetupAlert() {
    requestAnimationFrame(() => {
      setupAlertRef.current?.focus();
      if (typeof setupAlertRef.current?.scrollIntoView === "function") {
        setupAlertRef.current.scrollIntoView({ block: "center" });
      }
    });
  }

  async function previewGeneratedAgenda() {
    const formElement = boundaryFormRef.current;
    if (!formElement) return;
    setSetupError(null);
    if (!formElement.reportValidity()) {
      setSetupError("请先完成所有必填字段，再生成研究议程。");
      formElement.querySelector<HTMLElement>(":invalid")?.focus();
      return;
    }
    const epoch = ++agendaEpochRef.current;
    try {
      const input = agendaInput(new FormData(formElement));
      const preview = await generateFoundationAgenda(input);
      if (mountedRef.current && agendaEpochRef.current === epoch) {
        setAgendaPreview(preview);
        setConfirmationSummary({
          company: input.company.canonicalName,
          securities: input.securities.map((security) => security.symbol ?? security.canonicalName).join("、"),
          mandate: `${input.mandate.horizonYears} 年 · ${input.mandate.baseCurrency}`,
          timezone,
        });
      }
    } catch (error) {
      if (!mountedRef.current || agendaEpochRef.current !== epoch) return;
      setSetupError(error instanceof Error ? error.message : "议程预览失败");
      focusSetupAlert();
    }
  }

  async function establishBoundary(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitLockRef.current || !project || !draft || !selectedCompany) return;
    submitLockRef.current = true;
    const operationEpoch = ++operationEpochRef.current;
    const currentOperation = () => mountedRef.current && operationEpochRef.current === operationEpoch;
    const form = new FormData(event.currentTarget);
    setSubmitting(true);
    setSetupError(null);
    try {
      if (!agendaPreview || !confirmationSummary) throw new Error("请先预览并核对提交确认摘要");
      const currentAgenda = await generateFoundationAgenda(agendaInput(form));
      if (currentAgenda.inputSummaryHash !== agendaPreview.inputSummaryHash) {
        throw new Error("研究约束已变化，请重新预览议程");
      }
      if (!currentOperation()) return;
      const frozen = (name: string) => toFrozenIso(field(form, name), timezone);
      for (const security of selectedSecurities) {
        if (!security.trading_currency) throw new Error(`${security.symbol ?? security.external_key} 缺少交易货币身份`);
      }
      const benchmarkKey = foundation.inputs.mandate ? foundation.inputs.mandate.benchmark_key : optionalField(form, "benchmark_key");
      const requiredExcessReturn = foundation.inputs.mandate ? foundation.inputs.mandate.required_excess_return : optionalField(form, "required_excess_return");
      if ((benchmarkKey === null) !== (requiredExcessReturn === null)) {
        throw new Error("基准标识与必要超额回报必须同时填写或同时留空");
      }
      const focus = foundation.inputs.scope ? foundation.inputs.scope.user_focus : optionalField(form, "user_focus");
      const next = {
        ...foundation,
        prices: { ...foundation.prices },
        fxRates: { ...foundation.fxRates },
        rights: { ...foundation.rights },
        inputs: {
          ...foundation.inputs,
          prices: { ...foundation.inputs.prices },
          fxRates: { ...foundation.inputs.fxRates },
          rights: { ...foundation.inputs.rights },
        },
      };
      const mandateInput: CreateMandateRequest | undefined = next.mandate ? undefined : {
        schema_version: schemaVersion,
        horizon_years: Number(field(form, "horizon_years")),
        base_currency: baseCurrency,
        required_return: field(form, "required_return"),
        permanent_loss_limit: field(form, "permanent_loss_limit"),
        comparison_set: lines(field(form, "comparison_set")),
        benchmark_key: benchmarkKey,
        required_excess_return: requiredExcessReturn,
        effective_at: frozen("effective_at"),
        expires_at: optionalField(form, "expires_at") ? frozen("expires_at") : null,
        expected_parent_id: null,
      };
      const scopeInput: CreateScopeRequest | undefined = next.scope ? undefined : {
        schema_version: schemaVersion,
        primary_company_id: selectedCompany.object_id,
        target_security_ids: selectedSecurities.map((item) => item.object_id),
        industry_ids: selectedIndustries.map((item) => item.object_id),
        covered_segments: lines(optionalField(form, "covered_segments") ?? ""),
        user_focus: focus,
        exclusions: lines(optionalField(form, "exclusions") ?? ""),
        expected_parent_id: null,
      };
      const basisInput: CreateHistoricalBasisRequest | undefined = next.basis ? undefined : {
        schema_version: schemaVersion,
        cutoff_at: frozen("cutoff_at"),
        source_manifest_hash: field(form, "source_manifest_hash"),
        definition_bundle_hash: field(form, "definition_bundle_hash"),
        parser_bundle_hash: field(form, "parser_bundle_hash"),
      };
      const unresolvedRights = selectedSecurities.filter((security) => !next.rights[security.object_id]);
      const effectiveRights = await Promise.all(unresolvedRights.map(async (security) => ({
        security,
        result: await investmentResearchApi.effectiveSecurityRights(
          security.object_id,
          frozen(`rights_effective_from_${security.object_id}`),
        ),
      })));
      if (!currentOperation()) return;
      for (const { security, result } of effectiveRights) {
        if (result.effective) next.rights[security.object_id] = result.effective;
        else if (result.head_id) throw new Error(`${security.symbol ?? security.external_key} 在所选时点无有效权利版本，但存在 head ${result.head_id}；请先明确 successor 父版本`);
      }
      const priceInputs: Record<string, CreatePriceSnapshotRequest> = {};
      const rightsInputs: Record<string, CreateSecurityRightsRequest> = {};
      for (const security of selectedSecurities) {
        const key = security.object_id;
        const tradingCurrency = security.trading_currency;
        if (!tradingCurrency) throw new Error(`${security.symbol ?? security.external_key} 缺少交易货币身份`);
        if (!next.prices[key]) priceInputs[key] = {
          schema_version: schemaVersion,
          security_identity_id: security.object_id,
          price: field(form, `price_${key}`),
          currency: tradingCurrency,
          price_type: field(form, `price_type_${key}`),
          adjustment_basis: field(form, `adjustment_basis_${key}`),
          market_at: frozen(`price_market_at_${key}`),
          available_at: frozen(`price_available_at_${key}`),
          source_id: field(form, `price_source_${key}`),
          raw_hash: field(form, `price_raw_hash_${key}`),
        };
        if (!next.rights[key]) rightsInputs[key] = {
          schema_version: schemaVersion,
          security_identity_id: security.object_id,
          economic_units: field(form, `economic_units_${key}`),
          votes_per_unit: field(form, `votes_per_unit_${key}`),
          conversion_ratio: field(form, `conversion_ratio_${key}`),
          adr_ratio: field(form, `adr_ratio_${key}`),
          dividend_rights_per_unit: field(form, `dividend_rights_${key}`),
          effective_from: frozen(`rights_effective_from_${key}`),
          effective_to: optionalField(form, `rights_effective_to_${key}`) ? frozen(`rights_effective_to_${key}`) : null,
          source_id: field(form, `rights_source_${key}`),
          raw_hash: field(form, `rights_raw_hash_${key}`),
          expected_parent_id: null,
        };
      }
      const fxInputs: Record<string, CreateFxSnapshotRequest> = {};
      for (const currency of foreignCurrencies) {
        if (!next.fxRates[currency]) fxInputs[currency] = {
          schema_version: schemaVersion,
          base_currency: currency,
          quote_currency: baseCurrency,
          rate: field(form, `fx_rate_${currency}`),
          quote_direction: "quote_per_base",
          market_at: frozen(`fx_market_at_${currency}`),
          available_at: frozen(`fx_available_at_${currency}`),
          source_id: field(form, `fx_source_${currency}`),
          raw_hash: field(form, `fx_raw_hash_${currency}`),
        };
      }
      const capitalInput: CreateCapitalStructureRequest | undefined = next.capital ? undefined : {
        schema_version: schemaVersion,
        company_id: selectedCompany.object_id,
        currency: baseCurrency,
        cash: field(form, "cash"), debt: field(form, "debt"), minority_interest: field(form, "minority_interest"),
        investments: field(form, "investments"), pension_liabilities: field(form, "pension_liabilities"),
        other_adjustments: field(form, "other_adjustments"), basic_shares: field(form, "basic_shares"),
        diluted_shares: field(form, "diluted_shares"),
        potential_dilution_descriptors: lines(optionalField(form, "potential_dilution_descriptors") ?? ""),
        report_period_start: frozen("report_period_start"), report_period_end: frozen("report_period_end"),
        market_at: frozen("capital_market_at"), available_at: frozen("capital_available_at"),
        source_id: field(form, "capital_source_id"), raw_hash: field(form, "capital_raw_hash"),
      };

      const attempts: Promise<void>[] = [];
      if (mandateInput) attempts.push(investmentResearchApi.createMandate(project.id, mandateInput).then((value) => { next.mandate = value; next.inputs.mandate = mandateInput; }));
      if (scopeInput) attempts.push(investmentResearchApi.createScope(project.id, scopeInput).then((value) => { next.scope = value; next.inputs.scope = scopeInput; }));
      if (basisInput) attempts.push(investmentResearchApi.createHistoricalBasis(basisInput).then((value) => { next.basis = value; next.inputs.basis = basisInput; }));
      for (const [key, input] of Object.entries(priceInputs)) attempts.push(investmentResearchApi.createPriceSnapshot(input).then((value) => { next.prices[key] = value; next.inputs.prices[key] = input; }));
      for (const [key, input] of Object.entries(rightsInputs)) attempts.push(investmentResearchApi.createSecurityRights(input).then((value) => { next.rights[key] = value; next.inputs.rights[key] = input; }));
      for (const [key, input] of Object.entries(fxInputs)) attempts.push(investmentResearchApi.createFxSnapshot(input).then((value) => { next.fxRates[key] = value; next.inputs.fxRates[key] = input; }));
      if (capitalInput) attempts.push(investmentResearchApi.createCapitalStructure(capitalInput).then((value) => { next.capital = value; next.inputs.capital = capitalInput; }));

      const results = await Promise.allSettled(attempts);
      if (!currentOperation()) return;
      setFoundation(next);
      const failed = results.find((result) => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
      if (!next.scope || !next.mandate || !next.basis || !next.capital) throw new Error("基础步骤尚未完整保存，请重试");
      if (!next.agenda) {
        next.agenda = await investmentResearchApi.createAgenda(project.id, {
          schema_version: schemaVersion,
          scope_id: next.scope.id,
          items: agendaPreview.items,
          generator: agendaPreview.generator,
          expected_parent_id: null,
        });
        setFoundation(next);
      }
      if (!next.savedDraft) {
        next.savedDraft = await investmentResearchApi.saveDraft(project.id, {
          schema_version: schemaVersion,
          expected_lock_version: draft.lock_version,
          mandate_id: next.mandate.id,
          scope_id: next.scope.id,
          agenda_id: next.agenda.id,
          historical_basis_id: next.basis.id,
          price_snapshot_ids: selectedSecurities.map((item) => next.prices[item.object_id]?.id).filter((id): id is string => Boolean(id)),
          fx_snapshot_ids: foreignCurrencies.map((currency) => next.fxRates[currency]?.id).filter((id): id is string => Boolean(id)),
          capital_structure_snapshot_id: next.capital.id,
          security_rights_ids: selectedSecurities.map((item) => next.rights[item.object_id]?.id).filter((id): id is string => Boolean(id)),
          user_focus: focus,
        });
        setFoundation(next);
      }
      if (!next.publicationPreview) {
        next.publicationPreview = await investmentResearchApi.preview(project.id, {
          schema_version: schemaVersion,
          expected_lock_version: next.savedDraft.lock_version,
        });
        setFoundation(next);
      }
      if (currentOperation()) navigate(`/research/projects/${encodeURIComponent(project.id)}`);
    } catch (error) {
      if (!currentOperation()) return;
      const message = error instanceof InvestmentResearchRequestError || error instanceof Error
        ? error.message
        : "版本边界建立失败";
      setSetupError(message);
      focusSetupAlert();
    } finally {
      if (operationEpochRef.current === operationEpoch) submitLockRef.current = false;
      if (currentOperation()) setSubmitting(false);
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

      <section className="ir-step" aria-busy={searching || confirming} aria-labelledby="identity-step-title" aria-live="polite">
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
            <input autoComplete="off" disabled={Boolean(project)} name="research_object_query" value={query} onChange={(event) => setQuery(event.target.value)} />
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
        {searchCompleted && !searching && !searchError && results.length === 0 ? (
          <p className="ir-empty" role="status">没有找到匹配的 Company、Security 或 Industry。请检查名称、代码或身份标识。</p>
        ) : null}
        {!project ? (
          <div className="ir-step-action">
            <p>{canConfirm ? "提交后由身份账本校验关系；提交前不声称 Company 与 Security 已关联。" : "必须选择一家公司和至少一只证券；Industry 不能替代其中任一身份。"}</p>
            <button className="ir-button ir-button--primary" disabled={!canConfirm || confirming} onClick={confirmIdentity} type="button">
              {confirming ? "校验中" : "提交身份账本校验"}
            </button>
          </div>
        ) : <p className="ir-confirmed" role="status">关系已确认：身份账本已接受所选 Company 与 Security 组合。</p>}
        {identityError ? <p className="ir-alert" ref={identityAlertRef} role="alert" tabIndex={-1}>{identityError}</p> : null}
        {project && !draft ? (
          <div className="ir-step-action">
            <p>项目身份已保存，草稿尚未读取；重试不会再次创建项目。</p>
            <button className="ir-button" disabled={confirming} onClick={confirmIdentity} type="button">
              {confirming ? "读取中" : "重试读取草稿"}
            </button>
          </div>
        ) : null}
      </section>

      {project && draft ? (
        <form ref={boundaryFormRef} aria-busy={submitting} onInput={() => { agendaEpochRef.current += 1; setAgendaPreview(null); setConfirmationSummary(null); }} onSubmit={establishBoundary}>
          <section className="ir-step" aria-labelledby="mandate-step-title">
            <header><span>02</span><div><h2 id="mandate-step-title">研究任务与边界</h2><p>记录 InvestmentMandate 与 ResearchScope，不要求预先写出研究问题。</p></div></header>
            <div className="ir-form-grid">
              <FormField disabled={Boolean(foundation.mandate)} label="研究期限（年）" name="horizon_years" min="3" max="5" required type="number" />
              <label className="ir-field"><span>基础货币</span><select disabled={Boolean(foundation.mandate || foundation.capital || Object.keys(foundation.fxRates).length)} name="base_currency" value={baseCurrency} onChange={(event) => setBaseCurrency(event.target.value === "USD" ? "USD" : "CNY")}><option value="CNY">CNY</option><option value="USD">USD</option></select></label>
              <FormField disabled={Boolean(foundation.mandate)} hint="小数格式：0.15 = 15%" label="必要回报率" name="required_return" pattern={fractionPattern} required inputMode="decimal" />
              <FormField disabled={Boolean(foundation.mandate)} hint="填写 0 到 1 之间的小数" label="永久损失上限" name="permanent_loss_limit" pattern={fractionPattern} required inputMode="decimal" />
              <FormField disabled={Boolean(foundation.mandate)} label="比较集合" name="comparison_set" required />
              <FormField disabled={Boolean(foundation.mandate)} label="生效时间" name="effective_at" required type="datetime-local" />
              <label className="ir-field"><span>冻结时区 / UTC offset</span><select disabled={foundationStarted} name="frozen_timezone" value={timezone} onChange={(event) => setTimezone(event.target.value === "Z" ? "Z" : "+08:00")}><option value="+08:00">+08:00（中国标准时间）</option><option value="Z">Z（UTC）</option></select></label>
              <FormField disabled={Boolean(foundation.mandate)} label="基准标识（可选）" name="benchmark_key" />
              <FormField disabled={Boolean(foundation.mandate)} hint="小数格式，与基准标识同时填写" label="必要超额回报（与基准同时填写）" name="required_excess_return" pattern={fractionPattern} inputMode="decimal" />
              <FormField disabled={Boolean(foundation.mandate)} label="到期时间（可选）" name="expires_at" type="datetime-local" />
              <FormField disabled={Boolean(foundation.scope)} label="覆盖业务分部（可选）" name="covered_segments" />
              <FormField disabled={Boolean(foundation.scope)} label="研究焦点（可选）" name="user_focus" />
              <FormField disabled={Boolean(foundation.scope)} label="排除范围（可选）" name="exclusions" />
            </div>
          </section>

          <section className="ir-step" aria-labelledby="agenda-step-title">
            <header><span>03</span><div><h2 id="agenda-step-title">研究议程</h2><p>议程由固定版本的通用模板根据已选对象和上方约束确定性生成；它只列出待核验事项，不代表研究完成。</p></div></header>
            <button className="ir-button" disabled={submitting} onClick={previewGeneratedAgenda} type="button">预览模板议程</button>
            {agendaPreview ? (
              <div className="ir-agenda-preview" role="region" aria-label="模板议程预览">
                <ol>{agendaPreview.items.map((item) => <li key={item}>{item}</li>)}</ol>
                <p>模板 product.foundation.agenda / 1.0.0 · 输入与输出 SHA-256 已由浏览器计算。</p>
              </div>
            ) : <p className="ir-empty">填写研究约束后预览；预览后才能提交。</p>}
            {confirmationSummary ? (
              <section className="ir-confirmation-summary" role="region" aria-label="提交确认摘要">
                <h3>提交前确认</h3>
                <dl>
                  <div><dt>Company</dt><dd>{confirmationSummary.company}</dd></div>
                  <div><dt>Security</dt><dd>{confirmationSummary.securities}</dd></div>
                  <div><dt>Mandate</dt><dd>{confirmationSummary.mandate}</dd></div>
                  <div><dt>冻结时区</dt><dd>{confirmationSummary.timezone}</dd></div>
                </dl>
                <p>请核对身份、研究期限、基础货币与冻结时区；提交仍由后端身份账本和领域约束最终校验。</p>
              </section>
            ) : null}
          </section>

          <section className="ir-step" aria-labelledby="boundary-step-title">
            <header><span>04</span><div><h2 id="boundary-step-title">RevisionBoundary</h2><p>只记录用户提供的历史、市场、资本结构与证券权利快照，不推断缺失值。</p></div></header>
            <fieldset className="ir-fieldset">
              <legend>历史口径</legend>
              <div className="ir-form-grid">
                <FormField disabled={Boolean(foundation.basis)} label="历史截止时间" name="cutoff_at" required type="datetime-local" />
                <FormField disabled={Boolean(foundation.basis)} hint={hashHint} label="来源清单哈希" name="source_manifest_hash" pattern={hashPattern} required />
                <FormField disabled={Boolean(foundation.basis)} hint={hashHint} label="定义包哈希" name="definition_bundle_hash" pattern={hashPattern} required />
                <FormField disabled={Boolean(foundation.basis)} hint={hashHint} label="解析器包哈希" name="parser_bundle_hash" pattern={hashPattern} required />
              </div>
            </fieldset>
            {selectedSecurities.map((security) => {
              const key = security.object_id;
              const label = security.symbol ?? security.external_key;
              return (
                <fieldset className="ir-fieldset" disabled={Boolean(foundation.prices[key])} key={`price:${key}`}>
                  <legend>{label} 市场价格</legend>
                  <div className="ir-form-grid">
                    <FormField hint={`单位：${security.trading_currency ?? "身份未提供"}`} label={`${label} 价格`} name={`price_${key}`} pattern={positiveDecimalPattern} required inputMode="decimal" />
                    <label className="ir-field"><span>{label} 价格类型</span><select name={`price_type_${key}`}><option value="close">收盘价</option><option value="official_close">官方收盘价</option></select></label>
                    <label className="ir-field"><span>{label} 调整口径</span><select name={`adjustment_basis_${key}`}><option value="unadjusted">未复权</option><option value="split_adjusted">拆分调整</option></select></label>
                    <FormField label={`${label} 市场时间`} name={`price_market_at_${key}`} required type="datetime-local" />
                    <FormField label={`${label} 可用时间`} name={`price_available_at_${key}`} required type="datetime-local" />
                    <FormField label={`${label} 价格来源`} name={`price_source_${key}`} required />
                    <FormField hint={hashHint} label={`${label} 价格原文哈希`} name={`price_raw_hash_${key}`} pattern={hashPattern} required />
                  </div>
                </fieldset>
              );
            })}
            {foreignCurrencies.map((currency) => (
              <fieldset className="ir-fieldset" key={`fx:${currency}`}>
                <legend>{currency}/{baseCurrency} 汇率</legend>
                <div className="ir-form-grid">
                  <FormField disabled={Boolean(foundation.fxRates[currency])} hint={`1 ${currency} = 输入值 ${baseCurrency}`} label={`${currency}/${baseCurrency} 汇率`} name={`fx_rate_${currency}`} pattern={positiveDecimalPattern} required inputMode="decimal" />
                  <FormField disabled={Boolean(foundation.fxRates[currency])} label={`${currency}/${baseCurrency} 市场时间`} name={`fx_market_at_${currency}`} required type="datetime-local" />
                  <FormField disabled={Boolean(foundation.fxRates[currency])} label={`${currency}/${baseCurrency} 可用时间`} name={`fx_available_at_${currency}`} required type="datetime-local" />
                  <FormField disabled={Boolean(foundation.fxRates[currency])} label={`${currency}/${baseCurrency} 来源`} name={`fx_source_${currency}`} required />
                  <FormField disabled={Boolean(foundation.fxRates[currency])} hint={hashHint} label={`${currency}/${baseCurrency} 原文哈希`} name={`fx_raw_hash_${currency}`} pattern={hashPattern} required />
                </div>
              </fieldset>
            ))}
            <fieldset className="ir-fieldset" disabled={Boolean(foundation.capital)}>
              <legend>资本结构（{baseCurrency}）</legend>
              <p className="ir-field-help">金额与股数沿用原始来源单位；同组字段必须保持一致。</p>
              <div className="ir-form-grid">
                <FormField label="现金" name="cash" pattern={nonNegativeDecimalPattern} required inputMode="decimal" />
                <FormField label="债务" name="debt" pattern={nonNegativeDecimalPattern} required inputMode="decimal" />
                <FormField label="少数股东权益" name="minority_interest" pattern={nonNegativeDecimalPattern} required inputMode="decimal" />
                <FormField label="投资资产" name="investments" pattern={nonNegativeDecimalPattern} required inputMode="decimal" />
                <FormField label="养老金负债" name="pension_liabilities" pattern={nonNegativeDecimalPattern} required inputMode="decimal" />
                <FormField label="其他调整" name="other_adjustments" required inputMode="decimal" />
                <FormField label="基本股数" name="basic_shares" pattern={positiveDecimalPattern} required inputMode="decimal" />
                <FormField label="稀释股数" name="diluted_shares" pattern={positiveDecimalPattern} required inputMode="decimal" />
                <FormField label="潜在稀释说明（可选）" name="potential_dilution_descriptors" />
                <FormField label="报告期开始" name="report_period_start" required type="datetime-local" />
                <FormField label="报告期结束" name="report_period_end" required type="datetime-local" />
                <FormField label="资本结构市场时间" name="capital_market_at" required type="datetime-local" />
                <FormField label="资本结构可用时间" name="capital_available_at" required type="datetime-local" />
                <FormField label="资本结构来源" name="capital_source_id" required />
                <FormField hint={hashHint} label="资本结构原文哈希" name="capital_raw_hash" pattern={hashPattern} required />
              </div>
            </fieldset>
            {selectedSecurities.map((security) => {
              const key = security.object_id;
              const label = security.symbol ?? security.external_key;
              return (
                <fieldset className="ir-fieldset" disabled={Boolean(foundation.rights[key])} key={`rights:${key}`}>
                  <legend>{label} 证券权利</legend>
                  <div className="ir-form-grid">
                    <FormField label={`${label} 经济单位`} name={`economic_units_${key}`} pattern={positiveDecimalPattern} required inputMode="decimal" />
                    <FormField label={`${label} 每单位投票权`} name={`votes_per_unit_${key}`} pattern={nonNegativeDecimalPattern} required inputMode="decimal" />
                    <FormField label={`${label} 转换比例`} name={`conversion_ratio_${key}`} pattern={positiveDecimalPattern} required inputMode="decimal" />
                    <FormField label={`${label} ADR 比例`} name={`adr_ratio_${key}`} pattern={positiveDecimalPattern} required inputMode="decimal" />
                    <FormField label={`${label} 每单位分红权`} name={`dividend_rights_${key}`} pattern={nonNegativeDecimalPattern} required inputMode="decimal" />
                    <FormField label={`${label} 权利生效时间`} name={`rights_effective_from_${key}`} required type="datetime-local" />
                    <FormField label={`${label} 权利结束时间（可选）`} name={`rights_effective_to_${key}`} type="datetime-local" />
                    <FormField label={`${label} 权利来源`} name={`rights_source_${key}`} required />
                    <FormField hint={hashHint} label={`${label} 权利原文哈希`} name={`rights_raw_hash_${key}`} pattern={hashPattern} required />
                  </div>
                </fieldset>
              );
            })}
            {foundationStarted
              ? <p className="ir-confirmed" role="status">已保存步骤会在重试时保留；仅待重试步骤会再次请求。本页不承诺跨进程 exactly-once。</p>
              : null}
            {setupError ? <p className="ir-alert" ref={setupAlertRef} role="alert" tabIndex={-1}>{setupError}</p> : null}
            <div className="ir-boundary-submit">
              <p>提交后建立草稿边界并生成 publication preview；未满足证据门槛时会明确保持不可回答。</p>
              <button className="ir-button ir-button--primary" disabled={submitting || !agendaPreview} type="submit">
                {submitting ? "正在建立边界" : "建立版本边界并进入工作台"}
              </button>
            </div>
          </section>
        </form>
      ) : null}
    </main>
  );
}
