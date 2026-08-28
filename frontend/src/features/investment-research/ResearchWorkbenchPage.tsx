import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { investmentResearchApi, type CompanyResearchWorkspace, type ProductProject } from "../../data/investmentResearchApi";
import { COMPANY_RESEARCH_MODULES, answerabilityView, artifactByKind, type CompanyResearchModuleKey, numericObservationView, preparationIsActive } from "./companyResearchView";

type WorkspaceArtifact = CompanyResearchWorkspace["artifacts"][number];
type EvidenceArtifact = Extract<WorkspaceArtifact, { kind: "evidence_index" }>;
type NumericObservation = EvidenceArtifact["payload"]["facts"][number]["observation"];
type EvidenceFact = EvidenceArtifact["payload"]["facts"][number];
type ReviewDecision = "confirmed" | "rejected";

const PREPARATION_LABELS: Record<CompanyResearchWorkspace["preparation"]["status"], string> = {
  queued: "已排队", preparing_sources: "准备来源", awaiting_evidence_review: "等待证据审核", building_model: "构建模型",
  awaiting_judgment_review: "等待判断审核", ready_to_freeze: "可冻结", recoverable_failure: "可重试失败", blocked: "已阻塞", completed: "已完成",
};
const MODULE_STATE_LABELS: Record<CompanyResearchWorkspace["modules"][number]["state"], string> = {
  not_started: "未开始", preparing: "准备中", needs_review: "待审核", ready: "可查看", blocked: "已阻塞",
};

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function workspaceCutoff(workspace: CompanyResearchWorkspace): string | null {
  return artifactByKind(workspace, "evidence_index")?.payload.cutoff ?? null;
}

function NumericCard({ label, observation, cutoff }: { label: string; observation: NumericObservation | null; cutoff: string | null }) {
  const view = numericObservationView(observation);
  if (view === null) return <p className="ir-numeric-empty">{label}：数据尚未建立</p>;
  return <article className="ir-numeric-card" aria-label={`${label} ${view.value}`}>
    <h4>{label}</h4><strong>{view.value}</strong>
    <dl><div><dt>单位</dt><dd>{view.unit}</dd></div><div><dt>币种</dt><dd>{view.currency}</dd></div><div><dt>期间</dt><dd>{view.period}</dd></div><div><dt>截止</dt><dd>{cutoff ? new Date(cutoff).toLocaleString("zh-CN") : "截止时间未提供"}</dd></div><div><dt>状态</dt><dd>{view.state}</dd></div></dl>
    <a href={view.traceUrl ?? "#audit-details"} target={view.traceUrl ? "_blank" : undefined} rel={view.traceUrl ? "noreferrer" : undefined}>来源／缺口：{view.traceLabel}</a>
  </article>;
}

function EmptyModule({ message }: { message: string }) {
  return <div className="ir-module-empty"><p>{message}</p></div>;
}

type SourceRef = { fact_key?: string; source_url: string; source_locator: string; source_role: string };
function SourceRefList({ refs }: { refs: readonly SourceRef[] }) {
  if (refs.length === 0) return <p>尚未建立来源追踪。</p>;
  return <ul className="ir-source-list">{refs.map((ref) => <li key={`${ref.fact_key ?? "source"}-${ref.source_url}-${ref.source_locator}`}><a href={ref.source_url} target="_blank" rel="noreferrer">{ref.fact_key ?? ref.source_role} · {ref.source_locator}</a></li>)}</ul>;
}

function OverviewPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const answerability = answerabilityView(workspace);
  const valuation = artifactByKind(workspace, "valuation_set");
  const judgment = artifactByKind(workspace, "judgment_context");
  const cutoff = workspaceCutoff(workspace);
  return <div className="ir-answerability"><strong>{answerability.label}</strong>
    {answerability.status === "not_answerable" ? <p>当前正式证据不足，不形成投资方向、置信度、目标价或预期回报。</p> : answerability.status === "partially_answerable" ? <p>当前判断为暂定结论，必须先解除下列阻塞项。</p> : <p>价值与回报范围已建立；仍需持续核验最强反证。</p>}
    {answerability.blockers.length > 0 ? <section><h3>阻塞项</h3><ul>{answerability.blockers.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
    {answerability.status === "answerable" && valuation ? <section><h3>价值与回报范围</h3><div className="ir-numeric-grid">{valuation.payload.security_value_ranges.flatMap((range) => [<NumericCard key={`${range.security_external_key}-value-min`} label={`${range.security_external_key} 价值下限`} observation={range.usd_per_share.minimum} cutoff={cutoff} />, <NumericCard key={`${range.security_external_key}-value-max`} label={`${range.security_external_key} 价值上限`} observation={range.usd_per_share.maximum} cutoff={cutoff} />, <NumericCard key={`${range.security_external_key}-return-min`} label={`${range.security_external_key} 回报下限`} observation={range.cny_return.minimum} cutoff={cutoff} />, <NumericCard key={`${range.security_external_key}-return-max`} label={`${range.security_external_key} 回报上限`} observation={range.cny_return.maximum} cutoff={cutoff} />])}</div></section> : null}
    {answerability.status === "answerable" && judgment ? <section><h3>最强反证与下一验证</h3><SourceRefList refs={judgment.payload.strongest_counterevidence} /><ul>{judgment.payload.next_verification_events.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
  </div>;
}

function BusinessPanel({ workspace, industry = false }: { workspace: CompanyResearchWorkspace; industry?: boolean }) {
  const business = artifactByKind(workspace, "business_map");
  if (!business || !("_lineage" in business.payload)) return <EmptyModule message="公司业务地图仍在准备。" />;
  return <div className="ir-research-list">{business.payload.modules.map((module) => <article key={module.module_key}><h3>{module.module_key}</h3>{industry ? <><h4>行业、竞争与监管证据</h4><SourceRefList refs={module.fact_refs} /><p>待验证缺口：{module.gap_refs.length > 0 ? module.gap_refs.join("；") : "当前未记录"}</p></> : <dl><div><dt>收入来源</dt><dd>{module.revenue_sources.join("；")}</dd></div><div><dt>成本结构</dt><dd>{module.cost_structure.join("；")}</dd></div><div><dt>资本需求</dt><dd>{module.capital_needs.join("；")}</dd></div></dl>}</article>)}</div>;
}

function DriversPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const artifact = artifactByKind(workspace, "driver_map");
  const cutoff = workspaceCutoff(workspace);
  if (!artifact) return <EmptyModule message="关键经营变量仍在准备。" />;
  return <div className="ir-research-list">{artifact.payload.drivers.map((driver) => <article key={driver.driver_key}><h3>{driver.driver_key}</h3><p>{driver.equation}</p>{driver.assumption_rationale ? <p>{driver.assumption_rationale}</p> : null}<div className="ir-numeric-grid">{driver.values.map((item) => <NumericCard key={`${driver.driver_key}-${item.key}-${item.period}`} label={item.key} observation={item} cutoff={cutoff} />)}</div></article>)}</div>;
}

function EvidencePanel({ workspace, reviewingFact, onReview }: { workspace: CompanyResearchWorkspace; reviewingFact: string | null; onReview: (fact: EvidenceFact, decision: ReviewDecision, button: HTMLButtonElement) => void }) {
  const evidence = artifactByKind(workspace, "evidence_index");
  const gaps = artifactByKind(workspace, "research_gaps");
  if (!evidence) return <EmptyModule message="证据索引仍在准备。" />;
  const gapItems = gaps && "gaps" in gaps.payload ? gaps.payload.gaps : [];
  return <div className="ir-evidence-layout"><section><h3>事实候选与审核</h3><div className="ir-evidence-list">{evidence.payload.facts.map((fact) => {
    const reviewed = "review_decision" in fact ? fact.review_decision : null;
    return <article key={fact.fact_key} aria-label={`事实 ${fact.fact_key}`}><header><strong>{fact.metric_key}</strong><span>{reviewed === "confirmed" ? "已确认" : reviewed === "rejected" ? "已驳回" : "候选事实"}</span></header><NumericCard label={fact.metric_key} observation={fact.observation} cutoff={evidence.payload.cutoff} /><dl><div><dt>来源定位</dt><dd>{fact.source_locator}</dd></div><div><dt>可用时间</dt><dd>{new Date(fact.available_at).toLocaleString("zh-CN")}</dd></div><div><dt>冲突组</dt><dd>接口未提供（不可推断）</dd></div></dl><a href={fact.source_url} target="_blank" rel="noreferrer">查看来源 · {fact.source_locator}</a>{reviewed === null ? <div className="ir-review-actions"><button className="ir-button" disabled={reviewingFact === fact.fact_key} onClick={(event) => onReview(fact, "confirmed", event.currentTarget)} type="button">确认事实 {fact.fact_key}</button><button className="ir-button" disabled={reviewingFact === fact.fact_key} onClick={(event) => onReview(fact, "rejected", event.currentTarget)} type="button">驳回事实 {fact.fact_key}</button></div> : null}</article>;
  })}</div></section><section><h3>研究缺口</h3>{gapItems.length > 0 ? <ul>{gapItems.map((gap) => <li key={"gap_key" in gap ? gap.gap_key : gap.code}>{"gap_key" in gap ? `${gap.gap_key} · ${gap.reason}` : `${gap.code} · ${gap.severity} · ${gap.message}`}</li>)}</ul> : <p>当前未记录研究缺口。</p>}</section></div>;
}

function FinancialPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const artifact = artifactByKind(workspace, "financial_bridge");
  const cutoff = workspaceCutoff(workspace);
  if (!artifact) return <EmptyModule message="财务桥仍在准备。" />;
  return <div className="ir-research-list">{artifact.payload.rows.map((row) => <article key={row.period}><h3>{row.period}</h3><div className="ir-numeric-grid"><NumericCard label="收入" observation={row.revenue} cutoff={cutoff} /><NumericCard label="营业利润" observation={row.operating_income} cutoff={cutoff} /><NumericCard label="现金税率" observation={row.cash_tax_rate} cutoff={cutoff} /><NumericCard label="折旧" observation={row.depreciation} cutoff={cutoff} /><NumericCard label="资本开支" observation={row.capex} cutoff={cutoff} /><NumericCard label="营运资本变化" observation={row.working_capital_change} cutoff={cutoff} /><NumericCard label="FCFF" observation={row.fcff} cutoff={cutoff} /></div></article>)}</div>;
}

function ScenarioPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const scenarios = artifactByKind(workspace, "scenario_set");
  const valuation = artifactByKind(workspace, "valuation_set");
  const judgment = artifactByKind(workspace, "judgment_context");
  const cutoff = workspaceCutoff(workspace);
  if (!scenarios) return <EmptyModule message="情景模型仍在准备。" />;
  return <div className="ir-scenario-view"><section><h3>情景机制与驱动变化</h3><div className="ir-research-list">{scenarios.payload.scenarios.map((scenario) => {
    const dcf = valuation?.payload.scenario_dcf_values.find((item) => item.scenario_id === scenario.scenario_id) ?? null;
    return <article key={scenario.scenario_id}><h4>{scenario.scenario_id}</h4><p><b>机制</b> {scenario.mechanism_id}</p>{scenario.driver_overrides.map((override) => <div key={override.driver_key}><p><b>变化驱动</b> {override.driver_key}</p>{override.rationale ? <p>{override.rationale}</p> : null}<NumericCard label={override.driver_key} observation={override.observation} cutoff={cutoff} /></div>)}<h5>财务效果与价值</h5><NumericCard label={`${scenario.scenario_id} DCF 企业价值`} observation={dcf?.enterprise_value ?? null} cutoff={cutoff} /></article>;
  })}</div></section>{valuation ? <><section><h3>DCF 情景值</h3><div className="ir-numeric-grid">{valuation.payload.scenario_dcf_values.map((item) => <NumericCard key={item.scenario_id} label={`${item.scenario_id} DCF`} observation={item.enterprise_value} cutoff={cutoff} />)}</div></section><section><h3>反向 DCF</h3>{valuation.payload.reverse_dcf ? <div className="ir-numeric-grid"><NumericCard label="当前价格隐含 FCFF 倍数" observation={valuation.payload.reverse_dcf.implied_value} cutoff={cutoff} /><NumericCard label="求解残差" observation={valuation.payload.reverse_dcf.achieved_residual} cutoff={cutoff} /><NumericCard label="迭代次数" observation={valuation.payload.reverse_dcf.iteration_count} cutoff={cutoff} /></div> : <p>当前无法建立反向 DCF。</p>}</section><section><h3>证券价值范围</h3>{valuation.payload.security_value_ranges.map((range) => <article className="ir-security-range" key={range.security_external_key}><h4>{range.security_external_key}</h4><div className="ir-numeric-grid"><NumericCard label="每股价值下限" observation={range.usd_per_share.minimum} cutoff={cutoff} /><NumericCard label="每股价值上限" observation={range.usd_per_share.maximum} cutoff={cutoff} /><NumericCard label="回报下限" observation={range.cny_return.minimum} cutoff={cutoff} /><NumericCard label="回报上限" observation={range.cny_return.maximum} cutoff={cutoff} /></div></article>)}</section></> : <section><h3>估值</h3><p>情景已建立，但估值仍被数据缺口阻塞。</p></section>}<section><h3>反证</h3>{judgment ? <SourceRefList refs={judgment.payload.strongest_counterevidence} /> : <p>反证清单仍在准备。</p>}</section></div>;
}

function RisksPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const judgment = artifactByKind(workspace, "judgment_context");
  const gaps = artifactByKind(workspace, "research_gaps");
  return <div className="ir-research-list"><section><h3>最强反证</h3>{judgment ? <SourceRefList refs={judgment.payload.strongest_counterevidence} /> : <p>反证清单仍在准备。</p>}</section><section><h3>下一验证</h3>{judgment ? <ul>{judgment.payload.next_verification_events.map((item) => <li key={item}>{item}</li>)}</ul> : <p>验证事件仍在准备。</p>}</section><section><h3>未关闭风险</h3>{gaps ? <p>{workspace.gap_count} 个研究缺口仍需关闭。</p> : <p>缺口清单仍在准备。</p>}</section></div>;
}

function VersionsPanel({ workspace, lastSuccess }: { workspace: CompanyResearchWorkspace; lastSuccess: string | null }) {
  const memo = artifactByKind(workspace, "memo");
  return <div className="ir-version-summary"><strong>当前草稿版本 {workspace.draft.lock_version}</strong><p>证据审核累计 {workspace.change_summary.reviewed_fact_count} 项</p>{lastSuccess ? <p>{lastSuccess}</p> : null}<h3>制品版本变化</h3><dl>{Object.entries(workspace.change_summary.artifact_versions).map(([kind, version]) => <div key={kind}><dt>{kind}</dt><dd>v{version}</dd></div>)}</dl><h3>研究备忘录</h3>{memo ? <p>{memo.payload.candidate_status} · {memo.payload.assessment_status}</p> : <p>研究备忘录尚未建立。</p>}</div>;
}

