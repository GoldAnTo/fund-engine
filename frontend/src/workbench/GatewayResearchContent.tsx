import { useCallback, useEffect, useId, useRef, useState, type ReactElement } from "react";
import { GatewayHttpError, GatewaySchemaError } from "@/gateway/HttpGatewayClient";
import type { GatewayConnectionState, GatewayConversationClient, GatewayExecutionTask, GatewayRun } from "@/gateway/contracts";
import { assertEvidenceMatchesSummary, type GatewayAutomaticResult, type GatewayEvidenceSummary, type GatewayEvidenceDetail } from "@/gateway/researchContent";
import type { TeamCitation, TeamOutput } from "@/gateway/team";
import { GatewayExecutionPanel } from "./GatewayExecutionPanel";
import "./GatewayResearchContent.css";
import { GatewayAssessmentReview } from "./GatewayAssessmentReview";

type Props = {
  client: GatewayConversationClient; conversationId: string; run: GatewayRun;
  connection: GatewayConnectionState; refreshSequence: number; authorizedArtifactsKey: string; onAuthorizationLost: () => void;
  onSourceAccessInvalidated?: () => void;
  citationRequest?: { citation: TeamCitation; output: TeamOutput; focusReturn: HTMLElement | null; key: number } | null;
  renderLayout?: (panels: { nativeContent: ReactElement; evidence: ReactElement; evidenceRequestKey: number }) => ReactElement;
  evidenceRoles?: Record<string, string[]>;
  roleEvidenceRequest?: { key: number; label: string; ids: string[] } | null;
};
type ReadState<T> = { kind: "loading" } | { kind: "available"; data: T; refreshing?: boolean } | { kind: "error"; schema: boolean };
type Tab = "process" | "result";
const tabNames: Record<Tab, string> = { process: "执行过程", result: "研究结果" };
const terminal = new Set(["succeeded", "failed", "cancelled"]);
const resultStates = {
  pending: ["研究结果尚未生成", "仍在执行或等待可用证据。可以查看执行过程与已准入材料；重新读取不会重启研究。"],
  withheld: ["当前结果暂不可展示", "结果未通过当前访问或展示条件检查；未展示内容不能当作研究结论。"],
  failed: ["本轮执行失败", "没有可交付的研究结论。请查看任务历史中的异常说明与下一步建议。"],
  cancelled: ["本轮执行已取消", "本轮已停止，未完成的内容不构成研究结论。"],
} as const;
const reasonLabels: Record<string, string> = {
  source_unavailable: "来源暂不可用", source_policy_blocked: "来源不符合策略", parsing_failed: "材料解析未成功",
  evidence_not_admitted: "材料未能准入为证据", source_version_conflict: "来源版本存在冲突", processing_error: "任务处理异常",
};
const actionLabels: Record<string, string> = {
  wait_for_retry: "等待后台安排的重试；此处不会重新启动任务。", check_source_policy: "检查本轮来源策略与来源授权。",
  review_sources: "复核来源材料与研究范围，必要时补充材料并创建新一轮。", check_execution: "检查后台执行状态与任务异常。",
};

