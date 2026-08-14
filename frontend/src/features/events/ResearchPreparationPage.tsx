import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import {
  ConflictError,
  type ResearchPreparation,
  type ResearchPreparationEvent,
  type ResearchPreparationEventStep,
  type ResearchPreparationStatus,
} from "../../domain/researchPreparation";

const ACTOR = "human:researcher";

type ClaimDecision = {
  outcome: "" | "confirmed" | "modified" | "rejected";
  reason: string;
  normalizedText: string;
};

type Candidate = { id: string; text: string };

const STATUS_COPY: Record<ResearchPreparationStatus, { title: string; detail: string }> = {
  preparing: { title: "系统正在准备", detail: "系统正在解析冻结原文、起草协议与补证计划。准备完成后才会出现需要你确认的事项。" },
  awaiting_claim_review: { title: "核对候选陈述", detail: "逐项核对系统从冻结原文中拆出的候选陈述。确认并不启动正式研究。" },
  awaiting_protocol_confirmation: { title: "确认研究协议", detail: "核对研究问题、验证边界与反证规则。协议确认后，仍须单独授权补证计划。" },
  awaiting_plan_authorization: { title: "授权补证计划", detail: "这是启动正式研究前的最后一步。仅授权这一份已审阅的计划。" },
  recoverable_failure: { title: "恢复准备工作", detail: "后台准备未完成，尚未启动正式研究。确认后可重新排队准备任务。" },
  authorized: { title: "正式研究已创建", detail: "研究协议与补证计划已由研究员确认，正式研究运行已建立。" },
};

const STEP_LABELS = [
  { key: "claims", system: "candidateClaims", review: "candidateClaims", label: "解析冻结原文", description: "系统从已冻结原文生成候选陈述" },
  { key: "protocol", system: "protocol", review: "protocol", label: "生成研究协议草案", description: "系统起草研究问题、边界和反证规则" },
  { key: "plan", system: "evidencePlan", review: "evidencePlan", label: "生成补证计划草案", description: "系统列出待授权的来源与采集安排" },
] as const;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function candidatesFrom(preparation: ResearchPreparation): Candidate[] {
  const raw = preparation.artifacts.candidateClaims?.payload.candidates;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((candidate) => {
    const item = record(candidate);
    const id = stringValue(item?.candidate_id).trim();
    const normalizedText = stringValue(item?.normalized_text).trim();
    if (!UUID_PATTERN.test(id) || !normalizedText) return [];
    return [{ id, text: normalizedText }];
  });
}

function stepLabel(step: ResearchPreparationEventStep | null): string {
  return step === "parse_claims"
    ? "解析冻结原文"
    : step === "draft_protocol"
      ? "生成研究协议草案"
      : step === "draft_evidence_plan"
        ? "生成补证计划草案"
        : "准备工作";
}

function eventLabel(event: ResearchPreparationEvent): string {
  return stepLabel(event.step);
}

function eventDetails(event: ResearchPreparationEvent): string[] {
  const detail = event.detail ?? {};
  const output: string[] = [];
  if (detail.input_scope === "frozen_original") output.push("输入范围：冻结原文");
  const candidateCount = detail.candidate_count;
  if (typeof candidateCount === "number" && Number.isInteger(candidateCount) && candidateCount >= 0) output.push(`候选陈述：${candidateCount}`);
  const artifactSequence = detail.artifact_sequence;
  if (typeof artifactSequence === "number" && Number.isInteger(artifactSequence) && artifactSequence > 0) output.push(`草案版本：${artifactSequence}`);
  const attempt = detail.attempt;
  if (typeof attempt === "number" && Number.isInteger(attempt) && attempt > 0) output.push(`第 ${attempt} 次尝试`);
  const durationMs = detail.duration_ms;
  if (typeof durationMs === "number" && Number.isFinite(durationMs) && durationMs >= 0) output.push(`耗时：${(durationMs / 1000).toFixed(durationMs % 1000 === 0 ? 0 : 1)} 秒`);
  if (detail.retry_scheduled === true) output.push("已安排重试");
  return output;
}

