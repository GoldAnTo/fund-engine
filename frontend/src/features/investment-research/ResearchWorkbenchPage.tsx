import { useEffect, useId, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  COMPANY_RESEARCH_MODULE_ARTIFACTS,
  investmentResearchApi,
  type CompanyResearchFrozenRevision,
  type CompanyResearchPublicationPreview,
  type CompanyResearchWorkspace,
  type ProductProject,
} from "../../data/investmentResearchApi";
import {
  COMPANY_RESEARCH_MODULES,
  answerabilityView,
  artifactByKind,
  type CompanyResearchModuleKey,
  type CompanyResearchPublicationAction,
  numericObservationView,
  preparationIsActive,
  publicationAction,
  workspaceSnapshotIsMonotonic,
} from "./companyResearchView";

type WorkspaceArtifact = CompanyResearchWorkspace["artifacts"][number];
type EvidenceArtifact = Extract<WorkspaceArtifact, { kind: "evidence_index" }>;
type NumericObservation = EvidenceArtifact["payload"]["facts"][number]["observation"];
type EvidenceFact = EvidenceArtifact["payload"]["facts"][number];
type ReviewDecision = "confirmed" | "rejected";
type ModuleState = CompanyResearchWorkspace["modules"][number]["state"];
type ValuationState = CompanyResearchWorkspace["modules"][number]["valuation_state"];
type CommittedReview = { factKey: string; decision: ReviewDecision; previousEvidence: EvidenceArtifact; successor: EvidenceArtifact };
type PublicationMutation = "confirm" | "preview" | "publish" | "replay" | "export";
type PublicationPreviewSession = {
  data: CompanyResearchPublicationPreview;
  workspaceLockVersion: number;
  memoId: string;
  memoContentHash: string;
  idempotencyKey: string;
};
type MemoEditor = { memoId: string; value: string };

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

function normalizedMarkdown(value: string): string {
  return value.replace(/\r\n?/g, "\n").trim();
}

function publicationFailureIsConflict(error: unknown): boolean {
  return typeof error === "object" && error !== null && "status" in error && error.status === 409;
}

function restoreFocus(button: HTMLButtonElement): void {
  requestAnimationFrame(() => button.focus());
}

function machineMemoMarkdown(workspace: CompanyResearchWorkspace): string {
  const memo = artifactByKind(workspace, "memo");
  if (memo === null) return "";
  if (memo.payload.candidate_status === "human_confirmed") return memo.payload.markdown;
  const conclusion = memo.payload.assessment_status === "not_answerable"
    ? "当前正式证据不足，不能形成投资方向、置信度、目标价或预期回报。"
    : memo.payload.assessment_status === "partially_answerable"
      ? "当前判断仅为暂定结论，必须先解除明确阻塞项。"
      : "当前证据支持形成判断，仍需持续核验最强反证。";
  const blockers = memo.payload.gap_keys.length > 0
    ? `\n\n未关闭缺口：\n${memo.payload.gap_keys.map((gap) => `- ${gap}`).join("\n")}`
    : "";
  return `${conclusion}${blockers}`;
}

function jsonValueIsEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right) && left.length === right.length
      && left.every((item, index) => jsonValueIsEqual(item, right[index]));
  }
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  const leftRecord = left as Record<string, unknown>; const rightRecord = right as Record<string, unknown>;
  const leftKeys = Object.keys(leftRecord).sort(); const rightKeys = Object.keys(rightRecord).sort();
  return leftKeys.length === rightKeys.length
    && leftKeys.every((key, index) => key === rightKeys[index] && jsonValueIsEqual(leftRecord[key], rightRecord[key]));
}

function reviewSuccessorIsExact(projectId: string, previous: EvidenceArtifact, successor: EvidenceArtifact, factKey: string, decision: ReviewDecision): boolean {
  const submitted = previous.payload.facts.find((item) => item.fact_key === factKey);
  if (!submitted || "review_decision" in submitted || successor.project_id !== projectId || successor.version !== previous.version + 1
    || successor.schema_version !== previous.schema_version || successor.kind !== previous.kind
    || !jsonValueIsEqual(successor.source_refs, previous.source_refs)) return false;
  const expectedPayload = {
    ...previous.payload,
    facts: previous.payload.facts.map((fact) => fact.fact_key === factKey ? { ...fact, review_decision: decision } : fact),
  };
  return jsonValueIsEqual(successor.payload, expectedPayload);
}

function workspaceCutoff(workspace: CompanyResearchWorkspace): string | null {
  return artifactByKind(workspace, "evidence_index")?.payload.cutoff ?? null;
}

function NumericCard({ label, observation, cutoff, artifact }: { label: string; observation: NumericObservation | null; cutoff: string | null; artifact: WorkspaceArtifact }) {
  const reactId = useId();
  const view = numericObservationView(observation);
  if (view === null) return <p className="ir-numeric-empty">{label}：数据尚未建立</p>;
  const provenanceId = `provenance-${reactId.replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return <article className="ir-numeric-card" aria-label={`${label} ${view.value}`}>
    <h4>{label}</h4><strong>{view.value}</strong>
    <dl><div><dt>单位</dt><dd>{view.unit}</dd></div><div><dt>币种</dt><dd>{view.currency}</dd></div><div><dt>期间</dt><dd>{view.period}</dd></div><div><dt>截止</dt><dd>{cutoff ? new Date(cutoff).toLocaleString("zh-CN") : "截止时间未提供"}</dd></div><div><dt>状态</dt><dd>{view.state}</dd></div></dl>
    <a href={view.traceUrl ?? `#${provenanceId}`} target={view.traceUrl ? "_blank" : undefined} rel={view.traceUrl ? "noreferrer" : undefined}>来源／缺口：{view.traceLabel}</a>
    {view.traceUrl === null ? <dl className="ir-provenance" id={provenanceId}><div><dt>制品上下文</dt><dd>{artifact.kind} v{artifact.version}</dd></div><div><dt>{view.provenanceKind === "equation" ? "方程" : view.provenanceKind === "assumption" ? "假设" : view.provenanceKind === "gap" ? "缺口" : "来源"}</dt><dd>{view.provenanceKey}</dd></div></dl> : null}
  </article>;
}