function usePrivateRead<T>(load: (signal: AbortSignal) => Promise<T>, revision: string, onUnauthorized: () => void, coalesce = false, disclosureKey = "") {
  const [stored, setStored] = useState<{ disclosureKey: string; state: ReadState<T> }>({ disclosureKey, state: { kind: "loading" } });
  const [attempt, setAttempt] = useState(0);
  const disclosureKeyRef = useRef(disclosureKey);
  const trigger = `${revision}/${attempt}/${disclosureKey}`;
  const previousTrigger = useRef(trigger);
  const refreshRef = useRef<() => void>(() => undefined);
  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;
    let timer: number | undefined;
    let inFlight = false;
    let queued = false;
    function start() {
      if (!active) return;
      inFlight = true;
      queued = false;
      const requestController = new AbortController();
      controller = requestController;
      const requestKey = disclosureKeyRef.current;
      setStored((previous) => previous.disclosureKey === requestKey && previous.state.kind === "available"
        ? { disclosureKey: requestKey, state: { ...previous.state, refreshing: true } }
        : { disclosureKey: requestKey, state: { kind: "loading" } });
      void Promise.resolve().then(() => {
        if (!active || requestController.signal.aborted) throw new DOMException("Read cancelled", "AbortError");
        return load(requestController.signal);
      }).then((data) => {
        if (active && !requestController.signal.aborted) setStored({ disclosureKey: requestKey, state: { kind: "available", data } });
      }).catch((error: unknown) => {
        if (!active || requestController.signal.aborted) return;
        setStored({ disclosureKey: requestKey, state: { kind: "error", schema: error instanceof GatewaySchemaError } });
        if (error instanceof GatewayHttpError && (error.status === 401 || error.status === 403)) {
          queued = false;
          onUnauthorized();
        }
      }).finally(() => {
        inFlight = false;
        if (active && queued) timer = window.setTimeout(start, coalesce ? 250 : 0);
      });
    }
    refreshRef.current = () => {
      queued = true;
      if (inFlight) return;
      window.clearTimeout(timer);
      timer = window.setTimeout(start, coalesce ? 250 : 0);
    };
    timer = window.setTimeout(start, 0);
    return () => { active = false; controller?.abort(); window.clearTimeout(timer); };
  }, [load, onUnauthorized, coalesce]);
  useEffect(() => {
    disclosureKeyRef.current = disclosureKey;
    if (previousTrigger.current !== trigger) { previousTrigger.current = trigger; refreshRef.current(); }
  }, [trigger, disclosureKey]);
  // A changed authorization projection hides old private data during render,
  // even when its sequence is unchanged and the replacement read is slow.
  const state: ReadState<T> = stored.disclosureKey === disclosureKey ? stored.state : { kind: "loading" };
  return { state, retry: () => setAttempt((value) => value + 1) };
}

