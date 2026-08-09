import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import type { EventExtraction } from "../../domain/eventResearch";

const EMPTY_FACTORS = ["", "", ""];

export function EventCreatePage() {
  const navigate = useNavigate();
  const [rawInput, setRawInput] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceType, setSourceType] = useState("pasted_snapshot");
  const [draft, setDraft] = useState<EventExtraction | null>(null);
  const [factors, setFactors] = useState<string[]>(EMPTY_FACTORS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function extract() {
    if (!rawInput.trim()) return;
    setBusy(true); setError(null);
    try {
      const next = await researchClient.extractEventResearch({ rawInput: rawInput.trim(), sourceUrl: sourceUrl.trim() || undefined });
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
        eventTitle: draft.eventTitle?.trim() || rawInput.trim().slice(0, 80),
        candidateFactors: factors.map((factor) => factor.trim()).filter(Boolean),
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

  return (
    <main className="ros-page ros-create-page">
      <section className="ros-page__heading"><div><p className="ros-eyebrow">新建事件研究</p><h1>先固定研究范围，再让系统开始工作</h1><p className="ros-lede">原始材料、研究问题和关键因素会随 Case 保存；后续改动将形成可追溯的新版本。</p></div></section>
      <div className="ros-create-grid">
        <section className="ros-form-card"><div className="ros-step"><span>01</span><div><h2>提供事件材料</h2><p>可以粘贴新闻、公告、研报片段；不要把二手转述当作已确认事实。</p></div></div>
          <label>来源接入方式<select value={sourceType} onChange={(event) => setSourceType(event.target.value)}><option value="pasted_snapshot">粘贴快照</option><option value="uploaded_file">上传文件</option><option value="licensed_provider">授权数据源</option></select></label>
          <label>事件原始输入<textarea value={rawInput} onChange={(event) => setRawInput(event.target.value)} placeholder="粘贴原文或清晰描述发生了什么…" /></label>
          <label>来源链接（可选）<input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" /></label>
          <button className="ros-button ros-button--secondary" type="button" disabled={!rawInput.trim() || busy} onClick={extract}>{busy && !draft ? "正在识别…" : "识别事件与研究问题"}</button>
          <small className="ros-form-note">当前选择：{sourceType === "licensed_provider" ? "授权数据源" : sourceType === "uploaded_file" ? "上传文件" : "粘贴快照"}。来源许可会在审核与运行记录中呈现。</small>
        </section>
        <section className={`ros-form-card ros-form-card--scope${draft ? " is-ready" : ""}`} aria-live="polite"><div className="ros-step"><span>02</span><div><h2>确认可验证的研究范围</h2><p>系统仅提出候选；研究员决定问题和要验证的因素。</p></div></div>
          {!draft ? <div className="ros-empty ros-empty--compact">先识别事件，才能编辑研究问题与关键因素。</div> : <>
            <label>事件标题<input value={draft.eventTitle ?? ""} onChange={(event) => setDraft({ ...draft, eventTitle: event.target.value })} /></label>
            <label>研究问题<textarea aria-label="研究问题" value={draft.researchQuestion} onChange={(event) => setDraft({ ...draft, researchQuestion: event.target.value })} /></label>
            <div className="ros-factor-fields"><p className="ros-field-label">关键因素（至少 3 个）</p>{factors.map((factor, index) => <label key={index} className="ros-factor-input"><span>{String(index + 1).padStart(2, "0")}</span><input aria-label={`关键因素 ${index + 1}`} value={factor} onChange={(event) => updateFactor(index, event.target.value)} /></label>)}</div>
            <button className="ros-button ros-button--primary" type="button" disabled={busy || factors.filter((factor) => factor.trim()).length < 3 || !draft.researchQuestion.trim()} onClick={create}>{busy ? "正在创建…" : "创建事件 Case"} <span aria-hidden>→</span></button>
          </>}
          {error && <p className="ros-error" role="alert">{error}</p>}
        </section>
      </div>
    </main>
  );
}
