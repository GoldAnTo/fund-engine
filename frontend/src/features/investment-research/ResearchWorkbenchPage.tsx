import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  investmentResearchApi,
  type ProductDraft,
  type ProductPreview,
  type ProductProject,
  type ProductRevision,
} from "../../data/investmentResearchApi";

const modules = [
  "概览",
  "来源与证据",
  "行业",
  "公司模型",
  "预测与情景",
  "估值",
  "判断与反证",
  "版本与变化",
  "研究备忘录",
] as const;

type ModuleName = typeof modules[number];
const enabledModules = new Set<ModuleName>(["概览", "版本与变化"]);

function answerabilityLabel(preview: ProductPreview): string {
  if (preview.assessment.answerability === "not_answerable") return "insufficient_evidence";
  return preview.assessment.answerability;
}

export default function ResearchWorkbenchPage() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<ProductProject | null>(null);
  const [draft, setDraft] = useState<ProductDraft | null>(null);
  const [preview, setPreview] = useState<ProductPreview | null>(null);
  const [revision, setRevision] = useState<ProductRevision | null>(null);
  const [activeModule, setActiveModule] = useState<ModuleName>("概览");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const loadEpochRef = useRef(0);

  useEffect(() => {
    const epoch = ++loadEpochRef.current;
    let active = true;
    const current = () => active && loadEpochRef.current === epoch;
    async function load() {
      setLoading(true);
      setLoadError(null);
      setPreviewError(null);
      setProject(null);
      setDraft(null);
      setPreview(null);
      setRevision(null);
      try {
        const [loadedProject, loadedDraft] = await Promise.all([
          investmentResearchApi.project(projectId),
          investmentResearchApi.draft(projectId),
        ]);
        if (!current()) return;
        setProject(loadedProject);
        setDraft(loadedDraft);
        const previewPromise = investmentResearchApi.preview(projectId, {
          schema_version: "underwriting.v1",
          expected_lock_version: loadedDraft.lock_version,
        }).then((value) => {
          if (current() && value.project_id === projectId) setPreview(value);
        }).catch((error: Error) => {
          if (current()) setPreviewError(error.message);
        });
        const revisionPromise = loadedDraft.base_revision_id
          ? investmentResearchApi.revision(loadedDraft.base_revision_id).then((value) => {
            if (current() && value.project_id === projectId) setRevision(value);
          })
          : Promise.resolve();
        await Promise.all([previewPromise, revisionPromise]);
      } catch (error) {
        if (current()) setLoadError(error instanceof Error ? error.message : "研究项目无法读取");
      } finally {
        if (current()) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [projectId]);

  if (loading) {
    return (
      <main className="ir-page ir-workbench" aria-busy="true">
        <div className="ir-workbench-skeleton"><span /><span /><span /></div>
      </main>
    );
  }

  if (loadError || !project || !draft) {
    return (
      <main className="ir-page ir-workbench">
        <div className="ir-alert" role="alert">
          <h1>研究项目无法读取</h1>
          <p>{loadError ?? "项目响应不完整"}</p>
          <Link to="/research">返回研究目录</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="ir-page ir-workbench">
      <header className="ir-workbench-head">
        <div>
          <p className="ir-eyebrow">Independent research · {project.id}</p>
          <h1>研究工作台</h1>
          <h2>{project.company_identity.canonical_name}</h2>
          <p>{project.security_identities.map((security) => `${security.symbol} · ${security.share_class} · ${security.exchange}`).join("；")}</p>
        </div>
        <span className="ir-draft-state">草稿版本 {draft.lock_version}</span>
      </header>

      <div className="ir-workbench-grid">
        <nav className="ir-module-nav" aria-label="研究模块">
          {modules.map((module) => {
            const enabled = enabledModules.has(module);
            return (
              <button
                className={activeModule === module ? "is-active" : ""}
                disabled={!enabled}
                key={module}
                onClick={() => enabled ? setActiveModule(module) : undefined}
                type="button"
              >
                <span>{module}</span>
                {enabled ? <small>{activeModule === module ? "当前" : "可查看"}</small> : <small>尚未建立</small>}
              </button>
            );
          })}
        </nav>

        <section className="ir-module-content" aria-live="polite">
          {activeModule === "概览" ? (
            <>
              <header>
                <p className="ir-eyebrow">Publication preview</p>
                <h2>当前可回答性</h2>
              </header>
              {preview ? (
                <div className="ir-answerability">
                  <strong>{answerabilityLabel(preview)}</strong>
                  <p>{preview.assessment.answerability === "not_answerable"
                    ? "当前证据不足，不形成投资方向或置信度。"
                    : "当前状态仅来自可冻结边界，仍需人工核验。"}</p>
                  <dl>
                    <div><dt>方向</dt><dd>{preview.assessment.direction ?? "方向尚未形成"}</dd></div>
                    <div><dt>置信度</dt><dd>{preview.assessment.confidence ?? "置信度尚未形成"}</dd></div>
                    <div><dt>边界时间</dt><dd>{new Date(preview.boundary_as_of).toLocaleString("zh-CN")}</dd></div>
                  </dl>
                  <section>
                    <h3>阻塞项</h3>
                    {preview.assessment.blockers.length > 0
                      ? <ul>{preview.assessment.blockers.map((item) => <li key={item}>{item}</li>)}</ul>
                      : <p>未记录阻塞项</p>}
                  </section>
                  <section>
                    <h3>解除条件</h3>
                    {preview.assessment.resolution_requirements.length > 0
                      ? <ul>{preview.assessment.resolution_requirements.map((item) => <li key={item}>{item}</li>)}</ul>
                      : <p>未记录解除条件</p>}
                  </section>
                </div>
              ) : (
                <p className="ir-alert" role="alert">{previewError ?? "publication preview 尚未建立"}</p>
              )}
            </>
          ) : (
            <>
              <header><p className="ir-eyebrow">Version ledger</p><h2>版本与变化</h2></header>
              <div className="ir-version-summary">
                <strong>当前草稿版本 {draft.lock_version}</strong>
                <p>最后更新 {new Date(draft.updated_at).toLocaleString("zh-CN")}</p>
                {revision ? (
                  <dl>
                    <div><dt>已选冻结版本</dt><dd>{revision.id}</dd></div>
                    <div><dt>序列</dt><dd>{revision.sequence}</dd></div>
                    <div><dt>可回答性</dt><dd>{revision.answerability}</dd></div>
                  </dl>
                ) : <p>尚无父冻结版本；当前工作区从初始草稿开始。</p>}
              </div>
            </>
          )}
        </section>

        <aside className="ir-boundary" aria-label="版本边界元数据">
          <p className="ir-eyebrow">Revision boundary</p>
          <h2>边界元数据</h2>
          {preview ? (
            <dl>
              <div><dt>HistoricalBasis</dt><dd>{preview.boundary.historical_basis_id}</dd></div>
              <div><dt>Mandate</dt><dd>{preview.boundary.mandate_id}</dd></div>
              <div><dt>Scope</dt><dd>{preview.boundary.scope_id}</dd></div>
              <div><dt>Agenda</dt><dd>{preview.boundary.agenda_id}</dd></div>
              <div><dt>价格快照</dt><dd>{preview.boundary.price_snapshot_ids.length}</dd></div>
              <div><dt>汇率快照</dt><dd>{preview.boundary.fx_snapshot_ids.length}</dd></div>
              <div><dt>证券权利</dt><dd>{preview.boundary.security_rights_ids.length}</dd></div>
              <div><dt>边界哈希</dt><dd>{preview.boundary_hash}</dd></div>
            </dl>
          ) : <p>尚无法读取边界元数据。</p>}
        </aside>
      </div>
    </main>
  );
}
