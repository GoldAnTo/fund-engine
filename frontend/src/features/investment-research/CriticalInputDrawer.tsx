import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

import type {
  CompanyResearchCriticalInput,
  CompanyResearchCriticalInputDecision,
  CompanyResearchCriticalInputKind,
  CompanyResearchCriticalInputSet,
  CompanyResearchCriticalSurface,
  CriticalInputDecisionRequest,
} from "../../data/investmentResearchApi";

type TerminalDecision = Exclude<CompanyResearchCriticalInputDecision, "pending">;

const KIND_LABELS: Readonly<Record<CompanyResearchCriticalInputKind, string>> = {
  source_fact: "来源事实",
  management_guidance: "管理层指引",
  consensus: "一致预期",
  ai_assumption: "AI 假设",
  user_assumption: "用户假设",
  derived_calculation: "派生计算",
  unknown: "未知输入",
};

const SURFACE_LABELS: Readonly<Record<CompanyResearchCriticalSurface, string>> = {
  revenue: "收入预测",
  operating_profit: "经营利润",
  fcff: "自由现金流",
  capital_structure: "资本结构",
  discount_terminal: "折现与终值",
  scenario: "情景预测",
  security_value: "证券价值",
  security_return: "预期回报",
  answerability: "可回答性",
  direction: "研究方向",
  strongest_counterevidence: "最强反证",
};

function inputCanBeReplaced(input: CompanyResearchCriticalInput): boolean {
  if (input.kind === "derived_calculation") return false;
  return input.key.startsWith("fact:")
    || input.key.startsWith("assumption:")
    || input.key.startsWith("scenario:") && !input.key.startsWith("scenario:base:")
    || input.key.startsWith("market:capital:") && input.key !== "market:capital:bridge_policy"
    || input.key.startsWith("market:price:")
    || input.key === "market:fx:usd_cny";
}

function inputValue(input: CompanyResearchCriticalInput): string {
  if (input.value === null) return "未建立数值";
  return `${input.value}${input.unit ? ` ${input.unit}` : ""}${input.currency && input.currency !== "N/A" ? ` ${input.currency}` : ""}`;
}

function SourceOrEquation({ input }: { input: CompanyResearchCriticalInput }) {
  if (input.source_ref) return <div className="ir-critical-source">
    <dt>来源</dt>
    <dd><a href={input.source_ref.source_url} rel="noreferrer" target="_blank">{input.source_ref.source_role} · {input.source_ref.source_locator}</a></dd>
  </div>;
  if (input.equation_id) return <div className="ir-critical-source"><dt>推导</dt><dd>{input.equation_id}{input.parent_input_keys.length > 0 ? ` · 父输入 ${input.parent_input_keys.join("、")}` : ""}</dd></div>;
  if (input.rationale) return <div className="ir-critical-source"><dt>依据</dt><dd>{input.rationale}</dd></div>;
  if (input.unknown_reason) return <div className="ir-critical-source"><dt>缺失原因</dt><dd>{input.unknown_reason}{input.gap_key ? ` · ${input.gap_key}` : ""}</dd></div>;
  return <div className="ir-critical-source"><dt>依据</dt><dd>尚未建立可验证的来源或推导。</dd></div>;
}

function focusableElements(dialog: HTMLDialogElement): HTMLElement[] {
  return [...dialog.querySelectorAll<HTMLElement>(
    "button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])",
  )].filter((element) => element.getAttribute("aria-hidden") !== "true");
}

export interface CriticalInputDrawerProps {
  busy: boolean;
  criticalInputs: CompanyResearchCriticalInputSet;
  error: string | null;
  focusInputKey?: string | null;
  onClose: () => void;
  onDecide: (request: CriticalInputDecisionRequest) => void;
  returnFocusTo: HTMLElement | null;
}