function EmptyModule({ message }: { message: string }) {
  return <div className="ir-module-empty"><p>{message}</p></div>;
}

function moduleUnavailableCopy(label: string, state: ModuleState): string {
  if (state === "blocked") return `${label}已阻塞；当前没有可展示的专属制品。`;
  if (state === "not_started") return `${label}尚未开始；当前没有可展示的专属制品。`;
  if (state === "needs_review") return `${label}等待审核；当前没有可展示的专属制品。`;
  if (state === "preparing") return `${label}正在准备；当前没有可展示的专属制品。`;
  return `${label}标记为可查看，但所需制品缺失；接口响应不一致。`;
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
  const hasRanges = (valuation?.payload.security_value_ranges.length ?? 0) > 0;
  return <div className="ir-answerability"><strong>{answerability.label}</strong>
    {answerability.status === "preparing" ? <p>正式研究备忘录尚未建立；当前不推断可回答性。</p> : answerability.status === "not_answerable" ? <p>当前正式证据不足，不形成投资方向、置信度、目标价或预期回报。</p> : answerability.status === "partially_answerable" ? <p>当前判断为暂定结论，必须先解除下列阻塞项。</p> : hasRanges ? <p>价值与回报范围已建立；仍需持续核验最强反证。</p> : <p>备忘录标记为可回答，但价值与回报范围未提供或不一致。</p>}
    {answerability.blockers.length > 0 ? <section><h3>阻塞项</h3><ul>{answerability.blockers.map((item) => <li key={item}>{item}</li>)}</ul></section> : null}
    {answerability.status === "partially_answerable" && answerability.blockers.length === 0 ? <p>未提供阻塞项。</p> : null}
    {answerability.status === "preparing" && judgment ? <section><h3>已建立的判断上下文</h3>{judgment.payload.strongest_counterevidence.length > 0 ? <SourceRefList refs={judgment.payload.strongest_counterevidence} /> : <p>最强反证未提供。</p>}{judgment.payload.next_verification_events.length > 0 ? <ul>{judgment.payload.next_verification_events.map((item) => <li key={item}>{item}</li>)}</ul> : <p>下一验证事件未提供。</p>}</section> : null}
    {answerability.status === "answerable" && valuation && valuation.payload.security_value_ranges.length > 0 ? <section><h3>价值与回报范围</h3><div className="ir-numeric-grid">{valuation.payload.security_value_ranges.flatMap((range) => [<NumericCard artifact={valuation} key={`${range.security_external_key}-value-min`} label={`${range.security_external_key} 价值下限`} observation={range.usd_per_share.minimum} cutoff={cutoff} />, <NumericCard artifact={valuation} key={`${range.security_external_key}-value-max`} label={`${range.security_external_key} 价值上限`} observation={range.usd_per_share.maximum} cutoff={cutoff} />, <NumericCard artifact={valuation} key={`${range.security_external_key}-return-min`} label={`${range.security_external_key} 回报下限`} observation={range.cny_return.minimum} cutoff={cutoff} />, <NumericCard artifact={valuation} key={`${range.security_external_key}-return-max`} label={`${range.security_external_key} 回报上限`} observation={range.cny_return.maximum} cutoff={cutoff} />])}</div></section> : answerability.status === "answerable" ? <p>价值与回报范围未提供或不一致。</p> : null}
    {answerability.status === "answerable" ? <section><h3>最强反证与下一验证</h3>{judgment && judgment.payload.strongest_counterevidence.length > 0 ? <SourceRefList refs={judgment.payload.strongest_counterevidence} /> : <p>最强反证未提供。</p>}{judgment && judgment.payload.next_verification_events.length > 0 ? <ul>{judgment.payload.next_verification_events.map((item) => <li key={item}>{item}</li>)}</ul> : <p>下一验证事件未提供。</p>}</section> : null}
  </div>;
}

function BusinessPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const business = artifactByKind(workspace, "business_map");
  if (!business || !("_lineage" in business.payload)) return <EmptyModule message="公司业务地图仍在准备。" />;
  return <div className="ir-research-list">{business.payload.modules.map((module) => <article key={module.module_key}><h3>{module.module_key}</h3><dl><div><dt>收入来源</dt><dd>{module.revenue_sources.join("；")}</dd></div><div><dt>成本结构</dt><dd>{module.cost_structure.join("；")}</dd></div><div><dt>资本需求</dt><dd>{module.capital_needs.join("；")}</dd></div></dl></article>)}</div>;
}

function IndustryPanel({ state }: { state: ModuleState }) {
  return <EmptyModule message={`${state === "blocked" ? "行业、竞争与监管已阻塞。" : ""}接口未提供行业、竞争与监管专属语义（不可推断）。`} />;
}

function DriversPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const artifact = artifactByKind(workspace, "driver_map");
  const cutoff = workspaceCutoff(workspace);
  if (!artifact) return <EmptyModule message="关键经营变量仍在准备。" />;
  return <div className="ir-research-list">{artifact.payload.drivers.map((driver) => <article key={driver.driver_key}><h3>{driver.driver_key}</h3><p>{driver.equation}</p>{driver.assumption_rationale ? <p>{driver.assumption_rationale}</p> : null}<div className="ir-numeric-grid">{driver.values.map((item) => <NumericCard artifact={artifact} key={`${driver.driver_key}-${item.key}-${item.period}`} label={item.key} observation={item} cutoff={cutoff} />)}</div></article>)}</div>;
}

