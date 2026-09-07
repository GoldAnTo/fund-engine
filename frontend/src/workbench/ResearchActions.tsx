import { CaseAIUsage } from "./RunAIUsage";
import { AtomicClaimQueue } from "./AtomicClaimQueue";
import { ResearchDocuments } from "./ResearchDocuments";
import { MonitorPanel } from "./MonitorPanel";
import { EvidenceReviewPanel } from "./EvidenceReviewPanel";
import { ResearchRunPanel } from "./ResearchRunPanel";
import { researchRunApi } from "@/data/researchRunApi";
import { PreparationReviewPanel } from "./PreparationReviewPanel";
import { researchLabel } from "./researchPresentation";
import { useEffect, useRef, useState } from "react";
import { planItems, researchActionsApi, type PreparationData, type PreparationReview, type RunData } from "@/data/researchActionsApi";

/** Mounted with a case key: draft and write lifecycle never migrate to another case. */
export function ResearchActions({ caseId, published, hasPreparation, onRefresh }: { caseId: string; published: boolean; hasPreparation: boolean; onRefresh: () => void }) {
  const [preparation, setPreparation] = useState<PreparationData | null>(null);
  const [runs, setRuns] = useState<RunData[]>([]);
  const [runCursor, setRunCursor] = useState<string | null>(null);
  const [moreRunsBusy, setMoreRunsBusy] = useState(false);
  const [moreRunsError, setMoreRunsError] = useState("");
  const moreRunsRequest = useRef<AbortController>();
  const [readError, setReadError] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [actor, setActor] = useState("");
  const [raw, setRaw] = useState("");
  const [url, setUrl] = useState("");
  const [checked, setChecked] = useState(false);
  const mutation = useRef<AbortController>();
  const authorizationKeys = useRef(new Map<string, string>());
  useEffect(() => () => mutation.current?.abort(), []);
  useEffect(() => {
    moreRunsRequest.current?.abort(); setMoreRunsBusy(false); setMoreRunsError(""); setRunCursor(null);
    const controller = new AbortController(); setLoading(true); setReadError(""); setPreparation(null); setRuns([]); setChecked(false);
    Promise.all([hasPreparation ? researchActionsApi.preparation(caseId, controller.signal) : Promise.resolve(null), researchRunApi.list(caseId, controller.signal)])
      .then(([prep, records]) => { if (!controller.signal.aborted) { setPreparation(prep); setRuns(records.items); setRunCursor(records.has_more ? records.next_cursor : null); } })
      .catch((err: unknown) => { if (!controller.signal.aborted) setReadError(err instanceof Error ? err.message : "读取操作状态失败，请重试。"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); moreRunsRequest.current?.abort(); };
  }, [caseId, hasPreparation, refresh]);
  async function loadMoreRuns() {
    if (!runCursor || moreRunsBusy) return;
    const controller = new AbortController(); moreRunsRequest.current = controller; setMoreRunsBusy(true); setMoreRunsError("");
    try {
      const page = await researchRunApi.list(caseId, controller.signal, runCursor);
      if (!controller.signal.aborted) { setRuns((previous) => [...previous, ...page.items.filter((item) => !previous.some((run) => run.id === item.id))]); setRunCursor(page.has_more ? page.next_cursor : null); }
    } catch (err) { if (!controller.signal.aborted) setMoreRunsError(err instanceof Error ? err.message : "读取更多运行失败。"); }
    finally { if (!controller.signal.aborted) setMoreRunsBusy(false); }
  }
  const plan = preparation ? planItems(preparation) : null;
  const canAuthorize = Boolean(preparation && preparation.status === "awaiting_plan_authorization" && !preparation.research_run_id && preparation.artifacts.protocol?.state === "current" && !preparation.artifacts.protocol.display_withheld && preparation.review.claims?.state === "confirmed" && preparation.review.protocol?.state === "confirmed" && preparation.review.plan?.state === "awaiting_review" && plan);
  async function perform(kind: "material" | "retry" | "authorize" | "review", review?: PreparationReview) {
    if (busy || !actor.trim()) return;
    const controller = new AbortController(); mutation.current = controller; setBusy(true); setError(""); setNotice("");
    try {
      if (kind === "material") {
        await researchActionsApi.material(caseId, { raw_input: raw.trim(), actor: actor.trim(), source_type: "pasted_snapshot", ...(url.trim() ? { source_url: url.trim() } : {}) }, controller.signal);
        if (!controller.signal.aborted) { setRaw(""); setUrl(""); setNotice("材料已保存到研究收件箱，尚未自动启动研究。"); }
      } else if (preparation && kind === "review" && review) {
        if (review.kind === "claims") {
          await researchActionsApi.confirmClaims(caseId, { revision: preparation.revision, actor: actor.trim(), decisions: review.decisions }, controller.signal);
        } else {
          await researchActionsApi.confirmProtocol(caseId, { revision: preparation.revision, actor: actor.trim(), draft_sequence: review.draft_sequence, edits: review.edits }, controller.signal);
        }
        if (!controller.signal.aborted) setNotice(review.kind === "claims" ? "陈述审核已保存，协议准备状态以最新记录为准。" : "研究协议已确认，补证计划仍需单独核对与授权。");
      } else if (preparation && kind === "retry") {
        await researchActionsApi.retry(caseId, { revision: preparation.revision, actor: actor.trim() }, controller.signal);
        if (!controller.signal.aborted) setNotice("准备任务已重新排队，请刷新查看记录状态。");
      } else if (preparation && canAuthorize && checked) {
        const sequence = preparation.artifacts.plan!.sequence;
        const storageKey = `fundclaw:plan:${caseId}:${preparation.revision}:${sequence}:${actor.trim()}`;
        let key = authorizationKeys.current.get(storageKey);
        if (!key) { try { key = sessionStorage.getItem(storageKey) ?? undefined; } catch { /* memory retains retry identity */ } }
        if (!key) { key = crypto.randomUUID(); try { sessionStorage.setItem(storageKey, key); } catch { /* memory retains retry identity */ } }
        authorizationKeys.current.set(storageKey, key);
        await researchActionsApi.authorize(caseId, { revision: preparation.revision, actor: actor.trim(), plan_sequence: sequence, idempotency_key: key }, controller.signal);
        if (!controller.signal.aborted) setNotice("计划已授权，研究任务已排队。实际执行状态以下方记录为准。");
      }
      if (!controller.signal.aborted) { setRefresh((value) => value + 1); onRefresh(); }
    } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "操作失败，请重试。"); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  return <section className="live-research-actions">
    <h2>继续研究</h2>
    <div className="live-create"><label>操作人<input maxLength={128} value={actor} disabled={busy} onChange={(e) => setActor(e.target.value)} /></label></div>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <h3>补充原始材料</h3>
    {published ? <p>已发布研究需通过材料比较与结论决策流程补充资料，此入口尚未接入。</p> : <form className="live-create" onSubmit={(e) => { e.preventDefault(); void perform("material"); }}>
      <fieldset disabled={busy}><label>补充材料原文<textarea required rows={4} value={raw} onChange={(e) => setRaw(e.target.value)} /></label><label>补充材料来源链接（选填）<input type="url" value={url} onChange={(e) => setUrl(e.target.value)} /></label></fieldset>
      <button className="live-secondary" disabled={busy || !raw.trim() || !actor.trim()}>保存补充材料</button>
    </form>}
    <p><a href={`/events/${encodeURIComponent(caseId)}/market`}>打开市场研究与验证</a></p>
    <CaseAIUsage caseId={caseId} />
    <ResearchDocuments key={`documents:${caseId}`} caseId={caseId} />
    <h3>研究准备</h3>
    {loading && <p role="status">正在读取准备与运行状态…</p>}
    {readError && <p role="alert">{readError}</p>}
    {!loading && !readError && !hasPreparation && <p>本研究尚无准备流程记录。</p>}
    {preparation && <><p>准备状态：{researchLabel(preparation.status)} · 版本 {preparation.revision}</p><p>已完成 {preparation.progress.completed_steps} / {preparation.progress.total_steps} 个准备步骤</p>
      <dl className="live-progress">{Object.entries(preparation.system).map(([key, step]) => <div key={key}><dt>{({ claims: "原子陈述", protocol: "研究协议", plan: "补证计划" } as Record<string, string>)[key] ?? key}</dt><dd>{researchLabel(step.state)}</dd></div>)}</dl>
      {Object.values(preparation.system).some((step) => step.state === "failed") && <button className="live-secondary" disabled={busy || !actor.trim()} onClick={() => void perform("retry")}>重试失败准备步骤</button>}
      <PreparationReviewPanel key={`${caseId}:${preparation.revision}:${preparation.artifacts.claims?.sequence}:${preparation.artifacts.protocol?.sequence}`} preparation={preparation} actor={actor} busy={busy} onSubmit={(review) => void perform("review", review)} />
      {preparation.artifacts.protocol?.display_withheld && <p>已审协议暂不可展示，请先解决来源访问限制后再授权。</p>}
      {preparation.artifacts.plan?.display_withheld && <p>计划内容暂不可展示，请先解决来源访问限制。</p>}
      {plan && <><h3>待核对的补证计划</h3><ol className="live-factors">{plan.map((item, index) => <li key={index}><h4>{item.factor}</h4><p>{item.evidence_target}</p><p>来源角色：{item.allowed_source_roles.map(researchLabel).join("、")}</p><p>优先级：{researchLabel(item.priority)} · 预算：{item.budget}</p><p>停止条件：{item.stop_condition}</p></li>)}</ol></>}
      {canAuthorize && <div className="live-authorize"><p>授权后会将计划交给研究执行器，可能使用已配置的数据与模型服务。总任务预算：{plan!.reduce((sum, item) => sum + item.budget, 0)}。</p><label><input type="checkbox" checked={checked} disabled={busy} onChange={(e) => setChecked(e.target.checked)} />我已核对计划范围、来源和预算</label><button className="live-primary" disabled={busy || !checked || !actor.trim()} onClick={() => void perform("authorize")}>授权计划并启动研究</button></div>}
    </>}
    <AtomicClaimQueue key={`atomic:${caseId}`} caseId={caseId} actor={actor} onRefresh={onRefresh} />
    <EvidenceReviewPanel key={caseId} caseId={caseId} actor={actor} onRefresh={onRefresh} />
    <MonitorPanel key={`monitor:${caseId}`} caseId={caseId} actor={actor} onStarted={() => { setRefresh((value) => value + 1); onRefresh(); }} />
    <h3>最近研究运行</h3>
    {!loading && !readError && !runs.length && <p>暂无运行记录。</p>}
    <ul className="live-run-list">{runs.map((run) => <ResearchRunPanel key={`${caseId}:${run.id}`} caseId={caseId} run={run} actor={actor} onRefresh={onRefresh} />)}</ul>
    {moreRunsError && <p role="alert">{moreRunsError}</p>}
    {runCursor && <button className="live-secondary" disabled={loading || moreRunsBusy} onClick={() => void loadMoreRuns()}>{moreRunsBusy ? "正在读取更多运行…" : "加载更早运行"}</button>}
    <button className="live-secondary" disabled={busy || loading} onClick={() => setRefresh((value) => value + 1)}>刷新准备与运行</button>
  </section>;
}