export default function CriticalInputDrawer({
  busy,
  criticalInputs,
  error,
  focusInputKey = null,
  onClose,
  onDecide,
  returnFocusTo,
}: CriticalInputDrawerProps) {
  const titleId = useId();
  const descriptionId = useId();
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const pending = criticalInputs.inputs.filter((input) => input.decision === "pending");
  const input = pending.find((candidate) => candidate.key === focusInputKey) ?? pending[0] ?? null;
  const [decision, setDecision] = useState<TerminalDecision | null>(null);
  const [replacementValue, setReplacementValue] = useState("");
  const [replacementUnit, setReplacementUnit] = useState("");
  const [replacementRationale, setReplacementRationale] = useState("");

  useEffect(() => {
    setDecision(null);
    setReplacementValue("");
    setReplacementUnit("");
    setReplacementRationale("");
  }, [criticalInputs.artifact_id, input?.input_fingerprint]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog === null) return;
    const siblings = [...document.body.children].filter((element) => element !== dialog).map((element) => ({
      element: element as HTMLElement,
      ariaHidden: element.getAttribute("aria-hidden"),
      inert: (element as HTMLElement & { inert?: boolean }).inert ?? false,
    }));
    for (const sibling of siblings) {
      sibling.element.setAttribute("aria-hidden", "true");
      (sibling.element as HTMLElement & { inert?: boolean }).inert = true;
    }
    if (!dialog.open) dialog.showModal();
    headingRef.current?.focus();
    return () => {
      if (dialog.open) dialog.close();
      for (const sibling of siblings) {
        if (sibling.ariaHidden === null) sibling.element.removeAttribute("aria-hidden");
        else sibling.element.setAttribute("aria-hidden", sibling.ariaHidden);
        (sibling.element as HTMLElement & { inert?: boolean }).inert = sibling.inert;
      }
      returnFocusTo?.focus();
    };
  }, [criticalInputs.artifact_id, returnFocusTo]);

  if (input === null) return null;
  const processed = criticalInputs.inputs.length - pending.length;
  const replacementSelected = decision === "replaced_with_user_assumption";
  const replacementComplete = replacementValue.trim().length > 0
    && replacementUnit.trim().length > 0
    && replacementRationale.trim().length > 0;
  const submitDisabled = busy || decision === null || replacementSelected && !replacementComplete;
  const canReplace = inputCanBeReplaced(input);
  const canMarkUnknown = input.kind !== "unknown" && input.kind !== "derived_calculation";
  const canAcceptGap = input.kind === "unknown";

  function submit() {
    if (submitDisabled || decision === null) return;
    const base: CriticalInputDecisionRequest = {
      schema_version: "underwriting.v1",
      critical_input_key: input.key,
      expected_artifact_id: criticalInputs.artifact_id,
      expected_input_fingerprint: input.input_fingerprint,
      decision,
    };
    onDecide(decision === "replaced_with_user_assumption" ? {
      ...base,
      replacement_value: replacementValue.trim(),
      replacement_unit: replacementUnit.trim(),
      replacement_rationale: replacementRationale.trim(),
    } : base);
  }

  function trapFocus(event: React.KeyboardEvent<HTMLDialogElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      if (!busy) onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const focusable = focusableElements(event.currentTarget);
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !focusable.includes(document.activeElement as HTMLElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !focusable.includes(document.activeElement as HTMLElement))) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(<dialog
    aria-describedby={descriptionId}
    aria-labelledby={titleId}
    className="ir-critical-drawer"
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}
    onKeyDown={trapFocus}
    ref={dialogRef}
  >
    <div className="ir-critical-drawer__head">
      <div><p className="ir-eyebrow">Save checkpoint</p><h2 id={titleId}>确认关键输入</h2></div>
      <button aria-label="关闭关键输入确认" className="ir-button" disabled={busy} onClick={onClose} type="button">关闭</button>
    </div>
    <p id={descriptionId}>只处理会显著改变预测、估值或研究判断的输入。每次提交一项，服务端会返回完整的新研究状态。</p>
    <div className="ir-critical-progress" role="status">
      <strong>已处理 {processed} / {criticalInputs.inputs.length}，剩余 {pending.length}</strong>
      <progress aria-label="关键输入确认进度" max={criticalInputs.inputs.length} value={processed}>{processed}</progress>
    </div>
    <section className="ir-critical-input" aria-labelledby={`critical-input-${input.input_fingerprint}`}>
      <header><span>{KIND_LABELS[input.kind]}</span><h3 id={`critical-input-${input.input_fingerprint}`} ref={headingRef} tabIndex={-1}>{input.key}</h3></header>
      <dl className="ir-critical-facts">
        <div><dt>当前值</dt><dd>{inputValue(input)}</dd></div>
        <div><dt>期间</dt><dd>{input.period ?? "未指定期间"}</dd></div>
        <div><dt>单位</dt><dd>{input.unit ?? "不适用"}</dd></div>
        <SourceOrEquation input={input} />
      </dl>
      <div className="ir-critical-impact"><strong>影响范围</strong><ul>{input.impact.surfaces.map((surface) => <li key={surface}>{SURFACE_LABELS[surface]}</li>)}</ul><details><summary>查看依赖路径</summary><ul>{input.impact.dependency_paths.map((path) => <li key={path.join("|")}>{path.join(" → ")}</li>)}</ul></details></div>
    </section>
    <fieldset className="ir-critical-decisions" disabled={busy}>
      <legend>本项决定</legend>
      {input.kind !== "unknown" ? <label><input aria-label="确认当前输入" checked={decision === "confirmed"} name="critical-input-decision" onChange={() => setDecision("confirmed")} type="radio" /> <span><strong>确认当前输入</strong><small>沿用当前来源、假设或计算边界。</small></span></label> : null}
      {canReplace ? <label><input aria-label="改为用户假设" checked={decision === "replaced_with_user_assumption"} name="critical-input-decision" onChange={() => setDecision("replaced_with_user_assumption")} type="radio" /> <span><strong>改为用户假设</strong><small>新值不会伪装成来源事实。</small></span></label> : null}
      {canMarkUnknown ? <label><input aria-label="标记为未知" checked={decision === "marked_unknown"} name="critical-input-decision" onChange={() => setDecision("marked_unknown")} type="radio" /> <span><strong>标记为未知</strong><small>受影响的结论会按缺失数据重新计算。</small></span></label> : null}
      {canAcceptGap ? <label><input aria-label="接受为研究缺口" checked={decision === "accepted_gap"} name="critical-input-decision" onChange={() => setDecision("accepted_gap")} type="radio" /> <span><strong>接受为研究缺口</strong><small>保留具体缺口，不用默认值填补。</small></span></label> : null}
    </fieldset>
    {replacementSelected ? <section className="ir-critical-replacement" aria-label="用户假设替代值">
      {input.source_ref ? <p><strong>原始披露会保留，新值将另存为用户假设。</strong></p> : null}
      <label>替代值<input disabled={busy} maxLength={10_000} onChange={(event) => setReplacementValue(event.currentTarget.value)} value={replacementValue} /></label>
      <label>单位<input disabled={busy} maxLength={120} onChange={(event) => setReplacementUnit(event.currentTarget.value)} value={replacementUnit} /></label>
      <label>修改理由<textarea disabled={busy} maxLength={4_000} onChange={(event) => setReplacementRationale(event.currentTarget.value)} rows={4} value={replacementRationale} /></label>
    </section> : null}
    <p className="ir-critical-guidance"><strong>需要补充新的授权资料？</strong> 关闭面板，在研究依据中核对来源后重新运行。补充资料不会覆盖已经冻结的原始披露。</p>
    {error ? <p className="ir-field-error" role="alert">{error}</p> : null}
    <div className="ir-critical-drawer__actions">
      <button className="ir-button" disabled={busy} onClick={onClose} type="button">稍后处理</button>
      <button className="ir-button ir-button--primary" disabled={submitDisabled} onClick={submit} type="button">{busy ? "正在提交本项决定…" : "提交本项决定"}</button>
    </div>
  </dialog>, document.body);
}
