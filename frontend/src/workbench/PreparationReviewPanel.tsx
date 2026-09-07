import { useEffect, useRef, useState } from "react";
import { researchActionsApi, type ClaimCandidate, type ClaimDecision, type PreparationData, type PreparationReview } from "@/data/researchActionsApi";

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const labels: Record<string, string> = {
  outcomes: "研究结果", baseline: "基线", horizon: "研究期限", mechanisms: "机制", verification_rules: "核验规则",
  thesis_id: "研究因素编号", metric: "指标", metric_id: "指标标识", display_name: "显示名称", canonical_definition: "指标定义",
  entity_scope: "实体范围", unit: "单位", frequency: "频率", period_semantics: "期间口径", allowed_source_roles: "允许的来源角色", role_eligibility: "指标适用角色",
  binding: "结果绑定", direction: "预期方向", horizon_start: "起始日期", horizon_end: "结束日期", template_version_id: "机制模板版本编号",
  mechanism_edge_id: "机制关系编号", expected_direction: "预期方向", support_predicate: "支持条件", contradiction_predicate: "反驳条件",
  observed_period_start: "观察期开始", observed_period_end: "观察期结束", available_at_deadline: "证据可用截止日", next_verification_event: "下次核验事件",
  start: "开始日期", end: "结束日期", description: "说明", value: "数值", company: "公司", company_id: "公司编号", period: "期间", reference: "参考依据", rationale: "依据", name: "名称", source: "来源", source_ref: "冻结来源引用", observed_period: "基线观察期", available_at: "来源可用时间", business_line: "业务线", product_line: "产品线",
};
const readonlyKeys = new Set(["thesis_id", "template_version_id", "mechanism_edge_id", "document_version_id", "source_span_id", "candidate_id"]);
/** Render every source-draft field; only values are editable, preserving its structure and identifiers. */
function ProtocolField({ value, path, fieldKey, onChange }: { value: unknown; path: string; fieldKey: string; onChange: (value: unknown) => void }) {
  const numeric = useRef(typeof value === "number").current;
  if (Array.isArray(value)) return <fieldset><legend>{path}</legend>{value.length ? value.map((entry, index) => <ProtocolField key={index} value={entry} path={`${path} ${index + 1}`} fieldKey={fieldKey} onChange={(next) => onChange(value.map((old, i) => i === index ? next : old))} />) : <p>草稿未提供此项内容。</p>}</fieldset>;
  if (object(value)) return <fieldset><legend>{path}</legend>{Object.entries(value).map(([key, entry]) => <ProtocolField key={key} value={entry} path={`${path} ${labels[key] ?? key}`.trim()} fieldKey={key} onChange={(next) => onChange({ ...value, [key]: next })} />)}</fieldset>;
  if (typeof value === "boolean") return <label><input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />{path}</label>;
  if (value === null) return <p>{path}：未提供</p>;
  return <label>{path}<input aria-label={path} readOnly={readonlyKeys.has(fieldKey)} type={numeric ? "number" : "text"} value={String(value)} onChange={(e) => onChange(numeric ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)} /></label>;
}
function SourceDetails({ candidate }: { candidate: ClaimCandidate }) {
  return <><blockquote>{candidate.quote}</blockquote><p>来源：{candidate.document_source_url}</p><p>文档版本：{candidate.document_version_id} · 原文片段：{candidate.source_span_id}</p><p>定位：{JSON.stringify(candidate.locator)}</p><p>陈述类型：{candidate.claim_type} · 来源权威：{candidate.authority_level} · 陈述主体：{candidate.assertion_actor ?? "未提供"}</p><details><summary>提取校验与已有审核记录</summary><pre>{JSON.stringify({ structured_fields: candidate.structured_fields, validation_result: candidate.validation_result, review_state: candidate.review_state, review_history: candidate.review_history }, null, 2)}</pre></details></>;
}
interface Props { preparation: PreparationData; actor: string; busy: boolean; onSubmit: (review: PreparationReview) => void }
function ClaimsReview({ preparation, actor, busy, onSubmit }: Props) {
  const artifact = preparation.artifacts.claims!;
  const [candidates, setCandidates] = useState<ClaimCandidate[] | null>(null);
  const [error, setError] = useState("");
  const [decisions, setDecisions] = useState<Record<string, { outcome: string; reason: string; normalized_text: string }>>({});
  useEffect(() => {
    const controller = new AbortController();
    researchActionsApi.claims(preparation.case_id, controller.signal).then(({ items }) => {
      if (controller.signal.aborted) return;
      const refs = artifact.payload.candidates;
      const ids = Array.isArray(refs) ? refs.map((entry) => object(entry) ? entry.candidate_id : undefined) : [];
      const byId = new Map(items.map((candidate) => [candidate.id, candidate]));
      if (!ids.length || new Set(ids).size !== ids.length || ids.some((id) => typeof id !== "string" || !byId.has(id))) {
        setError("候选陈述未完整加载，无法审核。请刷新或联系管理员核对来源访问与候选数量。"); return;
      }
      setCandidates(ids.map((id) => byId.get(id as string)!));
    }).catch((err: unknown) => { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "候选陈述读取失败。"); });
    return () => controller.abort();
  }, [preparation.case_id, artifact]);
  const valid = candidates?.every((candidate) => {
    const decision = decisions[candidate.id];
    return decision && ["confirmed", "modified", "rejected"].includes(decision.outcome) && decision.reason.trim() && (decision.outcome !== "modified" || decision.normalized_text.trim());
  });
  if (error) return <p role="alert">{error}</p>;
  if (!candidates) return <p role="status">正在读取陈述原文与来源…</p>;
  return <form className="live-create live-preparation-review" onSubmit={(e) => { e.preventDefault(); if (busy || !actor.trim() || !valid) return; onSubmit({ kind: "claims", decisions: candidates.map((candidate) => { const decision = decisions[candidate.id]!; return { candidate_id: candidate.id, outcome: decision.outcome as ClaimDecision["outcome"], reason: decision.reason.trim(), ...(decision.outcome === "modified" ? { normalized_text: decision.normalized_text.trim() } : {}) }; }) }); }}>
    <h3>逐条审核陈述</h3><p>准备版本 {preparation.revision} · 陈述产物序号 {artifact.sequence}</p>
    <fieldset disabled={busy}>{candidates.map((candidate, index) => {
      const draft = decisions[candidate.id] ?? { outcome: "", reason: "", normalized_text: candidate.normalized_text };
      const update = (patch: Partial<typeof draft>) => setDecisions((old) => ({ ...old, [candidate.id]: { ...draft, ...patch } }));
      return <fieldset key={candidate.id}><legend>陈述 {index + 1}</legend><p>{candidate.normalized_text}</p><SourceDetails candidate={candidate} />
        <label>陈述 {index + 1} 审核决定<select required value={draft.outcome} onChange={(e) => update({ outcome: e.target.value })}><option value="">请选择审核决定</option><option value="confirmed">确认</option><option value="modified">修订后确认</option><option value="rejected">拒绝</option></select></label>
        {draft.outcome === "modified" && <label>陈述 {index + 1} 修订内容<textarea required value={draft.normalized_text} onChange={(e) => update({ normalized_text: e.target.value })} /></label>}
        <label>陈述 {index + 1} 审核理由<textarea required maxLength={2000} value={draft.reason} onChange={(e) => update({ reason: e.target.value })} /></label>
      </fieldset>;
    })}</fieldset><button className="live-primary" disabled={busy || !actor.trim() || !valid}>提交陈述审核</button>
  </form>;
}
function protocolComplete(draft: Record<string, unknown>): boolean {
  const nonempty = (value: unknown): boolean => typeof value === "string" && Boolean(value.trim());
  const strings = (value: unknown): boolean => Array.isArray(value) && value.length > 0 && value.every(nonempty);
  const fields = (value: unknown, keys: string[]): value is Record<string, unknown> => object(value) && keys.every((key) => nonempty(value[key]));
  const date = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
  const period = (start: unknown, end: unknown): boolean => date(start) && date(end) && start <= end;
  const required = ["outcomes", "baseline", "horizon", "mechanisms", "verification_rules"];
  if (Object.keys(draft).length !== required.length || !required.every((key) => key in draft)) return false;
  if (!object(draft.baseline) || !object(draft.horizon) || !period(draft.horizon.start, draft.horizon.end)) return false;
  if (!["outcomes", "mechanisms", "verification_rules"].every((key) => Array.isArray(draft[key]) && draft[key].length > 0 && draft[key].every((entry: unknown) => object(entry) && Object.keys(entry).length > 0))) return false;
  return (draft.outcomes as unknown[]).every((outcome) => {
    if (!fields(outcome, ["thesis_id", "template_version_id"])) return false;
    const { metric, binding, verification_rules: rules } = outcome;
    if (!fields(metric, ["metric_id", "display_name", "canonical_definition", "entity_scope", "unit", "frequency", "period_semantics"]) || !strings(metric.allowed_source_roles) || !strings(metric.role_eligibility)) return false;
    if (!fields(binding, ["direction"]) || !object(binding.entity_scope) || !object(binding.baseline) || !period(binding.horizon_start, binding.horizon_end)) return false;
    if (!fields(binding.entity_scope, ["company_id", String(metric.entity_scope)])) return false;
    if (!fields(binding.baseline, ["source_ref", "unit", "observed_period", "available_at"]) || binding.baseline.unit !== metric.unit) return false;
    const baselineValue = binding.baseline.value;
    if (!nonempty(baselineValue) && !(typeof baselineValue === "number" && Number.isFinite(baselineValue))) return false;
    const availableAt = binding.baseline.available_at as string;
    if (!/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$/.test(availableAt) || !date(availableAt.slice(0, 10)) || !Number.isFinite(Date.parse(availableAt))) return false;
    return Array.isArray(rules) && rules.length > 0 && rules.every((rule) => fields(rule, ["mechanism_edge_id", "expected_direction", "support_predicate", "contradiction_predicate", "next_verification_event"]) && strings(rule.allowed_source_roles) && period(rule.observed_period_start, rule.observed_period_end) && date(rule.available_at_deadline));
  });
}
function ProtocolReview({ preparation, actor, busy, onSubmit }: Props) {
  const artifact = preparation.artifacts.protocol!;
  const [draft, setDraft] = useState(artifact.payload);
  const [checked, setChecked] = useState(false);
  const complete = protocolComplete(draft);
  return <form className="live-create live-preparation-review" onSubmit={(e) => { e.preventDefault(); if (busy || !actor.trim() || !checked || !complete) return; const edits = Object.fromEntries(Object.entries(draft).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(artifact.payload[key]))); onSubmit({ kind: "protocol", draft_sequence: artifact.sequence, edits }); }}>
    <h3>研究协议审核</h3><p>准备版本 {preparation.revision} · 协议产物序号 {artifact.sequence}</p><p>来源上下文：{artifact.context_fingerprint ?? "未提供"}</p><p>请核对每个研究结果的指标、基线、期限、机制和核验规则。来源关联编号保留草稿值；服务端将校验研究范围和协议有效性。</p>
    {!complete && <p role="alert">协议草稿缺少必需内容或字段不一致，暂不能确认。请检查公司与指标实体范围、基线来源/数值/单位/观察期/可用时间及日期；基线单位必须与指标一致，可用时间须为带时区的 ISO 时间。</p>}
    <fieldset disabled={busy}>{Object.entries(draft).map(([key, value]) => <ProtocolField key={key} value={value} path={labels[key] ?? key} fieldKey={key} onChange={(next) => { setDraft((old) => ({ ...old, [key]: next })); setChecked(false); }} />)}</fieldset>
    <label><input type="checkbox" checked={checked} disabled={busy || !complete} onChange={(e) => setChecked(e.target.checked)} />我已核对研究协议全部字段及来源范围</label>
    <button className="live-primary" disabled={busy || !actor.trim() || !checked || !complete}>确认研究协议</button>
  </form>;
}
export function PreparationReviewPanel(props: Props) {
  const { preparation } = props;
  if (preparation.research_run_id) return null;
  if (preparation.status === "awaiting_claim_review" && preparation.review.claims?.state === "awaiting_review") {
    const artifact = preparation.artifacts.claims;
    if (artifact?.display_withheld) return <p>陈述内容暂不可展示，请先解决来源访问限制。</p>;
    if (!artifact || artifact.state !== "current") return <p>当前陈述草稿不可用，请刷新准备状态。</p>;
    return <ClaimsReview {...props} />;
  }
  if (preparation.status === "awaiting_protocol_confirmation" && preparation.review.claims?.state === "confirmed" && preparation.review.protocol?.state === "awaiting_review") {
    const artifact = preparation.artifacts.protocol;
    if (artifact?.display_withheld) return null;
    if (!artifact || artifact.state !== "current") return <p>当前协议草稿不可用，请刷新准备状态。</p>;
    return <ProtocolReview {...props} />;
  }
  return null;
}
