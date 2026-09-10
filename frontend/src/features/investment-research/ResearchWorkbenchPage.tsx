import { useEffect, useId, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  COMPANY_RESEARCH_MODULE_ARTIFACTS,
  investmentResearchApi,
  type CompanyResearchCriticalInputSet,
  type CompanyResearchFrozenRevision,
  type CompanyResearchPublicationPreview,
  type CompanyResearchRun,
  type CompanyResearchWorkspace,
  type CriticalInputDecisionRequest,
} from "../../data/investmentResearchApi";
import {
  COMPANY_RESEARCH_MODULES,
  answerabilityView,
  artifactByKind,
  type CompanyResearchModuleKey,
  type CompanyResearchPublicationAction,
  numericObservationView,
  publicationAction,
  researchRunIsActive,
  researchRunSnapshotIsMonotonic,
  workspaceSnapshotIsMonotonic,
} from "./companyResearchView";
import CompanyResearchProgress, { CompanyResearchProcess, type FrozenVerificationState } from "./CompanyResearchProgress";
import CompanyResearchResult from "./CompanyResearchResult";
import CriticalInputDrawer from "./CriticalInputDrawer";

type WorkspaceArtifact = CompanyResearchWorkspace["artifacts"][number];
type WorkspaceArtifactKind = WorkspaceArtifact["kind"];
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
type CriticalInputDrawerSession = { artifactId: string; focusInputKey: string | null };

const MODULE_STATE_LABELS: Record<CompanyResearchWorkspace["modules"][number]["state"], string> = {
  not_started: "未开始", preparing: "准备中", needs_review: "待审核", ready: "可查看", blocked: "已阻塞",
};
const ARTIFACT_MODULE: Readonly<Record<WorkspaceArtifactKind, CompanyResearchModuleKey>> = {
  evidence_index: "evidence_and_gaps", research_gaps: "evidence_and_gaps", business_map: "business_map",
  driver_map: "operating_drivers", financial_bridge: "financials_cash_flow_capital_allocation",
  scenario_set: "scenarios_valuation_implied_expectations", valuation_set: "scenarios_valuation_implied_expectations",
  judgment_context: "counterevidence_risks_next_checks", memo: "versions_changes_memo",
};
const FROZEN_STRATEGY_VERSION = "company-research-mainline.v1";
const FROZEN_MODEL_VERSION = "company-research-model.v1";
const PUBLICATION_FOCUS_IDS: Record<CompanyResearchPublicationAction["kind"], string> = {
  confirm_judgment: "company-research-confirm",
  preview_freeze: "company-research-preview",
  replay_export: "company-research-replay",
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

function focusById(id: string): void {
  requestAnimationFrame(() => requestAnimationFrame(() => document.getElementById(id)?.focus()));
}

function frozenRevisionMatchesRunArtifacts(run: CompanyResearchRun, revision: CompanyResearchFrozenRevision): boolean {
  const workspace = run.workspace;
  const criticalInputs = run.critical_inputs;
  if (revision.project_id !== workspace.project_id || revision.id !== workspace.selected_revision
    || revision.artifacts.length !== workspace.artifacts.length + (criticalInputs === null ? 0 : 1)) return false;
  const workspaceArtifacts = new Map(workspace.artifacts.map((artifact) => [artifact.kind, artifact]));
  if (workspaceArtifacts.size !== workspace.artifacts.length
    || new Set(revision.artifacts.map((descriptor) => descriptor.kind)).size !== revision.artifacts.length) return false;
  return revision.artifacts.every((descriptor) => {
    if (descriptor.kind === "critical_inputs") return criticalInputs !== null
      && descriptor.id === criticalInputs.artifact_id && descriptor.version === criticalInputs.version
      && descriptor.input_hash === criticalInputs.input_hash && descriptor.content_hash === criticalInputs.content_hash;
    const artifact = workspaceArtifacts.get(descriptor.kind);
    return artifact !== undefined && artifact.id === descriptor.id && artifact.version === descriptor.version
      && artifact.input_hash === descriptor.input_hash && artifact.content_hash === descriptor.content_hash;
  });
}

function frozenRevisionMatchesRunIdentity(run: CompanyResearchRun, revision: CompanyResearchFrozenRevision): boolean {
  const cutoff = workspaceCutoff(run.workspace);
  if (revision.project_id !== run.project_id || revision.id !== run.selected_revision || cutoff === null
    || revision.cutoff_at !== cutoff
    || revision.strategy_version !== FROZEN_STRATEGY_VERSION || revision.model_version !== FROZEN_MODEL_VERSION
    || revision.company.schema_version !== run.company.schema_version
    || revision.company.object_id !== run.company.object_id || revision.company.external_key !== run.company.external_key
    || revision.company.canonical_name !== run.company.canonical_name
    || revision.securities.length !== run.securities.length) return false;
  return revision.securities.every((security, index) => {
    const current = run.securities[index];
    return current !== undefined && security.schema_version === current.schema_version
      && security.object_id === current.object_id && security.external_key === current.external_key
      && security.canonical_name === current.canonical_name && security.symbol === current.symbol
      && security.exchange === current.exchange && security.share_class === current.share_class
      && security.trading_currency === current.trading_currency;
  });
}

function frozenRevisionMatchesRun(run: CompanyResearchRun, revision: CompanyResearchFrozenRevision): boolean {
  return frozenRevisionMatchesRunArtifacts(run, revision) && frozenRevisionMatchesRunIdentity(run, revision);
}

function criticalDecisionRunIsTrusted(current: CompanyResearchRun, next: CompanyResearchRun): boolean {
  if (!researchRunSnapshotIsMonotonic(current, next)) return false;
  if (workspaceSnapshotIsMonotonic(current.workspace, next.workspace)) return true;
  const currentInputs = current.critical_inputs;
  const nextInputs = next.critical_inputs;
  if (current.status !== "needs_input" || next.status !== "analyzing_company" || next.progress !== 25
    || currentInputs === null || nextInputs === null || nextInputs.version <= currentInputs.version
    || nextInputs.artifact_id === currentInputs.artifact_id
    || !nextInputs.inputs.some((input) => input.decision === "replaced_with_user_assumption" || input.decision === "marked_unknown")) return false;
  const currentWorkspace = current.workspace;
  const nextWorkspace = next.workspace;
  return nextWorkspace.project_id === currentWorkspace.project_id
    && nextWorkspace.company.id === currentWorkspace.company.id
    && nextWorkspace.preparation.id === currentWorkspace.preparation.id
    && nextWorkspace.preparation.status === "building_model"
    && nextWorkspace.preparation.current_step === "model_bundle"
    && nextWorkspace.preparation.progress === 25
    && nextWorkspace.draft.id === currentWorkspace.draft.id
    && nextWorkspace.draft.lock_version >= currentWorkspace.draft.lock_version
    && nextWorkspace.selected_revision === currentWorkspace.selected_revision
    && nextWorkspace.change_summary.artifact_versions.critical_inputs === nextInputs.version
    && Object.entries(currentWorkspace.change_summary.artifact_versions).every(([kind, version]) =>
      (nextWorkspace.change_summary.artifact_versions[kind] ?? 0) >= version);
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
    {answerability.status === "answerable" && valuation && valuation.payload.security_value_ranges.length > 0 ? <section><h3>价值与回报范围</h3><div className="ir-numeric-grid">{valuation.payload.security_value_ranges.flatMap((range) => [<NumericCard artifact={valuation} key={`${range.security_external_key}-value-min`} label={`${range.security_external_key} 价值下限 (${range.value_currency})`} observation={range.value_per_share.minimum} cutoff={cutoff} />, <NumericCard artifact={valuation} key={`${range.security_external_key}-value-max`} label={`${range.security_external_key} 价值上限 (${range.value_currency})`} observation={range.value_per_share.maximum} cutoff={cutoff} />, <NumericCard artifact={valuation} key={`${range.security_external_key}-return-min`} label={`${range.security_external_key} 回报下限`} observation={range.base_currency_return.minimum} cutoff={cutoff} />, <NumericCard artifact={valuation} key={`${range.security_external_key}-return-max`} label={`${range.security_external_key} 回报上限`} observation={range.base_currency_return.maximum} cutoff={cutoff} />])}</div></section> : answerability.status === "answerable" ? <p>价值与回报范围未提供或不一致。</p> : null}
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
  })}</div></section>{valuation ? <><section><h3>DCF 情景值</h3><div className="ir-numeric-grid">{valuation.payload.scenario_dcf_values.map((item) => <NumericCard artifact={valuation} key={item.scenario_id} label={`${item.scenario_id} DCF`} observation={item.enterprise_value} cutoff={cutoff} />)}</div></section><section><h3>反向 DCF</h3>{valuation.payload.reverse_dcf ? <div className="ir-numeric-grid"><NumericCard artifact={valuation} label="当前价格隐含 FCFF 倍数" observation={valuation.payload.reverse_dcf.implied_value} cutoff={cutoff} /><NumericCard artifact={valuation} label="求解残差" observation={valuation.payload.reverse_dcf.achieved_residual} cutoff={cutoff} /><NumericCard artifact={valuation} label="迭代次数" observation={valuation.payload.reverse_dcf.iteration_count} cutoff={cutoff} /></div> : <p>当前无法建立反向 DCF。</p>}</section><section><h3>证券价值范围</h3>{valuation.payload.security_value_ranges.map((range) => <article className="ir-security-range" key={range.security_external_key}><h4>{range.security_external_key}</h4><div className="ir-numeric-grid"><NumericCard artifact={valuation} label={`每股价值下限 (${range.value_currency})`} observation={range.value_per_share.minimum} cutoff={cutoff} /><NumericCard artifact={valuation} label={`每股价值上限 (${range.value_currency})`} observation={range.value_per_share.maximum} cutoff={cutoff} /><NumericCard artifact={valuation} label="回报下限" observation={range.base_currency_return.minimum} cutoff={cutoff} /><NumericCard artifact={valuation} label="回报上限" observation={range.base_currency_return.maximum} cutoff={cutoff} /></div></article>)}</section></> : <section><h3>估值</h3><p>{valuationState === "pending" ? "估值仍在准备（服务器状态：pending）" : valuationState === "blocked" ? "估值已阻塞（服务器状态：blocked）" : valuationState === "ready" ? "估值标记为可查看，但估值制品缺失；接口响应不一致。" : "估值不适用于当前模块。"}</p></section>}<section><h3>反证</h3>{judgment ? <SourceRefList refs={judgment.payload.strongest_counterevidence} /> : <p>反证清单仍在准备。</p>}</section></div>;
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

