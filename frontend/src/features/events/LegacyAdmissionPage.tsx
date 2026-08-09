import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type LegacyCaseAdmissionCandidate, type ResearchSession } from "../../app/researchOsApi";

export function LegacyAdmissionPage() {
  const [session, setSession] = useState<ResearchSession | null>(null);
  const [items, setItems] = useState<LegacyCaseAdmissionCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function load() {
    setError(null);
    try {
      const [identity, queue] = await Promise.all([
        researchOsApi.session(),
        researchOsApi.legacyAdmissionQueue(),
      ]);
      setSession(identity);
      setItems(queue.items);
    } catch {
      setItems(null);
      setError("当前身份不能读取历史 Case 准入队列，或队列暂不可用。未准入 Case 不会以空白信息替代展示。");
    }
  }

  useEffect(() => { void load(); }, []);

  return <main className="ros-page ros-admission-page">
    <header className="ros-page-head">
      <div><p className="ros-eyebrow">运营治理 · Case Admission</p><h1>历史 Case 准入</h1><p>这不是资料认领。管理员必须选择该 Case 已冻结并关联的原始资料，指定归属团队并说明依据；系统不会从创建人、内容哈希或来源地址推断租户。</p></div>
      <div className="ros-header-actions"><button className="ros-button ros-button--secondary" type="button" onClick={() => void load()}>刷新队列</button><Link className="ros-button ros-button--secondary" to="/monitoring">查看运行档案</Link></div>
    </header>
    {session && <p className="ros-admission-identity">当前管理员会话：<b>{session.tenant_id}</b>。提交会追加不可变准入记录，之后该 Case 才进入目标团队的工作台与运行档案。</p>}
    {message && <p className="ros-success" role="status">{message}</p>}
    {error && <p className="ros-error" role="alert">{error}</p>}
    {!items && !error && <AdmissionSkeleton />}
    {items?.length === 0 && <section className="ros-empty ros-empty--large"><h2>没有等待准入的历史事件 Case</h2><p>所有可见的旧事件 Case 均已拥有明确、可审计的租户归属。</p></section>}
    {items && items.length > 0 && <section className="ros-admission-list" aria-label="历史 Case 准入队列">{items.map((item) => <AdmissionRow key={item.case_id} item={item} defaultTenant={session?.tenant_id ?? ""} onAdmitted={(title) => { setMessage(`已准入「${title}」，它现在仅在目标团队的研究工作台中可见。`); void load(); }} />)}</section>}
  </main>;
}

function AdmissionRow({ item, defaultTenant, onAdmitted }: { item: LegacyCaseAdmissionCandidate; defaultTenant: string; onAdmitted: (title: string) => void }) {
  const [documentId, setDocumentId] = useState(item.documents[0]?.document_version_id ?? "");
  const [tenantId, setTenantId] = useState(defaultTenant);
  const [actor, setActor] = useState("human:case-administrator");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selected = item.documents.find((document) => document.document_version_id === documentId);

  async function submit() {
    if (!documentId || !tenantId.trim() || !actor.trim() || !reason.trim()) {
      setError("请选择冻结资料，并填写目标团队、操作人和准入依据。");
      return;
    }
    setSubmitting(true); setError(null);
    try {
      await researchOsApi.admitLegacyCase(item.case_id, { tenant_id: tenantId.trim(), initial_document_version_id: documentId, admitted_by: actor.trim(), reason: reason.trim() });
      onAdmitted(item.event_title);
    } catch {
      setError("准入未完成。请确认目标团队已由主机配置，并复核该资料确实属于当前 Case。");
    } finally { setSubmitting(false); }
  }

  return <article className="ros-admission-row">
    <header><div><p className="ros-eyebrow">未准入 Case · {new Date(item.created_at).toLocaleString("zh-CN")}</p><h2>{item.event_title}</h2><code>{item.case_id}</code></div><span className="ros-pill">需要人工归属</span></header>
    <div className="ros-admission-layout"><section><h3>选择初始冻结资料</h3><p>只能选择该 Case 在准入前已关联的资料。选择不会改写原文或重新解析内容。</p><div className="ros-admission-documents">{item.documents.map((document) => <label key={document.document_version_id} className={document.document_version_id === documentId ? "is-selected" : ""}><input type="radio" name={`source-${item.case_id}`} checked={document.document_version_id === documentId} onChange={() => setDocumentId(document.document_version_id)} /><span><strong>{document.title || "未命名冻结资料"}</strong><small>{document.source_url} · 可得于 {new Date(document.available_at).toLocaleString("zh-CN")}</small><code>{document.document_version_id}</code></span></label>)}</div></section>
      <section className="ros-admission-form"><h3>记录准入决定</h3><label>目标团队<input aria-label={`${item.event_title} 的目标团队`} value={tenantId} onChange={(event) => setTenantId(event.target.value)} placeholder="例如 public-equities" /></label><label>操作人<input aria-label={`${item.event_title} 的操作人`} value={actor} onChange={(event) => setActor(event.target.value)} /></label><label>准入依据<textarea aria-label={`${item.event_title} 的准入依据`} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明如何核验历史迁移记录、原始资料和团队归属" /></label>{selected && <p className="ros-admission-proof">将写入：<b>{selected.title || selected.document_version_id}</b>，以及上述团队、操作人与依据。</p>}{error && <p className="ros-error" role="alert">{error}</p>}<button className="ros-button ros-button--primary" type="button" disabled={submitting} onClick={() => void submit()}>{submitting ? "正在追加准入记录…" : "确认并准入此 Case"}</button></section></div>
  </article>;
}

function AdmissionSkeleton() { return <section className="ros-admission-list" aria-busy="true" aria-label="历史 Case 准入队列加载中">{[0, 1].map((item) => <article className="ros-admission-row ros-admission-row--loading" key={item}><i /><i /><i /></article>)}</section>; }