function ModulePanel({ activeModule, workspace, reviewingFact, onReview, lastSuccess }: { activeModule: CompanyResearchModuleKey; workspace: CompanyResearchWorkspace; reviewingFact: string | null; onReview: (fact: EvidenceFact, decision: ReviewDecision, button: HTMLButtonElement) => void; lastSuccess: string | null }) {
  if (activeModule === "overview") return <OverviewPanel workspace={workspace} />;
  if (activeModule === "business_map") return <BusinessPanel workspace={workspace} />;
  if (activeModule === "operating_drivers") return <DriversPanel workspace={workspace} />;
  if (activeModule === "evidence_and_gaps") return <EvidencePanel workspace={workspace} reviewingFact={reviewingFact} onReview={onReview} />;
  if (activeModule === "industry_competition_regulation") return <BusinessPanel workspace={workspace} industry />;
  if (activeModule === "financials_cash_flow_capital_allocation") return <FinancialPanel workspace={workspace} />;
  if (activeModule === "scenarios_valuation_implied_expectations") return <ScenarioPanel workspace={workspace} />;
  if (activeModule === "counterevidence_risks_next_checks") return <RisksPanel workspace={workspace} />;
  return <VersionsPanel workspace={workspace} lastSuccess={lastSuccess} />;
}