function PublicationPreviewDialog({
  busy,
  mutation,
  onClose,
  onPublish,
  preview,
}: {
  busy: boolean;
  mutation: PublicationMutation | null;
  onClose: () => void;
  onPublish: (button: HTMLButtonElement) => void;
  preview: CompanyResearchPublicationPreview;
}) {
  const dialogTitleId = useId();
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const initialFocusRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) return;
    if (!dialog.open) dialog.showModal();
    initialFocusRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
    };
  }, [preview.manifest_hash]);
  return <dialog
    aria-labelledby={dialogTitleId}
    className="ir-publication-dialog"
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}
    ref={dialogRef}
  >
    <h2 id={dialogTitleId}>确认冻结版本</h2>
    <p><strong>冻结后不可修改。</strong> 发布会创建不可变版本，后续工作区变化不会改写它。</p>
    <dl>
      <div><dt>schema version</dt><dd>{preview.schema_version}</dd></div>
      <div><dt>project id</dt><dd>{preview.project_id}</dd></div>
      <div><dt>公司</dt><dd>{preview.company.schema_version} · {preview.company.canonical_name} · {preview.company.external_key} · {preview.company.object_id}</dd></div>
      <div><dt>证据截止时间</dt><dd><time dateTime={preview.cutoff_at}>{preview.cutoff_at}</time></dd></div>
      <div><dt>expected lock </dt><dd>{preview.expected_lock_version}</dd></div>
      <div><dt>historical basis id</dt><dd>{preview.historical_basis_id}</dd></div>
      <div><dt>historical basis hash</dt><dd>{preview.historical_basis_content_hash}</dd></div>
      <div><dt>strategy</dt><dd>{preview.strategy_version}</dd></div>
      <div><dt>model</dt><dd>{preview.model_version}</dd></div>
      <div><dt>assessment schema</dt><dd>{preview.assessment.schema_version}</dd></div>
      <div><dt>可回答性</dt><dd>{preview.assessment.answerability}</dd></div>
      <div><dt>方向</dt><dd>{preview.assessment.direction ?? "未建立方向"}</dd></div>
      <div><dt>置信度</dt><dd>{preview.assessment.confidence ?? "未建立置信度"}</dd></div>
      <div><dt>assessment content hash</dt><dd>{preview.assessment.content_hash}</dd></div>
      <div><dt>价值范围</dt><dd>{preview.value_range === null ? "未建立价值范围" : `${preview.value_range.schema_version} · ${preview.value_range.minimum}–${preview.value_range.maximum} ${preview.value_range.currency}`}</dd></div>
      <div><dt>回报范围</dt><dd>{preview.return_range === null ? "未建立回报范围" : `${preview.return_range.schema_version} · ${preview.return_range.minimum}–${preview.return_range.maximum}`}</dd></div>
      <div><dt>manifest hash </dt><dd>{preview.manifest_hash}</dd></div>
    </dl>
    <h3>冻结证券</h3>
    <ul aria-label="冻结证券清单">{preview.securities.map((security) => <li key={security.object_id}>{security.schema_version} · {security.canonical_name} · {security.external_key} · {security.symbol} · {security.exchange} · {security.share_class} · {security.trading_currency} · {security.company_id} · {security.object_id}</li>)}</ul>
    <h3>最强反证</h3>
    {preview.strongest_counterevidence.length > 0 ? <ul aria-label="冻结最强反证">{preview.strongest_counterevidence.map((ref) => <li key={`${ref.fact_key}-${ref.source_url}-${ref.source_locator}`}>{ref.fact_key} · {ref.source_role} · {ref.source_url} · {ref.source_locator} · {ref.raw_hash}</li>)}</ul> : <p>未提供最强反证。</p>}
    <h3>下一验证事件</h3>
    {preview.next_verification_events.length > 0 ? <ul aria-label="冻结下一验证事件">{preview.next_verification_events.map((event) => <li key={event}>{event}</li>)}</ul> : <p>未提供下一验证事件。</p>}
    <h3>阻塞项</h3>
    {preview.blockers.length > 0 ? <ul aria-label="冻结阻塞项" className="ir-publication-blockers">{preview.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul> : <p>没有冻结阻塞项。</p>}
    <h3>研究备忘录</h3>
    <pre aria-label="冻结预览备忘录" className="ir-publication-memo">{preview.memo_markdown}</pre>
    <h3>冻结制品</h3>
    <ul aria-label="冻结制品清单">{preview.artifacts.map((artifact) => <li key={artifact.kind}>{artifact.schema_version} {artifact.kind} {artifact.id} v{artifact.version} input {artifact.input_hash} content {artifact.content_hash}</li>)}</ul>
    <div className="ir-review-actions">
      <button className="ir-button" disabled={busy} onClick={onClose} ref={initialFocusRef} type="button">返回检查</button>
      <button className="ir-button ir-button--primary" disabled={busy} onClick={(event) => onPublish(event.currentTarget)} type="button">{mutation === "publish" ? "正在冻结并发布…" : "冻结并发布"}</button>
    </div>
  </dialog>;
}

