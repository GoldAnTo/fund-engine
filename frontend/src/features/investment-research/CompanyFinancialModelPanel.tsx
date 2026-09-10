import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { investmentResearchApi, InvestmentResearchRequestError } from "../../data/investmentResearchApi";
import { FINANCIAL_DRIVERS, financialDecimal, isFinancialModelInputs, type CompanyFinancialModelInputs, type CompanyFinancialModelRecord, type CompanyFinancialModelWorkspace, type FinancialBaseline, type FinancialDriver, type FinancialMarket, type FinancialScenarioId } from "../../data/companyFinancialModel";
import "./CompanyFinancialModelPanel.css";

type Props = { projectId: string; parentRevisionId: string; parentManifestHash: string; mode: "forecast" | "valuation" };
const SCENARIOS: Record<FinancialScenarioId, string> = { base: "基准", bull: "乐观", bear: "谨慎" };
const DRIVERS: Record<FinancialDriver, string> = { revenue: "收入", operating_margin: "营业利润率", cash_tax_rate: "现金税率", depreciation: "固定资产折旧", capex: "资本开支", working_capital_change: "营运资金增加" };
const SCENARIO_IDS = ["base", "bull", "bear"] as const;
const copyInputs = (value: CompanyFinancialModelInputs): CompanyFinancialModelInputs => JSON.parse(JSON.stringify(value)) as CompanyFinancialModelInputs;
const dateLabel = (value: string) => value.slice(0, 10);
const amount = (value: string) => Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const percent = (value: string | null) => value === null ? "不适用" : `${(Number(value) * 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
const message = (error: unknown) => error instanceof InvestmentResearchRequestError && error.status === 409 ? "服务器已有新的模型草稿。你的输入已保留，请先更新版本信息再保存。" : error instanceof Error ? `${error.message}。输入已保留，可重试。` : "模型草稿暂时无法保存。输入已保留，可重试。";

function Baseline({ value, market }: { value: FinancialBaseline; market: FinancialMarket | null }) {
  const [showAll, setShowAll] = useState(false);
  const latestAnnual = value.facts.filter((fact) => fact.period_start.endsWith("01-01") && fact.period_end.endsWith("12-31")).map((fact) => fact.period_end).sort().at(-1);
  const latestHalf = value.facts.filter((fact) => fact.period_start.endsWith("01-01") && fact.period_end.endsWith("06-30")).map((fact) => fact.period_end).sort().at(-1);
  const recent = value.facts.filter((fact) => (fact.period_end === latestAnnual || fact.period_end === latestHalf) && /(?:revenue|operating_income|operating_margin|cfo|capex|fcf|ppe_depreciation|working_capital_investment)$/.test(fact.fact_key));
  const facts = showAll || value.facts.length <= 16 ? value.facts : recent;
  return <section className="ir-financial-baseline" aria-label="事实基线与缺口">
    <h4>已披露事实基线</h4><p>金额按来源单位展示；集团与云分部、年度与上半年分别列示。预测表是条件假设，不等于管理层承诺。</p>
    <div className="ir-financial-scroll" tabIndex={0} role="region" aria-label="财务基线表"><table><thead><tr><th>披露事实</th><th>范围 / 状态</th><th>期间</th><th>数值</th></tr></thead><tbody>{facts.map((fact) => <tr key={fact.fact_key}><th scope="row">{fact.label}<details><summary>原文与推导依据</summary><p>{fact.source_locator}</p><blockquote>{fact.quote}</blockquote>{fact.formula ? <p>计算式：{fact.formula}</p> : null}{fact.input_fact_keys.length ? <p>输入事实：{fact.input_fact_keys.join("、")}</p> : null}{fact.source_ids.map((id) => { const source = value.sources.find((s) => s.id === id); return source ? <p key={id}><a href={source.url} target="_blank" rel="noreferrer">{source.title}</a> · 可得日 {dateLabel(source.available_at)}</p> : null; })}</details></th><td>{fact.scope === "group" ? "集团" : "Google Cloud"}<small>{({ reported: "已披露", derived: "确定性推导", guidance: "管理层指引" })[fact.state]}</small></td><td>{fact.period_start} 至 {fact.period_end}</td><td title={fact.value}>{fact.unit === "ratio" ? percent(fact.value) : amount(fact.value)}<small>{({ USD_million: "百万美元", million_shares: "百万股", ratio: "比例" } as Record<string, string>)[fact.unit] ?? fact.unit}</small></td></tr>)}</tbody></table></div>
    {value.facts.length > 16 ? <button type="button" className="ir-button" onClick={() => setShowAll((old) => !old)}>{showAll ? "收起为最新年度与上半年关键基线" : `查看全部 ${value.facts.length} 项事实与推导`}</button> : null}
    {value.research_gaps.length ? <div className="ir-financial-gaps"><h4>尚缺的研究输入</h4><ul>{value.research_gaps.slice(0, 3).map((gap) => <li key={gap.key}><strong>{gap.label}</strong><p>{gap.detail}</p></li>)}</ul>{value.research_gaps.length > 3 ? <details><summary>全部 {value.research_gaps.length} 项研究缺口</summary><ul>{value.research_gaps.slice(3).map((gap) => <li key={gap.key}><strong>{gap.label}</strong><p>{gap.detail}</p></li>)}</ul></details> : null}</div> : null}
    {market ? <details><summary>冻结市场与资本结构</summary><p>市场参考日 {dateLabel(market.market_at)}；USD/CNY {amount(market.usd_cny_rate)}。币种转换是同一估值的表达，不是额外收益。</p><dl className="ir-financial-market">{(["cash", "debt", "investments", "diluted_shares"] as const).map((key) => <div key={key}><dt>{{ cash: "现金（百万美元）", debt: "债务（百万美元）", investments: "投资（百万美元）", diluted_shares: "稀释股数（百万股）" }[key]}</dt><dd>{amount(market.capital_structure[key])}</dd></div>)}</dl>{market.securities.map((s) => <p key={s.security_external_key}>{s.security_external_key}：市场价 {amount(s.market_price_usd)} USD · <a href={s.price_ref.source_url} target="_blank" rel="noreferrer">价格依据</a></p>)}<p><a href={market.capital_structure.source_ref.source_url} target="_blank" rel="noreferrer">资本结构依据</a> · <a href={market.fx_ref.source_url} target="_blank" rel="noreferrer">汇率依据</a></p></details> : <p>缺少可验证市场快照，仅显示集团企业价值，不提供每股价值比较。</p>}
  </section>;
}

export function CompanyFinancialModelPanel(props: Props) {
  // Key the entire editable session to its frozen identity; late completions cannot reach a new project.
  return <FinancialModelSession key={`${props.projectId}:${props.parentRevisionId}:${props.parentManifestHash}`} {...props} />;
}

function FinancialModelSession({ projectId, parentRevisionId, parentManifestHash, mode }: Props) {
  const [context, setContext] = useState<CompanyFinancialModelWorkspace | null>(null);
  const [inputs, setInputs] = useState<CompanyFinancialModelInputs | null>(null);
  const [selected, setSelected] = useState<CompanyFinancialModelRecord | null>(null);
  const [activeScenario, setActiveScenario] = useState<FinancialScenarioId>("base");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<"load" | "save" | "history" | "export" | null>("load");
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState(false);
  const mounted = useRef(false);
  const action = useRef(0);
  const pendingSave = useRef<{ body: string; key: string } | null>(null);
  function matches(value: { project_id: string; parent_revision_id: string; parent_manifest_hash: string }) { return value.project_id === projectId && value.parent_revision_id === parentRevisionId && value.parent_manifest_hash === parentManifestHash; }
  function assertParent(value: Parameters<typeof matches>[0]) { if (!matches(value)) throw new Error("模型草稿与当前冻结版本不一致"); }

  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    void investmentResearchApi.companyFinancialModel(projectId).then((value) => {
      if (cancelled) return; assertParent(value);
      setContext(value); setInputs(copyInputs(value.latest?.inputs ?? value.initial_inputs)); setSelected(value.latest);
    }).catch((reason: unknown) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "模型草稿暂时无法读取"); }).finally(() => { if (!cancelled) setBusy(null); });
    return () => { cancelled = true; mounted.current = false; action.current += 1; };
  // The keyed session's identity is immutable for its lifetime.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, parentRevisionId, parentManifestHash]);

  async function reloadContext() {
    const token = ++action.current; setBusy("load"); setError(null);
    try {
      const value = await investmentResearchApi.companyFinancialModel(projectId);
      if (!mounted.current || token !== action.current) return; assertParent(value); setContext(value);
      if (inputs === null) { setInputs(copyInputs(value.latest?.inputs ?? value.initial_inputs)); setSelected(value.latest); }
      setConflict(false); setNotice("版本信息已更新，当前输入已保留；请核对事实基线后再保存。");
    } catch (reason) { if (mounted.current && token === action.current) setError(message(reason)); }
    finally { if (mounted.current && token === action.current) setBusy(null); }
  }
  function edit(update: (current: CompanyFinancialModelInputs) => CompanyFinancialModelInputs) { setInputs((current) => current ? update(current) : current); setDirty(true); setNotice(null); }
  function changePath(key: FinancialDriver, index: number, value: string) { edit((current) => ({ ...current, scenarios: current.scenarios.map((scenario) => scenario.scenario_id !== activeScenario ? scenario : { ...scenario, paths: { ...scenario.paths, [key]: scenario.paths[key].map((old, i) => i === index ? value : old) } }) })); }
  function changeReason(key: FinancialDriver, value: string) { edit((current) => ({ ...current, scenarios: current.scenarios.map((scenario) => scenario.scenario_id !== activeScenario ? scenario : { ...scenario, rationales: { ...scenario.rationales, [key]: value } }) })); }
  async function save() {
    if (!context || !inputs || busy !== null || conflict) return;
    const allReasons = [...inputs.scenarios.flatMap((s) => [s.mechanism, ...Object.values(s.rationales)]), inputs.discount_rate_rationale, inputs.terminal_rationale, inputs.timing_rationale];
    if (!isFinancialModelInputs(inputs) || allReasons.some((reason) => reason.trim().length < 4 || reason.trim().length > 3000)) { setError("请填写有效的十进制数值及每项假设理由。输入已保留。"); return; }
    const body = { parent_revision_id: parentRevisionId, expected_latest_id: context.latest?.id ?? null, baseline_content_hash: context.baseline.content_hash, inputs: copyInputs(inputs) };
    const serialized = JSON.stringify(body);
    if (pendingSave.current?.body !== serialized) pendingSave.current = { body: serialized, key: crypto.randomUUID() };
    const key = pendingSave.current.key;
    const token = ++action.current; setBusy("save"); setError(null); setNotice(null);
    try {
      const saved = await investmentResearchApi.saveCompanyFinancialModel(projectId, body, key);
      if (!mounted.current || token !== action.current) return; assertParent(saved);
      setContext((old) => old ? { ...old, latest: saved, history: [{ id: saved.id, sequence: saved.sequence, created_at: saved.created_at, input_hash: saved.input_hash, content_hash: saved.content_hash }, ...old.history.filter((item) => item.id !== saved.id)] } : old);
      setSelected(saved); setInputs(copyInputs(saved.inputs)); setDirty(false); pendingSave.current = null;
      setNotice(`模型草稿 ${saved.sequence} 已保存，尚未复核。`);
    } catch (reason) { if (mounted.current && token === action.current) { setError(message(reason)); setConflict(reason instanceof InvestmentResearchRequestError && reason.status === 409); } }
    finally { if (mounted.current && token === action.current) setBusy(null); }
  }
  async function loadHistory(id: string) {
    if (busy !== null || !context) return;
    const token = ++action.current; setBusy("history"); setError(null);
    try {
      const value = await investmentResearchApi.companyFinancialModelDraft(projectId, id);
      if (!mounted.current || token !== action.current) return; assertParent(value);
      const expected = context.history.find((item) => item.id === id);
      if (value.id !== id || !expected || value.content_hash !== expected.content_hash || value.input_hash !== expected.input_hash || value.sequence !== expected.sequence) throw new Error("历史模型草稿收据不一致");
      setSelected(value); setInputs(copyInputs(value.inputs)); setDirty(false); setNotice(`正在回放模型草稿 ${value.sequence}`);
    } catch (reason) { if (mounted.current && token === action.current) setError(message(reason)); }
    finally { if (mounted.current && token === action.current) setBusy(null); }
  }
  async function exportSelected() {
    if (!selected || busy !== null) return;
    const token = ++action.current; setBusy("export"); setError(null);
    try {
      const envelope = await investmentResearchApi.exportCompanyFinancialModelDraft(projectId, selected.id);
      if (!mounted.current || token !== action.current) return;
      const href = URL.createObjectURL(new Blob([envelope.content], { type: envelope.media_type }));
      try { const anchor = document.createElement("a"); anchor.href = href; anchor.download = envelope.filename; anchor.click(); }
      finally { URL.revokeObjectURL(href); }
      setNotice(`模型草稿 ${selected.sequence} 已导出，状态仍为未复核。`);
    } catch (reason) { if (mounted.current && token === action.current) setError(message(reason)); }
    finally { if (mounted.current && token === action.current) setBusy(null); }
  }

  const scenario = inputs?.scenarios.find((value) => value.scenario_id === activeScenario);
  const result = selected?.result.scenarios.find((value) => value.scenario_id === activeScenario);
  const isHistory = selected !== null && context?.latest?.id !== selected.id;
  const busyLabel = { load: "正在读取模型草稿…", save: "正在保存并计算…", history: "正在验证历史草稿…", export: "正在验证导出…" };
  return <section className="ir-financial-model" aria-label="条件估值模型草稿" aria-busy={busy !== null}>
    <header><div><h3>条件估值模型草稿</h3><p>附着已冻结研究，独立保存条件模型，不改变已冻结研究的正式判断。保存与计算不代表人工确认。</p></div><span className="ir-financial-status">{selected ? `模型草稿 ${selected.sequence} · 未复核` : "输入草稿 · 尚未保存"}</span></header>
    {busy ? <p role="status">{busyLabel[busy]}</p> : null}
    {error ? <div className="ir-alert" role="alert"><p>{error}</p>{conflict || context === null ? <button className="ir-button" type="button" disabled={busy !== null} onClick={() => void reloadContext()}>{conflict ? "更新版本信息，保留输入" : "重试读取模型草稿"}</button> : null}</div> : null}
    {notice ? <p className="ir-confirmed" role="status">{notice}</p> : null}
    {context && inputs && scenario ? <>
      <p className="ir-financial-cutoff">资料截止日 {dateLabel(context.cutoff_at)} · 估值条件沿用冻结市场快照</p>
      {mode === "forecast" ? <details className="ir-financial-baseline-review"><summary>核对历史财务基线（{(selected?.baseline ?? context.baseline).facts.length} 项 / {(selected?.baseline ?? context.baseline).sources.length} 份原文）</summary><Baseline value={selected?.baseline ?? context.baseline} market={selected?.market ?? context.market} /></details> : null}
      <div className="ir-financial-tabs" role="tablist" aria-label="模型情景">{SCENARIO_IDS.map((id) => <button type="button" role="tab" key={id} aria-selected={activeScenario === id} onClick={() => setActiveScenario(id)}>{SCENARIOS[id]}</button>)}</div>
      {mode === "forecast" ? <form onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <fieldset disabled={busy !== null}><legend>{SCENARIOS[activeScenario]}情景 · 五年条件预测</legend>
          <label className="ir-financial-field">情景机制<textarea aria-label={`${SCENARIOS[activeScenario]} 情景机制`} value={scenario.mechanism} onChange={(event) => edit((current) => ({ ...current, scenarios: current.scenarios.map((s) => s.scenario_id === activeScenario ? { ...s, mechanism: event.target.value } : s) }))} /></label>
          <p>金额单位：百万美元。比例用小数填写，如 0.1 表示 10%。营运资金增加为正时扣减现金流。</p>
          <div className="ir-financial-scroll" tabIndex={0} role="region" aria-label={`${SCENARIOS[activeScenario]}五年预测输入`}><table><thead><tr><th>经营驱动</th>{Array.from({ length: 5 }, (_, i) => <th key={i}>{inputs.first_fiscal_year + i}</th>)}</tr></thead><tbody>{FINANCIAL_DRIVERS.map((key) => <tr key={key}><th scope="row">{DRIVERS[key]}<small>{["operating_margin", "cash_tax_rate"].includes(key) ? "比例（小数）" : "百万美元"}</small></th>{scenario.paths[key].map((value, i) => <td key={i}><input type="text" inputMode="decimal" aria-label={`${SCENARIOS[activeScenario]} ${inputs.first_fiscal_year + i} ${DRIVERS[key]}`} aria-invalid={value !== "" && !financialDecimal(value)} value={value} onChange={(event) => changePath(key, i, event.target.value)} /></td>)}</tr>)}</tbody></table></div>
          <details><summary>六项驱动假设的理由与边界</summary><div className="ir-financial-reasons">{FINANCIAL_DRIVERS.map((key) => <label className="ir-financial-field" key={key}>{DRIVERS[key]}理由<textarea aria-label={`${SCENARIOS[activeScenario]} ${DRIVERS[key]}理由`} value={scenario.rationales[key]} onChange={(event) => changeReason(key, event.target.value)} /></label>)}</div></details>
          <h4>估值与现金流时点假设</h4><div className="ir-financial-parameters">{([ ["discount_rate", "折现率"], ["terminal_growth", "永续增长率"], ["terminal_roic", "终值投入资本回报率"], ["first_year_cash_flow_fraction", "首年剩余现金流比例"] ] as const).map(([key, label]) => <label className="ir-financial-field" key={key}>{label}（小数）<input type="text" inputMode="decimal" value={inputs[key]} onChange={(event) => edit((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</div>
          <p>终值以增长率 ÷ 投入资本回报率计算再投资需求。首年比例作用于全年现金流金额；距年末的实际折现期由估值日期决定。</p>
          <details><summary>估值参数与时点理由</summary>{([["discount_rate_rationale", "折现率理由"], ["terminal_rationale", "终值增长与再投资理由"], ["timing_rationale", "首年现金流时点理由"]] as const).map(([key, label]) => <label className="ir-financial-field" key={key}>{label}<textarea value={inputs[key]} onChange={(event) => edit((current) => ({ ...current, [key]: event.target.value }))} /></label>)}</details>
          {isHistory ? <p>当前载入的是历史输入；保存会新增模型草稿，不覆盖历史。</p> : null}
          <button className="ir-button ir-button--primary" type="submit" disabled={conflict}>保存草稿并计算</button>
        </fieldset>
      </form> : <p><Link to={`/research/projects/${projectId}/forecast`}>编辑预测与估值假设</Link></p>}
      {selected && result ? <section className="ir-financial-results" aria-label="已保存的条件估值结果"><h4>{SCENARIOS[activeScenario]}情景 · 已保存结果</h4><p>模型草稿 {selected.sequence}，未复核。{dirty ? "当前输入修改尚未保存，以下仍为上次保存的结果。" : "结果取自已保存输入的确定性计算。"}</p><p>{result.mechanism}</p><p>价值与价格差是条件价值比较，不是年化收益率或收益承诺。</p><dl className="ir-financial-market"><div><dt>企业价值（百万美元）</dt><dd title={result.enterprise_value_usd_million}>{amount(result.enterprise_value_usd_million)}</dd></div><div><dt>终值占企业价值</dt><dd>{percent(result.terminal_value_share)}</dd></div></dl>
        <div className="ir-financial-scroll" tabIndex={0} role="region" aria-label="证券条件价值表"><table><thead><tr><th>证券</th><th>条件价值 USD / 股</th><th>CNY / 股</th><th>快照市场价 USD</th><th>价值与价格差</th></tr></thead><tbody>{result.securities.map((s) => <tr key={s.security_external_key}><th scope="row">{s.security_external_key}</th><td title={s.value_usd_per_share}>{amount(s.value_usd_per_share)}</td><td title={s.value_cny_per_share}>{amount(s.value_cny_per_share)}</td><td title={s.market_price_usd}>{amount(s.market_price_usd)}</td><td>{percent(s.value_price_gap_ratio)}</td></tr>)}</tbody></table></div>
        <details><summary>五年现金流计算与模型边界</summary><div className="ir-financial-scroll" tabIndex={0} role="region" aria-label="五年现金流计算表"><table><thead><tr><th>年度</th><th>营业利润</th><th>固定资产折旧</th><th>资本开支</th><th>营运资金增加</th><th>FCFF</th></tr></thead><tbody>{result.rows.map((row) => <tr key={row.fiscal_year}><th scope="row">{row.fiscal_year}</th><td title={row.operating_income}>{amount(row.operating_income)}</td><td title={row.depreciation}>{amount(row.depreciation)}</td><td title={row.capex}>{amount(row.capex)}</td><td title={row.working_capital_change}>{amount(row.working_capital_change)}</td><td title={row.fcff}>{amount(row.fcff)}</td></tr>)}</tbody></table></div><ul>{[...selected.result.warnings, ...selected.result.policies].map((warning, i) => <li key={i}>{warning}</li>)}</ul></details>
        <button className="ir-button" type="button" disabled={busy !== null} onClick={() => void exportSelected()}>导出模型草稿 {selected.sequence}</button>
      </section> : mode === "valuation" ? <p>尚未保存条件估值结果。请在预测页补充假设后保存草稿并计算。</p> : null}
      <details className="ir-financial-history"><summary>模型草稿历史（{context.history.length}）</summary>{dirty ? <p>加载历史会替换当前未保存的输入。</p> : null}<ul>{[...context.history].sort((a, b) => b.sequence - a.sequence).map((item) => <li key={item.id}><span>模型草稿 {item.sequence} · {dateLabel(item.created_at)} · 未复核</span><button className="ir-button" type="button" disabled={busy !== null} onClick={() => void loadHistory(item.id)}>加载模型草稿 {item.sequence}</button></li>)}</ul>{selected ? <dl><div><dt>父研究版本</dt><dd>{selected.parent_revision_id}</dd></div><div><dt>输入收据</dt><dd>{selected.input_hash}</dd></div><div><dt>内容收据</dt><dd>{selected.content_hash}</dd></div></dl> : null}</details>
    </> : null}
  </section>;
}