function EvidencePanel({ workspace, reviewLocked, onReview }: { workspace: CompanyResearchWorkspace; reviewLocked: boolean; onReview: (fact: EvidenceFact, decision: ReviewDecision, button: HTMLButtonElement) => void }) {
  const evidence = artifactByKind(workspace, "evidence_index");
  const gaps = artifactByKind(workspace, "research_gaps");
  if (!evidence) return <EmptyModule message="证据索引仍在准备。" />;
  const gapItems = gaps && "gaps" in gaps.payload ? gaps.payload.gaps : [];
  return <div className="ir-evidence-layout"><section><h3>事实候选与审核</h3><div className="ir-evidence-list">{evidence.payload.facts.map((fact) => {
    const reviewed = "review_decision" in fact ? fact.review_decision : null;
    return <article key={fact.fact_key} aria-label={`事实 ${fact.fact_key}`}><header><strong>{fact.metric_key}</strong><span>{reviewed === "confirmed" ? "已确认" : reviewed === "rejected" ? "已驳回" : "候选事实"}</span></header><NumericCard artifact={evidence} label={fact.metric_key} observation={fact.observation} cutoff={evidence.payload.cutoff} /><dl><div><dt>来源定位</dt><dd>{fact.source_locator}</dd></div><div><dt>可用时间</dt><dd>{new Date(fact.available_at).toLocaleString("zh-CN")}</dd></div><div><dt>冲突组</dt><dd>接口未提供（不可推断）</dd></div></dl><a href={fact.source_url} target="_blank" rel="noreferrer">查看来源 · {fact.source_locator}</a>{reviewed === null ? <div className="ir-review-actions"><button className="ir-button" disabled={reviewLocked} onClick={(event) => onReview(fact, "confirmed", event.currentTarget)} type="button">确认事实 {fact.fact_key}</button><button className="ir-button" disabled={reviewLocked} onClick={(event) => onReview(fact, "rejected", event.currentTarget)} type="button">驳回事实 {fact.fact_key}</button></div> : null}</article>;
  })}</div></section><section><h3>研究缺口</h3>{gapItems.length > 0 ? <ul>{gapItems.map((gap) => <li key={"gap_key" in gap ? gap.gap_key : gap.code}>{"gap_key" in gap ? `${gap.gap_key} · ${gap.reason}` : `${gap.code} · ${gap.severity} · ${gap.message}`}</li>)}</ul> : <p>当前未记录研究缺口。</p>}</section></div>;
}

function FinancialPanel({ workspace }: { workspace: CompanyResearchWorkspace }) {
  const artifact = artifactByKind(workspace, "financial_bridge");
  const cutoff = workspaceCutoff(workspace);
  if (!artifact) return <EmptyModule message="财务桥仍在准备。" />;
  return <div className="ir-research-list">{artifact.payload.rows.map((row) => <article key={row.period}><h3>{row.period}</h3><div className="ir-numeric-grid"><NumericCard artifact={artifact} label="收入" observation={row.revenue} cutoff={cutoff} /><NumericCard artifact={artifact} label="营业利润" observation={row.operating_income} cutoff={cutoff} /><NumericCard artifact={artifact} label="现金税率" observation={row.cash_tax_rate} cutoff={cutoff} /><NumericCard artifact={artifact} label="折旧" observation={row.depreciation} cutoff={cutoff} /><NumericCard artifact={artifact} label="资本开支" observation={row.capex} cutoff={cutoff} /><NumericCard artifact={artifact} label="营运资本变化" observation={row.working_capital_change} cutoff={cutoff} /><NumericCard artifact={artifact} label="FCFF" observation={row.fcff} cutoff={cutoff} /></div></article>)}</div>;
}