function PublicationPanel({
  action,
  busy,
  frozenRevision,
  memoMarkdown,
  mutation,
  onClosePreview,
  onConfirm,
  onOpenCriticalInputs,
  onExport,
  onMemoChange,
  onPreview,
  onPublish,
  onReplay,
  preview,
  criticalInputs,
  workspace,
}: {
  action: CompanyResearchPublicationAction;
  busy: boolean;
  frozenRevision: CompanyResearchFrozenRevision | null;
  memoMarkdown: string;
  mutation: PublicationMutation | null;
  onClosePreview: () => void;
  onConfirm: (button: HTMLButtonElement) => void;
  onOpenCriticalInputs: (button: HTMLButtonElement) => void;
  onExport: (button: HTMLButtonElement) => void;
  onMemoChange: (value: string) => void;
  onPreview: (button: HTMLButtonElement) => void;
  onPublish: (button: HTMLButtonElement) => void;
  onReplay: (button: HTMLButtonElement) => void;
  preview: CompanyResearchPublicationPreview | null;
  criticalInputs: CompanyResearchCriticalInputSet | null;
  workspace: CompanyResearchWorkspace;
}) {
  if (action.kind === "confirm_judgment") {
    const pendingCriticalInputs = criticalInputs?.inputs.filter((input) => input.decision === "pending") ?? [];
    if (pendingCriticalInputs.length > 0) return <section className="ir-version-summary" aria-labelledby="publication-critical-input-title">
      <h2 id="publication-critical-input-title">保存研究版本</h2>
      <p>保存前只确认会显著改变预测、估值或判断的关键输入，普通证据不会逐条阻塞。</p>
      <p>{pendingCriticalInputs.length} 项关键输入待处理。</p>
      <button className="ir-button ir-button--primary" disabled={busy} id="company-research-save" onClick={(event) => onOpenCriticalInputs(event.currentTarget)} type="button">保存研究版本</button>
    </section>;
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
      <button className="ir-button ir-button--primary" disabled={busy || normalizedMarkdown(memoMarkdown).length === 0} id="company-research-confirm" onClick={(event) => onConfirm(event.currentTarget)} type="button">{mutation === "confirm" ? "正在确认研究判断…" : action.label}</button>
    </section>;
  }
  if (action.kind === "preview_freeze") {
    return <section className="ir-version-summary" aria-labelledby="publication-preview-title">
      <h2 id="publication-preview-title">冻结研究版本</h2>
      <p>先读取零写入预览，再核对公司、证券、截止时间和证据边界。</p>
      <button className="ir-button ir-button--primary" disabled={busy} id="company-research-preview" onClick={(event) => onPreview(event.currentTarget)} type="button">{mutation === "preview" ? "正在生成冻结预览…" : action.label}</button>
      {preview ? <PublicationPreviewDialog busy={busy} mutation={mutation} onClose={onClosePreview} onPublish={onPublish} preview={preview} /> : null}
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
      <p>冻结版本 {frozenRevision.id}</p>
      <pre aria-label="冻结研究备忘录" className="ir-publication-memo">{frozenRevision.memo_markdown}</pre>
      <h3>冻结制品</h3>
      <ul>{frozenRevision.artifacts.map((artifact) => <li key={artifact.kind}>{artifact.kind} v{artifact.version}</li>)}</ul>
    </div> : <p>选择查看后，只从不可变版本记录载入内容。</p>}
    <div className="ir-review-actions">
      <button className="ir-button" disabled={busy} id="company-research-replay" onClick={(event) => onReplay(event.currentTarget)} type="button">{mutation === "replay" ? "正在载入冻结版本…" : action.label}</button>
      <button className="ir-button ir-button--primary" disabled={busy || frozenRevision === null} onClick={(event) => onExport(event.currentTarget)} type="button">{mutation === "export" ? "正在验证导出…" : "导出 Markdown"}</button>
    </div>
  </section>;
}