export default function ResearchWorkbenchPage() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<ProductProject | null>(null);
  const [workspace, setWorkspace] = useState<CompanyResearchWorkspace | null>(null);
  const [activeModule, setActiveModule] = useState<CompanyResearchModuleKey>("overview");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [lastSuccess, setLastSuccess] = useState<string | null>(null);
  const [reviewingFact, setReviewingFact] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [reloadAttempt, setReloadAttempt] = useState(0);
  const [pollTick, setPollTick] = useState(0);
  const [browserVisible, setBrowserVisible] = useState(() => document.visibilityState !== "hidden");
  const epochRef = useRef(0);
  const requestRef = useRef(0);
  const pollAttemptRef = useRef(0);

  useEffect(() => { const onVisibility = () => setBrowserVisible(document.visibilityState !== "hidden"); document.addEventListener("visibilitychange", onVisibility); return () => document.removeEventListener("visibilitychange", onVisibility); }, []);
  useEffect(() => {
    const epoch = ++epochRef.current; const request = ++requestRef.current; let active = true;
    setLoading(true); setLoadError(null); setActionError(null); setLastSuccess(null); setProject(null); setWorkspace(null); setActiveModule("overview"); pollAttemptRef.current = 0;
    void Promise.all([investmentResearchApi.project(projectId), investmentResearchApi.companyResearchWorkspace(projectId)]).then(([nextProject, nextWorkspace]) => {
      if (!active || epochRef.current !== epoch || requestRef.current !== request || nextWorkspace.project_id !== projectId || nextProject.id !== projectId) return;
      setProject(nextProject); setWorkspace(nextWorkspace);
    }).catch((error: unknown) => { if (active && epochRef.current === epoch && requestRef.current === request) setLoadError(errorMessage(error, "研究工作区无法读取")); }).finally(() => { if (active && epochRef.current === epoch && requestRef.current === request) setLoading(false); });
    return () => { active = false; };
  }, [projectId, reloadAttempt]);
  useEffect(() => {
    if (!workspace || !browserVisible || !preparationIsActive(workspace.preparation.status)) return;
    const epoch = epochRef.current; const delay = Math.min(1000 * 2 ** pollAttemptRef.current, 8000);
    const timer = window.setTimeout(() => { const request = ++requestRef.current; void investmentResearchApi.companyResearchWorkspace(projectId).then((nextWorkspace) => { if (epochRef.current !== epoch || requestRef.current !== request || nextWorkspace.project_id !== projectId) return; pollAttemptRef.current += 1; setWorkspace(nextWorkspace); }).catch(() => { if (epochRef.current === epoch && requestRef.current === request) { pollAttemptRef.current += 1; setPollTick((value) => value + 1); } }); }, delay);
    return () => window.clearTimeout(timer);
  }, [browserVisible, pollTick, projectId, workspace]);

  async function reviewFact(fact: EvidenceFact, decision: ReviewDecision, button: HTMLButtonElement) {
    if (!workspace || reviewingFact) return; const evidence = artifactByKind(workspace, "evidence_index"); if (!evidence) return;
    const epoch = epochRef.current; setReviewingFact(fact.fact_key); setActionError(null); setLastSuccess(null);
    let successorVersion: number;
    try {
      const result = await investmentResearchApi.reviewCompanyEvidence(projectId, { schema_version: "underwriting.v1", evidence_artifact_id: evidence.id, fact_key: fact.fact_key, decision, expected_head_id: evidence.id });
      if (epochRef.current !== epoch) return;
      successorVersion = result.evidence_artifact.version;
      setLastSuccess(`事实 ${fact.fact_key} 已${decision === "confirmed" ? "确认" : "驳回"}，证据版本 ${successorVersion}`);
    } catch (error) {
      if (epochRef.current === epoch) { setActionError(errorMessage(error, "证据审核无法写入")); requestAnimationFrame(() => button.focus()); setReviewingFact(null); }
      return;
    }
    try {
      const request = ++requestRef.current; const refreshed = await investmentResearchApi.companyResearchWorkspace(projectId);
      if (epochRef.current === epoch && requestRef.current === request && refreshed.project_id === projectId) setWorkspace(refreshed);
    } catch (error) {
      if (epochRef.current === epoch) setActionError(errorMessage(error, "审核已写入，但后继工作区无法读取"));
    } finally { if (epochRef.current === epoch) setReviewingFact(null); }
  }
  async function retryPreparation() {
    if (!workspace?.preparation.error?.retryable || retrying) return; const epoch = epochRef.current; setRetrying(true); setActionError(null);
    try { await investmentResearchApi.retryCompanyResearchProject(projectId); const request = ++requestRef.current; const refreshed = await investmentResearchApi.companyResearchWorkspace(projectId); if (epochRef.current === epoch && requestRef.current === request && refreshed.project_id === projectId) { pollAttemptRef.current = 0; setWorkspace(refreshed); } }
    catch (error) { if (epochRef.current === epoch) setActionError(errorMessage(error, "准备阶段无法重试")); }
    finally { if (epochRef.current === epoch) setRetrying(false); }
  }

  if (loading) return <main className="ir-page ir-workbench" aria-busy="true"><div className="ir-workbench-skeleton"><span /><span /><span /></div></main>;
  if (loadError || !project || !workspace) return <main className="ir-page ir-workbench"><div className="ir-alert" role="alert"><h1>研究项目无法读取</h1><p>{loadError ?? "工作区响应不完整"}</p><button className="ir-button" onClick={() => setReloadAttempt((value) => value + 1)} type="button">重试读取研究项目</button><Link to="/research">返回研究目录</Link></div></main>;
  const activeDefinition = COMPANY_RESEARCH_MODULES.find((item) => item.key === activeModule) ?? COMPANY_RESEARCH_MODULES[0];
  const preparationError = workspace.preparation.error;
  return <main className="ir-page ir-workbench" aria-busy={reviewingFact !== null || retrying}>
    <header className="ir-workbench-head"><div><p className="ir-eyebrow">Independent company research</p><h1>研究工作台</h1><h2>{workspace.company.canonical_name}</h2><p>{project.security_identities.map((security) => `${security.symbol} · ${security.share_class} · ${security.exchange}`).join("；")}</p></div><span className="ir-draft-state">草稿版本 {workspace.draft.lock_version}</span></header>
    <section className="ir-preparation" aria-live="polite"><div><strong>{PREPARATION_LABELS[workspace.preparation.status]}</strong><span>{workspace.preparation.progress}% · {workspace.preparation.current_step ?? "全部阶段"}</span></div><progress aria-label="研究准备进度" aria-valuemax={100} aria-valuemin={0} aria-valuenow={workspace.preparation.progress} max="100" value={workspace.preparation.progress}>{workspace.preparation.progress}%</progress>{preparationError ? <div className="ir-stage-error"><p>{preparationError.code}</p>{preparationError.retryable ? <button className="ir-button" disabled={retrying} onClick={() => void retryPreparation()} type="button">重试 {preparationError.failed_step}</button> : null}</div> : null}</section>
    {actionError ? <div className="ir-alert" role="alert">{actionError}</div> : null}{lastSuccess ? <p className="ir-confirmed" role="status">{lastSuccess}</p> : null}
    <div className="ir-workbench-grid"><nav className="ir-module-nav" aria-label="研究模块">{COMPANY_RESEARCH_MODULES.map((definition) => { const module = workspace.modules.find((item) => item.key === definition.key); return <button className={activeModule === definition.key ? "is-active" : ""} key={definition.key} onClick={() => setActiveModule(definition.key)} type="button"><span>{definition.label}</span><small>{module ? MODULE_STATE_LABELS[module.state] : "未开始"}</small></button>; })}</nav>
      <section className="ir-module-content" aria-live="polite"><header><p className="ir-eyebrow">Company research module</p><h2>{activeDefinition.label}</h2></header><ModulePanel activeModule={activeModule} workspace={workspace} reviewingFact={reviewingFact} onReview={reviewFact} lastSuccess={lastSuccess} /></section>
      <aside className="ir-boundary" aria-label="研究状态摘要"><p className="ir-eyebrow">Research state</p><h2>准备状态</h2><dl><div><dt>来源</dt><dd>{workspace.source_count}</dd></div><div><dt>缺口</dt><dd>{workspace.gap_count}</dd></div><div><dt>已审核事实</dt><dd>{workspace.change_summary.reviewed_fact_count}</dd></div></dl><details id="audit-details"><summary>审计详情</summary><dl><div><dt>Project</dt><dd>{workspace.project_id}</dd></div><div><dt>Preparation</dt><dd>{workspace.preparation.id}</dd></div><div><dt>Draft</dt><dd>{workspace.draft.id}</dd></div><div><dt>Selected revision</dt><dd>{workspace.selected_revision ?? "尚未选择冻结版本"}</dd></div></dl>{Object.entries(workspace.change_summary.artifact_versions).map(([kind, version]) => <span id={`audit-${encodeURIComponent(kind)}`} key={kind}>{kind} v{version}</span>)}</details></aside>
    </div>
  </main>;
}