function protocolText(preparation: ResearchPreparation): string {
  const payload = preparation.artifacts.protocol?.payload ?? {};
  return JSON.stringify(payload, null, 2);
}

function planText(preparation: ResearchPreparation): string {
  const payload = preparation.artifacts.evidencePlan?.payload ?? {};
  return JSON.stringify(payload, null, 2);
}

function artifactText(preparation: ResearchPreparation, key: keyof ResearchPreparation["artifacts"]): string {
  return JSON.stringify(preparation.artifacts[key]?.payload ?? {}, null, 2);
}

function isConflict(error: unknown): boolean {
  return error instanceof ConflictError
    || (error instanceof Error && "status" in error && (error as { status?: number }).status === 409);
}

function preparationErrorMessage(error: unknown): string {
  if (isConflict(error)) {
    return "准备版本已变化，请重新加载后再确认。";
  }
  return error instanceof Error ? error.message : "操作未完成，请检查当前准备状态后重试。";
}

function timelineState(preparation: ResearchPreparation, index: number): "done" | "current" | "waiting" | "failed" {
  const step = STEP_LABELS[index];
  const system = preparation.system[step.system].state;
  const review = preparation.review[step.review].state;
  if (system === "failed") return "failed";
  if (review === "awaiting_review" || (index === 0 && preparation.status === "awaiting_claim_review") || (index === 1 && preparation.status === "awaiting_protocol_confirmation") || (index === 2 && preparation.status === "awaiting_plan_authorization")) return "current";
  if (review === "confirmed") return "done";
  if (system === "succeeded" && review === "locked") return "waiting";
  return system === "running" || system === "retrying" ? "current" : "waiting";
}