function ScenarioPanel({ workspace, valuationState }: { workspace: CompanyResearchWorkspace; valuationState: ValuationState }) {
  const scenarios = artifactByKind(workspace, "scenario_set");
  const valuation = valuationState === "ready" ? artifactByKind(workspace, "valuation_set") : null;
  const judgment = artifactByKind(workspace, "judgment_context");
  const cutoff = workspaceCutoff(workspace);
  if (!scenarios) return <EmptyModule message="情景模型仍在准备。" />;
  return <div className="ir-scenario-view"><section><h3>情景机制与驱动变化</h3><div className="ir-research-list">{scenarios.payload.scenarios.map((scenario) => {
    const dcf = valuation?.payload.scenario_dcf_values.find((item) => item.scenario_id === scenario.scenario_id) ?? null;
    return <article key={scenario.scenario_id}><h4>{scenario.scenario_id}</h4><p><b>机制</b> {scenario.mechanism_id}</p>{scenario.driver_overrides.map((override) => <div key={override.driver_key}><p><b>变化驱动</b> {override.driver_key}</p>{override.rationale ? <p>{override.rationale}</p> : null}<NumericCard artifact={scenarios} label={override.driver_key} observation={override.observation} cutoff={cutoff} /></div>)}<p>逐情景财务效果：接口未提供（不可推断）</p>{valuationState === "ready" ? <><h5>DCF 估值</h5><NumericCard artifact={valuation ?? scenarios} label={`${scenario.scenario_id} DCF 企业价值`} observation={dcf?.enterprise_value ?? null} cutoff={cutoff} /></> : null}</article>;
  })}</div></section>{valuation ? <><section><h3>DCF 情景值</h3><div className="ir-numeric-grid">{valuation.payload.scenario_dcf_values.map((item) => <NumericCard artifact={valuation} key={item.scenario_id} label={`${item.scenario_id} DCF`} observation={item.enterprise_value} cutoff={cutoff} />)}</div></section><section><h3>反向 DCF</h3>{valuation.payload.reverse_dcf ? <div className="ir-numeric-grid"><NumericCard artifact={valuation} label="当前价格隐含 FCFF 倍数" observation={valuation.payload.reverse_dcf.implied_value} cutoff={cutoff} /><NumericCard artifact={valuation} label="求解残差" observation={valuation.payload.reverse_dcf.achieved_residual} cutoff={cutoff} /><NumericCard artifact={valuation} label="迭代次数" observation={valuation.payload.reverse_dcf.iteration_count} cutoff={cutoff} /></div> : <p>当前无法建立反向 DCF。</p>}</section><section><h3>证券价值范围</h3>{valuation.payload.security_value_ranges.map((range) => <article className="ir-security-range" key={range.security_external_key}><h4>{range.security_external_key}</h4><div className="ir-numeric-grid"><NumericCard artifact={valuation} label="每股价值下限" observation={range.usd_per_share.minimum} cutoff={cutoff} /><NumericCard artifact={valuation} label="每股价值上限" observation={range.usd_per_share.maximum} cutoff={cutoff} /><NumericCard artifact={valuation} label="回报下限" observation={range.cny_return.minimum} cutoff={cutoff} /><NumericCard artifact={valuation} label="回报上限" observation={range.cny_return.maximum} cutoff={cutoff} /></div></article>)}</section></> : <section><h3>估值</h3><p>{valuationState === "pending" ? "估值仍在准备（服务器状态：pending）" : valuationState === "blocked" ? "估值已阻塞（服务器状态：blocked）" : valuationState === "ready" ? "估值标记为可查看，但估值制品缺失；接口响应不一致。" : "估值不适用于当前模块。"}</p></section>}<section><h3>反证</h3>{judgment ? <SourceRefList refs={judgment.payload.strongest_counterevidence} /> : <p>反证清单仍在准备。</p>}</section></div>;
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

function ModulePanel({ activeModule, moduleState, valuationState, workspace, reviewLocked, onReview, lastSuccess }: { activeModule: CompanyResearchModuleKey; moduleState: ModuleState; valuationState: ValuationState; workspace: CompanyResearchWorkspace; reviewLocked: boolean; onReview: (fact: EvidenceFact, decision: ReviewDecision, button: HTMLButtonElement) => void; lastSuccess: string | null }) {
  const label = COMPANY_RESEARCH_MODULES.find((item) => item.key === activeModule)?.label ?? activeModule;
  const canRender = moduleState === "ready" || (activeModule === "evidence_and_gaps" && moduleState === "needs_review");
  if (!canRender) return activeModule === "overview" ? <div className="ir-answerability"><strong>判断尚在准备</strong><EmptyModule message={moduleUnavailableCopy(label, moduleState)} /></div> : <EmptyModule message={moduleUnavailableCopy(label, moduleState)} />;
  if (activeModule === "industry_competition_regulation") return <IndustryPanel state={moduleState} />;
  const required = COMPANY_RESEARCH_MODULE_ARTIFACTS[activeModule]?.[0] as WorkspaceArtifact["kind"] | undefined;
  if (required && artifactByKind(workspace, required) === null) {
    return activeModule === "overview" ? <div className="ir-answerability"><strong>判断尚在准备</strong><EmptyModule message={moduleUnavailableCopy(label, moduleState)} /></div> : <EmptyModule message={moduleUnavailableCopy(label, moduleState)} />;
  }
  if (activeModule === "overview") return <OverviewPanel workspace={workspace} />;
  if (activeModule === "business_map") return <BusinessPanel workspace={workspace} />;
  if (activeModule === "operating_drivers") return <DriversPanel workspace={workspace} />;
  if (activeModule === "evidence_and_gaps") return <EvidencePanel workspace={workspace} reviewLocked={reviewLocked} onReview={onReview} />;
  if (activeModule === "financials_cash_flow_capital_allocation") return <FinancialPanel workspace={workspace} />;
  if (activeModule === "scenarios_valuation_implied_expectations") return <ScenarioPanel workspace={workspace} valuationState={valuationState} />;
  if (activeModule === "counterevidence_risks_next_checks") return <RisksPanel workspace={workspace} />;
  return <VersionsPanel workspace={workspace} lastSuccess={lastSuccess} />;
}

function PublicationPanel({
  action,
  busy,
  frozenRevision,
  memoMarkdown,
  onClosePreview,
  onConfirm,
  onExport,
  onMemoChange,
  onPreview,
  onPublish,
  onReplay,
  preview,
  workspace,
}: {
  action: CompanyResearchPublicationAction;
  busy: boolean;
  frozenRevision: CompanyResearchFrozenRevision | null;
  memoMarkdown: string;
  onClosePreview: () => void;
  onConfirm: (button: HTMLButtonElement) => void;
  onExport: (button: HTMLButtonElement) => void;
  onMemoChange: (value: string) => void;
  onPreview: (button: HTMLButtonElement) => void;
  onPublish: (button: HTMLButtonElement) => void;
  onReplay: (button: HTMLButtonElement) => void;
  preview: CompanyResearchPublicationPreview | null;
  workspace: CompanyResearchWorkspace;
}) {
  const dialogTitleId = useId();
  if (action.kind === "confirm_judgment") {
    return <section className="ir-version-summary" aria-labelledby="publication-judgment-title">
      <h2 id="publication-judgment-title">确认研究判断</h2>
      <p>请保留诚实的证据边界。确认会追加人工备忘录，不会补造估值、方向或置信度。</p>
      <div className="ir-field">
        <label htmlFor="company-research-memo">研究备忘录 Markdown</label>
        <textarea
          aria-describedby="company-research-memo-help"
          disabled={busy}
          id="company-research-memo"
          maxLength={100_000}
          onChange={(event) => onMemoChange(event.currentTarget.value)}
          rows={10}
          value={memoMarkdown}
        />
      </div>
      <p id="company-research-memo-help">提交前会统一换行并移除首尾空白。</p>
      <button className="ir-button ir-button--primary" disabled={busy || normalizedMarkdown(memoMarkdown).length === 0} onClick={(event) => onConfirm(event.currentTarget)} type="button">{action.label}</button>
    </section>;
  }
  if (action.kind === "preview_freeze") {
    return <section className="ir-version-summary" aria-labelledby="publication-preview-title">
      <h2 id="publication-preview-title">冻结研究版本</h2>
      <p>先读取零写入预览，再核对公司、证券、截止时间和证据边界。</p>
      <button className="ir-button ir-button--primary" disabled={busy} onClick={(event) => onPreview(event.currentTarget)} type="button">{action.label}</button>
      {preview ? <dialog aria-labelledby={dialogTitleId} open>
        <h2 id={dialogTitleId}>确认冻结版本</h2>
        <p><strong>{preview.company.canonical_name}</strong></p>
        <p>{preview.securities.map((security) => `${security.symbol} · ${security.share_class}`).join("；")}</p>
        <dl>
          <div><dt>证据截止时间</dt><dd><time dateTime={preview.cutoff_at}>{preview.cutoff_at}</time></dd></div>
          <div><dt>可回答性</dt><dd>{preview.assessment.answerability}</dd></div>
          <div><dt>价值范围</dt><dd>{preview.value_range === null ? "未建立价值范围" : `${preview.value_range.minimum}–${preview.value_range.maximum}`}</dd></div>
          <div><dt>回报范围</dt><dd>{preview.return_range === null ? "未建立回报范围" : `${preview.return_range.minimum}–${preview.return_range.maximum}`}</dd></div>
        </dl>
        <h3>最强反证</h3>
        <SourceRefList refs={preview.strongest_counterevidence} />
        <h3>阻塞项</h3>
        <ul>{preview.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>
        <p><strong>冻结后不可修改。</strong>发布会创建不可变版本，后续工作区变化不会改写它。</p>
        <div className="ir-review-actions">
          <button className="ir-button" disabled={busy} onClick={onClosePreview} type="button">返回检查</button>
          <button className="ir-button ir-button--primary" disabled={busy} onClick={(event) => onPublish(event.currentTarget)} type="button">冻结并发布</button>
        </div>
      </dialog> : null}
    </section>;
  }
  return <section className="ir-version-summary" aria-labelledby="publication-revision-title">
    <h2 id="publication-revision-title">冻结版本</h2>
    <p className="ir-publication-revision-id">{workspace.selected_revision}</p>
    {frozenRevision ? <div aria-label="冻结版本回放">
      <dl>
        <div><dt>版本序号</dt><dd>{frozenRevision.sequence}</dd></div>
        <div><dt>发布时间</dt><dd><time dateTime={frozenRevision.published_at}>{frozenRevision.published_at}</time></dd></div>
        <div><dt>判断</dt><dd>{frozenRevision.assessment.answerability}</dd></div>
      </dl>
      <p>{frozenRevision.memo_markdown}</p>
      <h3>冻结制品</h3>
      <ul>{frozenRevision.artifacts.map((artifact) => <li key={artifact.kind}>{artifact.kind} v{artifact.version}</li>)}</ul>
    </div> : <p>选择查看后，只从不可变版本记录载入内容。</p>}
    <div className="ir-review-actions">
      <button className="ir-button" disabled={busy} onClick={(event) => onReplay(event.currentTarget)} type="button">{action.label}</button>
      <button className="ir-button ir-button--primary" disabled={busy || frozenRevision === null} onClick={(event) => onExport(event.currentTarget)} type="button">导出 Markdown</button>
    </div>
  </section>;
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
  const [committedReview, setCommittedReview] = useState<CommittedReview | null>(null);
  const [retrying, setRetrying] = useState(false);
  const [publicationMutation, setPublicationMutation] = useState<PublicationMutation | null>(null);
  const [publicationPreview, setPublicationPreview] = useState<PublicationPreviewSession | null>(null);
  const [frozenRevision, setFrozenRevision] = useState<CompanyResearchFrozenRevision | null>(null);
  const [memoEditor, setMemoEditor] = useState<MemoEditor | null>(null);
  const [reloadAttempt, setReloadAttempt] = useState(0);
  const [pollTick, setPollTick] = useState(0);
  const [browserVisible, setBrowserVisible] = useState(() => document.visibilityState !== "hidden");
  const lifecycleRef = useRef(0);
  const pollGenerationRef = useRef(0);
  const mutationGenerationRef = useRef(0);
  const mutationActiveRef = useRef(false);
  const workspaceRef = useRef<CompanyResearchWorkspace | null>(null);
  const pollAttemptRef = useRef(0);
  const previewTriggerRef = useRef<HTMLButtonElement | null>(null);

  function commitWorkspace(nextWorkspace: CompanyResearchWorkspace) {
    workspaceRef.current = nextWorkspace;
    setWorkspace(nextWorkspace);
  }

  useEffect(() => {
    const onVisibility = () => {
      const visible = document.visibilityState !== "hidden";
      if (!visible) pollGenerationRef.current += 1;
      setBrowserVisible(visible);
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => { pollGenerationRef.current += 1; document.removeEventListener("visibilitychange", onVisibility); };
  }, []);
  useEffect(() => {
    const lifecycle = ++lifecycleRef.current; pollGenerationRef.current += 1; mutationGenerationRef.current += 1; mutationActiveRef.current = false; let active = true;
    setLoading(true); setLoadError(null); setActionError(null); setLastSuccess(null); setProject(null); workspaceRef.current = null; previewTriggerRef.current = null; setWorkspace(null); setActiveModule("overview"); setReviewingFact(null); setCommittedReview(null); setRetrying(false); setPublicationMutation(null); setPublicationPreview(null); setFrozenRevision(null); setMemoEditor(null); pollAttemptRef.current = 0;
    void Promise.all([investmentResearchApi.project(projectId), investmentResearchApi.companyResearchWorkspace(projectId)]).then(([nextProject, nextWorkspace]) => {
      if (!active || lifecycleRef.current !== lifecycle) return;
      if (nextWorkspace.project_id !== projectId || nextProject.id !== projectId) throw new Error("工作区项目身份与研究项目不一致");
      if (nextWorkspace.company.id !== nextProject.primary_company_id) throw new Error("工作区公司身份与研究项目不一致");
      setProject(nextProject); commitWorkspace(nextWorkspace);
    }).catch((error: unknown) => { if (active && lifecycleRef.current === lifecycle) setLoadError(errorMessage(error, "研究工作区无法读取")); }).finally(() => { if (active && lifecycleRef.current === lifecycle) setLoading(false); });
    return () => { active = false; if (lifecycleRef.current === lifecycle) lifecycleRef.current += 1; pollGenerationRef.current += 1; mutationGenerationRef.current += 1; mutationActiveRef.current = false; };
  }, [projectId, reloadAttempt]);
  useEffect(() => {
    if (!workspace || !browserVisible || reviewingFact !== null || committedReview !== null || retrying || mutationActiveRef.current || !preparationIsActive(workspace.preparation.status)) return;
    const lifecycle = lifecycleRef.current; const delay = Math.min(1000 * 2 ** pollAttemptRef.current, 8000); let active = true;
    const timer = window.setTimeout(() => {
      if (!active || document.visibilityState === "hidden" || mutationActiveRef.current) return;
      const pollGeneration = ++pollGenerationRef.current;
      void investmentResearchApi.companyResearchWorkspace(projectId).then((nextWorkspace) => {
        if (!active || lifecycleRef.current !== lifecycle || pollGenerationRef.current !== pollGeneration || document.visibilityState === "hidden" || mutationActiveRef.current || nextWorkspace.project_id !== projectId) return;
        pollAttemptRef.current += 1;
        if (workspaceRef.current !== null && nextWorkspace.company.id !== workspaceRef.current.company.id) {
          setActionError("工作区同步失败：公司身份不一致；已保留当前工作区。");
          setPollTick((value) => value + 1);
        } else if (workspaceRef.current === null || workspaceSnapshotIsMonotonic(workspaceRef.current, nextWorkspace)) commitWorkspace(nextWorkspace); else setPollTick((value) => value + 1);
      }).catch(() => {
        if (active && lifecycleRef.current === lifecycle && pollGenerationRef.current === pollGeneration && document.visibilityState !== "hidden" && !mutationActiveRef.current) { pollAttemptRef.current += 1; setPollTick((value) => value + 1); }
      });
    }, delay);
    return () => { active = false; window.clearTimeout(timer); };
  }, [browserVisible, committedReview, pollTick, projectId, retrying, reviewingFact, workspace]);

  async function synchronizeCommittedReview(pending: CommittedReview, lifecycle: number, mutation: number): Promise<void> {
    try {
      const refreshed = await investmentResearchApi.companyResearchWorkspace(projectId);
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      const refreshedEvidence = artifactByKind(refreshed, "evidence_index");
      if (!reviewSuccessorIsExact(projectId, pending.previousEvidence, pending.successor, pending.factKey, pending.decision)
        || refreshed.project_id !== projectId || refreshedEvidence === null
        || refreshedEvidence.id !== pending.successor.id || refreshedEvidence.content_hash !== pending.successor.content_hash
        || !reviewSuccessorIsExact(projectId, pending.previousEvidence, refreshedEvidence, pending.factKey, pending.decision)
        || (workspaceRef.current !== null && !workspaceSnapshotIsMonotonic(workspaceRef.current, refreshed))) {
        throw new Error("后继工作区与已提交审核版本不一致");
      }
      commitWorkspace(refreshed);
      setCommittedReview(null);
      setActionError(null);
      setLastSuccess(`事实 ${pending.factKey} 已${pending.decision === "confirmed" ? "确认" : "驳回"}，证据版本 ${pending.successor.version}`);
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) setActionError(`审核已提交，工作区同步失败：${errorMessage(error, "后继工作区无法读取")}`);
    }
  }

  async function reviewFact(fact: EvidenceFact, decision: ReviewDecision, button: HTMLButtonElement) {
    if (!workspace || committedReview !== null || mutationActiveRef.current) return; const evidence = artifactByKind(workspace, "evidence_index"); if (!evidence) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1; const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setReviewingFact(fact.fact_key); setActionError(null); setLastSuccess(null);
    let pending: CommittedReview;
    try {
      const result = await investmentResearchApi.reviewCompanyEvidence(projectId, { schema_version: "underwriting.v1", evidence_artifact_id: evidence.id, fact_key: fact.fact_key, decision, expected_head_id: evidence.id });
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      pending = { factKey: fact.fact_key, decision, previousEvidence: evidence, successor: result.evidence_artifact };
      setCommittedReview(pending);
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { setActionError(errorMessage(error, "证据审核无法写入")); requestAnimationFrame(() => button.focus()); mutationActiveRef.current = false; setReviewingFact(null); }
      return;
    }
    await synchronizeCommittedReview(pending, lifecycle, mutation);
    if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setReviewingFact(null); }
  }
  async function retryCommittedReviewSync() {
    if (committedReview === null || mutationActiveRef.current) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1; const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setReviewingFact(committedReview.factKey); setActionError(null);
    await synchronizeCommittedReview(committedReview, lifecycle, mutation);
    if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setReviewingFact(null); }
  }
  async function retryPreparation() {
    if (!workspace?.preparation.error?.retryable || mutationActiveRef.current) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1; const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current; setRetrying(true); setActionError(null);
    try { await investmentResearchApi.retryCompanyResearchProject(projectId); const refreshed = await investmentResearchApi.companyResearchWorkspace(projectId); if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { if (workspaceRef.current !== null && refreshed.company.id !== workspaceRef.current.company.id) throw new Error("重试已提交，但工作区公司身份不一致"); if (refreshed.project_id !== projectId || workspaceRef.current === null || !workspaceSnapshotIsMonotonic(workspaceRef.current, refreshed, { allowRecovery: true })) throw new Error("重试已提交，但工作区同步响应无效"); pollAttemptRef.current = 0; commitWorkspace(refreshed); } }
    catch (error) { if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) setActionError(errorMessage(error, "准备阶段无法重试")); }
    finally { if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setRetrying(false); } }
  }

  async function refreshPublicationConflict(current: CompanyResearchWorkspace, lifecycle: number, mutation: number): Promise<void> {
    const refreshed = await investmentResearchApi.companyResearchWorkspace(projectId);
    if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
    if (refreshed.project_id !== projectId || refreshed.company.id !== current.company.id
      || !workspaceSnapshotIsMonotonic(current, refreshed)) throw new Error("最新工作区响应无效");
    commitWorkspace(refreshed);
  }

  async function confirmJudgment(button: HTMLButtonElement) {
    const current = workspaceRef.current;
    if (current === null || mutationActiveRef.current) return;
    const memo = artifactByKind(current, "memo");
    const markdown = memoEditor !== null && memoEditor.memoId === memo?.id ? normalizedMarkdown(memoEditor.value) : normalizedMarkdown(machineMemoMarkdown(current));
    if (memo === null || memo.payload.candidate_status !== "machine_draft" || markdown.length === 0) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1;
    const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setPublicationMutation("confirm"); setActionError(null); setLastSuccess(null);
    try {
      await investmentResearchApi.confirmCompanyResearchJudgment(projectId, {
        schema_version: "underwriting.v1",
        expected_lock_version: current.draft.lock_version,
        expected_memo_id: memo.id,
        expected_memo_content_hash: memo.content_hash,
        markdown,
      });
      const refreshed = await investmentResearchApi.companyResearchWorkspace(projectId);
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      if (refreshed.project_id !== projectId || refreshed.company.id !== current.company.id
        || !workspaceSnapshotIsMonotonic(current, refreshed)
        || publicationAction(refreshed)?.kind !== "preview_freeze") throw new Error("确认已提交，但后继工作区响应无效");
      commitWorkspace(refreshed); setMemoEditor(null); setPublicationPreview(null); setFrozenRevision(null);
      setLastSuccess("判断已确认，可以冻结版本。");
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) {
        if (publicationFailureIsConflict(error)) {
          try { await refreshPublicationConflict(current, lifecycle, mutation); } catch { /* Keep the authenticated current snapshot. */ }
          setActionError("工作区已更新，请审核最新判断后再确认。");
        } else setActionError(errorMessage(error, "判断确认失败"));
        restoreFocus(button);
      }
    } finally {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setPublicationMutation(null); }
    }
  }

  async function previewPublication(button: HTMLButtonElement) {
    const current = workspaceRef.current;
    if (current === null || mutationActiveRef.current) return;
    const memo = artifactByKind(current, "memo");
    if (memo === null || memo.payload.candidate_status !== "human_confirmed") return;
    previewTriggerRef.current = button; mutationActiveRef.current = true; pollGenerationRef.current += 1;
    const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setPublicationMutation("preview"); setActionError(null); setLastSuccess(null);
    try {
      const preview = await investmentResearchApi.previewCompanyResearchPublication(projectId, {
        schema_version: "underwriting.v1", expected_lock_version: current.draft.lock_version,
      });
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      const latest = workspaceRef.current; const latestMemo = latest ? artifactByKind(latest, "memo") : null;
      if (latest === null || latest.draft.lock_version !== current.draft.lock_version
        || latestMemo?.id !== memo.id || latestMemo.content_hash !== memo.content_hash) throw new Error("预览期间工作区已更新，请重新预览");
      setPublicationPreview({
        data: preview,
        workspaceLockVersion: current.draft.lock_version,
        memoId: memo.id,
        memoContentHash: memo.content_hash,
        idempotencyKey: crypto.randomUUID(),
      });
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { setActionError(errorMessage(error, "冻结预览无法读取")); restoreFocus(button); }
    } finally {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setPublicationMutation(null); }
    }
  }

  async function publishRevision(button: HTMLButtonElement) {
    const current = workspaceRef.current; const session = publicationPreview;
    if (current === null || session === null || mutationActiveRef.current) return;
    const memo = artifactByKind(current, "memo");
    if (current.draft.lock_version !== session.workspaceLockVersion || memo?.id !== session.memoId || memo.content_hash !== session.memoContentHash) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1;
    const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setPublicationMutation("publish"); setActionError(null); setLastSuccess(null);
    try {
      const published = await investmentResearchApi.publishCompanyResearch(projectId, {
        schema_version: "underwriting.v1",
        expected_lock_version: session.data.expected_lock_version,
        expected_manifest_hash: session.data.manifest_hash,
      }, session.idempotencyKey);
      const [refreshed, replayed] = await Promise.all([
        investmentResearchApi.companyResearchWorkspace(projectId),
        investmentResearchApi.companyResearchRevision(projectId, published.id),
      ]);
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      if (refreshed.project_id !== projectId || refreshed.company.id !== current.company.id
        || refreshed.selected_revision !== published.id || replayed.id !== published.id
        || replayed.manifest_hash !== published.manifest_hash
        || !workspaceSnapshotIsMonotonic(current, refreshed)
        || publicationAction(refreshed)?.kind !== "replay_export") throw new Error("版本已发布，但冻结回放响应无效");
      commitWorkspace(refreshed); setFrozenRevision(replayed); setPublicationPreview(null);
      setLastSuccess("冻结版本已发布，可以回放或导出。");
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) {
        if (publicationFailureIsConflict(error)) {
          try {
            await refreshPublicationConflict(current, lifecycle, mutation);
            setPublicationPreview(null);
            setActionError("工作区已更新，请审核最新判断后重新预览。");
            restoreFocus(previewTriggerRef.current ?? button);
          } catch {
            setActionError("发布发生冲突，且最新工作区无法读取；已保留当前预览。");
            restoreFocus(button);
          }
        } else { setActionError(errorMessage(error, "冻结版本无法发布")); restoreFocus(button); }
      }
    } finally {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setPublicationMutation(null); }
    }
  }

  async function replayRevision(button: HTMLButtonElement) {
    const current = workspaceRef.current; const revisionId = current?.selected_revision;
    if (current === null || revisionId === null || revisionId === undefined || mutationActiveRef.current) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1;
    const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setPublicationMutation("replay"); setActionError(null);
    try {
      const replayed = await investmentResearchApi.companyResearchRevision(projectId, revisionId);
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      if (workspaceRef.current?.selected_revision !== replayed.id) throw new Error("冻结版本选择已变化，请重新查看");
      setFrozenRevision(replayed); setLastSuccess("冻结版本已载入。");
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { setActionError(errorMessage(error, "冻结版本无法读取")); restoreFocus(button); }
    } finally {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setPublicationMutation(null); }
    }
  }

  async function exportRevision(button: HTMLButtonElement) {
    const current = workspaceRef.current; const revisionId = frozenRevision?.id;
    if (current === null || revisionId === undefined || current.selected_revision !== revisionId || mutationActiveRef.current) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1;
    const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setPublicationMutation("export"); setActionError(null);
    try {
      const envelope = await investmentResearchApi.exportCompanyResearchRevision(projectId, revisionId);
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation || workspaceRef.current?.selected_revision !== revisionId) return;
      const objectUrl = URL.createObjectURL(new Blob([envelope.content], { type: envelope.media_type }));
      try {
        const anchor = document.createElement("a"); anchor.href = objectUrl; anchor.download = envelope.filename;
        document.body.append(anchor); anchor.click(); anchor.remove();
      } finally { URL.revokeObjectURL(objectUrl); }
      setLastSuccess("Markdown 已验证并下载。");
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { setActionError(errorMessage(error, "Markdown 导出失败")); restoreFocus(button); }
    } finally {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setPublicationMutation(null); }
    }
  }

  if (loading) return <main className="ir-page ir-workbench" aria-busy="true"><div className="ir-workbench-skeleton"><span /><span /><span /></div></main>;
  if (loadError || !project || !workspace) return <main className="ir-page ir-workbench"><div className="ir-alert" role="alert"><h1>研究项目无法读取</h1><p>{loadError ?? "工作区响应不完整"}</p><button className="ir-button" onClick={() => setReloadAttempt((value) => value + 1)} type="button">重试读取研究项目</button><Link to="/research">返回研究目录</Link></div></main>;
  let currentPublicationAction: CompanyResearchPublicationAction | null;
  try { currentPublicationAction = publicationAction(workspace); }
  catch (error) { return <main className="ir-page ir-workbench"><div className="ir-alert" role="alert"><h1>研究发布状态无法读取</h1><p>{errorMessage(error, "研究发布状态不一致")}</p></div></main>; }
  const currentMemo = artifactByKind(workspace, "memo");
  const memoMarkdown = memoEditor !== null && memoEditor.memoId === currentMemo?.id ? memoEditor.value : machineMemoMarkdown(workspace);
  const activePreview = publicationPreview !== null && currentMemo !== null
    && publicationPreview.workspaceLockVersion === workspace.draft.lock_version
    && publicationPreview.memoId === currentMemo.id
    && publicationPreview.memoContentHash === currentMemo.content_hash
    ? publicationPreview.data : null;
  const activeFrozenRevision = frozenRevision?.id === workspace.selected_revision ? frozenRevision : null;
  const mutationBusy = reviewingFact !== null || committedReview !== null || retrying || publicationMutation !== null;
  const activeDefinition = COMPANY_RESEARCH_MODULES.find((item) => item.key === activeModule) ?? COMPANY_RESEARCH_MODULES[0];
  const activeServerModule = workspace.modules.find((item) => item.key === activeModule);
  const preparationError = workspace.preparation.error;
  return <main className="ir-page ir-workbench" aria-busy={mutationBusy}>
    <header className="ir-workbench-head"><div><p className="ir-eyebrow">Independent company research</p><h1>研究工作台</h1><h2>{workspace.company.canonical_name}</h2><p>{project.security_identities.map((security) => `${security.symbol} · ${security.share_class} · ${security.exchange}`).join("；")}</p></div><span className="ir-draft-state">草稿版本 {workspace.draft.lock_version}</span></header>
    <section className="ir-preparation" aria-live="polite"><div><strong>{PREPARATION_LABELS[workspace.preparation.status]}</strong><span>{workspace.preparation.progress}% · {workspace.preparation.current_step ?? "全部阶段"}</span></div><progress aria-label="研究准备进度" aria-valuemax={100} aria-valuemin={0} aria-valuenow={workspace.preparation.progress} max="100" value={workspace.preparation.progress}>{workspace.preparation.progress}%</progress>{preparationError ? <div className="ir-stage-error"><p>{preparationError.code}</p>{preparationError.retryable ? <button className="ir-button" disabled={mutationBusy} onClick={() => void retryPreparation()} type="button">重试 {preparationError.failed_step}</button> : null}</div> : null}</section>
    {actionError ? <div className="ir-alert" role="alert"><p>{actionError}</p>{committedReview ? <button className="ir-button" disabled={reviewingFact !== null} onClick={() => void retryCommittedReviewSync()} type="button">重试同步已提交审核</button> : null}</div> : null}{lastSuccess ? <p className="ir-confirmed" role="status">{lastSuccess}</p> : null}
    {currentPublicationAction ? <PublicationPanel
      action={currentPublicationAction}
      busy={mutationBusy}
      frozenRevision={activeFrozenRevision}
      memoMarkdown={memoMarkdown}
      onClosePreview={() => setPublicationPreview(null)}
      onConfirm={(button) => void confirmJudgment(button)}
      onExport={(button) => void exportRevision(button)}
      onMemoChange={(value) => currentMemo && setMemoEditor({ memoId: currentMemo.id, value })}
      onPreview={(button) => void previewPublication(button)}
      onPublish={(button) => void publishRevision(button)}
      onReplay={(button) => void replayRevision(button)}
      preview={activePreview}
      workspace={workspace}
    /> : null}
    <div className="ir-workbench-grid"><nav className="ir-module-nav" aria-label="研究模块">{COMPANY_RESEARCH_MODULES.map((definition) => { const module = workspace.modules.find((item) => item.key === definition.key); return <button aria-current={activeModule === definition.key ? "page" : undefined} className={activeModule === definition.key ? "is-active" : ""} key={definition.key} onClick={() => setActiveModule(definition.key)} type="button"><span>{definition.label}</span><small>{module ? MODULE_STATE_LABELS[module.state] : "未开始"}</small></button>; })}</nav>
      <section className="ir-module-content" aria-live="polite"><header><p className="ir-eyebrow">Company research module</p><h2>{activeDefinition.label}</h2></header><ModulePanel activeModule={activeModule} moduleState={activeServerModule?.state ?? "not_started"} valuationState={activeServerModule?.valuation_state ?? "not_applicable"} workspace={workspace} reviewLocked={mutationBusy} onReview={reviewFact} lastSuccess={lastSuccess} /></section>
      <aside className="ir-boundary" aria-label="研究状态摘要"><p className="ir-eyebrow">Research state</p><h2>准备状态</h2><dl><div><dt>来源</dt><dd>{workspace.source_count}</dd></div><div><dt>缺口</dt><dd>{workspace.gap_count}</dd></div><div><dt>已审核事实</dt><dd>{workspace.change_summary.reviewed_fact_count}</dd></div></dl><details id="audit-details"><summary>审计详情</summary><dl><div><dt>Project</dt><dd>{workspace.project_id}</dd></div><div><dt>Preparation</dt><dd>{workspace.preparation.id}</dd></div><div><dt>Draft</dt><dd>{workspace.draft.id}</dd></div><div><dt>Selected revision</dt><dd>{workspace.selected_revision ?? "尚未选择冻结版本"}</dd></div></dl>{Object.entries(workspace.change_summary.artifact_versions).map(([kind, version]) => <span id={`audit-${encodeURIComponent(kind)}`} key={kind}>{kind} v{version}</span>)}</details></aside>
    </div>
  </main>;
}
