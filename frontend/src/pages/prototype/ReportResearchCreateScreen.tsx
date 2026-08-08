import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { researchClient } from "../../data/researchClient";

type InputKind = "pasted_text" | "web_content" | "pdf_upload";

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
      <label>研报输入方式<select value={inputKind} onChange={(event) => setInputKind(event.target.value as InputKind)}><option value="pasted_text">粘贴研报正文</option><option value="web_content">网页正文</option><option value="pdf_upload">上传 PDF</option></select></label>
      <label>研报标题<input value={title} onChange={(event) => setTitle(event.target.value)} required /></label>
      <div className="report-create-form__meta"><label>发布机构（可选）<input value={publisher} onChange={(event) => setPublisher(event.target.value)} /></label><label>发布时间（可选，按本地时区）<input aria-label="发布时间（可选）" type="datetime-local" value={publishedAt} onChange={(event) => setPublishedAt(event.target.value)} /></label></div>
      {inputKind === "web_content" ? <label>来源网址<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://" /></label> : null}
      {inputKind === "pdf_upload" ? <label>PDF 文件<input aria-label="PDF 文件" type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /></label> : <label>{inputKind === "web_content" ? "网页正文" : "研报正文"}<textarea value={content} onChange={(event) => setContent(event.target.value)} rows={12} required placeholder="粘贴可公开核验的研报正文；原始文本会被冻结为研究来源。" /></label>}
      {error ? <div className="report-create-error" role="alert"><span>{error}</span><button type="button" onClick={() => void submit({ preventDefault() {} } as FormEvent)}>重试提交</button></div> : null}
      <div className="report-create-actions"><button className="prototype-button primary" type="submit" disabled={submitting}>{submitting ? "正在冻结并开始研究…" : "创建并开始自动研究"}</button><p>不会生成虚构的解析结果；缺少文本、市场数据或证据会明确显示为研究缺口。</p></div>
    </form>
  </main>;
}