export function ResearchPreparationPage() {
  const { caseId = "" } = useParams();
  const [preparation, setPreparation] = useState<ResearchPreparation | null>(null);
  const [events, setEvents] = useState<ResearchPreparationEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [activityError, setActivityError] = useState<string | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  const [commandNotice, setCommandNotice] = useState<string | null>(null);
  const [regenerationNotice, setRegenerationNotice] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [claimDecisions, setClaimDecisions] = useState<Record<string, ClaimDecision>>({});
  const activityCursor = useRef<number | null>(null);
  const requestEpoch = useRef(0);
  const activeCaseId = useRef(caseId);
  const authorizationKey = useRef<{ identity: string; key: string } | null>(null);

  const isCurrentRequest = useCallback((expectedCaseId: string, expectedEpoch: number) => (
    activeCaseId.current === expectedCaseId && requestEpoch.current === expectedEpoch
  ), []);

  const clearReviewDrafts = useCallback(() => {
    setClaimDecisions({});
  }, []);

  const loadActivity = useCallback(async (options: { afterSeq?: number; replace?: boolean } = {}) => {
    if (!caseId) return;
    const epoch = requestEpoch.current;
    let afterSeq = options.afterSeq;
    const received: ResearchPreparationEvent[] = [];
    for (;;) {
      const page = await researchClient.listResearchPreparationEvents(caseId, { afterSeq, limit: 50 });
      if (!isCurrentRequest(caseId, epoch)) return;
      received.push(...page.items);
      if (page.nextAfterSeq === null || page.nextAfterSeq === afterSeq || page.items.length === 0) {
        const latestSeq = Math.max(
          options.afterSeq ?? 0,
          ...received.map((event) => event.seq),
        );
        activityCursor.current = latestSeq || null;
        break;
      }
      afterSeq = page.nextAfterSeq;
    }
    setEvents((current) => {
      if (!isCurrentRequest(caseId, epoch)) return current;
      const combined = options.replace ? received : [...current, ...received];
      return [...new Map(combined.map((event) => [event.seq, event])).values()]
        .sort((left, right) => left.seq - right.seq);
    });
  }, [caseId, isCurrentRequest]);

  const refreshView = useCallback(async () => {
    if (!caseId) return null;
    const epoch = requestEpoch.current;
    const next = await researchClient.getResearchPreparation(caseId);
    if (!isCurrentRequest(caseId, epoch)) return null;
    setPreparation(next);
    setError(null);
    clearReviewDrafts();
    try {
      await loadActivity({ replace: true });
      if (isCurrentRequest(caseId, epoch)) setActivityError(null);
    } catch (activityFailure) {
      if (isCurrentRequest(caseId, epoch)) {
        setActivityError(preparationErrorMessage(activityFailure));
      }
    }
    return next;
  }, [caseId, clearReviewDrafts, isCurrentRequest, loadActivity]);

  const loadPreparation = useCallback(async () => {
    const epoch = requestEpoch.current;
    try {
      await refreshView();
    } catch (loadError) {
      if (isCurrentRequest(caseId, epoch)) setError(preparationErrorMessage(loadError));
    } finally {
      if (isCurrentRequest(caseId, epoch)) setLoading(false);
    }
  }, [caseId, isCurrentRequest, refreshView]);

  useEffect(() => {
    requestEpoch.current += 1;
    activeCaseId.current = caseId;
    activityCursor.current = null;
    authorizationKey.current = null;
    setPreparation(null);
    setEvents([]);
    setError(null);
    setActivityError(null);
    setCommandError(null);
    setCommandNotice(null);
    setRegenerationNotice(false);
    setBusy(false);
    clearReviewDrafts();
    setLoading(true);
    void loadPreparation();
  }, [caseId, clearReviewDrafts, loadPreparation]);

  useEffect(() => {
    if (!caseId || preparation?.status !== "preparing") return undefined;
    let active = true;
    const epoch = requestEpoch.current;
    const refresh = async () => {
      try {
        const next = await researchClient.getResearchPreparation(caseId);
        if (!active || !isCurrentRequest(caseId, epoch)) return;
        setPreparation(next);
        try {
          await loadActivity({ afterSeq: activityCursor.current ?? undefined });
          if (active && isCurrentRequest(caseId, epoch)) setActivityError(null);
        } catch (activityLoadError) {
          if (active && isCurrentRequest(caseId, epoch)) {
            setActivityError(preparationErrorMessage(activityLoadError));
          }
        }
      } catch (pollError) {
        if (active && isCurrentRequest(caseId, epoch)) {
          setActivityError(preparationErrorMessage(pollError));
        }
      }
    };
    const interval = window.setInterval(() => { void refresh(); }, 2000);
    return () => { active = false; window.clearInterval(interval); };
  }, [caseId, isCurrentRequest, loadActivity, preparation?.status]);

  const candidates = useMemo(() => preparation ? candidatesFrom(preparation) : [], [preparation]);
  const candidateDisplayWithheld = useMemo(() => {
    const raw = preparation?.artifacts.candidateClaims?.payload.candidates;
    return Array.isArray(raw) && raw.length > candidates.length;
  }, [candidates.length, preparation]);
  const claimsReady = candidates.length > 0 && candidates.every((candidate) => {
    const decision = claimDecisions[candidate.id];
    return decision?.outcome && decision.reason.trim() && (decision.outcome !== "modified" || decision.normalizedText.trim());
  });

  const execute = useCallback(async (command: () => Promise<ResearchPreparation>, options: { correctedClaims?: boolean } = {}) => {
    const epoch = requestEpoch.current;
    const expectedCaseId = caseId;
    setBusy(true);
    setCommandError(null);
    setCommandNotice(null);
    try {
      const next = await command();
      if (!isCurrentRequest(expectedCaseId, epoch)) return;
      setPreparation(next);
      clearReviewDrafts();
      try {
        await loadActivity({ replace: true });
        if (isCurrentRequest(expectedCaseId, epoch)) setActivityError(null);
      } catch (activityFailure) {
        if (isCurrentRequest(expectedCaseId, epoch)) setActivityError(preparationErrorMessage(activityFailure));
      }
      if (options.correctedClaims && isCurrentRequest(expectedCaseId, epoch)) setRegenerationNotice(true);
    } catch (commandFailure) {
      if (!isCurrentRequest(expectedCaseId, epoch)) return;
      if (isConflict(commandFailure)) {
        try {
          await refreshView();
          if (isCurrentRequest(expectedCaseId, epoch)) setCommandNotice("此准备版本已更新，已显示最新内容");
        } catch (refreshFailure) {
          if (isCurrentRequest(expectedCaseId, epoch)) setCommandError(preparationErrorMessage(refreshFailure));
        }
      } else {
        setCommandError(preparationErrorMessage(commandFailure));
      }
    } finally {
      if (isCurrentRequest(expectedCaseId, epoch)) setBusy(false);
    }
  }, [caseId, clearReviewDrafts, isCurrentRequest, loadActivity, refreshView]);

  const authorizationKeyFor = useCallback((next: ResearchPreparation) => {
    const identity = `${caseId}:${next.revision}:${next.artifacts.evidencePlan?.sequence ?? "missing"}`;
    if (authorizationKey.current?.identity !== identity) {
      authorizationKey.current = { identity, key: crypto.randomUUID() };
    }
    return authorizationKey.current.key;
  }, [caseId]);

  const retryActivity = useCallback(() => {
    const epoch = requestEpoch.current;
    const expectedCaseId = caseId;
    void loadActivity({ replace: true })
      .then(() => {
        if (isCurrentRequest(expectedCaseId, epoch)) setActivityError(null);
      })
      .catch((activityFailure) => {
        if (isCurrentRequest(expectedCaseId, epoch)) setActivityError(preparationErrorMessage(activityFailure));
      });
  }, [caseId, isCurrentRequest, loadActivity]);

  if (loading) return <main className="ros-page ros-preparation-page" aria-busy="true"><section className="ros-preparation-skeleton" aria-label="正在读取研究准备"><i /><i /><i /></section></main>;
  if (!preparation) return <main className="ros-page ros-preparation-page"><section className="ros-empty ros-empty--large" role="alert"><h1>无法读取研究准备</h1><p>{error ?? "这个 Case 没有可读取的研究准备状态。"}</p><button className="ros-button ros-button--secondary" type="button" onClick={() => void loadPreparation()}>重新读取</button></section></main>;

  const summary = STATUS_COPY[preparation.status];
  const runStarted = preparation.status === "authorized" && preparation.researchRunId;
  const evidencePlanSequence = preparation.artifacts.evidencePlan?.sequence;
  const canAuthorizeEvidencePlan = typeof evidencePlanSequence === "number" && Number.isInteger(evidencePlanSequence) && evidencePlanSequence > 0;
  const material = preparation.initialMaterial;
  const progress = preparation.progress;
  return <main className="ros-page ros-preparation-page">
    <header className="ros-page-head ros-preparation-head">
      <div>
        <p className="ros-eyebrow">事件研究 · 准备工作台</p>
        <h1>研究准备</h1>
        {preparation.caseTitle && <p className="ros-preparation-case-title">{preparation.caseTitle}</p>}
        {material && <p className="ros-preparation-material"><strong>{material.title || "冻结原文"}</strong><span>材料版本：{material.documentVersionId.slice(0, 8)}</span></p>}
        {progress && <p className="ros-preparation-progress">准备进度：{progress.completedSteps} / {progress.totalSteps}{progress.currentStep ? ` · 当前：${stepLabel(progress.currentStep)}` : ""}</p>}
        <p>{runStarted ? "正式研究运行已建立，后续补证将按已授权计划受控执行。" : "系统只准备草案，研究员逐步确认；在授权前不会创建 ResearchRun 或运行任何外部 Provider。"}</p>
      </div>
      <p className={runStarted ? "ros-preparation-run is-started" : "ros-preparation-run"}>{runStarted ? "正式研究已启动" : "正式研究尚未启动"}</p>
    </header>

    {error && <p className="ros-error" role="alert">{error}</p>}
    {commandError && <p className="ros-error" role="alert">{commandError}</p>}
    {commandNotice && <p className="ros-success" role="status">{commandNotice}</p>}
    {regenerationNotice && <p className="ros-success" role="status">协议草案和补证计划已因原文核验变更失效，系统将仅重新生成受影响步骤</p>}

    <div className="ros-preparation-layout">
      <section className="ros-preparation-activity" aria-label="系统准备活动">
        <header><p className="ros-eyebrow">系统正在做</p><h2>从冻结原文到可审阅的准备草案</h2></header>
        <ol className="ros-preparation-timeline">
          {STEP_LABELS.map((step, index) => {
            const state = timelineState(preparation, index);
            return <li className={`is-${state}`} key={step.key}>
              <span aria-hidden>{String(index + 1).padStart(2, "0")}</span>
              <div><strong>{step.label}</strong><p>{step.description}</p><small>{state === "done" ? "已由研究员确认" : state === "current" ? "正在准备或等待当前审核" : state === "failed" ? "需要恢复" : "等待上一步确认"}</small></div>
            </li>;
          })}
        </ol>
        <section className="ros-preparation-events" aria-label="准备活动记录">
          <h3>活动记录</h3>
          {activityError && <p className="ros-error" role="alert">无法读取活动记录，不影响准备状态。<button className="ros-button ros-button--secondary" type="button" onClick={retryActivity}>重新读取活动记录</button></p>}
          {events.length ? <ol>{events.map((event) => <li key={event.seq}><time dateTime={event.createdAt}>{new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(new Date(event.createdAt))}</time><span>{eventLabel(event)}</span>{eventDetails(event).map((detail) => <small key={detail}>{detail}</small>)}</li>)}</ol> : <p>尚无可显示的活动记录。</p>}
        </section>
      </section>

      <aside className="ros-preparation-task" aria-label="当前人工任务">
        <p className="ros-eyebrow">你需要做</p>
        <h2>{summary.title}</h2>
        <p>{summary.detail}</p>
        {preparation.status === "preparing" && <section className="ros-preparation-note"><strong>无需操作</strong><p>系统会每两秒更新此页。它只能生成草案，不会采纳陈述、批准协议或启动外部数据请求。</p></section>}
        {preparation.status === "awaiting_claim_review" && <ClaimTask candidates={candidates} displayWithheld={candidateDisplayWithheld} decisions={claimDecisions} busy={busy} ready={claimsReady} onChange={(id, patch) => setClaimDecisions((current) => {
          const currentDecision = current[id] ?? { outcome: "" as const, reason: "", normalizedText: "" };
          return { ...current, [id]: { ...currentDecision, ...patch } };
        })} onConfirm={() => void execute(() => researchClient.confirmResearchPreparationClaims({ caseId, revision: preparation.revision, actor: ACTOR, decisions: candidates.map((candidate) => { const decision = claimDecisions[candidate.id]; return { candidateId: candidate.id, outcome: decision.outcome as "confirmed" | "modified" | "rejected", reason: decision.reason.trim(), normalizedText: decision.outcome === "modified" ? decision.normalizedText.trim() : undefined }; }) }), { correctedClaims: candidates.some((candidate) => claimDecisions[candidate.id]?.outcome === "modified") })} />}
        {preparation.status === "awaiting_protocol_confirmation" && <ProtocolTask preparation={preparation} busy={busy} onConfirm={() => void execute(() => researchClient.confirmResearchPreparationProtocol({ caseId, revision: preparation.revision, actor: ACTOR, draftSequence: preparation.artifacts.protocol?.sequence ?? 0 }))} />}
        {preparation.status === "awaiting_plan_authorization" && <PlanTask preparation={preparation} busy={busy} canAuthorize={canAuthorizeEvidencePlan} onAuthorize={() => {
          if (!canAuthorizeEvidencePlan || typeof evidencePlanSequence !== "number") return;
          void execute(() => researchClient.authorizeResearchPreparation({ caseId, revision: preparation.revision, actor: ACTOR, planSequence: evidencePlanSequence, idempotencyKey: authorizationKeyFor(preparation) }));
        }} />}
        {preparation.status === "recoverable_failure" && <RecoveryTask preparation={preparation} busy={busy} onRetry={() => void execute(() => researchClient.retryResearchPreparation({ caseId, revision: preparation.revision, actor: ACTOR }))} />}
        {preparation.status === "authorized" && <section className="ros-preparation-note"><strong>研究运行已建立</strong><p>已授权计划被冻结在本次研究运行中。后续变更需要回到研究范围与运行记录处理。</p>{preparation.researchRunId && <Link to={`/events/${caseId}/monitor`} className="ros-button ros-button--secondary">查看研究运行</Link>}</section>}
        <FutureReviewPreview preparation={preparation} />
      </aside>
    </div>
  </main>;
}

