import { FormEvent, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { researchClient } from "../../data/researchClient";

type InputKind = "pasted_text" | "web_content" | "pdf_upload";
type IntakeRecovery = "needs_text_or_pages" | "no_initial_scope";

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
  const [inputKind, setInputKind] = useState<InputKind>("pasted_text");
  const [title, setTitle] = useState("");
  const [publisher, setPublisher] = useState("");
  const [publishedAt, setPublishedAt] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [recovery, setRecovery] = useState<IntakeRecovery | null>(null);
  const contentRef = useRef<HTMLTextAreaElement>(null);

  const clearRecovery = () => setRecovery(null);
  const focusContent = () => window.setTimeout(() => contentRef.current?.focus(), 0);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!title.trim()) { setError("请先填写研报标题。"); return; }
    if (inputKind === "pdf_upload" && !file) { setError("请选择需要解析的 PDF 文件。"); return; }
    if (inputKind !== "pdf_upload" && !content.trim()) { setError("请粘贴需要研究的研报正文。"); return; }
    setSubmitting(true);
    try {
      const common = { title: title.trim(), publisher: optional(publisher), publishedAt: localDateTimeToUtc(publishedAt) };
      const created = inputKind === "pdf_upload"
        ? await researchClient.createReportResearchPdf!({ ...common, file: file! })
        : await researchClient.createReportResearch!({ ...common, inputKind, sourceUrl: inputKind === "web_content" ? optional(sourceUrl) : undefined, content });
      if (created.needsTextOrPages || created.state === "needs_text_or_pages") {
        setRecovery("needs_text_or_pages");
        return;
      }
      if (!created.initialScopeVersion || created.initialScopeVersion < 1) {
        setRecovery("no_initial_scope");
        return;
      }
      navigate(`/reports/${created.caseId}`, { replace: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建研报研究失败，请重试。");
    } finally {
      setSubmitting(false);
    }
  };

  return <main className="prototype-screen report-create-screen">
    <header className="report-page-header"><div><p className="section-kicker">研报研究 · 第一步</p><h1>创建研报研究</h1><p>冻结原始报告，系统将提取主张与关系，并自动开始收集可核验的数据与证据。</p></div></header>
    <form className="report-create-form" onSubmit={(event) => void submit(event)}>
      <label>研报输入方式<select value={inputKind} onChange={(event) => { clearRecovery(); setInputKind(event.target.value as InputKind); }}><option value="pasted_text">粘贴研报正文</option><option value="web_content">网页正文</option><option value="pdf_upload">上传 PDF</option></select></label>
      <label>研报标题<input value={title} onChange={(event) => { clearRecovery(); setTitle(event.target.value); }} required /></label>
      <div className="report-create-form__meta"><label>发布机构（可选）<input value={publisher} onChange={(event) => { clearRecovery(); setPublisher(event.target.value); }} /></label><label>发布时间（可选，按本地时区）<input aria-label="发布时间（可选）" type="datetime-local" value={publishedAt} onChange={(event) => { clearRecovery(); setPublishedAt(event.target.value); }} /></label></div>
      {inputKind === "web_content" ? <label>来源网址<input type="url" value={sourceUrl} onChange={(event) => { clearRecovery(); setSourceUrl(event.target.value); }} placeholder="https://" /></label> : null}
      {inputKind === "pdf_upload" ? <label>PDF 文件<input aria-label="PDF 文件" type="file" accept="application/pdf,.pdf" onChange={(event) => { clearRecovery(); setFile(event.target.files?.[0] ?? null); }} /></label> : <label>{inputKind === "web_content" ? "网页正文" : "研报正文"}<textarea ref={contentRef} value={content} onChange={(event) => { clearRecovery(); setContent(event.target.value); }} rows={12} required placeholder="粘贴可公开核验的研报正文；原始文本会被冻结为研究来源。" /></label>}
      {recovery === "needs_text_or_pages" ? <section className="report-intake-recovery" role="status" aria-live="polite"><h2>原件已冻结，等待补充</h2><p>系统未能从该 PDF 提取可研究正文，因此尚未创建研究范围，也不会打开空工作台。可粘贴可读正文并标明对应页码，或在修正文件后重新提交。</p><div><button type="button" className="prototype-button" onClick={() => { clearRecovery(); setInputKind("pasted_text"); setFile(null); focusContent(); }}>补充正文或页码</button><button type="button" className="prototype-button" onClick={() => void submit({ preventDefault() {} } as FormEvent)}>重新提交当前 PDF</button></div></section> : null}
      {recovery === "no_initial_scope" ? <section className="report-intake-recovery" role="status" aria-live="polite"><h2>原件已冻结，尚未解析主张</h2><p>当前正文已保存，但尚未抽取到可研究主张，因而没有可打开的研究范围。请补充明确观点、对象或预测后重新解析；重新提交会新增研究尝试，不会覆盖已冻结原件。</p><div><button type="button" className="prototype-button" onClick={() => { clearRecovery(); focusContent(); }}>修改内容后继续解析</button><button type="button" className="prototype-button" onClick={() => void submit({ preventDefault() {} } as FormEvent)}>重新提交当前内容</button></div></section> : null}
      {error ? <div className="report-create-error" role="alert"><span>{error}</span><button type="button" onClick={() => void submit({ preventDefault() {} } as FormEvent)}>重试提交</button></div> : null}
      <div className="report-create-actions"><button className="prototype-button primary" type="submit" disabled={submitting}>{submitting ? "正在冻结并开始研究…" : "创建并开始自动研究"}</button><p>不会生成虚构的解析结果；缺少文本、市场数据或证据会明确显示为研究缺口。</p></div>
    </form>
  </main>;
}