export default function ResearchWorkbenchPage() {
  const { projectId = "" } = useParams();
  const [run, setRun] = useState<CompanyResearchRun | null>(null);
  const workspace = run?.workspace ?? null;
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
  const [processExpanded, setProcessExpanded] = useState(false);
  const [processLoading, setProcessLoading] = useState(false);
  const [criticalInputDrawer, setCriticalInputDrawer] = useState<CriticalInputDrawerSession | null>(null);
  const [criticalInputMutation, setCriticalInputMutation] = useState<string | null>(null);
  const [criticalInputError, setCriticalInputError] = useState<string | null>(null);
  const [browserVisible, setBrowserVisible] = useState(() => document.visibilityState !== "hidden");
  const lifecycleRef = useRef(0);
  const pollGenerationRef = useRef(0);
  const mutationGenerationRef = useRef(0);
  const mutationActiveRef = useRef(false);
  const runRef = useRef<CompanyResearchRun | null>(null);
  const workspaceRef = useRef<CompanyResearchWorkspace | null>(null);
  const pollAttemptRef = useRef(0);
  const previewTriggerRef = useRef<HTMLButtonElement | null>(null);
  const criticalInputTriggerRef = useRef<HTMLButtonElement | null>(null);
  const pendingPublicationFocusRef = useRef<string | null>(null);
  const attemptedAutomaticRevisionRef = useRef<string | null>(null);

  useEffect(() => {
    if (publicationMutation !== null || criticalInputMutation !== null || pendingPublicationFocusRef.current === null) return;
    const targetId = pendingPublicationFocusRef.current;
    pendingPublicationFocusRef.current = null;
    focusById(targetId);
  }, [criticalInputMutation, publicationMutation, workspace?.preparation.status, workspace?.selected_revision]);

  function commitRun(nextRun: CompanyResearchRun) {
    runRef.current = nextRun;
    workspaceRef.current = nextRun.workspace;
    setRun(nextRun);
  }

  function readRun(includeProcess: boolean): Promise<CompanyResearchRun> {
    return includeProcess
      ? investmentResearchApi.companyResearchRun(projectId, true)
      : investmentResearchApi.companyResearchRun(projectId);
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
    setLoading(true); setLoadError(null); setActionError(null); setLastSuccess(null); runRef.current = null; workspaceRef.current = null; previewTriggerRef.current = null; criticalInputTriggerRef.current = null; pendingPublicationFocusRef.current = null; attemptedAutomaticRevisionRef.current = null; setRun(null); setProcessExpanded(false); setProcessLoading(false); setCriticalInputDrawer(null); setCriticalInputMutation(null); setCriticalInputError(null); setActiveModule("overview"); setReviewingFact(null); setCommittedReview(null); setRetrying(false); setPublicationMutation(null); setPublicationPreview(null); setFrozenRevision(null); setMemoEditor(null); pollAttemptRef.current = 0;
    void investmentResearchApi.companyResearchRun(projectId).then((nextRun) => {
      if (!active || lifecycleRef.current !== lifecycle) return;
      if (nextRun.project_id !== projectId || nextRun.workspace.project_id !== projectId) throw new Error("研究运行项目身份不一致");
      if (nextRun.workspace.company.id !== nextRun.company.object_id) throw new Error("研究运行公司身份不一致");
      commitRun(nextRun);
    }).catch((error: unknown) => { if (active && lifecycleRef.current === lifecycle) setLoadError(errorMessage(error, "研究工作区无法读取")); }).finally(() => { if (active && lifecycleRef.current === lifecycle) setLoading(false); });
    return () => { active = false; if (lifecycleRef.current === lifecycle) lifecycleRef.current += 1; pollGenerationRef.current += 1; mutationGenerationRef.current += 1; mutationActiveRef.current = false; };
  }, [projectId, reloadAttempt]);
  useEffect(() => {
    if (!run || !browserVisible || processLoading || reviewingFact !== null || committedReview !== null || retrying || mutationActiveRef.current || !researchRunIsActive(run)) return;
    const lifecycle = lifecycleRef.current; const delay = Math.min(1000 * 2 ** pollAttemptRef.current, 8000); let active = true;
    const timer = window.setTimeout(() => {
      if (!active || document.visibilityState === "hidden" || mutationActiveRef.current) return;
      const pollGeneration = ++pollGenerationRef.current;
      void readRun(processExpanded).then((nextRun) => {
        if (!active || lifecycleRef.current !== lifecycle || pollGenerationRef.current !== pollGeneration || document.visibilityState === "hidden" || mutationActiveRef.current || nextRun.project_id !== projectId) return;
        pollAttemptRef.current += 1;
        if (runRef.current !== null && nextRun.company.object_id !== runRef.current.company.object_id) {
          setActionError("研究运行同步失败：公司身份不一致；已保留当前结果。");
          setPollTick((value) => value + 1);
        } else if (runRef.current === null || researchRunSnapshotIsMonotonic(runRef.current, nextRun)
          && workspaceSnapshotIsMonotonic(workspaceRef.current!, nextRun.workspace)) commitRun(nextRun); else setPollTick((value) => value + 1);
      }).catch(() => {
        if (active && lifecycleRef.current === lifecycle && pollGenerationRef.current === pollGeneration && document.visibilityState !== "hidden" && !mutationActiveRef.current) { pollAttemptRef.current += 1; setPollTick((value) => value + 1); }
      });
    }, delay);
    return () => { active = false; window.clearTimeout(timer); };
  }, [browserVisible, committedReview, pollTick, processExpanded, processLoading, projectId, publicationMutation, retrying, reviewingFact, run]);

  async function synchronizeCommittedReview(pending: CommittedReview, lifecycle: number, mutation: number): Promise<void> {
    try {
      const refreshedRun = await readRun(processExpanded);
      const refreshed = refreshedRun.workspace;
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      const refreshedEvidence = artifactByKind(refreshed, "evidence_index");
      if (!reviewSuccessorIsExact(projectId, pending.previousEvidence, pending.successor, pending.factKey, pending.decision)
        || refreshed.project_id !== projectId || refreshedEvidence === null
        || refreshedEvidence.id !== pending.successor.id || refreshedEvidence.content_hash !== pending.successor.content_hash
        || !reviewSuccessorIsExact(projectId, pending.previousEvidence, refreshedEvidence, pending.factKey, pending.decision)
        || (workspaceRef.current !== null && !workspaceSnapshotIsMonotonic(workspaceRef.current, refreshed))) {
        throw new Error("后继工作区与已提交审核版本不一致");
      }
      commitRun(refreshedRun);
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
    try { await investmentResearchApi.retryCompanyResearchProject(projectId); const refreshedRun = await readRun(processExpanded); const refreshed = refreshedRun.workspace; if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { if (workspaceRef.current !== null && refreshed.company.id !== workspaceRef.current.company.id) throw new Error("重试已提交，但工作区公司身份不一致"); if (refreshed.project_id !== projectId || workspaceRef.current === null || !workspaceSnapshotIsMonotonic(workspaceRef.current, refreshed, { allowRecovery: true })) throw new Error("重试已提交，但工作区同步响应无效"); pollAttemptRef.current = 0; commitRun(refreshedRun); } }
    catch (error) { if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) setActionError(errorMessage(error, "准备阶段无法重试")); }
    finally { if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setRetrying(false); } }
  }

  async function refreshPublicationConflict(current: CompanyResearchWorkspace, lifecycle: number, mutation: number): Promise<CompanyResearchWorkspace | null> {
    const refreshedRun = await readRun(processExpanded);
    const refreshed = refreshedRun.workspace;
    if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return null;
    if (refreshed.project_id !== projectId || refreshed.company.id !== current.company.id
      || !workspaceSnapshotIsMonotonic(current, refreshed)) throw new Error("最新工作区响应无效");
    commitRun(refreshedRun);
    return refreshed;
  }

  async function bindFrozenRevision(nextWorkspace: CompanyResearchWorkspace, lifecycle: number, mutation: number): Promise<CompanyResearchFrozenRevision> {
    const revisionId = nextWorkspace.selected_revision;
    if (revisionId === null) throw new Error("冻结版本尚未选择");
    const replayed = await investmentResearchApi.companyResearchRevision(projectId, revisionId);
    if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) throw new Error("冻结版本请求已失效");
    const currentRun = runRef.current;
    if (currentRun === null || currentRun.workspace !== nextWorkspace) throw new Error("冻结版本与研究运行身份或制品不一致");
    if (!frozenRevisionMatchesRunArtifacts(currentRun, replayed)) throw new Error("冻结版本与工作区制品不一致");
    if (!frozenRevisionMatchesRunIdentity(currentRun, replayed)) throw new Error("冻结版本与研究运行身份或制品不一致");
    setFrozenRevision(replayed);
    return replayed;
  }

  async function resolvePublicationConflict(current: CompanyResearchWorkspace, lifecycle: number, mutation: number): Promise<CompanyResearchPublicationAction | null> {
    const refreshed = await refreshPublicationConflict(current, lifecycle, mutation);
    if (refreshed === null) return null;
    const nextAction = publicationAction(refreshed);
    setPublicationPreview(null);
    setMemoEditor(null);
    setFrozenRevision(null);
    if (nextAction !== null) applyPublicationConflictFocus(nextAction);
    if (nextAction?.kind === "replay_export") await bindFrozenRevision(refreshed, lifecycle, mutation);
    return nextAction;
  }

  function applyPublicationConflictFocus(action: CompanyResearchPublicationAction): void {
    pendingPublicationFocusRef.current = PUBLICATION_FOCUS_IDS[action.kind];
  }

  useEffect(() => {
    const revisionId = workspace?.selected_revision;
    if (workspace?.preparation.status !== "completed" || revisionId === null || revisionId === undefined
      || frozenRevision?.id === revisionId || attemptedAutomaticRevisionRef.current === revisionId
      || publicationMutation !== null || processLoading || mutationActiveRef.current) return;
    attemptedAutomaticRevisionRef.current = revisionId;
    mutationActiveRef.current = true; pollGenerationRef.current += 1;
    const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setPublicationMutation("replay"); setActionError(null);
    void bindFrozenRevision(workspace, lifecycle, mutation).then(() => {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) setLastSuccess("冻结版本已自动验证并载入。");
    }).catch((error: unknown) => {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) setActionError(errorMessage(error, "冻结版本无法读取"));
    }).finally(() => {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) {
        mutationActiveRef.current = false;
        setPublicationMutation(null);
      }
    });
  }, [frozenRevision?.id, processLoading, projectId, publicationMutation, workspace?.preparation.status, workspace?.selected_revision]);

  function openCriticalInputs(button: HTMLButtonElement) {
    const criticalInputs = runRef.current?.critical_inputs;
    if (criticalInputs === null || criticalInputs === undefined
      || !criticalInputs.inputs.some((input) => input.decision === "pending")
      || mutationActiveRef.current) return;
    criticalInputTriggerRef.current = button;
    setCriticalInputError(null);
    setCriticalInputDrawer({ artifactId: criticalInputs.artifact_id, focusInputKey: null });
  }

  async function decideCriticalInput(request: CriticalInputDecisionRequest) {
    const currentRun = runRef.current;
    const currentInputs = currentRun?.critical_inputs;
    const currentInput = currentInputs?.inputs.find((input) => input.key === request.critical_input_key);
    if (currentRun === null || currentInputs === null || currentInputs === undefined || currentInput === undefined
      || currentInputs.artifact_id !== request.expected_artifact_id
      || currentInput.input_fingerprint !== request.expected_input_fingerprint
      || currentInput.decision !== "pending" || mutationActiveRef.current) return;
    mutationActiveRef.current = true; pollGenerationRef.current += 1;
    const lifecycle = lifecycleRef.current; const mutation = ++mutationGenerationRef.current;
    setCriticalInputMutation(currentInput.key); setCriticalInputError(null); setActionError(null); setLastSuccess(null);
    try {
      const nextRun = await investmentResearchApi.decideCompanyResearchCriticalInput(projectId, request);
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      const nextInputs = nextRun.critical_inputs;
      const decided = nextInputs?.inputs.find((input) => input.key === currentInput.key);
      if (nextRun.project_id !== projectId || nextRun.company.object_id !== currentRun.company.object_id
        || nextInputs === null || nextInputs.version <= currentInputs.version
        || nextInputs.artifact_id === currentInputs.artifact_id || decided === undefined
        || decided.input_fingerprint !== currentInput.input_fingerprint || decided.decision !== request.decision
        || !criticalDecisionRunIsTrusted(currentRun, nextRun)) {
        throw new Error("关键输入已提交，但后继研究运行响应无效");
      }
      commitRun(nextRun); setMemoEditor(null); setPublicationPreview(null); setFrozenRevision(null);
      const nextPending = nextInputs.inputs.find((input) => input.decision === "pending");
      if (nextPending) {
        setCriticalInputDrawer({ artifactId: nextInputs.artifact_id, focusInputKey: nextPending.key });
        setLastSuccess(`关键输入 ${currentInput.key} 已处理，请继续确认下一项。`);
      } else {
        setCriticalInputDrawer(null);
        const nextAction = publicationAction(nextRun.workspace);
        if (nextAction) applyPublicationConflictFocus(nextAction);
        setLastSuccess(nextAction?.kind === "preview_freeze"
          ? "关键输入已全部处理，可以预览冻结版本。"
          : "关键输入决定已保存，研究模型将基于新边界继续计算。");
      }
    } catch (error) {
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      if (publicationFailureIsConflict(error)) {
        try {
          const freshRun = await readRun(processExpanded);
          if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
          if (freshRun.project_id !== projectId || freshRun.company.object_id !== currentRun.company.object_id
            || !criticalDecisionRunIsTrusted(currentRun, freshRun)) {
            throw new Error("关键输入冲突后的最新研究运行响应无效");
          }
          commitRun(freshRun); setMemoEditor(null); setPublicationPreview(null); setFrozenRevision(null);
          const changed = freshRun.critical_inputs?.inputs.find((input) => input.key === currentInput.key && input.decision === "pending");
          if (freshRun.critical_inputs && changed) {
            setCriticalInputDrawer({ artifactId: freshRun.critical_inputs.artifact_id, focusInputKey: changed.key });
            setCriticalInputError("关键输入已更新，请基于新值重新确认。");
          } else {
            setCriticalInputDrawer(null);
            const nextAction = publicationAction(freshRun.workspace);
            if (nextAction) applyPublicationConflictFocus(nextAction);
            setActionError("该关键输入已由其他请求处理，已载入最新研究状态。");
          }
        } catch (refreshError) {
          setCriticalInputDrawer(null);
          setActionError(errorMessage(refreshError, "关键输入冲突后无法读取最新研究状态"));
        }
      } else setCriticalInputError(errorMessage(error, "关键输入决定无法保存"));
    } finally {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) {
        mutationActiveRef.current = false;
        setCriticalInputMutation(null);
      }
    }
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
      const refreshedRun = await readRun(processExpanded);
      const refreshed = refreshedRun.workspace;
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      if (refreshed.project_id !== projectId || refreshed.company.id !== current.company.id
        || !workspaceSnapshotIsMonotonic(current, refreshed)
        || publicationAction(refreshed)?.kind !== "preview_freeze") throw new Error("确认已提交，但后继工作区响应无效");
      commitRun(refreshedRun); setMemoEditor(null); setPublicationPreview(null); setFrozenRevision(null);
      setLastSuccess("判断已确认，可以冻结版本。");
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) {
        if (publicationFailureIsConflict(error)) {
          try {
            const nextAction = await resolvePublicationConflict(current, lifecycle, mutation);
            if (nextAction === null) return;
            if (nextAction.kind === "confirm_judgment") setActionError("工作区已更新，请审核最新判断后再确认。");
            else if (nextAction.kind === "preview_freeze") { setActionError(null); setLastSuccess("判断已由并发请求确认，可以冻结版本。"); }
            else { setActionError(null); setLastSuccess("冻结版本已由并发请求发布。"); }
            applyPublicationConflictFocus(nextAction);
          } catch (refreshError) {
            setActionError(errorMessage(refreshError, "工作区并发更新后无法读取"));
            if (pendingPublicationFocusRef.current === null) restoreFocus(button);
          }
        } else setActionError(errorMessage(error, "判断确认失败"));
        if (!publicationFailureIsConflict(error)) restoreFocus(button);
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
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) {
        if (publicationFailureIsConflict(error)) {
          try {
            const nextAction = await resolvePublicationConflict(current, lifecycle, mutation);
            if (nextAction === null) return;
            if (nextAction.kind === "replay_export") { setActionError(null); setLastSuccess("冻结版本已由并发请求发布。"); }
            else if (nextAction.kind === "preview_freeze") setActionError("工作区已更新，请重新生成冻结预览。");
            else setActionError("工作区已更新，请审核最新判断后再确认。");
          } catch (refreshError) {
            setActionError(errorMessage(refreshError, "冻结预览冲突后无法读取最新工作区"));
            if (pendingPublicationFocusRef.current === null) restoreFocus(button);
          }
        } else { setActionError(errorMessage(error, "冻结预览无法读取")); restoreFocus(button); }
      }
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
      const [refreshedRun, replayed] = await Promise.all([
        readRun(processExpanded),
        investmentResearchApi.companyResearchRevision(projectId, published.id),
      ]);
      const refreshed = refreshedRun.workspace;
      if (lifecycleRef.current !== lifecycle || mutationGenerationRef.current !== mutation) return;
      if (refreshed.project_id !== projectId || refreshed.company.id !== current.company.id
        || refreshed.selected_revision !== published.id || replayed.id !== published.id
        || replayed.manifest_hash !== published.manifest_hash
        || !frozenRevisionMatchesRunArtifacts(refreshedRun, replayed) || !frozenRevisionMatchesRunIdentity(refreshedRun, replayed)
        || !workspaceSnapshotIsMonotonic(current, refreshed)
        || publicationAction(refreshed)?.kind !== "replay_export") throw new Error("版本已发布，但冻结回放响应无效");
      commitRun(refreshedRun); setFrozenRevision(replayed); setPublicationPreview(null);
      setLastSuccess("冻结版本已发布，可以回放或导出。");
      pendingPublicationFocusRef.current = "company-research-replay";
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) {
        if (publicationFailureIsConflict(error)) {
          try {
            const nextAction = await resolvePublicationConflict(current, lifecycle, mutation);
            if (nextAction === null) return;
            if (nextAction.kind === "replay_export") { setActionError(null); setLastSuccess("冻结版本已由并发请求发布。"); }
            else if (nextAction.kind === "preview_freeze") setActionError("工作区已更新，请重新生成冻结预览。");
            else setActionError("工作区已更新，请审核最新判断后再确认。");
            applyPublicationConflictFocus(nextAction);
          } catch (refreshError) {
            setActionError(`发布发生冲突，且最新工作区无法读取：${errorMessage(refreshError, "响应无效")}`);
            if (pendingPublicationFocusRef.current === null) restoreFocus(button);
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
      const latest = workspaceRef.current;
      if (latest === null || latest.selected_revision !== replayed.id) throw new Error("冻结版本选择已变化，请重新查看");
      if (runRef.current === null || !frozenRevisionMatchesRunArtifacts(runRef.current, replayed)) throw new Error("冻结版本与工作区制品不一致");
      if (runRef.current === null || !frozenRevisionMatchesRunIdentity(runRef.current, replayed)) throw new Error("冻结版本与研究运行身份或制品不一致");
      setFrozenRevision(replayed); setLastSuccess("冻结版本已载入。");
    } catch (error) {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { setActionError(errorMessage(error, "冻结版本无法读取")); restoreFocus(button); }
    } finally {
      if (lifecycleRef.current === lifecycle && mutationGenerationRef.current === mutation) { mutationActiveRef.current = false; setPublicationMutation(null); }
    }
  }

  async function exportRevision(button: HTMLButtonElement) {
    const current = workspaceRef.current; const revisionId = frozenRevision?.id;
    if (current === null || frozenRevision === null || revisionId === undefined || current.selected_revision !== revisionId
      || runRef.current === null || !frozenRevisionMatchesRun(runRef.current, frozenRevision) || mutationActiveRef.current) return;
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

  async function toggleProcess() {
    if (processLoading || mutationActiveRef.current) return;
    if (processExpanded) {
      setProcessExpanded(false);
      return;
    }
    const lifecycle = lifecycleRef.current;
    const pollGeneration = ++pollGenerationRef.current;
    mutationActiveRef.current = true;
    setProcessLoading(true);
    setActionError(null);
    try {
      const expandedRun = await investmentResearchApi.companyResearchRun(projectId, true);
      if (lifecycleRef.current !== lifecycle || pollGenerationRef.current !== pollGeneration) return;
      if (runRef.current === null || expandedRun.project_id !== projectId
        || !researchRunSnapshotIsMonotonic(runRef.current, expandedRun)
        || !workspaceSnapshotIsMonotonic(workspaceRef.current!, expandedRun.workspace)) throw new Error("完整研究过程响应无效");
      commitRun(expandedRun);
      setProcessExpanded(true);
    } catch (error) {
      if (lifecycleRef.current === lifecycle && pollGenerationRef.current === pollGeneration) setActionError(errorMessage(error, "完整研究过程无法读取"));
    } finally {
      if (lifecycleRef.current === lifecycle) {
        mutationActiveRef.current = false;
        setProcessLoading(false);
      }
    }
  }

  function openResearchArtifact(kind: WorkspaceArtifactKind) {
    const module = ARTIFACT_MODULE[kind];
    setActiveModule(module);
    const basis = document.getElementById("company-research-basis") as HTMLDetailsElement | null;
    if (basis) basis.open = true;
    requestAnimationFrame(() => document.getElementById(`research-module-${module}`)?.focus());
  }

  if (loading) return <main className="ir-page ir-workbench" aria-busy="true"><div className="ir-workbench-skeleton"><span /><span /><span /></div></main>;
  if (loadError || !run || !workspace) return <main className="ir-page ir-workbench"><div className="ir-alert" role="alert"><h1>研究项目无法读取</h1><p>{loadError ?? "研究运行响应不完整"}</p><button className="ir-button" onClick={() => setReloadAttempt((value) => value + 1)} type="button">重试读取研究项目</button><Link to="/research">返回研究目录</Link></div></main>;
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
  const activeFrozenRevision = frozenRevision !== null && frozenRevisionMatchesRun(run, frozenRevision) ? frozenRevision : null;
  const frozenWorkspaceIsBound = currentPublicationAction?.kind !== "replay_export" || activeFrozenRevision !== null;
  const mutationBusy = processLoading || reviewingFact !== null || committedReview !== null || retrying || publicationMutation !== null || criticalInputMutation !== null;
  const activeCriticalInputs = criticalInputDrawer !== null && run.critical_inputs?.artifact_id === criticalInputDrawer.artifactId
    && run.critical_inputs.inputs.some((input) => input.decision === "pending")
    ? run.critical_inputs
    : null;
  const activeDefinition = COMPANY_RESEARCH_MODULES.find((item) => item.key === activeModule) ?? COMPANY_RESEARCH_MODULES[0];
  const activeServerModule = workspace.modules.find((item) => item.key === activeModule);
  const frozenVerification: FrozenVerificationState = run.selected_revision === null
    ? "draft"
    : activeFrozenRevision !== null
      ? "verified"
      : publicationMutation === "replay" || attemptedAutomaticRevisionRef.current !== run.selected_revision
        ? "verifying"
        : "failed";
  const primaryResultVerified = run.selected_revision === null || activeFrozenRevision !== null;
  return <main className="ir-page ir-workbench" aria-busy={mutationBusy}>
    <header className="ir-workbench-head"><div><p className="ir-eyebrow">Company research</p><h1>{run.company.canonical_name}</h1><p>{run.securities.map((security) => `${security.symbol} · ${security.share_class} · ${security.exchange}`).join("；")}</p></div><span className="ir-draft-state">资料截止 {workspaceCutoff(workspace) ? new Date(workspaceCutoff(workspace)!).toLocaleDateString("zh-CN") : "待确认"}</span></header>
    <CompanyResearchProgress
      frozenVerification={frozenVerification}
      run={run}
    />
    {actionError ? <div className="ir-alert" role="alert"><p>{actionError}</p>{committedReview ? <button className="ir-button" disabled={reviewingFact !== null} onClick={() => void retryCommittedReviewSync()} type="button">重试同步已提交审核</button> : null}</div> : null}{lastSuccess ? <p className="ir-confirmed" role="status">{lastSuccess}</p> : null}
    {primaryResultVerified ? <CompanyResearchResult onOpenArtifact={openResearchArtifact} run={run} verifiedRevision={activeFrozenRevision} /> : <section className="ir-result-gate" role="status"><h2>{frozenVerification === "failed" ? "冻结版本验证失败" : "正在验证冻结版本"}</h2><p>{frozenVerification === "failed" ? "未验证的当前工作区结果已隐藏，请重新查看冻结版本。" : "正在核对不可变版本与全部制品哈希，完成前不会展示结果。"}</p></section>}
    <CompanyResearchProcess
      onProcessToggle={() => void toggleProcess()}
      onRetry={() => void retryPreparation()}
      processExpanded={processExpanded}
      processLoading={processLoading}
      retrying={retrying}
      run={run}
    />
    {currentPublicationAction ? <PublicationPanel
      action={currentPublicationAction}
      busy={mutationBusy}
      criticalInputs={run.critical_inputs}
      frozenRevision={activeFrozenRevision}
      memoMarkdown={memoMarkdown}
      mutation={publicationMutation}
      onClosePreview={() => { setPublicationPreview(null); if (previewTriggerRef.current) restoreFocus(previewTriggerRef.current); }}
      onConfirm={(button) => void confirmJudgment(button)}
      onExport={(button) => void exportRevision(button)}
      onMemoChange={(value) => currentMemo && setMemoEditor({ memoId: currentMemo.id, value })}
      onOpenCriticalInputs={openCriticalInputs}
      onPreview={(button) => void previewPublication(button)}
      onPublish={(button) => void publishRevision(button)}
      onReplay={(button) => void replayRevision(button)}
      preview={activePreview}
      workspace={workspace}
    /> : null}
    {activeCriticalInputs ? <CriticalInputDrawer
      busy={criticalInputMutation !== null}
      criticalInputs={activeCriticalInputs}
      error={criticalInputError}
      focusInputKey={criticalInputDrawer?.focusInputKey}
      key={activeCriticalInputs.artifact_id}
      onClose={() => { if (criticalInputMutation === null) { setCriticalInputDrawer(null); setCriticalInputError(null); } }}
      onDecide={(request) => void decideCriticalInput(request)}
      returnFocusTo={criticalInputTriggerRef.current}
    /> : null}
    <details className="ir-research-basis" id="company-research-basis" open>
      <summary>研究依据</summary>
      <div aria-label="研究依据" role="group">
        <div className="ir-workbench-grid"><div className="ir-module-nav" aria-label="详细研究模块">{COMPANY_RESEARCH_MODULES.map((definition) => { const module = workspace.modules.find((item) => item.key === definition.key); return <button aria-current={activeModule === definition.key ? "page" : undefined} className={activeModule === definition.key ? "is-active" : ""} id={`research-module-${definition.key}`} key={definition.key} onClick={() => setActiveModule(definition.key)} type="button"><span>{definition.label}</span><small>{module ? MODULE_STATE_LABELS[module.state] : "未开始"}</small></button>; })}</div>
          <section className="ir-module-content" aria-live="polite"><header><p className="ir-eyebrow">Company research module</p><h2>{activeDefinition.label}</h2></header>{frozenWorkspaceIsBound ? <>{activeFrozenRevision ? <p>冻结版本 {activeFrozenRevision.id}</p> : null}<ModulePanel activeModule={activeModule} moduleState={activeServerModule?.state ?? "not_started"} valuationState={activeServerModule?.valuation_state ?? "not_applicable"} workspace={workspace} reviewLocked={mutationBusy} onReview={reviewFact} lastSuccess={lastSuccess} /></> : <EmptyModule message="冻结版本尚未验证；当前工作区模块内容已隐藏。" />}</section>
          <aside className="ir-boundary" aria-label="研究状态摘要"><p className="ir-eyebrow">Research state</p><h2>准备状态</h2><dl><div><dt>来源</dt><dd>{workspace.source_count}</dd></div><div><dt>缺口</dt><dd>{workspace.gap_count}</dd></div><div><dt>已审核事实</dt><dd>{workspace.change_summary.reviewed_fact_count}</dd></div></dl><details id="audit-details"><summary>审计详情</summary><dl><div><dt>Project</dt><dd>{workspace.project_id}</dd></div><div><dt>Preparation</dt><dd>{workspace.preparation.id}</dd></div><div><dt>Draft</dt><dd>{workspace.draft.id}</dd></div><div><dt>Selected revision</dt><dd>{workspace.selected_revision ?? "尚未选择冻结版本"}</dd></div></dl>{Object.entries(workspace.change_summary.artifact_versions).map(([kind, version]) => <span id={`audit-${encodeURIComponent(kind)}`} key={kind}>{kind} v{version}</span>)}</details></aside>
        </div>
      </div>
    </details>
  </main>;
}