function ClaimTask({ candidates, displayWithheld, decisions, busy, ready, onChange, onConfirm }: { candidates: Candidate[]; displayWithheld: boolean; decisions: Record<string, ClaimDecision>; busy: boolean; ready: boolean; onChange: (id: string, patch: Partial<ClaimDecision>) => void; onConfirm: () => void }) {
  const hasCorrection = candidates.some((candidate) => decisions[candidate.id]?.outcome === "modified");
  return <form className="ros-preparation-form" onSubmit={(event) => { event.preventDefault(); onConfirm(); }}>
    {candidates.map((candidate, index) => {
      const decision = decisions[candidate.id] ?? { outcome: "", reason: "", normalizedText: "" };
      return <fieldset key={candidate.id}><legend>候选陈述 {index + 1}</legend><p>{candidate.text}</p><label>决定<select aria-label={`候选陈述 ${index + 1} 的决定`} value={decision.outcome} onChange={(event) => onChange(candidate.id, { outcome: event.target.value as ClaimDecision["outcome"] })}><option value="">请选择</option><option value="confirmed">确认</option><option value="modified">修正</option><option value="rejected">不采纳</option></select></label>{decision.outcome === "modified" && <label>修正后陈述<textarea value={decision.normalizedText} onChange={(event) => onChange(candidate.id, { normalizedText: event.target.value })} /></label>}<label>核对说明<textarea aria-label={`候选陈述 ${index + 1} 的核对说明`} value={decision.reason} onChange={(event) => onChange(candidate.id, { reason: event.target.value })} placeholder="说明与冻结原文的核对结果" /></label></fieldset>;
    })}
    {!candidates.length && <p className="ros-error">{displayWithheld ? "候选陈述受来源展示许可限制，无法在此页显示。请在已授权材料中核对后重试。" : "候选陈述草案不完整，请重新加载后再确认。"}</p>}
    {hasCorrection && <p className="ros-preparation-boundary">协议草案与补证计划会标记为过期并由系统重生成。重新生成完成后，仍需按顺序再次确认；不会启动正式研究。</p>}
    <button className="ros-button ros-button--primary" type="submit" disabled={busy || !ready}>{busy ? "正在保存确认…" : "确认候选陈述"}</button>
  </form>;
}

