import { useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";

type InputKind = "pasted_text" | "web_content" | "pdf_upload";
type RecoveryReason = "needs_text_or_pages" | "needs_supplement";
export type RecoveryRouteState = {
  reason: RecoveryReason;
  target: { caseId: string; documentId: string; inputKind: InputKind };
};

/** Reject partial, stale, and unknown recovery URLs rather than guessing a Case target. */
export function parseRecoveryRouteState(search: URLSearchParams): RecoveryRouteState | null {
  const knownKeys = ["resume_case_id", "resume_document_id", "resume_input_kind", "resume_reason"] as const;
  for (const key of search.keys()) {
    if (key.startsWith("resume_") && !knownKeys.includes(key as typeof knownKeys[number])) return null;
  }
  const values = Object.fromEntries(knownKeys.map((key) => [key, search.getAll(key)])) as Record<typeof knownKeys[number], string[]>;
  if (knownKeys.some((key) => values[key].length !== 1)) return null;
  const caseId = values.resume_case_id[0];
  const documentId = values.resume_document_id[0];
  const inputKind = values.resume_input_kind[0];
  const reason = values.resume_reason[0];
  if (!caseId || !documentId || !inputKind || !reason) return null;
  if (!(["pdf_upload", "pasted_text", "web_content"] as string[]).includes(inputKind)) return null;
  if (!(["needs_text_or_pages", "needs_supplement"] as string[]).includes(reason)) return null;
  return { reason: reason as RecoveryReason, target: { caseId, documentId, inputKind: inputKind as InputKind } };
}

function optional(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed || undefined;
}

function localDateTimeToUtc(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  const date = new Date(trimmed);
  return Number.isNaN(date.valueOf()) ? undefined : date.toISOString();
}

export function ReportResearchCreateScreen() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const persistedRecovery = parseRecoveryRouteState(searchParams);
  const [inputKind, setInputKind] = useState<InputKind>("pasted_text");
  const [title, setTitle] = useState("");
  const [publisher, setPublisher] = useState("");
  const [publishedAt, setPublishedAt] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sourceContractId, setSourceContractId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recoveryTarget, setRecoveryTarget] = useState<RecoveryRouteState["target"] | null>(() => persistedRecovery?.target ?? null);
  const [continuingRecovery, setContinuingRecovery] = useState(() => persistedRecovery === null);
  const [pageReference, setPageReference] = useState("");
  const contentRef = useRef<HTMLTextAreaElement>(null);
  const isRecoveryChoice = recoveryTarget !== null && !continuingRecovery;

  const clearRecoveryRoute = () => {
    setRecoveryTarget(null);
    setContinuingRecovery(true);
    setInputKind("pasted_text");
    setPageReference("");
    setError(null);
    navigate("/reports/new", { replace: true });
  };

  const continueRecovery = () => {
    setContinuingRecovery(true);
    setInputKind("pasted_text");
    window.setTimeout(() => contentRef.current?.focus(), 0);
  };

  const submit = async () => {
    setError(null);
    if (!sourceContractId.trim()) { setError("请选择或填写已获授权的来源合同版本 ID。当前部署尚未提供合同选择服务。"); return; }
    if (isRecoveryChoice) return;
    if (!recoveryTarget && !title.trim()) { setError("请先填写研报标题。"); return; }
    if (!recoveryTarget && inputKind === "pdf_upload" && !file) { setError("请选择需要冻结的 PDF 文件。"); return; }
    if (inputKind !== "pdf_upload" && !content.trim()) { setError("请粘贴需要冻结的研报正文。"); return; }
    setSubmitting(true);
    try {
      const common = { title: title.trim(), publisher: optional(publisher), publishedAt: localDateTimeToUtc(publishedAt), sourceContractId: sourceContractId.trim() };
      const created = recoveryTarget
        ? await researchClient.supplementReportResearch!({ caseId: recoveryTarget.caseId, documentId: recoveryTarget.documentId, content, pageReference: optional(pageReference), sourceContractId: common.sourceContractId })
        : inputKind === "pdf_upload"
          ? await researchClient.createReportResearchPdf!({ ...common, file: file! })
          : await researchClient.createReportResearch!({ ...common, inputKind, sourceUrl: inputKind === "web_content" ? optional(sourceUrl) : undefined, content });
      navigate(`/reports/${created.caseId}/intake`, { replace: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "冻结资料失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return <main className="prototype-screen report-create-screen">
    <header className="report-page-header"><div><p className="section-kicker">资料收件箱 · 冻结资料</p><h1>{recoveryTarget ? "补充冻结资料" : "接入研究资料"}</h1><p>资料先以可追溯快照冻结。候选陈述、人工审核和研究定义都将在下一步明确呈现，不会自动生成研究结论。</p></div></header>
    {isRecoveryChoice ? <section className="report-recovery-choice" role="status" aria-live="polite">
      <p className="section-kicker">恢复原 Case</p><h2>原件已冻结，等待独立补充内容</h2><p>补充正文会作为新的冻结快照关联原件，不会改写原 PDF 或直接打开研究工作台。</p>
      <div className="report-recovery-choice__actions">
        <button type="button" className="prototype-button primary" onClick={continueRecovery}>继续补充原 Case</button>
        <button type="button" className="prototype-button" onClick={clearRecoveryRoute}>放弃恢复并新建资料</button>
        <button type="button" className="prototype-button text" onClick={() => navigate(-1)}>取消</button>
      </div>
    </section> : null}
    <form className="report-create-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      {!recoveryTarget ? <label>研报输入方式<select value={inputKind} onChange={(event) => setInputKind(event.target.value as InputKind)}><option value="pasted_text">粘贴研报正文</option><option value="web_content">网页正文</option><option value="pdf_upload">上传 PDF</option></select></label> : null}
      {!recoveryTarget ? <label>研报标题<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label> : null}
      {!recoveryTarget ? <div className="report-create-form__meta"><label>发布机构（可选）<input value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label><label>发布时间（可选，按本地时区）<input aria-label="发布时间（可选）" type="datetime-local" value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} /></label></div> : null}
      {!recoveryTarget && inputKind === "web_content" ? <label>来源网址<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://" /></label> : null}
      {recoveryTarget ? <label>对应原始 {recoveryTarget.inputKind === "pdf_upload" ? "PDF 页码" : "内容位置"}（可选）<input value={pageReference} onChange={(event) => setPageReference(event.target.value)} placeholder={recoveryTarget.inputKind === "pdf_upload" ? "例如：第 3 页" : "例如：第 2 段"} /></label> : null}
      {!recoveryTarget && inputKind === "pdf_upload" ? <label>PDF 文件<input aria-label="PDF 文件" type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label> : <label>{recoveryTarget ? "研报正文" : inputKind === "web_content" ? "网页正文" : "研报正文"}<textarea ref={contentRef} value={content} onChange={(event) => setContent(event.target.value)} rows={12} required placeholder="粘贴可定位的原文内容；原始文本会被冻结为独立资料快照。" /></label>}
      <label>来源授权合同版本 ID<input aria-describedby="source-contract-help" value={sourceContractId} onChange={(event) => setSourceContractId(event.target.value)} required placeholder="由来源治理配置提供" /></label>
      <p id="source-contract-help" className="report-form-help">当前部署尚未提供可选择的合同清单。请使用已获授权且允许 AI 处理的合同版本 ID，系统会在写入前校验；不会填入示例合同冒充真实授权。</p>
      {error ? <div className="report-create-error" role="alert"><span>{error}</span><button type="button" onClick={() => void submit()}>重试提交</button></div> : null}
      {!isRecoveryChoice ? <div className="report-create-actions"><button className="prototype-button primary" type="submit" disabled={submitting}>{submitting ? "正在冻结资料…" : recoveryTarget ? "冻结补充内容并进入下一步" : "冻结资料并进入下一步"}</button><p>下一页会显示唯一可执行动作、阻塞原因及其解锁条件。未审核材料不会进入正式研究或市场表达。</p></div> : null}
    </form>
  </main>;
}
