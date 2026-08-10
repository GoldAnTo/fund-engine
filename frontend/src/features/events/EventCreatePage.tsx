import { useEffect, useState, type ChangeEvent } from "react";
import { useNavigate } from "react-router-dom";

import { researchClient } from "../../data/researchClient";
import type { EventExtraction, EventSourceType } from "../../domain/eventResearch";
import {
  DEFAULT_SOURCE_GOVERNANCE,
  SourceGovernanceFields,
  sourceGovernanceMetadata,
} from "../sources/SourceGovernanceFields";

const EMPTY_FACTORS = ["", "", ""];
type SourceAuthority = "unknown" | "primary_disclosure" | "licensed_research" | "secondary_source" | "user_supplied";

export function EventCreatePage() {
  const navigate = useNavigate();
  const [rawInput, setRawInput] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceType, setSourceType] = useState<EventSourceType>("pasted_snapshot");
  const [sourceAuthority, setSourceAuthority] = useState<SourceAuthority>("unknown");
  const [sourceMetadata, setSourceMetadata] = useState<Record<string, unknown>>({});
  const [sourcePermissions, setSourcePermissions] = useState({ ai_processing: true, display: true, export: false, api: false });
  const [sourceGovernance, setSourceGovernance] = useState(DEFAULT_SOURCE_GOVERNANCE);
  const [originalFile, setOriginalFile] = useState<File | null>(null);
  const [providerName, setProviderName] = useState("");
  const [providerRecordId, setProviderRecordId] = useState("");
  const [providerRequestScope, setProviderRequestScope] = useState("");
  const [draft, setDraft] = useState<EventExtraction | null>(null);
  const [factors, setFactors] = useState<string[]>(EMPTY_FACTORS);
  const [destination, setDestination] = useState<"new" | "existing">("new");
  const [existingCases, setExistingCases] = useState<Array<{ id: string; eventTitle: string; status: string }>>([]);
  const [existingCasesError, setExistingCasesError] = useState(false);
  const [selectedExistingCase, setSelectedExistingCase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sourceReady = sourceType === "licensed_provider"
    ? Boolean(providerName.trim() && providerRecordId.trim() && providerRequestScope.trim())
    : sourceType === "public_url"
      ? Boolean(sourceUrl.trim())
      : true;
  const frozenSourceMetadata = {
    ...sourceMetadata,
    ...sourceGovernanceMetadata(sourceGovernance),
    authority_level: sourceAuthority,
    permissions: sourcePermissions,
    ...(sourceType === "licensed_provider" ? { provider_name: providerName.trim(), provider_record_id: providerRecordId.trim(), request_scope: { declared_scope: providerRequestScope.trim() }, retrieval_reference: sourceUrl.trim() || undefined } : {}),
    ...(sourceType === "public_url" ? { intake_note: "公开网页 URL 仅作为可复查线索；已冻结内容尚未完成原文核验。" } : {}),
  };

  useEffect(() => {
    let active = true;
    researchClient.listEventResearch()
      .then((items) => {
        if (!active) return;
        const eligible = items.filter((item) => item.status !== "published");
        setExistingCases(eligible);
        setExistingCasesError(false);
      })
      .catch(() => {
        if (!active) return;
        setExistingCases([]);
        setExistingCasesError(true);
      });
    return () => { active = false; };
  }, []);

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
        sourceType: originalFile && sourceType === "uploaded_file" ? "pasted_snapshot" : sourceType,
        sourceMetadata: originalFile && sourceType === "uploaded_file"
          ? { ...sourceGovernanceMetadata(sourceGovernance), authority_level: "user_supplied", permissions: sourcePermissions, intake_note: "用于识别事件的人工输入；原件另行冻结" }
          : frozenSourceMetadata,
        eventTitle: draft.eventTitle?.trim() || rawInput.trim().slice(0, 80),
        candidateFactors: factors.map((factor) => factor.trim()).filter(Boolean),
        researchProtocolRequired: true,
        createdBy: "human:researcher",
      });
      if (originalFile && sourceType === "uploaded_file") {
        const uploaded = await researchClient.uploadEventMaterial({
          caseId: created.caseId,
          file: originalFile,
          sourceMetadata: frozenSourceMetadata,
          actor: "human:researcher",
        });
        navigate(`/events/${created.caseId}/documents?document=${encodeURIComponent(uploaded.documentVersionId)}`);
        return;
      }
      navigate(`/events/${created.caseId}`);
    } catch {
      setError("Case 尚未创建。请修正必填信息后重试。");
    } finally { setBusy(false); }
  }

  async function attachToExistingCase() {
    if (!selectedExistingCase || (!rawInput.trim() && !originalFile)) return;
    setBusy(true); setError(null);
    try {
      if (originalFile && sourceType === "uploaded_file") {
        const attached = await researchClient.uploadEventMaterial({
          caseId: selectedExistingCase,
          file: originalFile,
          sourceMetadata: frozenSourceMetadata,
          actor: "human:researcher",
        });
        navigate(`/events/${selectedExistingCase}/documents?document=${encodeURIComponent(attached.documentVersionId)}`);
      } else {
        const attached = await researchClient.attachEventMaterial({
          caseId: selectedExistingCase,
          rawInput: rawInput.trim(),
          sourceUrl: sourceUrl.trim() || undefined,
          sourceType,
          sourceMetadata: frozenSourceMetadata,
          actor: "human:researcher",
        });
        navigate(`/events/${selectedExistingCase}/documents?document=${encodeURIComponent(attached.documentVersionId)}`);
      }
    } catch {
      setError("材料未能归入当前 Case。已发布 Case 必须从原文资料页进行变化比较和人工决定。");
    } finally { setBusy(false); }
  }

  function updateFactor(index: number, value: string) {
    setFactors((current) => current.map((factor, position) => position === index ? value : factor));
  }

  function changeSourceType(next: EventSourceType) {
    setSourceType(next);
    if (next !== "uploaded_file") setOriginalFile(null);
    setSourcePermissions(next === "licensed_provider"
      ? { ai_processing: false, display: false, export: false, api: false }
      : next === "public_url"
        ? { ai_processing: false, display: true, export: false, api: false }
        : { ai_processing: true, display: true, export: false, api: false });
  }

  async function loadOriginalFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setOriginalFile(file);
    setSourceMetadata((current) => ({ ...current, file_name: file.name, mime_type: file.type || "text/plain", byte_size: file.size }));
    if (file.type === "application/pdf") {
      setError(null);
      return;
    }
    try {
      const text = typeof file.text === "function" ? await file.text() : await readWithFileReader(file);
      setRawInput(text);
      setError(null);
    } catch {
      setError("原件会被保留，但浏览器无法读取正文。请填写事件描述后继续，或归入已有 Case 后在原文资料页补充正文。");
    }
  }

  return (
    <main className="ros-page ros-create-page">
      <section className="ros-page__heading"><div><p className="ros-eyebrow">资料收件箱</p><h1>先冻结材料，再决定它属于哪个研究</h1><p className="ros-lede">先将事件、新闻、研报片段或文本材料固定为可追溯快照；再由研究员选择创建新 Case，或归入一个未发布的既有 Case。</p></div></section>
      {existingCasesError && <p className="ros-error" role="alert">无法读取可归入 Case 清单；仍可创建新 Case，但暂不能将材料归入已有 Case。</p>}
      <div className="ros-create-grid">
        <section className="ros-form-card"><div className="ros-step"><span>01</span><div><h2>提供事件材料</h2><p>可以粘贴新闻、公告、研报片段；不要把二手转述当作已确认事实。</p></div></div>
          <label>来源接入方式<select value={sourceType} onChange={(event) => changeSourceType(event.target.value as EventSourceType)}><option value="pasted_snapshot">粘贴快照</option><option value="uploaded_file">上传原件文件</option><option value="licensed_provider">授权数据源快照</option><option value="public_url">公开网页快照</option></select></label>
          <label>来源权威性<select aria-label="来源权威性" value={sourceAuthority} onChange={(event) => setSourceAuthority(event.target.value as SourceAuthority)}><option value="unknown">未知，待核验</option><option value="primary_disclosure">公司或发行人一手披露</option><option value="licensed_research">授权研报</option><option value="secondary_source">二手报道或转述</option><option value="user_supplied">用户提供材料</option></select><small>这是随资料冻结的声明，仍须核对原文、发布方与许可；系统不会直接把二手转述写成已披露事实。</small></label>
          {sourceType === "uploaded_file" && <label>上传原件文件<input aria-label="选择上传原件文件" type="file" accept="application/pdf,text/plain,text/markdown,text/csv,.pdf,.txt,.md,.csv" onChange={loadOriginalFile} /><small>支持 PDF、TXT、Markdown、CSV（最多 20 MiB）。原件与解析片段分别冻结；扫描或异常 PDF 会保留原件并进入补充正文恢复，不会伪造可读正文。</small></label>}
          {sourceType === "licensed_provider" && <section className="ros-source-governance"><label>供应商名称<input aria-label="供应商名称" value={providerName} onChange={(event) => setProviderName(event.target.value)} placeholder="例如：聚源" /></label><label>供应商记录 ID<input aria-label="供应商记录 ID" value={providerRecordId} onChange={(event) => setProviderRecordId(event.target.value)} placeholder="可重取的报告或公告记录 ID" /></label><label>供应商查询口径<textarea aria-label="供应商查询口径" value={providerRequestScope} onChange={(event) => setProviderRequestScope(event.target.value)} placeholder="例如：研报 / 标的 000001 / 2026H1" /></label><small>授权来源必须固定供应商、具体记录和查询口径；否则只能作为线索，不能创建或归入 Case。</small></section>}
          <fieldset className="ros-source-governance"><legend>资料使用许可声明</legend><small>这些权限会随冻结版本保存；勾选只表示当前团队获得的许可，不会把材料自动变成已审核证据。</small>{([['ai_processing', '允许 AI 处理'], ['display', '允许团队展示'], ['export', '允许导出'], ['api', '允许 API 使用']] as const).map(([key, label]) => <label key={key}><input aria-label={label} type="checkbox" checked={sourcePermissions[key]} onChange={(event) => setSourcePermissions((current) => ({ ...current, [key]: event.target.checked }))} /> {label}</label>)}</fieldset>
          <SourceGovernanceFields value={sourceGovernance} onChange={setSourceGovernance} />
          <label>{sourceType === "public_url" ? "网页原文或事件摘要" : "事件原始输入"}<textarea aria-label={sourceType === "public_url" ? "网页原文或事件摘要" : "事件原始输入"} value={rawInput} onChange={(event) => setRawInput(event.target.value)} placeholder="粘贴原文或清晰描述发生了什么…" /></label>
          <label>{sourceType === "licensed_provider" ? "供应商记录或可重取链接" : sourceType === "public_url" ? "公开网页链接（必填）" : "来源链接（可选）"}<input aria-label={sourceType === "public_url" ? "公开网页链接（必填）" : undefined} type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://…" /></label>
          {sourceType === "public_url" && <p className="ros-note">系统只冻结你提交的正文快照，不会抓取网页、绕过访问限制，或把 URL 视为已核验内容。它只能作为待人工核验的线索。</p>}
          <button className="ros-button ros-button--secondary" type="button" disabled={!rawInput.trim() || !sourceReady || busy} onClick={extract}>{busy && !draft ? "正在识别…" : "识别事件与研究问题"}</button>
          <small className="ros-form-note">当前选择：{sourceType === "licensed_provider" ? sourceReady ? "授权数据源快照（供应商记录已填写）" : "授权数据源快照（仍缺供应商记录）" : sourceType === "uploaded_file" ? originalFile ? `原件待冻结 · ${originalFile.name}` : "上传原件文件（尚未选择）" : sourceType === "public_url" ? sourceReady ? "公开网页快照（URL 已冻结，正文待核验）" : "公开网页快照（仍缺公开 URL）" : "粘贴快照（需后续核验）"}；权威性为 {sourceAuthority}。来源类型、许可与输入元数据会随 Case 冻结。</small>
        </section>
        <section className={`ros-form-card ros-form-card--scope${draft ? " is-ready" : ""}`} aria-live="polite"><div className="ros-step"><span>02</span><div><h2>确认可验证的研究范围</h2><p>系统仅提出候选；研究员决定问题和要验证的因素。</p></div></div>
          {!draft ? originalFile && sourceType === "uploaded_file" ? <section className="ros-existing-case-intake"><p>可直接把原件冻结到未发布 Case；PDF 无需先在浏览器解析。已发布 Case 仍须从原文资料页做变化比较和人工决定。</p><label>选择原件目标 Case<select aria-label="选择原件目标 Case" value={selectedExistingCase} onChange={(event) => setSelectedExistingCase(event.target.value)}><option value="" disabled>{existingCases.length ? "请选择 Case" : "暂无可归入的未发布 Case"}</option>{existingCases.map((item) => <option key={item.id} value={item.id}>{item.eventTitle} · {item.status}</option>)}</select></label><button className="ros-button ros-button--primary" type="button" disabled={busy || !sourceReady || !selectedExistingCase} onClick={attachToExistingCase}>{busy ? "正在冻结原件…" : "冻结原件并归入当前 Case"} <span aria-hidden>→</span></button></section> : <div className="ros-empty ros-empty--compact">先识别事件，才能决定它应创建新研究还是归入已有 Case。</div> : <>
            <fieldset className="ros-material-destination">
              <legend>决定材料归属</legend>
              <label><input type="radio" name="material-destination" checked={destination === "new"} onChange={() => setDestination("new")} /> 创建新 Case</label>
              <label><input type="radio" name="material-destination" checked={destination === "existing"} onChange={() => setDestination("existing")} /> 归入已有 Case</label>
              <small>归入已有 Case 只冻结材料并保留来源元数据，不会启动运行、改写结论或改变既有研究范围。</small>
            </fieldset>
            {destination === "new" ? <>
            <label>事件标题<input value={draft.eventTitle ?? ""} onChange={(event) => setDraft({ ...draft, eventTitle: event.target.value })} /></label>
            <label>研究问题<textarea aria-label="研究问题" value={draft.researchQuestion} onChange={(event) => setDraft({ ...draft, researchQuestion: event.target.value })} /></label>
            <div className="ros-factor-fields"><p className="ros-field-label">关键因素（至少 3 个）</p>{factors.map((factor, index) => <label key={index} className="ros-factor-input"><span>{String(index + 1).padStart(2, "0")}</span><input aria-label={`关键因素 ${index + 1}`} value={factor} onChange={(event) => updateFactor(index, event.target.value)} /></label>)}</div>
            <div className="ros-protocol-optin"><span><b>新建 Case 默认采用严格研究协议</b><small>必须固定结果指标、范围、基线、时间窗、机制与反证规则，才能进入正式验证。既有 Case 的历史标记不会被这里改写。</small></span></div>
            <button className="ros-button ros-button--primary" type="button" disabled={busy || !sourceReady || factors.filter((factor) => factor.trim()).length < 3 || !draft.researchQuestion.trim()} onClick={create}>{busy ? "正在建立…" : "建立 Case，进入资料核验"} <span aria-hidden>→</span></button>
            </> : <section className="ros-existing-case-intake">
              <p>这份材料将作为当前 Case 内的新冻结版本。研究员仍需在资料页核验原文，再决定是否提出或审核关系。</p>
              <label>选择目标 Case<select aria-label="选择目标 Case" value={selectedExistingCase} onChange={(event) => setSelectedExistingCase(event.target.value)}><option value="" disabled>{existingCases.length ? "请选择 Case" : "暂无可归入的未发布 Case"}</option>{existingCases.map((item) => <option key={item.id} value={item.id}>{item.eventTitle} · {item.status}</option>)}</select></label>
              <p className="ros-note">已发布 Case 必须走变化比较和人工决定，不能从收件箱直接归入。</p>
              <button className="ros-button ros-button--primary" type="button" disabled={busy || !sourceReady || !selectedExistingCase} onClick={attachToExistingCase}>{busy ? "正在冻结材料…" : "冻结并归入当前 Case"} <span aria-hidden>→</span></button>
            </section>}
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