function ProtocolTask({ preparation, busy, onConfirm }: { preparation: ResearchPreparation; busy: boolean; onConfirm: () => void }) {
  const draftSequence = preparation.artifacts.protocol?.sequence;
  const canConfirm = typeof draftSequence === "number" && Number.isInteger(draftSequence) && draftSequence > 0;
  return <form className="ros-preparation-form" onSubmit={(event) => { event.preventDefault(); if (canConfirm) onConfirm(); }}><label>协议草案<pre>{protocolText(preparation)}</pre></label>{!canConfirm && <p className="ros-error">协议草案缺失，无法提交确认。请重新读取准备状态。</p>}<p className="ros-preparation-boundary">确认协议不会启动正式研究，也不会执行补证计划。</p><button className="ros-button ros-button--primary" type="submit" disabled={busy || !canConfirm}>{busy ? "正在确认协议…" : "确认研究协议"}</button></form>;
}

function PlanTask({ preparation, busy, canAuthorize, onAuthorize }: { preparation: ResearchPreparation; busy: boolean; canAuthorize: boolean; onAuthorize: () => void }) {
  return <section className="ros-preparation-form"><h3>待授权补证计划</h3><pre>{planText(preparation)}</pre>{!canAuthorize && <p className="ros-error">补证计划草案缺失或版本无效，无法授权。请重新读取准备状态。</p>}<p className="ros-preparation-boundary">只有点击下方按钮，系统才会创建一个正式 ResearchRun，并按这份冻结计划开始受控补证。</p><button className="ros-button ros-button--primary" type="button" disabled={busy || !canAuthorize} onClick={onAuthorize}>{busy ? "正在授权…" : "授权补证计划并启动正式研究"}</button></section>;
}

