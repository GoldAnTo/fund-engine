import { useState, type ChangeEvent } from "react";
import { useNavigate } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import type { EventExtraction } from "../../domain/eventResearch";

const EMPTY_FACTORS = ["", "", ""];
type SourceAuthority = "unknown" | "primary_disclosure" | "licensed_research" | "secondary_source" | "user_supplied";

export function EventCreatePage() {
  const navigate = useNavigate();
  const [rawInput, setRawInput] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceType, setSourceType] = useState<"pasted_snapshot" | "uploaded_file" | "licensed_provider">("pasted_snapshot");
  const [sourceAuthority, setSourceAuthority] = useState<SourceAuthority>("unknown");
  const [sourceMetadata, setSourceMetadata] = useState<Record<string, unknown>>({});
  const [draft, setDraft] = useState<EventExtraction | null>(null);
  const [factors, setFactors] = useState<string[]>(EMPTY_FACTORS);
  const [researchProtocolRequired, setResearchProtocolRequired] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const frozenSourceMetadata = { ...sourceMetadata, authority_level: sourceAuthority };

  async function extract() {
    if (!rawInput.trim()) return;
    setBusy(true); setError(null);
    try {
      const next = await researchClient.extractEventResearch({ rawInput: rawInput.trim(), sourceUrl: sourceUrl.trim() || undefined, sourceType, sourceMetadata: frozenSourceMetadata });
      setDraft(next);
      setFactors(next.candidateFactors.slice(0, 5));
    } catch {
      setError("无法识别此事件。请检查来源或补充原始文本。");
    } finally { setBusy(false); }
  }

  async function create() {
    if (!draft || factors.filter((factor) => factor.trim()).length < 3) return;
    setBusy(true); setError(null);
    try {
      const created = await researchClient.createEventResearch({
        ...draft,
        rawInput: rawInput.trim(),
        sourceUrl: sourceUrl.trim() || undefined,
        sourceType,
        sourceMetadata: frozenSourceMetadata,
        eventTitle: draft.eventTitle?.trim() || rawInput.trim().slice(0, 80),
        candidateFactors: factors.map((factor) => factor.trim()).filter(Boolean),
        researchProtocolRequired,
        createdBy: "human:researcher",
      });
      navigate(`/events/${created.caseId}`);
    } catch {
      setError("Case 尚未创建。请修正必填信息后重试。");
    } finally { setBusy(false); }
  }

  function updateFactor(index: number, value: string) {
    setFactors((current) => current.map((factor, position) => position === index ? value : factor));
  }

  async function loadTextFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const text = typeof file.text === "function" ? await file.text() : await readWithFileReader(file);
      setRawInput(text);
      setSourceMetadata((current) => ({ ...current, file_name: file.name, mime_type: file.type || "text/plain", byte_size: file.size }));
      setError(null);
    } catch {
      setError("无法读取该文件。当前入口仅支持可直接读取正文的文本文件，原件未被伪装为已解析资料。");
    }
  }

  return (
    <main className="ros-page ros-create-page">
      <section className="ros-page__heading"><div><p className="ros-eyebrow">新建事件研究</p><h1>先固定研究范围，再让系统开始工作</h1><p className="ros-lede">原始材料、研究问题和关键因素会随 Case 保存；后续改动将形成可追溯的新版本。</p></div></section>
      <div className="ros-create-grid">
        <section className="ros-form-card"><div className="ros-step"><span>01</span><div><h2>提供事件材料</h2><p>可以粘贴新闻、公告、研报片段；不要把二手转述当作已确认事实。</p></div></div>
          <label>来源接入方式<select value={sourceType} onChange={(event) => setSourceType(event.target.value as "pasted_snapshot" | "uploaded_file" | "licensed_provider")}><option value="pasted_snapshot">粘贴快照</option><option value="uploaded_file">上传文本文件</option><option value="licensed_provider">授权数据源快照</option></select></label>
          <label>来源权威性<select aria-label="来源权威性" value={sourceAuthority} onChange={(event) => setSourceAuthority(event.target.value as SourceAuthority)}><option value="unknown">未知，待核验</option><option value="primary_disclosure">公司或发行人一手披露</option><option value="licensed_research">授权研报</option><option value="secondary_source">二手报道或转述</option><option value="user_supplied">用户提供材料</option></select><small>这是随资料冻结的声明，仍须核对原文、发布方与许可；系统不会直接把二手转述写成已披露事实。</small></label>
          {sourceType === "uploaded_file" && <label>上传正文文件<input aria-label="上传正文文件" type="file" accept="text/plain,text/markdown,.txt,.md,.csv" onChange={loadTextFile} /><small>当前 V1 读取并冻结文本正文快照；不保存或冒充原件 PDF/Office 文件。</small></label>}
          <label>事件原始输入<textarea value={rawInput} onChange={(event) => setRawInput(event.target.value)} placeholder="粘贴原文或清晰描述发生了什么…" /></label>
          <label>{sourceType === "licensed_provider" ? "供应商记录或可重取链接" : "来源链接（可选）"}<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" /></label>
          <button className="ros-button ros-button--secondary" type="button" disabled={!rawInput.trim() || busy} onClick={extract}>{busy && !draft ? "正在识别…" : "识别事件与研究问题"}</button>
          <small className="ros-form-note">当前选择：{sourceType === "licensed_provider" ? "授权数据源快照（合同权限仍需后端配置）" : sourceType === "uploaded_file" ? "上传文本快照（原件未保存）" : "粘贴快照（需后续核验）"}；权威性为 {sourceAuthority}。来源类型与本次输入元数据会随 Case 冻结。</small>
        </section>
        <section className={`ros-form-card ros-form-card--scope${draft ? " is-ready" : ""}`} aria-live="polite"><div className="ros-step"><span>02</span><div><h2>确认可验证的研究范围</h2><p>系统仅提出候选；研究员决定问题和要验证的因素。</p></div></div>
          {!draft ? <div className="ros-empty ros-empty--compact">先识别事件，才能编辑研究问题与关键因素。</div> : <>
            <label>事件标题<input value={draft.eventTitle ?? ""} onChange={(event) => setDraft({ ...draft, eventTitle: event.target.value })} /></label>
            <label>研究问题<textarea aria-label="研究问题" value={draft.researchQuestion} onChange={(event) => setDraft({ ...draft, researchQuestion: event.target.value })} /></label>
            <div className="ros-factor-fields"><p className="ros-field-label">关键因素（至少 3 个）</p>{factors.map((factor, index) => <label key={index} className="ros-factor-input"><span>{String(index + 1).padStart(2, "0")}</span><input aria-label={`关键因素 ${index + 1}`} value={factor} onChange={(event) => updateFactor(index, event.target.value)} /></label>)}</div>
            <label className="ros-protocol-optin"><input type="checkbox" checked={researchProtocolRequired} onChange={(event) => setResearchProtocolRequired(event.target.checked)} /><span><b>启用严格研究协议</b><small>创建后必须固定结果指标、范围、基线、时间窗、机制与反证规则，才能进入正式验证；关闭时会明确标记为既有流程。</small></span></label>
            <button className="ros-button ros-button--primary" type="button" disabled={busy || factors.filter((factor) => factor.trim()).length < 3 || !draft.researchQuestion.trim()} onClick={create}>{busy ? "正在建立…" : "建立 Case，进入资料核验"} <span aria-hidden>→</span></button>
          </>}
          {error && <p className="ros-error" role="alert">{error}</p>}
        </section>
      </div>
    </main>
  );
}

function readWithFileReader(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsText(file);
  });
}