function safeSourceUrl(value: string | null): string | null {
  if (!value || /[\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
function SourceLink({ url, children }: { url: string | null; children: string }): ReactElement {
  const href = safeSourceUrl(url);
  return href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}<span aria-hidden="true"> ↗</span></a>
    : <span>{children} · {url ? "链接不可安全打开" : "未提供链接"}</span>;
}
function authorityLabel(value: string): string {
  if (["user_supplied", "user_material", "intake_material"].includes(value)) return "用户材料 · 待独立核验";
  if (value === "licensed_research") return "授权研报 · 非人工审核结论";
  if (value === "primary_disclosure" || value === "official_disclosure") return "官方披露 · 仍需核对期间与口径";
  return "未确认来源类别";
}
function directionLabel(value: string): string {
  if (["support", "supports"].includes(value)) return "支持方向检索";
  if (["contradict", "contradicts", "counter_evidence"].includes(value)) return "反证方向检索";
  if (["alternative", "alternative_explanation"].includes(value)) return "替代解释方向检索";
  return "关系待复核";
}
function dateLabel(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未提供";
}
function ReadNotice({ state, label, retry }: { state: Exclude<ReadState<unknown>, { kind: "available" }>; label: string; retry: () => void }) {
  return <div className="research-read-notice" role={state.kind === "error" ? "alert" : "status"}>
    <p>{state.kind === "loading" ? `正在读取${label}…` : state.schema ? `${label}校验未通过，已隐藏不一致内容。` : `${label}暂时无法读取，请检查连接后重试。`}</p>
    {state.kind === "error" ? <button type="button" onClick={retry}>重新读取{label}</button> : null}
  </div>;
}

async function readSourceScoped<T>(read: () => Promise<T>, signal: AbortSignal, onSourceUnavailable: () => void): Promise<T> {
  try { return await read(); }
  catch (error) {
    // Both quote and task-history reads conceal source-contract revocation
    // behind 404. Neither can leave the containing reader's old data visible.
    if (!signal.aborted && error instanceof GatewayHttpError && error.status === 404) onSourceUnavailable();
    throw error;
  }
}

/** The keyed boundary removes every old detail synchronously on a route/run switch. */
export function GatewayResearchContent(props: Props): ReactElement {
  return <ScopedResearchContent key={`${props.conversationId}/${props.run.runSpecId}`} {...props} />;
}

function ScopedResearchContent({ client, conversationId, run, connection, refreshSequence, authorizedArtifactsKey, onAuthorizationLost, onSourceAccessInvalidated, citationRequest, renderLayout, evidenceRoles, roleEvidenceRequest }: Props): ReactElement {
  const id = useId();
  const ended = terminal.has(run.status);
  const [selection, setSelection] = useState<{ ended: boolean; tab: Tab } | null>(null);
  const tab = selection?.ended === ended ? selection.tab : ended ? "result" : "process";
  const [selected, setSelected] = useState<{ id: string; accessKey: string } | null>(null);
  const [taskFilter, setTaskFilter] = useState<string | null>(null);
  const [assessmentFilter, setAssessmentFilter] = useState<{ label: string; ids: string[]; accessKey: string; kind?: "role" } | null>(null);
  const evidenceAside = useRef<HTMLElement>(null);
  const central = useRef<HTMLDivElement>(null);
  const evidenceTrigger = useRef<HTMLElement | null>(null);
  const [redacted, setRedacted] = useState(false);
  const [sourceAccessRevision, setSourceAccessRevision] = useState(0);
  const [evidenceRequestKey, setEvidenceRequestKey] = useState(0);
  const contentAccessKey = JSON.stringify([authorizedArtifactsKey, sourceAccessRevision]);
  const revalidateSources = useCallback(() => {
    // A source-specific 404 can conceal revocation. Invalidate all readers for
    // this run, including team outputs derived from its authorized sources.
    setSelected(null);
    setSourceAccessRevision((value) => value + 1);
    onSourceAccessInvalidated?.();
  }, [onSourceAccessInvalidated]);
  const unauthorized = useCallback(() => {
    setRedacted(true);
    setSelected(null);
    onAuthorizationLost();
  }, [onAuthorizationLost]);
  const runSpecId = run.runSpecId;
  const load = useCallback((signal: AbortSignal) => client.getResearch(conversationId, runSpecId, signal), [client, conversationId, runSpecId]);
  const revision = JSON.stringify([run.status, refreshSequence,
    run.execution?.tasks.reduce((count, task) => count + task.counts.admitted, 0)]);
  const reading = usePrivateRead(load, revision, unauthorized, true, contentAccessKey);
  const data = reading.state.kind === "available" ? reading.state.data : null;
  const evidence = data?.evidence ?? [];
  const selectedEvidence = selected?.accessKey === contentAccessKey ? evidence.find((entry) => entry.evidenceLinkId === selected.id) : undefined;

  function chooseTab(next: Tab) { setSelection({ ended, tab: next }); if (central.current) central.current.scrollTop = 0; }
  const currentAssessment = assessmentFilter?.accessKey === contentAccessKey ? assessmentFilter : null;
  function rememberTrigger() { evidenceTrigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; }
  function focusAside() { evidenceAside.current?.focus({ preventScroll: true }); if (window.matchMedia?.("(max-width: 760px)").matches) evidenceAside.current?.scrollIntoView({ block: "start" }); }
  function focusExistingOriginal(entry: GatewayEvidenceSummary) {
    if (selectedEvidence?.evidenceLinkId !== entry.evidenceLinkId) return;
    const heading = evidenceAside.current?.querySelector<HTMLElement>(".research-evidence-original h3");
    heading?.focus({ preventScroll: true });
    if (evidenceAside.current) evidenceAside.current.scrollTop = 0;
    if (window.matchMedia?.("(max-width: 760px)").matches) heading?.scrollIntoView({ block: "start" });
  }
  function showEvidence(taskId: string | null = null) {
    setEvidenceRequestKey((value) => value + 1);
    rememberTrigger(); setTaskFilter(taskId); setAssessmentFilter(null);
    const entry = taskId ? evidence.find((item) => item.taskId === taskId) : undefined;
    setSelected(entry ? { id: entry.evidenceLinkId, accessKey: contentAccessKey } : null); focusAside(); if (entry) focusExistingOriginal(entry);
  }
  function openEvidence(entry: GatewayEvidenceSummary) { setEvidenceRequestKey((value) => value + 1); rememberTrigger(); setSelected({ id: entry.evidenceLinkId, accessKey: contentAccessKey }); focusExistingOriginal(entry); }
  function closeEvidence() { setSelected(null); const target = evidenceTrigger.current?.isConnected ? evidenceTrigger.current : document.getElementById(`${id}-${tab}-tab`); target?.focus({ preventScroll: true }); if (window.matchMedia?.("(max-width: 760px)").matches) target?.scrollIntoView({ block: "center" }); }
  const visibleEvidence = taskFilter ? evidence.filter((entry) => entry.taskId === taskFilter) : currentAssessment ? evidence.filter((entry) => currentAssessment.ids.includes(entry.evidenceLinkId)) : evidence;
  useEffect(() => {
    if (!citationRequest || reading.state.kind !== "available") return;
    setEvidenceRequestKey((value) => value + 1);
    const entry = evidence.find((item) => item.evidenceLinkId === citationRequest.citation.evidence_link_id);
    evidenceTrigger.current = citationRequest.focusReturn;
    setTaskFilter(null);
    setAssessmentFilter(null);
    setSelected(entry ? { id: entry.evidenceLinkId, accessKey: contentAccessKey } : null);
    focusAside();
    if (entry) focusExistingOriginal(entry);
  }, [citationRequest?.key, reading.state.kind, contentAccessKey]);
  useEffect(() => {
    if (!roleEvidenceRequest) {
      setAssessmentFilter((current) => current?.kind === "role" ? null : current);
      return;
    }
    if (reading.state.kind !== "available") return;
    setTaskFilter(null);
    setSelected(null);
    setAssessmentFilter({ ...roleEvidenceRequest, kind: "role", accessKey: contentAccessKey });
    setEvidenceRequestKey((value) => value + 1);
  }, [roleEvidenceRequest?.key, reading.state.kind, contentAccessKey]);
  if (redacted || connection === "access_revoked") return <p role="alert">访问权限已失效，已清除研究内容。</p>;

  const nativeContent = <div ref={central} className="research-desk-central">
    <div className="research-reader-tabs" role="tablist" aria-label="研究阅读视图">
      {(["process", "result"] as const).map((name) => <button key={name} type="button" role="tab"
        id={`${id}-${name}-tab`} aria-controls={`${id}-${name}-panel`} aria-selected={tab === name}
        tabIndex={tab === name ? 0 : -1} onClick={() => chooseTab(name)}
        onKeyDown={(event) => {
          const names: Tab[] = ["process", "result"];
          const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
          const next = event.key === "Home" ? "process" : event.key === "End" ? "result" : offset ? names[(names.indexOf(name) + offset + names.length) % names.length] : undefined;
          if (next) { event.preventDefault(); chooseTab(next); document.getElementById(`${id}-${next}-tab`)?.focus(); }
        }}>{tabNames[name]}</button>)}
    </div>
    <div id={`${id}-${tab}-panel`} role="tabpanel" aria-labelledby={`${id}-${tab}-tab`} className="research-reader-panel">
      {sourceAccessRevision > 0 ? <p className="research-integrity-note" role="status">{reading.state.kind === "available"
        ? "已按当前权限重新读取；旧原文已关闭。"
        : "来源内容不可用，已隐藏本轮旧内容并重新校验访问权限。"}</p> : null}
      {reading.state.kind === "available" && reading.state.refreshing ? <p className="research-evidence-total" role="status">正在更新研究内容，以下保留上次成功读取的授权内容。</p> : null}
      {sourceAccessRevision > 0 && reading.state.kind !== "available" ? <ReadNotice state={reading.state} label="研究内容" retry={reading.retry} /> : tab === "process" ? <>
        <GatewayExecutionPanel run={run} connection={connection} renderTaskDetails={(task) => <TaskHistory key={contentAccessKey}
          client={client} conversationId={conversationId} runSpecId={runSpecId} task={task} onUnauthorized={unauthorized}
          onSourceUnavailable={revalidateSources}
          evidenceCount={evidence.filter((entry) => entry.taskId === task.taskId).length} onEvidence={() => showEvidence(task.taskId)} />} />
        {!run.execution ? <p className="research-read-notice">本轮暂无可展示的任务明细，请参阅下方执行阶段记录。</p> : null}
      </> : reading.state.kind !== "available" ? <ReadNotice state={reading.state} label="研究内容" retry={reading.retry} /> : <>
        <div className="research-result-heading"><h2>研究结果</h2><button className="research-primary-action" type="button" onClick={() => showEvidence()}>查看证据 / 原文溯源</button></div>
        <p className="research-review-label">系统生成，未经人工审核</p>
        <details className="research-reading-rules"><summary>阅读边界与限制{data!.warnings.length > 0 ? `（${data!.warnings.length} 项来源提示）` : ""}</summary>
          <p>请复核来源、数据期与口径。支持、反证等检索方向不等于已证实的反证或支持关系；自动准入也不等于人工核验。</p>
          <p>论点归组不代表逐句引用映射。分项关联保留实际评估输入；质量检查是只读提示，不改写原始判断与报告。</p>
          {data!.warnings.length > 0 ? <ul>{data!.warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul> : null}
        </details>
        {data!.result ? <GatewayAssessmentReview review={data!.assessmentReview} evidence={evidence} selectedEvidenceId={selectedEvidence?.evidenceLinkId} onOpen={(entry, context) => {
          setTaskFilter(null); setAssessmentFilter(context ? { ...context, accessKey: contentAccessKey } : null); openEvidence(entry);
        }}>
          <ResultBody result={data!.result} />
        </GatewayAssessmentReview> : <div className="research-result-state" role="status">
          <h3>{resultStates[data!.state as keyof typeof resultStates][0]}</h3><p>{resultStates[data!.state as keyof typeof resultStates][1]}</p>
        </div>}
      </>}
    </div>
    </div>;
  const evidenceContent = <aside ref={evidenceAside} tabIndex={-1} className="research-reader-panel research-desk-evidence" aria-label="证据与来源">
        <div className="research-result-heading"><h2>证据与来源</h2>{reading.state.kind === "available" ? <button type="button" onClick={reading.retry}>重新读取研究内容</button> : null}</div>
        {reading.state.kind !== "available" ? (tab === "process" && sourceAccessRevision === 0 ? <ReadNotice state={reading.state} label="研究内容" retry={reading.retry} /> : <p>{reading.state.kind === "error" ? "证据暂不可读取，请在中央内容区重新读取。" : "证据将在研究内容读取完成后显示。"}</p>) : <>
        {selectedEvidence ? <EvidenceOriginal key={selectedEvidence.evidenceLinkId} client={client} conversationId={conversationId} runSpecId={runSpecId}
          evidence={selectedEvidence} onUnauthorized={unauthorized} onSourceUnavailable={revalidateSources} onClose={closeEvidence} /> : <p className="research-read-notice">选择材料查看原文</p>}
        {currentAssessment ? <p className="research-integrity-note">{currentAssessment.kind === "role" ? `当前角色：${currentAssessment.label}。以下为该产出关联的已授权证据。` : `所选判断：${currentAssessment.label}。以下材料是实际评估输入，不代表逐句验证。`}</p> : null}
        {tab === "process" ? <details className="research-reading-rules"><summary>证据关系说明</summary><p>以下按研究论点归组，不代表逐句引用映射。关系来自检索任务方向，内容是否真正支持或反驳仍需复核。</p></details> : null}
        <p className="research-evidence-total">当前展示 {visibleEvidence.length} / {data!.totalEvidence} 条自动准入记录 · 未经人工审核</p>
        {data!.truncated ? <p role="status">证据列表已截断，仅展示前 200 条；不能据此判断完整证据覆盖。</p> : null}
        {taskFilter || currentAssessment ? <div className="research-filter"><span>{taskFilter ? `仅显示所选任务关联证据 · ${run.execution?.tasks.find((task) => task.taskId === taskFilter)?.sourceName ?? "当前无可定位任务"}` : currentAssessment?.kind === "role" ? "仅显示所选角色关联证据" : "仅显示所选判断的实际输入"}</span><button type="button" onClick={() => { setTaskFilter(null); setAssessmentFilter(null); }}>显示全部论点证据</button></div> : null}
        {renderLayout ? <EvidenceCards idPrefix={id} evidence={visibleEvidence} onOpen={openEvidence} roles={evidenceRoles} />
          : <EvidenceGroups idPrefix={id} taskIds={run.execution?.tasks.map((task) => task.taskId) ?? []} evidence={visibleEvidence} onOpen={openEvidence} />}
      </>}
    </aside>;
  // Both panes share one private reader and one authorization boundary.
  // Layout changes must never introduce a second copy of the evidence loader.
  return renderLayout ? renderLayout({ nativeContent, evidence: evidenceContent, evidenceRequestKey })
    : <section className="gateway-research-reader" aria-label="研究内容阅读区">{nativeContent}{evidenceContent}</section>;
}

function EvidenceCards({ idPrefix, evidence, onOpen, roles }: { idPrefix: string; evidence: GatewayEvidenceSummary[]; onOpen: (entry: GatewayEvidenceSummary) => void; roles?: Record<string, string[]> }): ReactElement {
  if (!evidence.length) return <p className="research-read-notice">尚无可展示的已准入证据。资料采集完成后，可在这里核对来源与原文。</p>;
  return <div className="gateway-evidence-cards">{evidence.map((entry) => <article key={entry.evidenceLinkId}>
    <h3>{entry.title || "未命名材料"}</h3>
    <small>{entry.publishedAt ? new Date(entry.publishedAt).toLocaleDateString("zh-CN") : "发布日期未提供"} · {authorityLabel(entry.sourceAuthority)}</small>
    <p className="gateway-evidence-excerpt">{entry.statement}</p>
    <footer><span>{directionLabel(entry.relationship)}</span><button id={`${idPrefix}-evidence-${entry.evidenceLinkId}`} type="button" aria-label={`查看原文：${entry.title || "未命名材料"}`} onClick={() => onOpen(entry)}>查看原文 ↗</button></footer>
    <div className="gateway-evidence-owners">关联角色：{roles?.[entry.evidenceLinkId]?.join("、") || "尚无专业角色引用"}</div>
  </article>)}</div>;
}

function ResultBody({ result }: { result: GatewayAutomaticResult }): ReactElement {
  return <article className="research-result-body">
    <section><h3>结论</h3><p className="research-conclusion">{result.conclusion || "未提供结论正文。"}</p></section>
    <ResultList title="关键发现" items={result.keyFindings} empty="没有可展示的关键发现。" />
    <ResultList title="反向材料与缺口" items={result.counterEvidence} empty="没有可展示的反向材料；不表示不存在反证。" />
    <ResultList title="局限与待复核项" items={result.limitations} empty="未列出具体局限，不代表证据充分或结论已经确认。" />
    <section><h3>报告列示来源</h3>{result.sources.length ? <ul>{result.sources.map((source, index) => <li key={index}>
      <SourceLink url={source.url}>{source.title ?? "未命名来源"}</SourceLink><small> · {directionLabel(source.role)} · 自动准入</small>
    </li>)}</ul> : <p>报告未列示来源，请查看证据与来源中的可追溯记录。</p>}</section>
  </article>;
}
function ResultList({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return <section><h3>{title}</h3>{items.length ? <ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul> : <p>{empty}</p>}</section>;
}
function EvidenceGroups({ idPrefix, evidence, taskIds, onOpen }: { idPrefix: string; evidence: GatewayEvidenceSummary[]; taskIds: string[]; onOpen: (entry: GatewayEvidenceSummary) => void }) {
  const groups = new Map<string, GatewayEvidenceSummary[]>();
  for (const entry of evidence) { const group = groups.get(entry.thesisId); if (group) group.push(entry); else groups.set(entry.thesisId, [entry]); }
  if (!evidence.length) return <p className="research-read-notice">尚无可展示的已准入证据。已发现、读取或冻结的材料不等于可用证据。</p>;
  return <div className="research-evidence-groups">{[...groups].map(([thesisId, entries]) => <section key={thesisId} className="research-evidence-group">
    <h3>{entries[0]!.thesisStatement || "未提供论点描述"}</h3>
    <ul>{entries.map((entry) => <li key={entry.evidenceLinkId}>
      <p>{entry.statement}</p><div className="research-evidence-meta"><span>{authorityLabel(entry.sourceAuthority)}</span><span>数据期：{entry.observedPeriod ?? "未提供"}</span><span>{directionLabel(entry.relationship)}</span></div>
      {!taskIds.includes(entry.taskId) ? <p className="research-evidence-total">没有可定位的当前资料任务</p> : null}
      <button id={`${idPrefix}-evidence-${entry.evidenceLinkId}`} type="button" onClick={() => onOpen(entry)}>查看原文：{entry.title || "未命名材料"}</button>
    </li>)}</ul>
  </section>)}</div>;
}
function EvidenceOriginal({ client, conversationId, runSpecId, evidence, onUnauthorized, onSourceUnavailable, onClose }: {
  client: GatewayConversationClient; conversationId: string; runSpecId: string; evidence: GatewayEvidenceSummary; onUnauthorized: () => void; onSourceUnavailable: () => void; onClose: () => void;
}) {
  const { evidenceLinkId, taskId, thesisId, documentVersionId } = evidence;
  const load = useCallback(async (signal: AbortSignal) => {
    const detail = await readSourceScoped(() => client.getEvidenceDetail(conversationId, runSpecId, evidenceLinkId, signal), signal, onSourceUnavailable);
    assertEvidenceMatchesSummary(detail, { evidenceLinkId, taskId, thesisId, documentVersionId });
    return detail;
  }, [client, conversationId, runSpecId, evidenceLinkId, taskId, thesisId, documentVersionId, onSourceUnavailable]);
  const reading = usePrivateRead(load, "detail", onUnauthorized);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); heading.current?.closest(".research-desk-evidence")?.scrollTo?.({ top: 0 }); if (window.matchMedia?.("(max-width: 760px)").matches) heading.current?.scrollIntoView({ block: "start" }); }, []);
  return <section className="research-evidence-original" role="region" aria-label="证据原文">
    <header><h3 tabIndex={-1} ref={heading}>证据原文</h3><button type="button" onClick={onClose}>关闭原文</button></header>
    {reading.state.kind !== "available" ? <ReadNotice state={reading.state} label="证据原文" retry={reading.retry} /> : <OriginalBody detail={reading.state.data} />}
  </section>;
}
function OriginalBody({ detail }: { detail: GatewayEvidenceDetail }) {
  return <>
    <p className="research-review-label">{authorityLabel(detail.sourceAuthority)} · 自动准入，未经人工审核</p>
    <h4>{detail.title}</h4><blockquote>{detail.quote}</blockquote>
    <dl className="research-source-facts">
      <div><dt>来源发布日期</dt><dd>{dateLabel(detail.publishedAt)}</dd></div>
      <div><dt>数据期</dt><dd>{detail.observedPeriod ?? "未提供"}</dd></div>
      <div><dt>材料获取时间</dt><dd>{dateLabel(detail.acquiredAt)}</dd></div>
      <div><dt>冻结材料定位</dt><dd>{detail.locator.page !== null ? `冻结材料第 ${detail.locator.page} 页` : "未提供页码"}
        {detail.locator.paragraph !== null ? ` · 解析器段落编号：${detail.locator.paragraph}` : " · 未提供段落编号"}</dd></div>
    </dl>
    {["user_supplied", "user_material", "intake_material"].includes(detail.sourceAuthority) ? <p className="research-integrity-note">此定位指向冻结的用户材料，不是材料文字声称的报告页码；仍需独立核对原始报告。</p> : null}
    <SourceLink url={detail.sourceUrl}>打开原始来源</SourceLink>
    <details className="research-identifiers"><summary>核对冻结版本与证据标识</summary><dl>
      <div><dt>内容 SHA-256</dt><dd>{detail.contentSha256}</dd></div><div><dt>冻结版本</dt><dd>{detail.documentVersionId}</dd></div>
      <div><dt>原文片段</dt><dd>{detail.sourceSpanId}</dd></div><div><dt>证据标识</dt><dd>{detail.evidenceLinkId}</dd></div>
      <div><dt>关联任务</dt><dd>{detail.taskId}</dd></div>
    </dl></details>
  </>;
}
function TaskHistory({ client, conversationId, runSpecId, task, evidenceCount, onEvidence, onUnauthorized, onSourceUnavailable }: {
  client: GatewayConversationClient; conversationId: string; runSpecId: string; task: GatewayExecutionTask;
  evidenceCount: number; onEvidence: () => void; onUnauthorized: () => void; onSourceUnavailable: () => void;
}) {
  const [open, setOpen] = useState(false);
  return <div className="research-task-history"><div className="research-task-actions">
    <button type="button" aria-expanded={open} onClick={() => setOpen((value) => !value)}>{open ? "收起任务历史" : "展开任务历史"}</button>
    <button type="button" onClick={onEvidence}>查看本任务证据（{evidenceCount}）</button>
  </div>{open ? <TaskTrace client={client} conversationId={conversationId} runSpecId={runSpecId} task={task} onUnauthorized={onUnauthorized} onSourceUnavailable={onSourceUnavailable} /> : null}</div>;
}
function TaskTrace({ client, conversationId, runSpecId, task, onUnauthorized, onSourceUnavailable }: {
  client: GatewayConversationClient; conversationId: string; runSpecId: string; task: GatewayExecutionTask; onUnauthorized: () => void; onSourceUnavailable: () => void;
}) {
  const taskId = task.taskId;
  const load = useCallback((signal: AbortSignal) => readSourceScoped(
    () => client.getTaskTrace(conversationId, runSpecId, taskId, signal), signal, onSourceUnavailable,
  ), [client, conversationId, runSpecId, taskId, onSourceUnavailable]);
  const reading = usePrivateRead(load, JSON.stringify([task.stage, task.status, task.attempt, task.counts.exceptions]), onUnauthorized, true);
  if (reading.state.kind !== "available") return <ReadNotice state={reading.state} label="任务历史" retry={reading.retry} />;
  const trace = reading.state.data;
  return <section aria-label="任务历史" className="research-task-trace">
    <p className="research-task-id">关联任务：{taskId}</p>
    {trace.events.length ? <ol>{[...trace.events].sort((left, right) => left.sequence - right.sequence).map((event) => <li key={event.sequence}><time dateTime={event.occurredAt}>{dateLabel(event.occurredAt)}</time><span>{event.label}</span></li>)}</ol> : <p>尚无可展示的阶段记录。</p>}
    {trace.exceptions.map((exception, index) => <div className="research-trace-exception" key={index}>
      <h4>{reasonLabels[exception.reasonCode] ?? "任务异常待复核"} · 发生 {exception.count} 次</h4>
      <p>{exception.message}</p><p>{actionLabels[exception.nextAction] ?? "检查任务状态与研究范围后再决定下一步。"}</p>
    </div>)}
    {trace.exceptions.length === 0 ? <p>当前记录未报告异常；不代表来源已通过独立核验。</p> : null}
    {trace.truncated ? <p role="status">历史记录已截断，当前并非完整执行历史。</p> : null}
    <button type="button" onClick={reading.retry}>重新读取任务历史</button>
  </section>;
}