function RecoveryTask({ preparation, busy, onRetry }: { preparation: ResearchPreparation; busy: boolean; onRetry: () => void }) {
  const failedStep = preparation.progress?.failedStep;
  const retryAt = preparation.nextAttemptAt ? new Date(preparation.nextAttemptAt) : null;
  const retryLabel = retryAt && !Number.isNaN(retryAt.valueOf()) ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "short", timeStyle: "short" }).format(retryAt) : null;
  return <section className="ros-preparation-form"><p className="ros-error">{preparation.lastErrorMessage ?? "准备草案未能生成。"}</p>{failedStep && <p>失败步骤：{stepLabel(failedStep)}</p>}{retryLabel && <p>下次重试：{retryLabel}</p>}<p>重新准备只会重新生成待审核草案，不会创建正式研究或调用补证 Provider。</p><ArtifactPreviews preparation={preparation} keys={["candidateClaims", "protocol", "evidencePlan"]} readOnly /><button className="ros-button ros-button--primary" type="button" disabled={busy} onClick={onRetry}>{busy ? "正在重新排队…" : "重新准备研究草案"}</button></section>;
}

function ArtifactPreviews({ preparation, keys, readOnly = false }: { preparation: ResearchPreparation; keys: Array<keyof ResearchPreparation["artifacts"]>; readOnly?: boolean }) {
  const labels: Record<keyof ResearchPreparation["artifacts"], string> = { candidateClaims: "候选陈述草案", protocol: "研究协议草案", evidencePlan: "补证计划草案" };
  return <div className="ros-preparation-artifacts">{keys.filter((key) => preparation.artifacts[key]).map((key) => <fieldset disabled={!readOnly} key={key}><legend>{labels[key]}</legend><textarea aria-label={`${labels[key]}预览`} value={artifactText(preparation, key)} readOnly /><small>{readOnly ? "保留的草案，仅供核对" : "等待上一步确认"}</small>{!readOnly && <button type="button" disabled>等待上一步确认</button>}</fieldset>)}</div>;
}

function FutureReviewPreview({ preparation }: { preparation: ResearchPreparation }) {
  const remaining = preparation.status === "awaiting_claim_review"
    ? ["protocol", "evidencePlan"] as Array<keyof ResearchPreparation["artifacts"]>
    : preparation.status === "awaiting_protocol_confirmation"
      ? ["evidencePlan"] as Array<keyof ResearchPreparation["artifacts"]>
      : [];
  if (!remaining.length) return null;
  return <section className="ros-preparation-preview" aria-label="后续确认步骤"><p className="ros-eyebrow">后续确认</p><ArtifactPreviews preparation={preparation} keys={remaining} /></section>;
}
