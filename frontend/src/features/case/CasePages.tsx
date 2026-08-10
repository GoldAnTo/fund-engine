import { useEffect, useState, type ChangeEvent, type ReactNode } from "react";
import {
  Link,
  NavLink,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";

import {
  researchOsApi,
  type AtomicClaimCandidate,
  type CaseMechanismProtocol,
  type MechanismTemplate,
  type MetricDefinition,
  type Monitor,
  type MonitorDetail,
  type Researchability,
  type ResearchNetwork,
} from "../../app/researchOsApi";
import { researchClient } from "../../data/researchClient";
import type {
  EventResearchClient,
  EventSourceType,
  EventWorkbench,
} from "../../domain/eventResearch";
import {
  decodeRecoveryRouteState,
  encodeRecoveryRouteState,
} from "../../domain/recoveryRoute";
import {
  formatRunEventDetails,
  runStageLabel,
  runStatusLabel,
  runTriggerLabel,
} from "../../domain/runPresentation";
import {
  parseQualityLabel,
  sourceAuthorityLabel,
  sourceRetentionLabel,
  sourceTypeLabel,
  sourceTypeListLabel,
} from "../../domain/sourcePresentation";
import {
  DEFAULT_SOURCE_GOVERNANCE,
  SourceGovernanceFields,
  sourceGovernanceMetadata,
  sourceGovernanceValidationError,
} from "../sources/SourceGovernanceFields";
import type { DocumentSpan, SourceDocumentView } from "../../domain/types";
import { MarketExpressionContent } from "./MarketExpressionContent";
import {
  MarketFundProfile,
  MarketStockProfile,
} from "./MarketInstrumentProfiles";
import { CaseRelationsContent } from "./CaseRelationsContent";
import { WikiInspectorContent } from "./WikiInspectorContent";

const tabs = [
  ["", "研究结论"],
  ["evidence", "命题与证据"],
  ["documents", "原文资料"],
  ["review", "证据审核"],
  ["wiki", "Wiki 图谱"],
  ["market", "市场与表达"],
  ["monitor", "监测与运行"],
  ["relations", "关联研究"],
] as const;

const relationLabels: Record<
  ResearchNetwork["reviewed_relations"][number]["relation_type"],
  string
> = {
  shared_driver: "共享驱动",
  follow_up_validation: "后续验证",
  potential_conflict: "可能冲突",
  shared_material: "共享资料",
};

function CaseFrame({
  children,
}: {
  children: (workbench: EventWorkbench, caseId: string) => ReactNode;
}) {
  const { caseId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [data, setData] = useState<EventWorkbench | null>(null);
  const [caseOptions, setCaseOptions] = useState<
    Array<{ id: string; eventTitle: string }>
  >([]);
  const [loadError, setLoadError] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    if (!caseId) return () => { active = false; };
    setData(null);
    setLoadError(false);
    researchClient
      .getEventWorkbench(caseId)
      .then((workbench) => {
        if (active) setData(workbench);
      })
      .catch(() => {
        if (active) setLoadError(true);
      });
    return () => { active = false; };
  }, [caseId, reload]);
  useEffect(() => {
    let active = true;
    researchClient
      .listEventResearch()
      .then((items) => {
        if (!active) return;
        setCaseOptions(
          items.map((item) => ({ id: item.id, eventTitle: item.eventTitle })),
        );
      })
      .catch(() => {
        if (active) setCaseOptions([]);
      });
    return () => { active = false; };
  }, []);
  function switchCase(nextCaseId: string) {
    const suffix = location.pathname.startsWith(`/events/${caseId}`)
      ? location.pathname.slice(`/events/${caseId}`.length)
      : "";
    navigate(`/events/${nextCaseId}${suffix}${location.search}`);
  }
  if (!data)
    return (
      <main className="ros-page ros-case-page">
        {loadError ? <div className="ros-empty" role="alert">
          {loadError ? (
            <>
              <strong>无法读取这个 Case</strong>
              <p>没有展示替代数据；请检查权限、网络或 Case 标识。</p>
              <button
                className="ros-button ros-button--secondary"
                type="button"
                onClick={() => setReload((value) => value + 1)}
              >
                重试
              </button>
            </>
          ) : (
            "正在读取 Case；若无权限或 Case 不存在，系统不会展示替代数据。"
          )}
        </div> : <CaseWorkbenchSkeleton />}
      </main>
    );
  return (
    <main className="ros-page ros-case-page">
      <header className="ros-case-header">
        <div className="ros-case-header__controls">
          <Link to="/events" className="ros-button ros-button--secondary">
            返回研究调度
          </Link>
          <label>
            切换 ResearchCase
            <select
              aria-label="切换 ResearchCase"
              value={caseId}
              onChange={(event) => switchCase(event.target.value)}
            >
              {caseOptions.some((item) => item.id === caseId) ? null : (
                <option value={caseId}>{data.event.eventTitle}</option>
              )}
              {caseOptions.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.eventTitle}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="ros-eyebrow">
          ResearchCase v{data.scope.version} ·{" "}
          {data.event.ticker || "未绑定股票"}
        </p>
        <h1>{data.event.eventTitle}</h1>
        <p>{data.lifecycle.summary}</p>
        <div className="ros-case-header__facts">
          <span>已审核证据 {data.progress.verified}</span>
          <span>待审核 {data.progress.pending}</span>
          <span>无效来源 {data.progress.invalidSource}</span>
          <span>
            {data.lifecycle.activeRunId ? "运行记录可查看" : "无后台运行"}
          </span>
        </div>
      </header>
      <nav className="ros-case-tabs" aria-label="Case 页面">
        {tabs.map(([suffix, label]) => (
          <NavLink
            key={suffix}
            end={suffix === ""}
            to={`/events/${caseId}${suffix ? `/${suffix}` : ""}`}
          >
            {label}
          </NavLink>
        ))}
      </nav>
      {children(data, caseId)}
    </main>
  );
}

function CaseWorkbenchSkeleton() {
  return <section className="ros-case-loading" aria-label="Case 工作台加载中" aria-busy="true">
    <header className="ros-case-loading__header"><Link to="/events" className="ros-button ros-button--secondary">返回研究调度</Link><span /></header>
    <div className="ros-case-loading__title"><i /><b /><em /></div>
    <nav className="ros-case-tabs" aria-label="Case 页面加载中"><span /><span /><span /><span /><span /><span /><span /><span /></nav>
    <div className="ros-case-loading__body">{[0, 1, 2].map((item) => <div data-testid="case-workbench-skeleton" key={item}><i /><b /><em /></div>)}</div>
  </section>;
}

function FactorList({ data }: { data: EventWorkbench }) {
  return (
    <section className="ros-factor-list">
      <p className="ros-eyebrow">关键因素与验证缺口</p>
      {data.factors.map((factor) => (
        <article className="ros-factor-row" key={factor.position}>
          <span>{String(factor.position).padStart(2, "0")}</span>
          <div>
            <strong>{factor.statement}</strong>
            <small>
              已审核支持 {factor.reviewedSupportCount} · 反证{" "}
              {factor.reviewedContradictionCount} · AI 待审{" "}
              {factor.pendingProposalCount}
            </small>
            {factor.currentGap && <em>还缺：{factor.currentGap}</em>}
          </div>
        </article>
      ))}
    </section>
  );
}

function CaseRelationRail({ caseId }: { caseId: string }) {
  const [relations, setRelations] = useState<ResearchNetwork | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let active = true;
    setRelations(null);
    setUnavailable(false);
    researchOsApi.caseRelations(caseId)
      .then((value) => active && setRelations(value))
      .catch(() => active && setUnavailable(true));
    return () => {
      active = false;
    };
  }, [caseId]);

  const reviewed = relations?.reviewed_relations.slice(0, 3) ?? [];
  const candidateCount = relations?.candidate_relations.length ?? 0;
  return <section className="ros-rail-section ros-case-relation-rail">
    <p className="ros-eyebrow">关联研究</p>
    <h2>已审核关联</h2>
    {unavailable ? <p>关联状态暂不可读取；不会显示其他 Case 的替代内容。</p> : !relations ? <p>正在读取当前 Case 的关联上下文…</p> : reviewed.length ? <ul>{reviewed.map((relation) => {
      const other = relation.source_case.case_id === caseId ? relation.target_case : relation.source_case;
      return <li key={relation.id}><Link to={`/events/${other.case_id}`}>{other.title}</Link><small>{relationLabels[relation.relation_type]} · {relation.reason}</small></li>;
    })}</ul> : <p>当前没有已审核关联。</p>}
    {candidateCount > 0 && <p className="ros-note">另有 {candidateCount} 条 AI 候选，未经人工复核。<Link to={`/events/${caseId}/relations`}>审核关联候选 →</Link></p>}
    <Link to={`/events/${caseId}/relations`}>查看全部关联 →</Link>
  </section>;
}

export function CaseEvidencePage() {
  return (
    <CaseFrame>
      {(data, caseId) => (
        <section className="ros-evidence-page">
          <header className="ros-section-heading">
            <div>
              <p className="ros-eyebrow">命题与证据</p>
              <h2>每一条关系都保留原文、时点与审核边界</h2>
            </div>
            <Link
              className="ros-button ros-button--secondary"
              to={`/events/${caseId}/documents`}
            >
              原文资料
            </Link>
          </header>
          <FactorList data={data} />
          <section className="ros-evidence-list">
            <p className="ros-eyebrow">已关联资料</p>
            {data.evidence.length === 0 ? (
              <div className="ros-empty">
                当前没有可展示的关联资料；未审核候选不会被写成正式依据。
              </div>
            ) : (
              data.evidence.map((evidence, index) => (
                <article
                  className="ros-evidence-row"
                  key={`${evidence.factorStatement}-${index}`}
                >
                  <div>
                    <span
                      className={`ros-pill ${evidence.reviewState === "reviewed" ? "ros-pill--system" : "ros-pill--human"}`}
                    >
                      {evidence.reviewState === "reviewed"
                        ? "已审核关系"
                        : "AI 候选，未经复核"}
                    </span>
                    <h3>{evidence.factorStatement}</h3>
                    {evidence.sourceVisibleInCase ? (
                      <blockquote>{evidence.excerpt}</blockquote>
                    ) : (
                      <p className="ros-muted">
                        原文内容受当前来源许可控制，不能在此 Case 展示。
                      </p>
                    )}
                  </div>
                  <dl>
                    <div>
                      <dt>关系角色</dt>
                      <dd>{evidence.role}</dd>
                    </div>
                    {evidence.sourceVisibleInCase && (
                      <div>
                        <dt>精确定位</dt>
                        <dd>{JSON.stringify(evidence.locator)}</dd>
                      </div>
                    )}
                    <div>
                      <dt>可用时点</dt>
                      <dd>{evidence.availableAt}</dd>
                    </div>
                  </dl>
                  <div className="ros-evidence-row__actions">
                    {evidence.sourceTitle && (
                      <span>{evidence.sourceTitle}</span>
                    )}
                    {evidence.sourceVisibleInCase && evidence.documentVersionId ? (
                      <Link
                        className="ros-button ros-button--secondary"
                        to={`/events/${caseId}/documents?document=${encodeURIComponent(evidence.documentVersionId)}`}
                      >
                        定位到冻结原文
                      </Link>
                    ) : (
                      <span>当前未获在此 Case 定位原文的许可</span>
                    )}
                  </div>
                </article>
              ))
            )}
          </section>
        </section>
      )}
    </CaseFrame>
  );
}

export function CaseDocumentsPage() {
  return (
    <CaseFrame>
      {(data, caseId) => (
        <CaseDocumentsContent
          caseId={caseId}
          isPublished={data.lifecycle.status === "published"}
          publishedConclusion={
            data.conclusion.state === "published" ? data.conclusion.text : null
          }
        />
      )}
    </CaseFrame>
  );
}
function CaseDocumentsContent({
  caseId,
  isPublished,
  publishedConclusion,
}: {
  caseId: string;
  isPublished: boolean;
  publishedConclusion: string | null;
}) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [documents, setDocuments] = useState<SourceDocumentView[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{
    document: SourceDocumentView;
    spans: DocumentSpan[];
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [extractingId, setExtractingId] = useState<string | null>(null);
  const [extractionNotice, setExtractionNotice] = useState<string | null>(null);
  const [noClaimDocumentId, setNoClaimDocumentId] = useState<string | null>(
    null,
  );
  const [documentReload, setDocumentReload] = useState(0);
  const [preferredDocumentId, setPreferredDocumentId] = useState<string | null>(
    null,
  );
  useEffect(() => {
    let active = true;
    setDocuments(null);
    setError(null);
    researchClient
      .getDocuments({ caseId })
      .then((value) => {
        if (!active) return;
        setDocuments(value);
        const requested = preferredDocumentId || searchParams.get("document");
        setSelectedId(
          value.some((item) => item.id === requested)
            ? requested
            : (value[0]?.id ?? null),
        );
      })
      .catch(
        () =>
          active &&
          setError(
            "无法读取这个 Case 的原文资料；系统没有展示其他 Case 的替代内容。",
          ),
      );
    return () => {
      active = false;
    };
  }, [caseId, searchParams, preferredDocumentId, documentReload]);
  useEffect(() => {
    let active = true;
    if (!selectedId) return;
    setDetail(null);
    researchClient
      .getDocumentDetail(selectedId, caseId)
      .then((value) => active && setDetail(value))
      .catch(() => active && setError("无法读取所选资料的冻结内容。"));
    return () => {
      active = false;
    };
  }, [caseId, selectedId]);
  async function extractCandidates(document: SourceDocumentView) {
    setExtractingId(document.id);
    setError(null);
    setExtractionNotice(null);
    try {
      const result = await researchClient.extractStatements(document.id);
      if (result.candidateCount) {
        setNoClaimDocumentId(null);
        setExtractionNotice(
          `已从此冻结版本创建 ${result.candidateCount} 条待人工审核的原子陈述；尚未发布为正式证据，也没有启动后台补证。`,
        );
      } else {
        setNoClaimDocumentId(document.id);
        setExtractionNotice(
          `${result.reason || "本次抽取未产生候选"}；原文与抽取记录仍保留。你可以补充可定位正文后重新核验，不会创建新的 Case。`,
        );
      }
    } catch {
      setError(
        "候选抽取没有完成；系统未发布任何正式陈述，也没有启动后台运行。",
      );
    } finally {
      setExtractingId(null);
    }
  }
  const focusSpanId = searchParams.get("span");
  const recoveryTarget = decodeRecoveryRouteState(searchParams);
  function beginRecovery(
    documentId: string,
    reason: "parse_failed" | "no_claims",
  ) {
    setSearchParams(encodeRecoveryRouteState({ documentId, reason }));
  }
  function cancelRecovery() {
    const next = new URLSearchParams(searchParams);
    next.delete("recovery");
    next.delete("recovery_reason");
    setSearchParams(next);
  }
  function selectDocument(documentId: string) {
    setPreferredDocumentId(documentId);
    setSelectedId(documentId);
    const next = new URLSearchParams(searchParams);
    next.set("document", documentId);
    next.delete("span");
    if (recoveryTarget?.documentId !== documentId) {
      next.delete("recovery");
      next.delete("recovery_reason");
    }
    setSearchParams(next);
  }
  const currentRecoveryReason =
    detail?.document.parse_quality === "failed" ? "parse_failed" : "no_claims";
  const needsSupplement = Boolean(
    detail &&
    (detail.document.parse_quality === "failed" ||
      noClaimDocumentId === detail.document.id ||
      (recoveryTarget?.documentId === detail.document.id &&
        recoveryTarget.reason === "no_claims")),
  );
  return (
    <section className="ros-documents">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">来源阅读</p>
          <h2>原文资料</h2>
          <p>
            只显示已关联当前 Case
            的资料；正文、定位与版本来自资料记录，而非系统推测。
          </p>
        </div>
        <Link
          className="ros-button ros-button--secondary"
          to={`/events/${caseId}/evidence`}
        >
          返回命题与证据
        </Link>
      </header>
      {isPublished && (
        <PublishedMaterialDecisionForm
          caseId={caseId}
          publishedConclusion={publishedConclusion}
          onFrozen={(id) => {
            setPreferredDocumentId(id);
            setDocumentReload((value) => value + 1);
          }}
        />
      )}
      {error && <p className="ros-error">{error}</p>}
      {extractionNotice && (
        <p className="ros-success">
          {extractionNotice}{" "}
          <Link to={`/events/${caseId}/review`}>进入证据审核 →</Link>
        </p>
      )}
      {!documents ? (
        <div className="ros-empty">正在读取当前 Case 的原文资料…</div>
      ) : documents.length === 0 ? (
        <div className="ros-empty">
          当前 Case 暂无可读取的原文资料；这不表示其他 Case 的资料可以被复用。
        </div>
      ) : (
        <div className="ros-documents-grid">
          <section
            className="ros-document-list"
            aria-label="当前 Case 原文资料"
          >
            {documents.map((document) => (
              <button
                type="button"
                key={document.id}
                className={`ros-document-row${document.id === selectedId ? " is-selected" : ""}`}
                onClick={() => selectDocument(document.id)}
              >
                <span
                  className={`ros-pill ${document.parse_quality === "ok" ? "ros-pill--system" : "ros-pill--human"}`}
                >
                  {document.parse_quality === "ok"
                    ? "解析完成"
                    : document.parse_quality === "partial"
                      ? "解析不完整"
                      : "解析失败"}
                </span>
                <strong>{document.title || "未命名资料"}</strong>
                <small>
                  {document.publisher || "发布方未记录"} ·{" "}
                  {document.document_type || "类型未记录"}
                </small>
                <small>
                  {document.version_label || "版本未记录"} ·{" "}
                  {document.span_count} 个定位片段
                </small>
              </button>
            ))}
          </section>
          <section className="ros-document-reader" aria-live="polite">
            {!detail ? (
              <div className="ros-empty ros-empty--compact">
                正在读取冻结内容…
              </div>
            ) : (
              <DocumentReader
                caseId={caseId}
                detail={detail}
                isPublished={isPublished}
                focusSpanId={focusSpanId}
                extracting={extractingId === detail.document.id}
                onExtract={() => extractCandidates(detail.document)}
                onSupplementCreated={(id) => {
                  setNoClaimDocumentId(null);
                  setPreferredDocumentId(id);
                  setDocumentReload((value) => value + 1);
                }}
                needsSupplement={needsSupplement}
                recoveryReason={currentRecoveryReason}
                recoveryActive={
                  recoveryTarget?.documentId === detail.document.id &&
                  recoveryTarget.reason === currentRecoveryReason
                }
                onBeginRecovery={() =>
                  beginRecovery(detail.document.id, currentRecoveryReason)
                }
                onCancelRecovery={cancelRecovery}
                onStartNewMaterials={() => navigate("/events/new")}
              />
            )}
          </section>
        </div>
      )}
    </section>
  );
}

function PublishedMaterialDecisionForm({
  caseId,
  publishedConclusion,
  onFrozen,
}: {
  caseId: string;
  publishedConclusion: string | null;
  onFrozen: (documentId: string) => void;
}) {
  const navigate = useNavigate();
  const [rawInput, setRawInput] = useState("");
  const [originalFile, setOriginalFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [sourceType, setSourceType] = useState<EventSourceType>("pasted_snapshot");
  const [sourceMetadata, setSourceMetadata] = useState<Record<string, unknown>>({});
  const [sourcePermissions, setSourcePermissions] = useState({ ai_processing: true, display: true, export: false, api: false });
  const [sourceGovernance, setSourceGovernance] = useState(DEFAULT_SOURCE_GOVERNANCE);
  const [providerName, setProviderName] = useState("");
  const [providerRecordId, setProviderRecordId] = useState("");
  const [providerRequestScope, setProviderRequestScope] = useState("");
  const [decision, setDecision] = useState<"reopen" | "no_change">("reopen");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const governanceError = sourceGovernanceValidationError(sourceGovernance);
  const sourceReady = sourceType === "licensed_provider"
    ? Boolean(providerName.trim() && providerRecordId.trim() && providerRequestScope.trim())
    : sourceType === "public_url"
      ? Boolean(sourceUrl.trim())
      : true;
  const materialReady = sourceType === "uploaded_file"
    ? Boolean(originalFile)
    : Boolean(rawInput.trim());
  async function submit() {
    if (!materialReady || !reason.trim() || !sourceReady || governanceError) return;
    setBusy(true);
    setMessage(null);
    try {
      const materialMetadata = {
          ...sourceMetadata,
          ...sourceGovernanceMetadata(sourceGovernance),
          permissions: sourcePermissions,
          ...(sourceType === "licensed_provider" ? { provider_name: providerName.trim(), provider_record_id: providerRecordId.trim(), request_scope: { declared_scope: providerRequestScope.trim() }, retrieval_reference: sourceUrl.trim() || undefined } : {}),
          ...(sourceType === "uploaded_file" && sourceUrl.trim()
            ? { retrieval_reference: sourceUrl.trim() }
            : {}),
          ...(sourceType === "public_url" ? { intake_note: "公开网页 URL 仅作为可复查线索；已冻结内容尚未完成原文核验。" } : {}),
          authority_level:
            sourceType === "licensed_provider"
              ? "licensed_research"
              : "user_supplied",
        };
      const result = sourceType === "uploaded_file" && originalFile
        ? await researchClient.decidePublishedUploadedMaterial({
            caseId,
            file: originalFile,
            sourceMetadata: materialMetadata,
            decision,
            reason: reason.trim(),
            actor: "human:researcher",
          })
        : await researchClient.decidePublishedMaterial({
            caseId,
            rawInput: rawInput.trim(),
            sourceUrl: sourceUrl.trim() || undefined,
            sourceType,
            sourceMetadata: materialMetadata,
            decision,
            reason: reason.trim(),
            actor: "human:researcher",
          });
      onFrozen(result.documentVersionId);
      setMessage(
        result.recoveryRequired
          ? `已冻结原件 ${result.documentVersionId}，但解析尚未完成；未创建后继运行，请从原文资料恢复后再决定是否重新复核。`
          : result.decision === "reopen"
          ? `已冻结新材料并创建后继运行 ${result.runId}；此前发布结论未被改写。`
          : `已冻结新材料并记录“不改变当前判断”的人工决定 ${result.decisionEventId}；发布结论保持有效。`,
      );
      if (result.decision === "reopen" && !result.recoveryRequired) navigate(`/events/${caseId}/monitor`);
    } catch {
      setMessage(
        "新材料或人工决定未保存；此前发布结论保持不变。请检查必填信息后重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  async function loadOriginalFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setOriginalFile(file);
    setSourceMetadata((current) => ({
      ...current,
      file_name: file.name,
      mime_type: file.type || "text/plain",
      byte_size: file.size,
    }));
    if (file.type === "application/pdf") {
      setRawInput("");
      setMessage("PDF 原件将由服务端冻结并解析；浏览器不会把它伪装成可读正文。");
      return;
    }
    try {
      const text =
        typeof file.text === "function"
          ? await file.text()
          : await readTextSnapshot(file);
      setRawInput(text);
      setMessage(null);
    } catch {
      setMessage(
        "无法预览该文件；提交后仍会冻结原件，并由服务端记录可恢复的解析结果。",
      );
    }
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
  return (
    <section className="ros-material-continuation">
      <p className="ros-eyebrow">已发布结论 · 新材料决定</p>
      <h3>新材料是否需要改变复核范围？</h3>
      <p>
        先冻结这份材料，再由研究员决定纳入重新复核或记录为不改变当前判断。两种决定都会保留原因；系统不会静默改写发布结论。
      </p>
      <section className="ros-material-comparison" aria-label="已发布结论与新材料对照">
        <article>
          <p className="ros-eyebrow">当前已发布结论</p>
          <blockquote>{publishedConclusion || "当前发布结论文本不可读取；不能据此推定内容。"}</blockquote>
        </article>
        <article>
          <p className="ros-eyebrow">待冻结的新材料</p>
          <blockquote>{originalFile
            ? `${originalFile.name} · ${originalFile.type || "未知类型"} · ${originalFile.size.toLocaleString()} 字节${originalFile.type === "application/pdf" ? "。PDF 原件将在服务端冻结并解析；本页不伪造正文预览。" : rawInput.trim() ? `\n\n${rawInput.trim()}` : "。原件将被完整冻结后进入人工复核。"}`
            : rawInput.trim() || "输入新材料后在此逐字对照；系统不会自动判断差异或改写结论。"}</blockquote>
        </article>
      </section>
      <p className="ros-note">请在决定理由中说明它影响的判断、关键因素或反证条件；并列对照只帮助人工复核，不构成自动差异结论。</p>
      <label>
        新材料正文
        <textarea
          aria-label="新增材料正文"
          value={rawInput}
          onChange={(event) => setRawInput(event.target.value)}
          placeholder="粘贴新研报、公告或其他已获准使用的正文…"
        />
      </label>
      <label>
        来源接入方式
        <select
          aria-label="新增材料来源接入方式"
          value={sourceType}
          onChange={(event) =>
            changeSourceType(event.target.value as typeof sourceType)
          }
        >
          <option value="pasted_snapshot">粘贴快照</option>
          <option value="uploaded_file">上传原件（PDF/TXT/MD/CSV）</option>
          <option value="licensed_provider">授权数据源快照</option>
          <option value="public_url">公开网页快照</option>
        </select>
      </label>
      {sourceType === "licensed_provider" && (
        <section className="ros-source-governance">
          <label>
            新增材料供应商名称
            <input aria-label="新增材料供应商名称" value={providerName} onChange={(event) => setProviderName(event.target.value)} placeholder="例如：聚源" />
          </label>
          <label>
            新增材料供应商记录 ID
            <input aria-label="新增材料供应商记录 ID" value={providerRecordId} onChange={(event) => setProviderRecordId(event.target.value)} placeholder="可重取的报告或公告记录 ID" />
          </label>
          <label>
            新增材料供应商查询口径
            <textarea aria-label="新增材料供应商查询口径" value={providerRequestScope} onChange={(event) => setProviderRequestScope(event.target.value)} placeholder="例如：研报 / 标的 000001 / 2026H1" />
          </label>
          <small>授权来源必须固定供应商、具体记录和查询口径；否则不能作为此决定的已冻结材料。</small>
        </section>
      )}
      <fieldset className="ros-source-governance">
        <legend>新增材料使用许可声明</legend>
        <small>这些权限会随冻结版本保存；勾选并不代表材料已审核或足以改变已发布结论。</small>
        {([['ai_processing', '新增材料允许 AI 处理'], ['display', '新增材料允许团队展示'], ['export', '新增材料允许导出'], ['api', '新增材料允许 API 使用']] as const).map(([key, label]) => (
          <label key={key}><input aria-label={label} type="checkbox" checked={sourcePermissions[key]} onChange={(event) => setSourcePermissions((current) => ({ ...current, [key]: event.target.checked }))} /> {label}</label>
        ))}
      </fieldset>
      <SourceGovernanceFields value={sourceGovernance} onChange={setSourceGovernance} />
      {sourceType === "uploaded_file" && (
        <label>
          上传新增材料原件
          <input
            aria-label="上传新增材料原件"
            type="file"
            accept="application/pdf,text/plain,text/markdown,.txt,.md,.csv"
            onChange={loadOriginalFile}
          />
          <small>
            冻结原始 PDF、TXT、Markdown 或 CSV。PDF 正文只在服务端解析；解析失败也会保留原件、许可与恢复入口。
          </small>
        </label>
      )}
      <label>
        {sourceType === "public_url" ? "公开网页链接（必填）" : "来源链接（可选）"}
        <input
          aria-label={sourceType === "public_url" ? "公开网页链接（必填）" : undefined}
          value={sourceUrl}
          onChange={(event) => setSourceUrl(event.target.value)}
          placeholder="https://…"
        />
      </label>
      {sourceType === "public_url" && <p className="ros-note">系统只冻结你提交的正文快照，不会抓取网页或将 URL 视为已核验内容；它仍需人工核对后才能进入正式复核。</p>}
      <fieldset>
        <legend>人工决定</legend>
        <label>
          <input
            type="radio"
            checked={decision === "reopen"}
            onChange={() => setDecision("reopen")}
          />
          纳入重新复核：创建后继运行，旧结论保持可回放
        </label>
        <label>
          <input
            type="radio"
            checked={decision === "no_change"}
            onChange={() => setDecision("no_change")}
          />
          记录为不改变当前判断：保留材料和理由，不启动运行
        </label>
      </fieldset>
      <label>
        决定理由
        <textarea
          aria-label="新材料决定理由"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="说明它影响或不影响哪个已发布判断、关键因素或反证条件…"
        />
      </label>
      <button
        className="ros-button ros-button--primary"
        type="button"
        disabled={!materialReady || !reason.trim() || !sourceReady || Boolean(governanceError) || busy}
        onClick={() => void submit()}
      >
        {busy
          ? "正在冻结并记录决定…"
          : decision === "reopen"
            ? "冻结材料并纳入重新复核"
            : "冻结材料并记录不改变判断"}
      </button>
      {message && (
        <p
          className={message.startsWith("已冻结")
            ? "ros-success"
            : message.startsWith("无法") || message.startsWith("新材料")
              ? "ros-error"
              : "ros-note"}
        >
          {message}
        </p>
      )}
    </section>
  );
}

function readTextSnapshot(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error("file read failed"));
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsText(file);
  });
}
function DocumentReader({
  caseId,
  detail,
  isPublished,
  focusSpanId,
  extracting,
  onExtract,
  onSupplementCreated,
  needsSupplement,
  recoveryReason,
  recoveryActive,
  onBeginRecovery,
  onCancelRecovery,
  onStartNewMaterials,
}: {
  caseId: string;
  detail: { document: SourceDocumentView; spans: DocumentSpan[] };
  isPublished: boolean;
  focusSpanId: string | null;
  extracting: boolean;
  onExtract: () => void;
  onSupplementCreated: (documentId: string) => void;
  needsSupplement: boolean;
  recoveryReason: "parse_failed" | "no_claims";
  recoveryActive: boolean;
  onBeginRecovery: () => void;
  onCancelRecovery: () => void;
  onStartNewMaterials: () => void;
}) {
  const { document, spans } = detail;
  const contract = document.source_contract;
  const originalFile = document.original_file;
  const permissionText = contract
    ? `AI ${contract.permissions.ai_processing ? "允许" : "禁止"} · 展示 ${contract.permissions.display ? "允许" : "禁止"} · 导出 ${contract.permissions.export ? "允许" : "禁止"} · API ${contract.permissions.api ? "允许" : "禁止"}`
    : "未记录；不得据此推定可处理或可导出";
  const publicSourceUrl = document.source_url?.match(/^https?:\/\//i)
    ? document.source_url
    : null;
  const contentDisplayAllowed = contract?.permissions.display !== false;
  const displayableSpans = contentDisplayAllowed ? spans : [];
  const extractionAllowed =
    document.parse_quality !== "failed" &&
    contentDisplayAllowed &&
    contract?.permissions.ai_processing !== false &&
    contract?.status === "admitted";
  const continuationAllowed = contract?.status === "admitted";
  const contractRestrictionNotice =
    !contentDisplayAllowed
      ? "来源合同禁止展示：系统仅保留审核元数据，不显示正文、定位或引用。"
      : contract?.status === "restricted"
      ? "来源合同当前受限：此快照仅用于审计回放，不能继续提取或提出候选。"
      : !contract
        ? "来源许可未完整记录：此快照不可据此继续处理或提出候选。"
        : null;
  const focused =
    focusSpanId && displayableSpans.some((span) => span.id === focusSpanId);
  const [reason, setReason] = useState("");
  const [continuing, setContinuing] = useState(false);
  const [continuationNotice, setContinuationNotice] = useState<string | null>(
    null,
  );
  const [candidateSpanId, setCandidateSpanId] = useState<string | null>(null);
  const [candidateText, setCandidateText] = useState("");
  const [candidateType, setCandidateType] = useState<
    "reported_claim" | "forecast" | "research_opinion"
  >("reported_claim");
  const [candidateSubmitting, setCandidateSubmitting] = useState(false);
  const [candidateNotice, setCandidateNotice] = useState<string | null>(null);
  async function continueFromMaterial() {
    if (!reason.trim()) return;
    setContinuing(true);
    setContinuationNotice(null);
    try {
      const next = await researchClient.continueEventResearch({
        caseId,
        documentVersionId: document.id,
        reason: reason.trim(),
        triggeredBy: "human:researcher",
      });
      setContinuationNotice(
        `已创建后继运行 ${next.runId}；此前发布结论未被改写，新材料将先进入审核。`,
      );
    } catch {
      setContinuationNotice(
        "无法创建后继研究；未改写此前结论。请确认资料已冻结、当前 Case 已配置监控，并重试。",
      );
    } finally {
      setContinuing(false);
    }
  }
  function beginCandidate(span: DocumentSpan) {
    setCandidateSpanId(span.id);
    setCandidateText(span.verbatim_text);
    setCandidateType("reported_claim");
    setCandidateNotice(null);
  }
  async function createCandidate(span: DocumentSpan) {
    if (!candidateText.trim()) return;
    setCandidateSubmitting(true);
    setCandidateNotice(null);
    try {
      await researchOsApi.createAtomicClaim(caseId, {
        source_span_id: span.id,
        normalized_text: candidateText.trim(),
        claim_type: candidateType,
        assertion_actor: document.publisher,
        scope: { research_case_id: caseId },
        actor: "human:researcher",
      });
      setCandidateNotice("已创建待审候选，尚未写入正式结论。");
      setCandidateSpanId(null);
    } catch {
      setCandidateNotice(
        "无法创建待审候选；原文、既有候选和结论均未被改写。请确认本资料仍获展示许可后重试。",
      );
    } finally {
      setCandidateSubmitting(false);
    }
  }
  return (
    <>
      <header>
        <span
          className={`ros-pill ${contract?.status === "admitted" ? "ros-pill--system" : "ros-pill--human"}`}
        >
          {contract?.status === "admitted"
            ? "来源已准入"
            : contract
              ? "来源当前受限"
              : "许可未完整记录"}
        </span>
        <h3>{document.title || "未命名资料"}</h3>
        <p>
          {!contentDisplayAllowed
            ? "资料正文与原件信息不予展示；仅保留审计元数据。"
            : originalFile
            ? originalFile.mime_type === "application/pdf"
              ? "PDF 原件已冻结；解析定位另行保存。"
              : "文本原件已冻结；解析内容作为定位片段另行展示。"
            : "内容快照（当前 V1 未提供原件文件）"}
        </p>
      </header>
      {contractRestrictionNotice && (
        <p className="ros-error" role="alert">
          {contractRestrictionNotice}
        </p>
      )}
      <dl className="ros-definition">
        <div>
          <dt>冻结资料 ID</dt>
          <dd>
            <code>{document.id}</code>
          </dd>
        </div>
        <div>
          <dt>来源类型</dt>
          <dd>{sourceTypeLabel(contract?.source_type || document.document_type)}</dd>
        </div>
        <div>
          <dt>来源定位</dt>
          <dd>
            {contentDisplayAllowed && publicSourceUrl ? (
              <a
                href={publicSourceUrl}
                target="_blank"
                rel="noreferrer"
                aria-label="打开来源链接"
              >
                {publicSourceUrl}
              </a>
            ) : contentDisplayAllowed && document.source_url ? (
              <code>{document.source_url}</code>
            ) : (
              "未记录"
            )}
          </dd>
        </div>
        <div>
          <dt>来源权威性</dt>
          <dd>{sourceAuthorityLabel(document.source_authority)}</dd>
        </div>
        <div>
          <dt>发布方</dt>
          <dd>
            {contract?.provider_or_tenant || document.publisher || "未记录"}
          </dd>
        </div>
        {contract?.provider_record && (
          <>
            <div>
              <dt>供应商记录</dt>
              <dd>
                {contract.provider_record.provider_name} · {contract.provider_record.provider_record_id}
              </dd>
            </div>
            <div>
              <dt>获取口径</dt>
              <dd>{JSON.stringify(contract.provider_record.request_scope) || "未记录"}</dd>
            </div>
            <div>
              <dt>可重取凭证</dt>
              <dd>{contract.provider_record.retrieval_reference || "未记录"}</dd>
            </div>
          </>
        )}
        <div>
          <dt>发布日期</dt>
          <dd>{document.publish_date || "未记录"}</dd>
        </div>
        <div>
          <dt>可用 / 采集时点</dt>
          <dd>
            {document.available_at} / {document.acquired_at}
          </dd>
        </div>
        <div>
          <dt>解析版本</dt>
          <dd>
            {parseQualityLabel(document.parse_quality)} · 解析器 {document.parser_version}
          </dd>
        </div>
        {contentDisplayAllowed && originalFile && (
          <>
            <div>
              <dt>原件文件</dt>
              <dd>{originalFile.file_name} · {originalFile.mime_type} · {originalFile.byte_size} B</dd>
            </div>
            <div>
              <dt>对象版本</dt>
              <dd><code>{originalFile.object_version}</code></dd>
            </div>
            <div>
              <dt>上传人 / 保留</dt>
              <dd>{originalFile.uploaded_by} · {originalFile.retention_policy}</dd>
            </div>
          </>
        )}
        <div>
          <dt>许可 / 展示范围</dt>
          <dd>{permissionText}</dd>
        </div>
        {contract && (
          <>
            <div>
              <dt>授权有效期</dt>
              <dd>
                {contract.effective_from || "未记录"} 至 {contract.effective_until || "未记录"}
              </dd>
            </div>
            <div>
              <dt>合同 / 许可版本</dt>
              <dd>{contract.contract_version || "未记录"}</dd>
            </div>
            <div>
              <dt>保留策略</dt>
              <dd>
                {sourceRetentionLabel(contract.retention_policy)} · {sourceRetentionLabel(contract.deletion_policy)}
              </dd>
            </div>
            <div>
              <dt>下游限制</dt>
              <dd>
                {contract.downstream_restrictions.join("；") || "无额外记录"}
              </dd>
            </div>
          </>
        )}
      </dl>
      {isPublished && (
        <section className="ros-material-continuation">
          <p className="ros-eyebrow">新材料后的后继研究</p>
          <h4>以此冻结版本重新核验</h4>
          <p>
            这会记录触发原因并创建新的受控运行；此前发布结论保持不变，新材料先经过原文与证据审核。
          </p>
          <label>
            重新研究原因
            <textarea
              aria-label="重新研究原因"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="说明这份资料可能影响哪个关键因素或已发布判断…"
            />
          </label>
          <button
            className="ros-button ros-button--primary"
            type="button"
            disabled={!reason.trim() || !continuationAllowed || continuing}
            onClick={continueFromMaterial}
          >
            {continuing ? "正在创建后继运行…" : "以此资料启动重新研究"}
          </button>
          {!continuationAllowed && (
            <small>
              此资料的来源合同当前不允许继续研究；可查看冻结审计记录，但不能作为后继运行输入。
            </small>
          )}
          {continuationNotice && (
            <p
              className={
                continuationNotice.startsWith("已创建")
                  ? "ros-success"
                  : "ros-error"
              }
            >
              {continuationNotice}
            </p>
          )}
        </section>
      )}
      {needsSupplement ? (
        <SupplementRecovery
          caseId={caseId}
          documentId={document.id}
          failureStage={
            recoveryReason === "parse_failed"
              ? document.parse_failure_stage || "未记录阶段"
              : "未产生可研究陈述"
          }
          onCreated={onSupplementCreated}
          active={recoveryActive}
          onBegin={onBeginRecovery}
          onCancel={onCancelRecovery}
          onStartNewMaterials={onStartNewMaterials}
        />
      ) : (
        <>
          <section className="ros-document-extract">
            <p>
              <b>下一步：从冻结原文提取候选。</b>
              只会创建待人工审核的原子陈述；不会发布
              SourceStatement、不会启动补证、监控或市场任务。
            </p>
            <button
              className="ros-button ros-button--primary"
              type="button"
              disabled={!extractionAllowed || extracting}
              onClick={onExtract}
            >
              {extracting ? "正在提取候选…" : "从冻结资料提取候选"}
            </button>
            {!extractionAllowed && (
              <small>
                此版本解析失败、来源合同禁止 AI 处理，或合同当前不在有效期内，不能请求候选抽取。
              </small>
            )}
          </section>
          <section className="ros-source-spans">
            <p className="ros-eyebrow">可定位正文片段</p>
            {candidateNotice && (
              <p
                className={
                  candidateNotice.startsWith("已创建")
                    ? "ros-note"
                    : "ros-error"
                }
                role={candidateNotice.startsWith("已创建") ? "status" : "alert"}
              >
                {candidateNotice}
                {candidateNotice.startsWith("已创建") && (
                  <>
                    {" "}
                    <Link to={`/events/${caseId}/review`}>
                      前往审核此候选
                    </Link>
                  </>
                )}
              </p>
            )}
            {focused && (
              <p className="ros-note">
                已定位到审核候选对应的冻结原文片段；请核对原文、定位、许可与候选表述后再决定。
              </p>
            )}
            {displayableSpans.length ? (
              displayableSpans.map((span) => (
                <article
                  className={span.id === focusSpanId ? "is-focused" : ""}
                  key={span.id}
                >
                  <code>{JSON.stringify(span.locator)}</code>
                  <blockquote>{span.verbatim_text}</blockquote>
                  <small>
                    {span.cited_by.length
                      ? `已被 ${span.cited_by.length} 条证据关系引用`
                      : "尚未被证据关系引用"}
                  </small>
                  <button
                    className="ros-button ros-button--secondary"
                    type="button"
                    disabled={contract?.status !== "admitted"}
                    onClick={() => beginCandidate(span)}
                  >
                    将此段纳入待审候选
                  </button>
                  {candidateSpanId === span.id && (
                    <form
                      className="ros-inline-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void createCandidate(span);
                      }}
                    >
                      <p>
                        这只创建待审原子陈述。原文引用、定位和正式结论不会被直接修改。
                      </p>
                      <label>
                        候选性质
                        <select
                          aria-label="候选性质"
                          value={candidateType}
                          onChange={(event) =>
                            setCandidateType(
                              event.target.value as typeof candidateType,
                            )
                          }
                        >
                          <option value="reported_claim">来源陈述</option>
                          <option value="forecast">预测</option>
                          <option value="research_opinion">研究观点</option>
                        </select>
                      </label>
                      <label>
                        候选表述
                        <textarea
                          aria-label="候选表述"
                          value={candidateText}
                          onChange={(event) => setCandidateText(event.target.value)}
                        />
                      </label>
                      <div className="ros-header-actions">
                        <button
                          className="ros-button ros-button--primary"
                          type="submit"
                          disabled={!candidateText.trim() || candidateSubmitting}
                        >
                          {candidateSubmitting ? "正在创建候选…" : "创建待审候选"}
                        </button>
                        <button
                          className="ros-button ros-button--secondary"
                          type="button"
                          disabled={candidateSubmitting}
                          onClick={() => setCandidateSpanId(null)}
                        >
                          取消
                        </button>
                      </div>
                    </form>
                  )}
                </article>
              ))
            ) : (
              <div className="ros-empty ros-empty--compact">
                当前资料没有返回可定位正文片段。
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

function SupplementRecovery({
  caseId,
  documentId,
  failureStage,
  onCreated,
  active,
  onBegin,
  onCancel,
  onStartNewMaterials,
}: {
  caseId: string;
  documentId: string;
  failureStage: string;
  onCreated: (documentId: string) => void;
  active: boolean;
  onBegin: () => void;
  onCancel: () => void;
  onStartNewMaterials: () => void;
}) {
  const [rawText, setRawText] = useState("");
  const [page, setPage] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const noClaims = failureStage === "未产生可研究陈述";
  async function submit() {
    if (!rawText.trim() || !page.trim()) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await researchOsApi.createDocumentSupplement(documentId, {
        case_id: caseId,
        raw_text: rawText.trim(),
        claimed_page_reference: page.trim(),
        created_by: "human:researcher",
        source_metadata: { authority_level: "user_supplied" },
      });
      setMessage(
        result.extraction_allowed
          ? "补充正文已独立冻结；已切换到新版本，可提取待审候选。"
          : "补充正文已独立冻结；已切换到新版本，但继承后的许可不允许 AI 处理。可保留供人工核验。",
      );
      onCreated(result.document_version_id);
      onCancel();
    } catch {
      setMessage(
        "补充正文未保存；原件保持不变。请检查 Case 权限和必填项后重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="ros-recovery">
      <p className="ros-rulebox">
        {noClaims
          ? "该资料尚未产生可研究陈述；原文与本次抽取记录均保留，暂不能把它写成证据。"
          : `该资料在 ${failureStage} 解析失败；保留资料记录，暂不能把它写成证据。`}
        补充正文会作为独立版本关联原件，页码仅作为你的声明。
      </p>
      <p>不会改写原件，也不会自动启动研究、发布正式陈述或市场任务。</p>
      {!active ? (
        <button
          className="ros-button ros-button--primary"
          type="button"
          onClick={onBegin}
        >
          继续补充原 Case
        </button>
      ) : (
        <div className="ros-recovery__form">
          <p className="ros-note">
            恢复目标已固定为当前冻结资料。刷新此链接会继续同一目标；未知恢复状态不会提交补充正文。
          </p>
          <label>
            补充正文
            <textarea
              aria-label="补充正文"
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
              placeholder="粘贴可核验的原文内容…"
            />
          </label>
          <label>
            声称页码或位置
            <input
              aria-label="声称页码或位置"
              value={page}
              onChange={(event) => setPage(event.target.value)}
              placeholder="例如：第 3 页"
            />
          </label>
          <div>
            <button
              className="ros-button ros-button--primary"
              type="button"
              disabled={!rawText.trim() || !page.trim() || busy}
              onClick={submit}
            >
              {busy ? "正在冻结补充正文…" : "冻结补充正文"}
            </button>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              disabled={busy}
              onClick={onCancel}
            >
              取消恢复
            </button>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              disabled={busy}
              onClick={onStartNewMaterials}
            >
              放弃恢复并新建资料
            </button>
          </div>
        </div>
      )}
      {message && (
        <p
          className={
            message.startsWith("补充正文已") ? "ros-success" : "ros-error"
          }
        >
          {message}
        </p>
      )}
    </section>
  );
}

export function CaseConclusionPage() {
  return (
    <CaseFrame>
      {(data, caseId) => (
        <section className="ros-case-columns">
          <div>
            <article className="ros-panel ros-panel--conclusion">
              <p className="ros-eyebrow">
                当前判断 ·{" "}
                {data.conclusion.state === "published"
                  ? "已人工发布"
                  : data.conclusion.state === "ai_draft"
                    ? "AI 草案，未发布"
                    : "暂不下结论"}
              </p>
              <h2>{data.conclusion.text}</h2>
              <p className="ros-conclusion-text">
                {data.conclusion.state === "published"
                  ? "该版本只基于已审核资料；新运行只会追加待审证据，不会自动重写结论。"
                  : "尚未审核的候选、二手转述和无授权材料都不会自动进入当前判断。"}
              </p>
            </article>
            <FactorList data={data} />
          </div>
          <aside className="ros-case-rail">
            <section className="ros-action-card">
              <p className="ros-eyebrow">下一步</p>
              <h2>{data.nextAction.label}</h2>
              <p>
                {data.lifecycle.currentGap ||
                  "先完成当前人工判断，再决定是否创建新的结论或范围版本。"}
              </p>
              <Link
                className="ros-button ros-button--primary"
                to={`/events/${caseId}/${data.nextAction.kind === "review_intake" ? "documents" : data.nextAction.kind === "review_evidence" || data.nextAction.kind === "review_conclusion" ? "review" : data.nextAction.kind === "view_conclusion_change" ? "history" : data.nextAction.kind === "edit_factors" ? "scope" : "monitor"}`}
              >
                {data.nextAction.kind === "review_intake"
                  ? "核验冻结原文"
                  : data.nextAction.kind.includes("review")
                    ? "进入审核"
                    : data.nextAction.kind === "view_conclusion_change"
                      ? "查看结论版本"
                      : data.nextAction.kind === "edit_factors"
                        ? "调整研究范围"
                        : "查看持续研究"}
              </Link>
            </section>
            <section className="ros-rail-section">
              <p className="ros-eyebrow">持续研究</p>
              <h2>
                {data.lifecycle.activeRunId
                  ? "系统正在受控补证"
                  : "尚未授权后台运行"}
              </h2>
              <p>
                {data.lifecycle.activeRunId
                  ? `范围版本 v${data.scope.version} · 仅允许来源内的材料可进入后续审核。`
                  : "完成原文核验、来源许可与研究协议后，才可配置并触发一次可回放的补证运行。"}
              </p>
              <Link to={`/events/${caseId}/monitor`}>查看运行记录 →</Link>
            </section>
            <section className="ros-rail-section">
              <p className="ros-eyebrow">结论依据</p>
              <p>
                {data.conclusion.citations.length}{" "}
                条可回溯引用；每条都保留原文定位、可用时点与审核状态。
              </p>
            </section>
            <CaseRelationRail caseId={caseId} />
          </aside>
        </section>
      )}
    </CaseFrame>
  );
}

export function CaseConclusionHistoryPage() {
  return (
    <CaseFrame>
      {(_data, caseId) => <ConclusionHistoryContent caseId={caseId} />}
    </CaseFrame>
  );
}
export function CaseScopePage() {
  return (
    <CaseFrame>
      {(data, caseId) => (
        <ScopeEditor
          caseId={caseId}
          initialFactors={data.scope.factors}
          currentVersion={data.scope.version}
        />
      )}
    </CaseFrame>
  );
}
function ScopeEditor({
  caseId,
  initialFactors,
  currentVersion,
}: {
  caseId: string;
  initialFactors: EventWorkbench["scope"]["factors"];
  currentVersion: number;
}) {
  const [factors, setFactors] = useState(() =>
    initialFactors.map((factor) => factor.statement),
  );
  const [reason, setReason] = useState("调整关键因素以补足当前验证缺口");
  const [history, setHistory] = useState<
    Awaited<ReturnType<typeof researchOsApi.scopeHistory>>["items"]
  >([]);
  const [historyError, setHistoryError] = useState(false);
  const [historyReload, setHistoryReload] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    setFactors(initialFactors.map((factor) => factor.statement));
    setNotice(null);
  }, [initialFactors]);
  useEffect(() => {
    let active = true;
    setHistoryError(false);
    researchOsApi
      .scopeHistory(caseId)
      .then((value) => {
        if (!active) return;
        setHistory(value.items);
        setHistoryError(false);
      })
      .catch(() => active && setHistoryError(true));
    return () => {
      active = false;
    };
  }, [caseId, notice, historyReload]);
  const normalized = factors.map((factor) => factor.trim()).filter(Boolean);
  function edit(index: number, value: string) {
    setFactors((current) =>
      current.map((factor, position) => (position === index ? value : factor)),
    );
  }
  async function save() {
    if (
      normalized.length < 3 ||
      normalized.length > 5 ||
      new Set(normalized).size !== normalized.length
    ) {
      setNotice("请保留 3–5 个不重复的关键因素；空白项不会保存。");
      return;
    }
    if (!reason.trim()) {
      setNotice("请记录本次范围调整的原因，历史版本才可复现。");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const updated = await researchClient.updateEventResearchScope({
        caseId,
        factors: normalized,
        changedBy: "human:researcher",
        changeReason: reason.trim(),
      });
      setFactors(updated.factors.map((factor) => factor.statement));
      setNotice(
        `已创建范围版本 v${updated.version}；旧版本与其证据映射仍可回放。`,
      );
    } catch {
      setNotice(
        "范围未更新。已发布 Case 不能直接改写范围；请先从新材料启动后继研究。 ",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="ros-scope-editor">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">范围版本 v{currentVersion}</p>
          <h2>调整关键因素，创建新的研究范围</h2>
          <p>
            范围更新会重新映射已审核证据，并只影响后续研究；过去的运行和结论仍使用各自冻结的范围版本。
          </p>
        </div>
        <Link
          className="ros-button ros-button--secondary"
          to={`/events/${caseId}/evidence`}
        >
          查看当前证据
        </Link>
      </header>
      <div className="ros-scope-editor__form">
        {factors.map((factor, index) => (
          <label key={index}>
            关键因素 {index + 1}
            <textarea
              aria-label={`关键因素 ${index + 1}`}
              value={factor}
              onChange={(event) => edit(index, event.target.value)}
            />
          </label>
        ))}
        <label>
          本次调整原因
          <textarea
            aria-label="本次调整原因"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
      </div>
      <div className="ros-scope-editor__actions">
        <button
          className="ros-button ros-button--secondary"
          type="button"
          disabled={factors.length <= 3}
          onClick={() => setFactors((current) => current.slice(0, -1))}
        >
          移除最后一项
        </button>
        <button
          className="ros-button ros-button--secondary"
          type="button"
          disabled={factors.length >= 5}
          onClick={() => setFactors((current) => [...current, ""])}
        >
          增加因素
        </button>
        <button
          className="ros-button ros-button--primary"
          type="button"
          disabled={busy}
          onClick={save}
        >
          {busy ? "正在创建范围版本…" : "保存新的研究范围"}
        </button>
      </div>
      {notice && (
        <p
          className={notice.startsWith("已创建") ? "ros-success" : "ros-error"}
        >
          {notice}
        </p>
      )}
      <section className="ros-rule-history">
        <p className="ros-eyebrow">范围版本历史</p>
        <h2>谁在何时因何调整了范围</h2>
        {historyError ? (
          <div className="ros-empty ros-empty--compact" role="alert">
            <strong>无法读取范围版本历史</strong>
            <p>当前编辑内容仍未保存；系统不会把空列表写成没有历史。</p>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              onClick={() => setHistoryReload((value) => value + 1)}
            >
              重新读取范围版本历史
            </button>
          </div>
        ) : (
          <ol>
            {history.map((item) => (
              <li key={item.version}>
                <strong>v{item.version}</strong>
                <span>
                  {item.changed_by} · {item.created_at}
                </span>
                <p>{item.change_reason}</p>
                <small>
                  {item.factors.map((factor) => factor.statement).join("；")}
                </small>
              </li>
            ))}
          </ol>
        )}
      </section>
    </section>
  );
}
function ConclusionHistoryContent({ caseId }: { caseId: string }) {
  const [versions, setVersions] = useState<Awaited<
    ReturnType<EventResearchClient["getEventConclusionHistory"]>
  > | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setVersions(null);
    setLoadError(false);
    researchClient
      .getEventConclusionHistory(caseId)
      .then((value) => {
        if (!active) return;
        setVersions(value);
        setLoadError(false);
      })
      .catch(() => active && setLoadError(true));
    return () => {
      active = false;
    };
  }, [caseId, reload]);
  if (versions === null)
    return (
      <section className="ros-empty ros-page-gap" role={loadError ? "alert" : undefined}>
        {loadError ? (
          <>
            <strong>无法读取不可变结论版本</strong>
            <p>系统不会以当前结论替代历史记录。</p>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              onClick={() => setReload((value) => value + 1)}
            >
              重试读取结论版本
            </button>
          </>
        ) : (
          "正在读取不可变结论版本；未返回记录时不会以当前结论替代历史。"
        )}
      </section>
    );
  return (
    <section className="ros-conclusion-history">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">结论审计链</p>
          <h2>结论版本与人工发布边界</h2>
          <p>
            每一版保留当时的范围、依据数量、草案来源与人工发布人。新的材料只能进入待审流程，不能自动改写这里的任何结论。
          </p>
        </div>
        <Link
          className="ros-button ros-button--secondary"
          to={`/events/${caseId}/monitor`}
        >
          查看补证运行
        </Link>
      </header>
      {versions.length === 0 ? (
        <div className="ros-empty">
          当前尚无已保存的结论版本；系统不会把临时页面文字当作历史结论。
        </div>
      ) : (
        <ol className="ros-conclusion-history__list">
          {versions.map((version) => (
            <li key={version.id}>
              <span
                className={`ros-pill ${version.state === "published" ? "ros-pill--system" : "ros-pill--human"}`}
              >
                {version.state === "published" ? "人工发布" : "AI 草案，未发布"}
              </span>
              <article>
                <header>
                  <div>
                    <p className="ros-eyebrow">
                      版本 {version.sequence} · {version.createdAt}
                    </p>
                    <h3>{version.primaryFactor || "未声明主要因素"}</h3>
                  </div>
                  <small>
                    范围 v{version.scopeVersion ?? "历史未记录"} ·{" "}
                    {version.evidenceCount} 条冻结依据
                  </small>
                </header>
                <blockquote>{version.text}</blockquote>
                <p>
                  {version.basedOnConclusionId
                    ? `基于草案 ${version.basedOnConclusionId}`
                    : "独立草案起点"}{" "}
                  ·{" "}
                  {version.reviewer
                    ? `发布/审核人：${version.reviewer}`
                    : "尚未人工发布"}
                </p>
              </article>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export function CaseReviewPage() {
  return (
    <CaseFrame>
      {(_data, caseId) => <ReviewContent caseId={caseId} />}
    </CaseFrame>
  );
}
function ReviewContent({ caseId }: { caseId: string }) {
  const [queue, setQueue] = useState<Awaited<
    ReturnType<EventResearchClient["getEventReviewQueue"]>
  > | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setQueue(null);
    setLoadError(false);
    researchClient
      .getEventReviewQueue(caseId)
      .then((value) => {
        if (!active) return;
        setQueue(value);
        setLoadError(false);
      })
      .catch(() => active && setLoadError(true));
    return () => {
      active = false;
    };
  }, [caseId, reload]);
  if (!queue)
    return (
      <div className="ros-empty ros-page-gap" role={loadError ? "alert" : undefined}>
        {loadError ? (
          <>
            <strong>无法读取待审核证据</strong>
            <p>不可访问的来源不会进入审核动作；系统也不会把它当作空队列。</p>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              onClick={() => setReload((value) => value + 1)}
            >
              重试读取待审核证据
            </button>
          </>
        ) : (
          "正在读取待审核证据；不可访问的来源不会进入审核动作。"
        )}
      </div>
    );
  const actionable = queue.items.filter((item) => item.canAccept);
  const blocked = queue.items.filter((item) => !item.canAccept);
  return (
    <section className="ros-review-workbench">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">人工审核</p>
          <h2>{queue.summary.pending} 条待审核关系</h2>
        </div>
        <span className="ros-muted">候选仅供核对；不会自动采纳</span>
      </header>
      {actionable.length === 0 ? (
        <div className="ros-empty">当前没有待审核候选。</div>
      ) : (
        actionable.map((item) => (
          <ReviewItem
            item={item}
            onDecided={() => setReload((value) => value + 1)}
            key={item.proposalId}
          />
        ))
      )}
      {blocked.length > 0 && (
        <section className="ros-rulebox">
          <p className="ros-eyebrow">资料受限，不能采纳</p>
          <p>以下候选保留审计记录，但不会进入结论或审核动作。</p>
          {blocked.map((item) => (
            <article key={item.proposalId}>
              <strong>{item.sourceTitle || "来源未记录"}</strong>
              <p>{item.sourceStatusReason}</p>
            </article>
          ))}
        </section>
      )}
      <AtomicClaimReviewPanel caseId={caseId} />
    </section>
  );
}

function AtomicClaimReviewPanel({ caseId }: { caseId: string }) {
  const [claims, setClaims] = useState<AtomicClaimCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    setClaims(null);
    setError(null);
    researchOsApi
      .atomicClaims(caseId)
      .then((value) => active && setClaims(value.items))
      .catch(
        () =>
          active &&
          setError("原子陈述队列暂不可读；系统不会据此推定抽取结果已审核。"),
      );
    return () => {
      active = false;
    };
  }, [caseId]);
  return (
    <section className="ros-atomic-review">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">抽取证据门禁</p>
          <h2>原子陈述审核</h2>
          <p>
            模型和表格规则的输出只能停在这里；只有人工决定才会发布为正式
            SourceStatement。
          </p>
        </div>
        <span className="ros-muted">
          {claims
            ? `${claims.filter((item) => item.review_state === "awaiting_review").length} 条待审核`
            : "正在读取"}
        </span>
      </header>
      {claims === null ? (
        <div className="ros-empty ros-empty--compact">
          正在读取带原文定位的抽取候选…
        </div>
      ) : error ? (
        <p className="ros-error">{error}</p>
      ) : claims.length === 0 ? (
        <div className="ros-empty ros-empty--compact">
          当前 Case 没有待展示的原子陈述候选。
        </div>
      ) : (
        claims.map((claim) => (
          <AtomicClaimItem key={claim.id} caseId={caseId} claim={claim} />
        ))
      )}
    </section>
  );
}

function AtomicClaimItem({
  caseId,
  claim,
}: {
  caseId: string;
  claim: AtomicClaimCandidate;
}) {
  const [reason, setReason] = useState("");
  const [editedText, setEditedText] = useState(claim.normalized_text);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reviewState, setReviewState] = useState(claim.review_state);
  const [sourceDetail, setSourceDetail] = useState<{
    document: SourceDocumentView;
    spans: DocumentSpan[];
  } | null>(null);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [loadingSource, setLoadingSource] = useState(false);
  const quotedSpan = sourceDetail?.spans.find(
    (span) => span.id === claim.source_span_id,
  );

  async function inspectSource() {
    if (sourceDetail || loadingSource) return;
    setLoadingSource(true);
    setSourceError(null);
    try {
      setSourceDetail(
        await researchClient.getDocumentDetail(claim.document_version_id, caseId),
      );
    } catch {
      setSourceError(
        "无法读取冻结原文；候选不会因此被自动确认。可刷新后重试或使用原文资料页。 ",
      );
    } finally {
      setLoadingSource(false);
    }
  }
  async function decide(outcome: "confirmed" | "modified" | "rejected") {
    if (!reason.trim() || (outcome === "modified" && !editedText.trim()))
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const review = await researchOsApi.reviewAtomicClaim(claim.id, {
        outcome,
        normalized_text: outcome === "modified" ? editedText.trim() : null,
        reviewer: "human:researcher",
        reason: reason.trim(),
        idempotency_key: `atomic-review:${claim.id}:${Date.now()}`,
      });
      setReviewState(review.outcome);
      setNotice(
        review.published_source_statement
          ? "已发布为正式陈述；候选、原文定位和审核记录仍可回放。"
          : "已驳回候选；原文和审核理由保留在审计记录中。",
      );
    } catch {
      setError("提交原子陈述审核失败；当前候选没有被自动发布。请刷新后重试。 ");
    } finally {
      setBusy(false);
    }
  }
  const isPending = reviewState === "awaiting_review";
  return (
    <article className="ros-atomic-claim">
      <div className="ros-atomic-claim__main">
        <span
          className={`ros-pill ${isPending ? "ros-pill--human" : "ros-pill--system"}`}
        >
          {isPending
            ? "待人工审核"
            : reviewState === "rejected"
              ? "已驳回"
              : "已审核"}
        </span>
        <h3>{claim.normalized_text}</h3>
        <blockquote>{claim.quote}</blockquote>
        <p className="ros-atomic-claim__hash">
          连续定位 {claim.quote_start}–{claim.quote_end} · SHA-256{" "}
          {claim.quote_sha256.slice(0, 12)}…
        </p>
      </div>
      <dl>
        <div>
          <dt>来源定位</dt>
          <dd>{JSON.stringify(claim.locator)}</dd>
        </div>
        <div>
          <dt>权威等级</dt>
          <dd>{claim.authority_level}</dd>
        </div>
        <div>
          <dt>抽取运行</dt>
          <dd>{String(claim.structured_fields.run_ref || "未记录")}</dd>
        </div>
        <div>
          <dt>历史审核</dt>
          <dd>
            {claim.review_history.length
              ? claim.review_history
                  .map((review) => `${review.outcome} · ${review.reviewer}`)
                  .join("；")
              : "尚未审核"}
          </dd>
        </div>
      </dl>
      <div className="ros-review-actions">
        <button
          className="ros-button ros-button--secondary"
          type="button"
          onClick={() => void inspectSource()}
          disabled={loadingSource}
        >
          {loadingSource ? "正在读取冻结原文…" : "在此页核对原文"}
        </button>
        <Link
          className="ros-button ros-button--secondary"
          to={`/events/${caseId}/documents?document=${encodeURIComponent(claim.document_version_id)}&span=${encodeURIComponent(claim.source_span_id)}`}
        >
          定位到冻结原文
        </Link>
        <a
          className="ros-button ros-button--secondary"
          href={claim.document_source_url}
          target="_blank"
          rel="noopener noreferrer"
        >
          打开原始链接
        </a>
        <span>候选 ID · {claim.id}</span>
      </div>
      {sourceDetail && (
        <section className="ros-atomic-source-check">
          <div>
            <p className="ros-eyebrow">在此页核对的冻结原文</p>
            <strong>{sourceDetail.document.title || "未命名资料"}</strong>
            <small>
              {sourceDetail.document.publisher || "发布方未记录"} ·{" "}
              {sourceDetail.document.available_at}
            </small>
          </div>
          <blockquote>
            {quotedSpan?.verbatim_text ||
              "资料已读取，但候选引用的定位片段不在本次返回中；不可据此确认候选。请前往原文资料页复核。"}
          </blockquote>
          <code>{JSON.stringify(quotedSpan?.locator || claim.locator)}</code>
        </section>
      )}
      {sourceError && <p className="ros-error">{sourceError}</p>}
      {isPending && (
        <div className="ros-review-decision">
          <label>
            原子陈述审核理由
            <textarea
              aria-label="原子陈述审核理由"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="核对原文、定位、主体、数值、期间与来源权限"
            />
          </label>
          {editing && (
            <label>
              审核后规范表述
              <textarea
                aria-label="审核后规范表述"
                value={editedText}
                onChange={(event) => setEditedText(event.target.value)}
              />
            </label>
          )}
          <div>
            <button
              className="ros-button ros-button--primary"
              type="button"
              disabled={!reason.trim() || busy}
              onClick={() => decide("confirmed")}
            >
              确认并发布
            </button>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              disabled={busy}
              onClick={() => setEditing((value) => !value)}
            >
              {editing ? "取消修改" : "修改后发布"}
            </button>
            {editing && (
              <button
                className="ros-button ros-button--primary"
                type="button"
                disabled={!reason.trim() || !editedText.trim() || busy}
                onClick={() => decide("modified")}
              >
                发布审核后表述
              </button>
            )}
            <button
              className="ros-button ros-button--secondary"
              type="button"
              disabled={!reason.trim() || busy}
              onClick={() => decide("rejected")}
            >
              驳回候选
            </button>
          </div>
          {error && <p className="ros-error">{error}</p>}
        </div>
      )}
      {notice && <p className="ros-success">{notice}</p>}
    </article>
  );
}

function ReviewItem({
  item,
  onDecided,
}: {
  item: Awaited<
    ReturnType<EventResearchClient["getEventReviewQueue"]>
  >["items"][number];
  onDecided: () => void;
}) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function decide(
    outcome: "confirmed" | "rejected" | "needs_more_evidence",
  ) {
    if (!reason.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await researchClient.reviewProposal(item.proposalId, {
        outcome,
        reason: reason.trim(),
        reviewer_id: "human:researcher",
        expected_version: item.proposalVersion,
      });
      onDecided();
    } catch {
      setError("提交审核决定失败；候选未被自动采纳。请刷新后重试。");
    } finally {
      setSubmitting(false);
    }
  }
  return (
    <article className="ros-review-item">
      <div>
        <span className="ros-pill ros-pill--human">未经人工复核</span>
        <h3>{item.thesisStatement || "未关联关键因素"}</h3>
        <blockquote>
          {item.verbatimText || item.statementText || "未抽取到可定位原文"}
        </blockquote>
      </div>
      <dl>
        <div>
          <dt>原文定位</dt>
          <dd>{JSON.stringify(item.locator)}</dd>
        </div>
        <div>
          <dt>可用时点</dt>
          <dd>{item.availableAt || "未记录"}</dd>
        </div>
        <div>
          <dt>来源与许可</dt>
          <dd>{item.sourceStatusReason}</dd>
        </div>
      </dl>
      <div className="ros-review-actions">
        <span>
          {item.aiRole || "候选关系"} · {item.proposalReason}
        </span>
        {item.documentSourceUrl ? (
          <a
            className="ros-button ros-button--secondary"
            href={item.documentSourceUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            打开冻结来源
          </a>
        ) : (
          <span>冻结来源地址未记录</span>
        )}
      </div>
      <div className="ros-review-decision">
        <label>
          审核理由
          <textarea
            aria-label="审核理由"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="说明为何原文支持或不支持这个关系"
          />
        </label>
        <div>
          <button
            className="ros-button ros-button--primary"
            type="button"
            disabled={!reason.trim() || submitting}
            onClick={() => decide("confirmed")}
          >
            确认采纳
          </button>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            disabled={!reason.trim() || submitting}
            onClick={() => decide("needs_more_evidence")}
          >
            要求补充证据
          </button>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            disabled={!reason.trim() || submitting}
            onClick={() => decide("rejected")}
          >
            驳回候选
          </button>
        </div>
        {error && <p className="ros-error">{error}</p>}
      </div>
    </article>
  );
}

export function CaseWikiPage() {
  return (
    <CaseFrame>
      {(_data, caseId) => <WikiInspectorContent caseId={caseId} />}
    </CaseFrame>
  );
}
export function CaseRelationsPage() {
  return (
    <CaseFrame>
      {(_data, caseId) => <CaseRelationsContent caseId={caseId} />}
    </CaseFrame>
  );
}

export function CaseMarketPage() {
  return (
    <CaseFrame>
      {(data, caseId) => (
        <MarketExpressionContent
          caseId={caseId}
          theses={data.factors.flatMap((factor) =>
            factor.thesisId
              ? [{ id: factor.thesisId, statement: factor.statement }]
              : [],
          )}
        />
      )}
    </CaseFrame>
  );
}
export function CaseStockProfilePage() {
  const { stockId = "" } = useParams();
  return (
    <CaseFrame>
      {(_data, caseId) => (
        <MarketStockProfile caseId={caseId} stockId={stockId} />
      )}
    </CaseFrame>
  );
}
export function CaseFundProfilePage() {
  const { fundId = "" } = useParams();
  return (
    <CaseFrame>
      {(_data, caseId) => <MarketFundProfile caseId={caseId} fundId={fundId} />}
    </CaseFrame>
  );
}
const protocolReason: Record<string, string> = {
  missing_outcome_binding: "尚未固定结果指标、实体范围、可回溯基线和观察窗口",
  binding_not_approved: "结果绑定仍是草案，尚未经过人工审核",
  missing_mechanism_template: "尚未选择可检验的机制模板",
  missing_verification_rule: "尚未声明支持、反证与证据优先级规则",
  insufficient_primary_metrics:
    "仅一个独立主指标：只能受限监测，正式判断仅可为证据不足或未到验证时点",
  missing_counter_hypothesis: "尚未定义竞争解释或反向检验",
};

export function CaseProtocolPage() {
  return (
    <CaseFrame>
      {(data, caseId) => (
        <>
          <ProtocolContent caseId={caseId} data={data} />
          <MechanismProtocolPanel caseId={caseId} />
          <MechanismRuleConfig caseId={caseId} />
          <MechanismRuleHistory caseId={caseId} />
        </>
      )}
    </CaseFrame>
  );
}
function ProtocolContent({
  caseId,
  data,
}: {
  caseId: string;
  data: EventWorkbench;
}) {
  const [selectedId, setSelectedId] = useState(data.factors[0]?.thesisId ?? "");
  const [states, setStates] = useState<Record<string, Researchability>>({});
  const [metrics, setMetrics] = useState<MetricDefinition[]>([]);
  const [baselineDocuments, setBaselineDocuments] = useState<
    SourceDocumentView[] | null
  >(null);
  const [showForm, setShowForm] = useState(false);
  const [metricId, setMetricId] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [scopeName, setScopeName] = useState("");
  const [baselineSource, setBaselineSource] = useState("");
  const [baselineValue, setBaselineValue] = useState("");
  const [baselinePeriod, setBaselinePeriod] = useState("");
  const [availableAt, setAvailableAt] = useState("");
  const [horizonStart, setHorizonStart] = useState("");
  const [horizonEnd, setHorizonEnd] = useState("");
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const selected =
    data.factors.find((factor) => factor.thesisId === selectedId) ??
    data.factors[0];
  const state = selected?.thesisId ? states[selected.thesisId] : undefined;
  const selectedMetric = metrics.find((metric) => metric.id === metricId);
  async function refresh() {
    const factors = data.factors.filter((factor) => factor.thesisId);
    const results = await Promise.all(
      factors.map(
        async (factor) =>
          [
            factor.thesisId!,
            await researchOsApi.researchability(factor.thesisId!),
          ] as const,
      ),
    );
    setStates(Object.fromEntries(results));
  }
  useEffect(() => {
    refresh().catch(() =>
      setNotice("研究协议状态暂不可读；系统不会以默认通过替代真实状态。"),
    );
    researchOsApi
      .metrics()
      .then((items) => {
        setMetrics(items);
        setMetricId((current) => current || items[0]?.id || "");
      })
      .catch(() =>
        setNotice("指标库暂不可读；尚未创建或授权的指标不能被当作可用指标。"),
      );
  }, [data.event.id]);
  useEffect(() => {
    let active = true;
    setBaselineDocuments(null);
    researchClient
      .getDocuments({ caseId })
      .then((items) => {
        if (active)
          setBaselineDocuments(
            items.filter(
              (item) =>
                item.source_contract?.status === "admitted" &&
                item.source_contract.permissions.display,
            ),
          );
      })
      .catch(() => active && setBaselineDocuments([]));
    return () => {
      active = false;
    };
  }, [caseId]);
  async function saveBinding() {
    if (
      !selected?.thesisId ||
      !selectedMetric ||
      !companyId.trim() ||
      !scopeName.trim() ||
      !baselineSource.trim() ||
      !baselineValue.trim() ||
      !baselinePeriod ||
      !availableAt ||
      !horizonStart ||
      !horizonEnd ||
      !reason.trim()
    ) {
      setNotice(
        "请完整填写指标、实体范围、可回溯基线、时间窗和记录原因。系统不会补写缺失字段。",
      );
      return;
    }
    if (baselineDocuments === null) {
      setNotice(
        "正在核对当前 Case 的冻结资料；资料未核对完成前不能登记结果基线。",
      );
      return;
    }
    const normalizedSource = baselineSource.trim();
    const knownDocument = baselineDocuments.find(
      (document) => normalizedSource === `document:${document.id}`,
    );
    if (!knownDocument) {
      setNotice(
        "基线来源必须是当前 Case 已冻结、已准入资料的 document:<资料 ID>；请从“原文资料”页复制冻结资料 ID。",
      );
      return;
    }
    if (availableAt !== knownDocument.available_at) {
      setNotice(
        "基线可用时点必须与所选冻结资料记录完全一致；请从“原文资料”页复制该时点。",
      );
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      const binding = await researchOsApi.createOutcomeBinding(
        selected.thesisId,
        {
          metric_definition_id: selectedMetric.id,
          entity_scope: {
            company_id: companyId.trim(),
            [selectedMetric.entity_scope]: scopeName.trim(),
          },
          direction: "increase",
          baseline: {
            source_ref: normalizedSource,
            value: baselineValue.trim(),
            unit: selectedMetric.unit,
            observed_period: baselinePeriod,
            available_at: availableAt,
          },
          horizon_start: horizonStart,
          horizon_end: horizonEnd,
          reviewer: "human:researcher",
          reason: reason.trim(),
        },
      );
      setNotice(`结果绑定已登记为草案 ${binding.id}；仍需独立审核后才生效。`);
      await refresh();
    } catch {
      setNotice(
        "未能登记结果绑定；当前协议没有被部分写入。请检查字段与服务状态。",
      );
    } finally {
      setBusy(false);
    }
  }
  async function approveBinding() {
    if (!state?.effective_binding_id || !reason.trim()) {
      setNotice("请先填写审核理由。审核会追加新版本，不会修改原草案。");
      return;
    }
    setBusy(true);
    setNotice(null);
    try {
      await researchOsApi.approveOutcomeBinding(state.effective_binding_id, {
        reviewer: "human:reviewer",
        reason: reason.trim(),
      });
      setNotice(
        "结果绑定已审核并固定为新的不可变版本；后续机制和验证规则仍需补齐。",
      );
      await refresh();
    } catch {
      setNotice("审核未完成；原结果绑定没有被改写。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="ros-protocol">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">研究协议 · 可研究性门槛</p>
          <h2>研究协议与可研究性门槛</h2>
          <p>
            先固定“验证什么、在哪个实体、以什么基线、到何时”为可回放记录；门槛缺口不能由模型或市场涨跌自动填补。
          </p>
        </div>
        <span
          className={`ros-pill ${state?.status === "ready" ? "ros-pill--system" : "ros-pill--human"}`}
        >
          {state?.status === "ready"
            ? "可进入正式验证"
            : state?.status === "not_applicable"
              ? "既有流程"
              : "尚未满足门槛"}
        </span>
      </header>
      <div className="ros-protocol-grid">
        <section className="ros-protocol-list">
          <p className="ros-eyebrow">当前 Case 命题</p>
          {data.factors.map((factor) => (
            <button
              key={factor.position}
              type="button"
              className={`ros-protocol-factor${factor.thesisId === selected?.thesisId ? " is-selected" : ""}`}
              onClick={() => setSelectedId(factor.thesisId ?? "")}
            >
              <span>{String(factor.position).padStart(2, "0")}</span>
              <div>
                <strong>{factor.statement}</strong>
                <small>
                  {factor.thesisId
                    ? states[factor.thesisId]?.status === "ready"
                      ? "门槛已满足"
                      : states[factor.thesisId]?.status === "not_applicable"
                        ? "既有流程，不适用严格门槛"
                        : states[factor.thesisId]
                          ? "等待补齐研究协议"
                          : "正在读取协议状态"
                    : "未绑定可研究命题"}
                </small>
              </div>
            </button>
          ))}
        </section>
        <section className="ros-protocol-main">
          <p className="ros-eyebrow">
            选中命题 · {selected ? selected.statement : "无可用命题"}
          </p>
          {state?.status === "not_applicable" ? (
            <div className="ros-empty">
              <strong>该命题属于既有研究流程</strong>
              <p>
                它创建时没有启用严格研究协议。历史 Case
                仍可保留原有证据链，但不能因此被描述为已经通过结果指标、机制与反证门槛。
              </p>
            </div>
          ) : (
            <>
              <div className="ros-protocol-next">
                <span>下一步</span>
                <strong>{state?.next_action || "正在读取可研究性状态"}</strong>
                <p>
                  {state
                    ? "下面列出阻塞原因和可执行配置，任何缺口都会保留到人工补齐。"
                    : "系统正在读取该命题的冻结协议状态。"}
                </p>
              </div>
              <div className="ros-protocol-reasons">
                {state?.reason_codes.map((code) => (
                  <article key={code}>
                    <i aria-hidden="true">!</i>
                    <div>
                      <strong>{protocolReason[code] || code}</strong>
                      <small>{code}</small>
                    </div>
                  </article>
                )) || (
                  <p className="ros-empty ros-empty--compact">
                    尚未获得门槛状态，不能假设研究已可执行。
                  </p>
                )}
              </div>
              <div className="ros-header-actions">
                <button
                  className="ros-button ros-button--primary"
                  type="button"
                  onClick={() => setShowForm((value) => !value)}
                >
                  {showForm ? "收起配置" : "设定结果指标与验证窗口"}
                </button>
                {state?.reason_codes.includes("binding_not_approved") && (
                  <button
                    className="ros-button ros-button--secondary"
                    type="button"
                    disabled={busy}
                    onClick={approveBinding}
                  >
                    {busy ? "正在审核…" : "审核并固定结果绑定"}
                  </button>
                )}
              </div>
              {showForm && (
                <ProtocolBindingForm
                  metrics={metrics}
                  metricId={metricId}
                  setMetricId={setMetricId}
                  companyId={companyId}
                  setCompanyId={setCompanyId}
                  scopeName={scopeName}
                  setScopeName={setScopeName}
                  baselineSource={baselineSource}
                  setBaselineSource={setBaselineSource}
                  baselineValue={baselineValue}
                  setBaselineValue={setBaselineValue}
                  baselinePeriod={baselinePeriod}
                  setBaselinePeriod={setBaselinePeriod}
                  availableAt={availableAt}
                  setAvailableAt={setAvailableAt}
                  horizonStart={horizonStart}
                  setHorizonStart={setHorizonStart}
                  horizonEnd={horizonEnd}
                  setHorizonEnd={setHorizonEnd}
                  reason={reason}
                  setReason={setReason}
                  busy={busy}
                  onSave={saveBinding}
                />
              )}
            </>
          )}
        </section>
        <aside className="ros-protocol-rail">
          <p className="ros-eyebrow">审计边界</p>
          <dl className="ros-definition">
            <div>
              <dt>命题 ID</dt>
              <dd>{selected?.thesisId || "未记录"}</dd>
            </div>
            <div>
              <dt>有效绑定</dt>
              <dd>{state?.effective_binding_id || "尚未形成"}</dd>
            </div>
            <div>
              <dt>处理原则</dt>
              <dd>草案不能替代已审核协议；新版本不覆写旧版本。</dd>
            </div>
          </dl>
          <p className="ros-rulebox">
            <b>不做黑盒：</b>
            指标定义、基线来源、可用时点、窗口、审核人与原因都进入可回放记录。市场观测不能自行证明研究命题。
          </p>
        </aside>
      </div>
      {notice && (
        <p
          className={
            notice.startsWith("结果绑定已") ? "ros-success" : "ros-error"
          }
        >
          {notice}
        </p>
      )}
    </section>
  );
}
function ProtocolBindingForm(props: {
  metrics: MetricDefinition[];
  metricId: string;
  setMetricId: (value: string) => void;
  companyId: string;
  setCompanyId: (value: string) => void;
  scopeName: string;
  setScopeName: (value: string) => void;
  baselineSource: string;
  setBaselineSource: (value: string) => void;
  baselineValue: string;
  setBaselineValue: (value: string) => void;
  baselinePeriod: string;
  setBaselinePeriod: (value: string) => void;
  availableAt: string;
  setAvailableAt: (value: string) => void;
  horizonStart: string;
  setHorizonStart: (value: string) => void;
  horizonEnd: string;
  setHorizonEnd: (value: string) => void;
  reason: string;
  setReason: (value: string) => void;
  busy: boolean;
  onSave: () => void;
}) {
  const { caseId = "" } = useParams();
  const metric = props.metrics.find((item) => item.id === props.metricId);
  const [documents, setDocuments] = useState<SourceDocumentView[] | null>(null);
  useEffect(() => {
    let active = true;
    researchClient
      .getDocuments({ caseId })
      .then((items) => {
        if (active)
          setDocuments(
            items.filter(
              (item) =>
                item.source_contract?.status === "admitted" &&
                item.source_contract.permissions.display,
            ),
          );
      })
      .catch(() => active && setDocuments([]));
    return () => {
      active = false;
    };
  }, [caseId]);
  function selectBaseline(documentId: string) {
    const document = documents?.find((item) => item.id === documentId);
    if (!document) return;
    props.setBaselineSource(`document:${document.id}`);
    props.setAvailableAt(document.available_at);
  }
  return (
    <section className="ros-protocol-form">
      <p className="ros-eyebrow">登记结果绑定 · 先形成草案</p>
      {props.metrics.length === 0 ? (
        <div className="ros-empty ros-empty--compact">
          指标库为空。请先由数据治理角色登记并审核指标定义；系统不会根据自然语言临时造出指标。
        </div>
      ) : (
        <>
          <label>
            结果指标
            <select
              value={props.metricId}
              onChange={(event) => props.setMetricId(event.target.value)}
            >
              {props.metrics.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.display_name} · v{item.version} · {item.unit}
                </option>
              ))}
            </select>
          </label>
          <div className="ros-protocol-form__two">
            <label>
              公司 ID
              <input
                value={props.companyId}
                onChange={(event) => props.setCompanyId(event.target.value)}
                placeholder="company:tsmc"
              />
            </label>
            <label>
              {metric?.entity_scope || "实体"} 范围
              <input
                value={props.scopeName}
                onChange={(event) => props.setScopeName(event.target.value)}
                placeholder="例如：先进封装业务线"
              />
            </label>
          </div>
          <div className="ros-rulebox">
            <b>基线必须可回放：</b>仅接受当前 Case 已冻结且已准入资料的{" "}
            <code>document:&lt;资料 ID&gt;</code>
            ；可用时点必须与资料记录完全一致。
          </div>
          <label>
            选择冻结基线资料
            <select
              value={props.baselineSource.replace("document:", "")}
              onChange={(event) => selectBaseline(event.target.value)}
              disabled={documents === null || documents.length === 0}
            >
              <option value="">
                {documents === null
                  ? "正在核对当前 Case 的资料…"
                  : documents.length === 0
                    ? "当前没有可用的已准入资料"
                    : "选择一份已准入冻结资料"}
              </option>
              {documents?.map((document) => (
                <option key={document.id} value={document.id}>
                  {document.title || "未命名资料"} · {document.available_at}
                </option>
              ))}
            </select>
          </label>
          <div className="ros-protocol-form__two">
            <label>
              基线来源引用
              <input
                value={props.baselineSource}
                onChange={(event) =>
                  props.setBaselineSource(event.target.value)
                }
                placeholder="document:&lt;当前 Case 冻结资料 UUID&gt;"
              />
            </label>
            <label>
              基线数值
              <input
                value={props.baselineValue}
                onChange={(event) => props.setBaselineValue(event.target.value)}
                placeholder={metric?.unit || "数值"}
              />
            </label>
          </div>
          <div className="ros-protocol-form__two">
            <label>
              基线观察期
              <input
                type="date"
                value={props.baselinePeriod}
                onChange={(event) =>
                  props.setBaselinePeriod(event.target.value)
                }
              />
            </label>
            <label>
              基线可用时点（ISO 8601）
              <input
                value={props.availableAt}
                onChange={(event) => props.setAvailableAt(event.target.value)}
                placeholder="2026-03-01T00:00:00Z"
              />
            </label>
          </div>
          <div className="ros-protocol-form__two">
            <label>
              验证窗口起点
              <input
                type="date"
                value={props.horizonStart}
                onChange={(event) => props.setHorizonStart(event.target.value)}
              />
            </label>
            <label>
              验证窗口终点
              <input
                type="date"
                value={props.horizonEnd}
                onChange={(event) => props.setHorizonEnd(event.target.value)}
              />
            </label>
          </div>
          <label>
            登记原因
            <textarea
              value={props.reason}
              onChange={(event) => props.setReason(event.target.value)}
              placeholder="说明为何选择该指标、基线与窗口"
            />
          </label>
          <button
            className="ros-button ros-button--primary"
            type="button"
            disabled={props.busy}
            onClick={props.onSave}
          >
            {props.busy ? "正在登记…" : "登记为待审核结果绑定"}
          </button>
        </>
      )}
    </section>
  );
}
function MechanismProtocolPanel({ caseId }: { caseId: string }) {
  const [templates, setTemplates] = useState<MechanismTemplate[]>([]);
  const [protocol, setProtocol] = useState<CaseMechanismProtocol | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function refresh() {
    const [templateItems, protocolItem] = await Promise.all([
      researchOsApi.mechanismTemplates(),
      researchOsApi.caseMechanismProtocol(caseId),
    ]);
    setTemplates(templateItems);
    setProtocol(protocolItem);
  }
  useEffect(() => {
    refresh().catch(() =>
      setMessage("机制模板暂不可读；系统不会自行假设路径成立。"),
    );
  }, [caseId]);
  async function select(templateId: string) {
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.selectMechanismTemplate(caseId, {
        template_version_id: templateId,
        reviewer: "human:researcher",
        reason: "研究员确认本 Case 使用该机制范围",
      });
      await refresh();
      setMessage("机制模板已作为新的 Case 协议版本保存；旧选择仍可回放。");
    } catch {
      setMessage("未能选择机制模板；当前 Case 协议未被改写。");
    } finally {
      setBusy(false);
    }
  }
  const template = protocol?.template;
  const nodeById = new Map(
    template?.nodes.map((node) => [node.id, node]) ?? [],
  );
  const newerTemplate = template
    ? templates
        .filter(
          (item) =>
            item.template_key === template.template_key &&
            item.version > template.version,
        )
        .sort((left, right) => right.version - left.version)[0]
    : null;
  return (
    <section className="ros-mechanism">
      <header>
        <div>
          <p className="ros-eyebrow">机制模板与验证规则</p>
          <h2>
            {template
              ? `${template.display_name} · v${template.version}`
              : "先选择可检验的机制模板"}
          </h2>
          <p>
            系统不会把市场表现自动写成机制成立。模板只定义要验证的路径、竞争解释和范围保护；每条边仍需独立规则与来源。
          </p>
        </div>
      </header>
      {message && (
        <p
          className={
            message.startsWith("机制模板已") ? "ros-success" : "ros-error"
          }
        >
          {message}
        </p>
      )}
      {!protocol ? (
        <div className="ros-empty ros-empty--compact">
          正在读取当前 Case 的机制协议…
        </div>
      ) : !template ? (
        <div className="ros-mechanism-choices">
          {templates.map((item) => (
            <article key={item.id}>
              <strong>
                {item.display_name} · v{item.version}
              </strong>
              <p>{item.reason}</p>
              <button
                className="ros-button ros-button--primary"
                type="button"
                disabled={busy}
                onClick={() => select(item.id)}
              >
                {busy ? "正在保存…" : "选择此模板"}
              </button>
            </article>
          ))}
        </div>
      ) : (
        <>
          <div className="ros-mechanism-meta">
            <span>审核人：{protocol.selection?.reviewer}</span>
            <span>选择理由：{protocol.selection?.reason}</span>
            <span>版本时间：{protocol.selection?.created_at}</span>
          </div>
          {newerTemplate && (
            <section className="ros-rulebox">
              <b>发现已审核模板 v{newerTemplate.version}：</b>
              {newerTemplate.reason}
              <p>
                它不会自动改变当前
                Case。采用前必须重新确认适用范围，随后为新模板中的每条机制边重新配置验证与反证规则。
              </p>
              <button
                className="ros-button ros-button--secondary"
                type="button"
                disabled={busy}
                onClick={() => select(newerTemplate.id)}
              >
                {busy
                  ? "正在保存…"
                  : `重新复核并采用 v${newerTemplate.version}`}
              </button>
            </section>
          )}
          <div className="ros-mechanism-nodes">
            {template.nodes.map((node) => (
              <article key={node.id}>
                <span>{node.role}</span>
                <strong>{node.display_name}</strong>
                <small>{node.node_key}</small>
              </article>
            ))}
          </div>
          <section className="ros-mechanism-edges">
            <h3>验证规则与反证</h3>
            {template.edges.map((edge) => {
              const rule = protocol.rules.find(
                (item) => item.mechanism_edge_id === edge.id,
              );
              return (
                <article key={edge.id}>
                  <div>
                    <strong>
                      {nodeById.get(edge.source_node_id)?.display_name} →{" "}
                      {nodeById.get(edge.target_node_id)?.display_name}
                    </strong>
                    <small>{edge.edge_key}</small>
                  </div>
                  {rule ? (
                    <dl>
                      <div>
                        <dt>支持</dt>
                        <dd>{rule.support_predicate}</dd>
                      </div>
                      <div>
                        <dt>反证</dt>
                        <dd>{rule.contradiction_predicate}</dd>
                      </div>
                      <div>
                        <dt>允许来源</dt>
                        <dd>{rule.allowed_source_roles.join("、")}</dd>
                      </div>
                      <div>
                        <dt>下一验证</dt>
                        <dd>{rule.next_verification_event}</dd>
                      </div>
                    </dl>
                  ) : (
                    <p>尚未定义可执行验证规则；该边不能被自动视为成立。</p>
                  )}
                </article>
              );
            })}
          </section>
        </>
      )}
    </section>
  );
}

function MechanismRuleConfig({ caseId }: { caseId: string }) {
  const [protocol, setProtocol] = useState<CaseMechanismProtocol | null>(null);
  const [metrics, setMetrics] = useState<MetricDefinition[]>([]);
  const [edgeId, setEdgeId] = useState("");
  const [metricId, setMetricId] = useState("");
  const [support, setSupport] = useState("");
  const [contradiction, setContradiction] = useState("");
  const [direction, setDirection] = useState<
    "increase" | "decrease" | "stable" | "mixed"
  >("increase");
  const [sourceRoles, setSourceRoles] = useState("primary_disclosure");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [availableDeadline, setAvailableDeadline] = useState("");
  const [nextEvent, setNextEvent] = useState("");
  const [reason, setReason] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function load() {
    const [current, metricItems] = await Promise.all([
      researchOsApi.caseMechanismProtocol(caseId),
      researchOsApi.metrics(),
    ]);
    setProtocol(current);
    setMetrics(metricItems);
    setEdgeId((value) => value || current.template?.edges[0]?.id || "");
    setMetricId((value) => value || metricItems[0]?.id || "");
  }
  useEffect(() => {
    load().catch(() =>
      setMessage("规则配置暂不可读；不会用空白规则放行研究。"),
    );
  }, [caseId]);
  useEffect(() => {
    const current = protocol?.rules.find(
      (rule) => rule.mechanism_edge_id === edgeId,
    );
    if (!current) return;
    setMetricId(current.metric_definition_id);
    setDirection(current.expected_direction as typeof direction);
    setSourceRoles(current.allowed_source_roles.join(", "));
    setPeriodStart(current.observed_period_start);
    setPeriodEnd(current.observed_period_end);
    setAvailableDeadline(current.available_at_deadline);
    setNextEvent(current.next_verification_event);
    setSupport(current.support_predicate);
    setContradiction(current.contradiction_predicate);
    setReason(current.reason);
  }, [edgeId, protocol]);
  async function save() {
    const allowedSourceRoles = sourceRoles
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    if (
      !edgeId ||
      !metricId ||
      !support.trim() ||
      !contradiction.trim() ||
      !nextEvent.trim() ||
      !reason.trim() ||
      !periodStart ||
      !periodEnd ||
      !availableDeadline ||
      !allowedSourceRoles.length
    ) {
      setMessage(
        "请填写支持与反证条件、来源角色、观察期、可用截止日、下一验证事件和记录原因。",
      );
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      await researchOsApi.createVerificationRule(caseId, edgeId, {
        metric_definition_id: metricId,
        expected_direction: direction,
        support_predicate: support.trim(),
        contradiction_predicate: contradiction.trim(),
        allowed_source_roles: allowedSourceRoles,
        observed_period_start: periodStart,
        observed_period_end: periodEnd,
        available_at_deadline: availableDeadline,
        next_verification_event: nextEvent.trim(),
        reviewer: "human:researcher",
        reason: reason.trim(),
      });
      await load();
      setMessage("验证规则已保存为新版本；上一规则仍可回放。");
    } catch {
      setMessage("验证规则未保存；当前协议保持原样。");
    } finally {
      setBusy(false);
    }
  }
  if (!protocol?.template) return null;
  const activeRule = protocol.rules.find(
    (rule) => rule.mechanism_edge_id === edgeId,
  );
  return (
    <section className="ros-rule-config">
      <header>
        <p className="ros-eyebrow">配置验证规则 · 当前 Case 独有</p>
        <h2>为机制边写下可观察的支持与反证</h2>
        <p>
          提交会为当前 Case 新增版本，不覆盖旧规则，也不会被其他 Case
          继承；来源角色必须在所选指标的允许范围内。
        </p>
        {activeRule && (
          <p className="ros-note">
            正在调整 {activeRule.reviewer} 于 {activeRule.created_at}{" "}
            记录的当前版本；保存后可按版本回放。
          </p>
        )}
      </header>
      <div className="ros-rule-config__fields">
        <label>
          机制边
          <select
            value={edgeId}
            onChange={(event) => setEdgeId(event.target.value)}
          >
            {protocol.template.edges.map((edge) => (
              <option key={edge.id} value={edge.id}>
                {edge.edge_key}
              </option>
            ))}
          </select>
        </label>
        <label>
          验证指标
          <select
            value={metricId}
            onChange={(event) => setMetricId(event.target.value)}
          >
            {metrics.map((metric) => (
              <option key={metric.id} value={metric.id}>
                {metric.display_name} · v{metric.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          预期方向
          <select
            value={direction}
            onChange={(event) =>
              setDirection(event.target.value as typeof direction)
            }
          >
            <option value="increase">增长</option>
            <option value="decrease">下降</option>
            <option value="stable">稳定</option>
            <option value="mixed">混合</option>
          </select>
        </label>
        <label>
          允许来源角色（逗号分隔）
          <input
            value={sourceRoles}
            onChange={(event) => setSourceRoles(event.target.value)}
            placeholder="primary_disclosure"
          />
        </label>
        <label>
          观察期起点
          <input
            type="date"
            value={periodStart}
            onChange={(event) => setPeriodStart(event.target.value)}
          />
        </label>
        <label>
          观察期终点
          <input
            type="date"
            value={periodEnd}
            onChange={(event) => setPeriodEnd(event.target.value)}
          />
        </label>
        <label>
          最晚可用时点
          <input
            type="date"
            value={availableDeadline}
            onChange={(event) => setAvailableDeadline(event.target.value)}
          />
        </label>
        <label>
          下一验证事件
          <input
            value={nextEvent}
            onChange={(event) => setNextEvent(event.target.value)}
            placeholder="例如：2026Q1 财报"
          />
        </label>
        <label>
          支持条件
          <textarea
            value={support}
            onChange={(event) => setSupport(event.target.value)}
            placeholder="例如：公司一手披露的实际 CapEx 同比增长"
          />
        </label>
        <label>
          反证条件
          <textarea
            value={contradiction}
            onChange={(event) => setContradiction(event.target.value)}
            placeholder="例如：同口径 CapEx 下调或未投向目标架构"
          />
        </label>
        <label>
          登记原因
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="说明为何该条件能支持或反驳此机制边"
          />
        </label>
      </div>
      <button
        className="ros-button ros-button--primary"
        type="button"
        disabled={busy || !metrics.length}
        onClick={save}
      >
        {busy ? "正在保存…" : "保存为新的验证规则版本"}
      </button>
      {message && (
        <p
          className={
            message.startsWith("验证规则已") ? "ros-success" : "ros-error"
          }
        >
          {message}
        </p>
      )}
    </section>
  );
}

function MechanismRuleHistory({ caseId }: { caseId: string }) {
  const [protocol, setProtocol] = useState<CaseMechanismProtocol | null>(null);
  useEffect(() => {
    researchOsApi
      .caseMechanismProtocol(caseId)
      .then(setProtocol)
      .catch(() => setProtocol(null));
  }, [caseId]);
  const history = protocol?.rule_history ?? [];
  if (!protocol?.template) return null;
  const edgeById = new Map(
    protocol.template.edges.map((edge) => [edge.id, edge]),
  );
  return (
    <section className="ros-rule-history">
      <header>
        <p className="ros-eyebrow">验证规则版本记录</p>
        <h2>每次调整都可回放</h2>
        <p>
          以下记录只属于当前
          Case；当前有效规则与历史版本均保留审核人、原因和时点。
        </p>
      </header>
      {history.length === 0 ? (
        <div className="ros-empty ros-empty--compact">
          尚无规则版本；系统不会把空白配置当作默认规则。
        </div>
      ) : (
        <ol>
          {history.map((rule) => (
            <li key={rule.id}>
              <strong>
                {edgeById.get(rule.mechanism_edge_id)?.edge_key ||
                  rule.mechanism_edge_id}
              </strong>
              <span>{rule.supersedes_id ? "替代上一版本" : "首个版本"}</span>
              <p>
                {rule.support_predicate}；反证：{rule.contradiction_predicate}
              </p>
              <small>
                {rule.reviewer} · {rule.created_at} · {rule.reason}
              </small>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

type RunEvent = {
  seq: number;
  stage: string | null;
  status: string | null;
  message: string | null;
  details: Record<string, unknown>;
  createdAt?: string;
};
export function CaseMonitorPage() {
  return (
    <CaseFrame>
      {(data, caseId) => (
        <MonitorContent
          caseId={caseId}
          activeRunId={data.lifecycle.activeRunId}
        />
      )}
    </CaseFrame>
  );
}
function MonitorContent({
  caseId,
  activeRunId,
}: {
  caseId: string;
  activeRunId: string | null;
}) {
  const [detail, setDetail] = useState<MonitorDetail | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [drawer, setDrawer] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [monitorLoadError, setMonitorLoadError] = useState(false);
  const [runEventsLoadError, setRunEventsLoadError] = useState(false);
  const [protocolLoadError, setProtocolLoadError] = useState(false);
  const [protocolReload, setProtocolReload] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(
    activeRunId,
  );
  const [starting, setStarting] = useState(false);
  const [protocolStates, setProtocolStates] = useState<
    Record<string, Researchability>
  >({});
  const loadMonitor = () => {
    setMonitorLoadError(false);
    return researchOsApi
      .monitor(caseId)
      .then((value) => {
        setDetail(value);
        setSelectedRunId(value.latest_run?.id ?? null);
      })
      .catch(() => setMonitorLoadError(true));
  };
  const loadEvents = (runId: string) => {
    setRunEventsLoadError(false);
    return researchOsApi
      .runEvents(runId)
      .then((response) =>
        setEvents(
          response.items.map((event) => ({
            seq: event.seq,
            stage: event.stage ?? null,
            status: event.status ?? null,
            message: event.message ?? null,
            details: event.details ?? {},
            createdAt: event.created_at,
          })),
        ),
      )
      .catch(() => {
        setEvents([]);
        setRunEventsLoadError(true);
      });
  };
  useEffect(() => {
    void loadMonitor();
  }, [caseId]);
  useEffect(() => {
    if (!selectedRunId) {
      setEvents([]);
      return;
    }
    void loadEvents(selectedRunId);
    const refresh = window.setInterval(
      () => void loadEvents(selectedRunId),
      5_000,
    );
    return () => window.clearInterval(refresh);
  }, [selectedRunId]);
  useEffect(() => {
    let active = true;
    const factors = detail?.confirmed_factors ?? [];
    setProtocolLoadError(false);
    if (!factors.length) {
      setProtocolStates({});
      return () => {
        active = false;
      };
    }
    Promise.all(
      factors.map(
        async (factor) =>
          [factor.id, await researchOsApi.researchability(factor.id)] as const,
      ),
    )
      .then((items) => {
        if (active) setProtocolStates(Object.fromEntries(items));
      })
      .catch(() => {
        if (!active) return;
        setProtocolStates({});
        setProtocolLoadError(true);
      });
    return () => {
      active = false;
    };
  }, [detail, protocolReload]);
  const run =
    selectedRunId && detail?.latest_run?.id === selectedRunId
      ? detail.latest_run
      : null;
  const protocolBlockers = Object.entries(protocolStates)
    .filter(([, state]) => state.status === "blocked")
    .flatMap(([id, state]) =>
      state.reason_codes.map((code) => ({
        id,
        code,
        nextAction: state.next_action,
      })),
    );
  async function startNow() {
    if (!detail?.monitor) {
      setError("请先保存一份 CaseMonitor 配置，系统才能冻结本次补证范围。");
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const created = await researchOsApi.startMonitorRun(caseId);
      setSelectedRunId(created.id);
      await Promise.all([loadMonitor(), loadEvents(created.id)]);
      setDrawer(true);
    } catch {
      setError(
        "无法按当前 CaseMonitor 版本创建立即补证运行；没有写入部分运行。请检查服务状态后重试。",
      );
    } finally {
      setStarting(false);
    }
  }
  async function cancelRun(changeReason: string) {
    if (!selectedRunId) return;
    setError(null);
    try {
      await researchOsApi.cancelRun(selectedRunId, changeReason);
      await Promise.all([loadMonitor(), loadEvents(selectedRunId)]);
    } catch {
      setError(
        "停止运行失败；原运行状态与记录未被页面伪造修改。请刷新后重试。",
      );
    }
  }
  return (
    <section className="ros-monitor">
      <header className="ros-section-heading">
        <div>
          <p className="ros-eyebrow">监控与版本</p>
          <h2>每一次运行均可检查、复现与配置</h2>
        </div>
        <div className="ros-header-actions">
          <button
            className="ros-button ros-button--primary"
            type="button"
            disabled={
              starting ||
              !detail?.monitor ||
              protocolLoadError ||
              protocolBlockers.length > 0
            }
            onClick={startNow}
          >
            {starting ? "正在创建运行…" : "立即补证一次"}
          </button>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            disabled={!selectedRunId}
            onClick={() => setDrawer(true)}
          >
            打开运行详情
          </button>
        </div>
      </header>
      {protocolBlockers.length > 0 && (
        <section className="ros-rulebox">
          <b>研究协议尚未通过，不能启动补证。</b>
          {protocolBlockers.map((blocker) => (
            <p key={`${blocker.id}:${blocker.code}`}>
              {protocolReason[blocker.code] || blocker.code}；下一步：
              {blocker.nextAction}
            </p>
          ))}
          <Link
            className="ros-button ros-button--primary"
            to={`/events/${caseId}/protocol`}
          >
            补齐研究协议
          </Link>
        </section>
      )}
      {monitorLoadError && (
        <section className="ros-empty ros-empty--compact" role="alert">
          <strong>监控配置暂不可用</strong>
          <p>
            当前无法确认此 Case 的监控范围、配置版本或最近运行；不会以旧页面状态代替真实记录。
          </p>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            onClick={() => void loadMonitor()}
          >
            重新读取监控配置
          </button>
        </section>
      )}
      {runEventsLoadError && (
        <section className="ros-empty ros-empty--compact" role="alert">
          <strong>本次运行事件暂不可读取</strong>
          <p>
            当前无法确认此运行的阶段、排除理由或候选输出；不会将上一次读取到的事件当作本次真实记录。
          </p>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            disabled={!selectedRunId}
            onClick={() => selectedRunId && void loadEvents(selectedRunId)}
          >
            重新读取本次运行事件
          </button>
        </section>
      )}
      {protocolLoadError && (
        <section className="ros-empty ros-empty--compact" role="alert">
          <strong>研究协议状态暂不可读取</strong>
          <p>
            系统无法确认当前因素是否满足结果指标、范围和验证规则，立即补证保持关闭，避免创建无法解释的运行。
          </p>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            onClick={() => setProtocolReload((value) => value + 1)}
          >
            重新读取研究协议状态
          </button>
        </section>
      )}
      {error && <p className="ros-error">{error}</p>}
      <div className="ros-monitor-grid">
        <section className="ros-card ros-run-card">
          <header className="ros-card-head">
            <div>
              <p className="ros-eyebrow">
                {run ? "选中的运行" : selectedRunId ? "运行状态待重读" : "尚未开始运行"}
              </p>
              <h2>{run ? `${runStageLabel(run.stage)} · ${runStatusLabel(run.status)}` : selectedRunId ? "运行状态暂不可读取" : "先配置持续研究"}</h2>
            </div>
            <span>{run ? `更新于 ${run.updated_at}` : selectedRunId ? "等待服务返回实际状态" : ""}</span>
          </header>
          <div className="ros-run-summary">
            <strong>
              {events.length
                ? "系统的每一步都在记录"
                : "运行将从范围冻结开始记录"}
            </strong>
            <p>
              {events.length
                ? "查看每个阶段的输入、允许来源、排除理由和输出；系统不会在后台悄悄改写结论。"
                : "立即运行或定时任务都会绑定有效的 CaseMonitor 配置版本。"}
            </p>
          </div>
          <ol className="ros-run-log">
            {events.length ? (
              events.map((event) => (
                <li key={event.seq}>
                  <span>{event.seq}</span>
                  <div>
                    <strong>
                      {runStageLabel(event.stage)} · {runStatusLabel(event.status)}
                    </strong>
                    <p>{event.message || "无文字摘要"}</p>
                    <small>
                      {formatRunEventDetails(event.details) || "无额外字段"}
                    </small>
                  </div>
                </li>
              ))
            ) : (
              <li>
                <span>1</span>
                <div>
                  <strong>等待运行</strong>
                  <p>
                    尚无结构化运行事件；新运行会从读取并冻结配置开始逐条记录。
                  </p>
                </div>
              </li>
            )}
          </ol>
        </section>
        <aside className="ros-monitor-config">
          <section className="ros-panel">
            <p className="ros-eyebrow">
              当前 CaseMonitor ·{" "}
              {detail?.monitor ? `配置 v${detail.monitor.version}` : "未配置"}
            </p>
            <h2>用于未来运行的配置</h2>
            <p>
              它只决定下一次立即补证与定时任务；历史运行必须以它自己的冻结事件回放。
            </p>
            {detail?.monitor ? (
              <dl className="ros-definition">
                <div>
                  <dt>运行频率</dt>
                  <dd>{detail.monitor.frequency} · 中国标准时间</dd>
                </div>
                <div>
                  <dt>最近一次运行</dt>
                  <dd>
                    {detail.latest_run
                      ? new Date(detail.latest_run.updated_at).toLocaleString(
                          "zh-CN",
                          { timeZone: "Asia/Shanghai" },
                        )
                      : "尚无运行记录"}
                  </dd>
                </div>
                <div>
                  <dt>下次定时检查</dt>
                  <dd>
                    {detail.next_scheduled_at
                      ? new Date(detail.next_scheduled_at).toLocaleString(
                          "zh-CN",
                          { timeZone: "Asia/Shanghai" },
                        )
                      : detail.monitor.status === "paused"
                        ? "已暂停"
                        : "当前频率未被调度器支持"}
                  </dd>
                </div>
                <div>
                  <dt>允许来源</dt>
                  <dd>{sourceTypeListLabel(detail.monitor.allowed_source_types)}</dd>
                </div>
                <div>
                  <dt>下一验证</dt>
                  <dd>{detail.monitor.next_verification_event}</dd>
                </div>
                <div>
                  <dt>单次上限</dt>
                  <dd>{detail.monitor.budget} 份候选资料</dd>
                </div>
              </dl>
            ) : (
              <div className="ros-empty ros-empty--compact">
                尚未设置监控条件。
              </div>
            )}
            <Link
              className="ros-button ros-button--primary"
              to={`/events/${caseId}/monitor/config`}
            >
              调整配置
            </Link>
          </section>
          <p className="ros-rulebox">
            <b>可复现要求：</b>
            触发人/原因、配置版本、查询口径、来源许可、资料版本、阶段输出、排除理由和失败原因均要可查看。
          </p>
        </aside>
      </div>
      {drawer && selectedRunId && (
        <RunDrawer
          run={run}
          detail={detail}
          events={events}
          onCancel={cancelRun}
          onClose={() => setDrawer(false)}
        />
      )}
    </section>
  );
}

function RunDrawer({
  run,
  detail,
  events,
  onCancel,
  onClose,
}: {
  run:
    | MonitorDetail["latest_run"]
    | { id: string; status: string; stage: string; updated_at: string }
    | null;
  detail: MonitorDetail | null;
  events: RunEvent[];
  onCancel: (reason: string) => Promise<void>;
  onClose: () => void;
}) {
  const [reason, setReason] = useState("");
  const [stopping, setStopping] = useState(false);
  const scope = events.find((event) => event.stage === "scope")?.details;
  const value = (key: string) => scope?.[key];
  const list = (key: string) => {
    const current = value(key);
    return Array.isArray(current)
      ? current.join("、")
      : current
        ? String(current)
        : null;
  };
  const canStop = ["queued", "running", "waiting_for_review"].includes(
    run?.status || "",
  );
  async function stop() {
    if (!reason.trim()) return;
    setStopping(true);
    try {
      await onCancel(reason.trim());
      setReason("");
    } finally {
      setStopping(false);
    }
  }
  return (
    <aside className="ros-run-drawer" aria-label="运行详情">
      <header>
        <div>
          <p className="ros-eyebrow">ResearchRun · {run?.id || "尚无运行"}</p>
          <h2>{run ? `${runStageLabel(run.stage)} · ${runStatusLabel(run.status)}` : "运行详情"}</h2>
          <p>
            {scope
              ? "以下口径来自本次运行的冻结事件。"
              : "冻结口径尚未在事件记录中提供，不能用当前配置替代。"}
          </p>
        </div>
        <button aria-label="关闭运行详情" type="button" onClick={onClose}>
          ×
        </button>
      </header>
      <div className="ros-drawer-body">
        <section className="ros-run-scope">
          <p className="ros-eyebrow">本次运行口径</p>
          <dl>
            <div>
              <dt>触发方式</dt>
              <dd>{runTriggerLabel(list("trigger"))}</dd>
            </div>
            <div>
              <dt>配置版本</dt>
              <dd>{list("monitor_version_id") || "未记录"}</dd>
            </div>
            <div>
              <dt>范围因素</dt>
              <dd>
                {list("factor_statements") || list("factor_ids") || "未记录"}
              </dd>
            </div>
            <div>
              <dt>允许来源</dt>
              <dd>
                {sourceTypeListLabel(
                  value("allowed_source_types") as string[] | null | undefined,
                )}
              </dd>
            </div>
            <div>
              <dt>检索预算</dt>
              <dd>{list("budget") || "未记录"}</dd>
            </div>
            <div>
              <dt>运行事件</dt>
              <dd>{events.length} 条</dd>
            </div>
          </dl>
          {!scope && detail?.monitor && (
            <p className="ros-note">
              当前配置为 v{detail.monitor.version}，仅供下一次运行使用。
            </p>
          )}
        </section>
        {canStop && (
          <section className="ros-run-control">
            <p className="ros-eyebrow">人工停止</p>
            <p>
              停止会取消尚未完成的任务，但不覆盖本次冻结范围、已产生的阶段记录或历史结论。
            </p>
            <label>
              停止原因
              <textarea
                aria-label="停止原因"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder="说明为什么现在应停止这次运行"
              />
            </label>
            <button
              className="ros-button ros-button--secondary"
              type="button"
              disabled={!reason.trim() || stopping}
              onClick={() => void stop()}
            >
              {stopping ? "正在停止…" : "停止本次运行"}
            </button>
          </section>
        )}
        <ol className="ros-run-log">
          {events.map((event) => (
            <li key={event.seq}>
              <span>{event.seq}</span>
              <div>
                <strong>{runStageLabel(event.stage)} · {runStatusLabel(event.status)}</strong>
                <p>{event.message || "无文字摘要"}</p>
                <small>
                  {event.createdAt
                    ? new Date(event.createdAt).toLocaleString("zh-CN")
                    : "时间未记录"}{" "}
                  ·{" "}
                  {formatRunEventDetails(event.details) || "无额外字段"}
                </small>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </aside>
  );
}

export function MonitorConfigPage() {
  return (
    <CaseFrame>
      {(_data, caseId) => (
        <>
          <MonitorConfigForm caseId={caseId} />
          <MonitorHistory caseId={caseId} />
        </>
      )}
    </CaseFrame>
  );
}
function MonitorHistory({ caseId }: { caseId: string }) {
  const [history, setHistory] = useState<Monitor[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setLoadError(false);
    researchOsApi
      .monitor(caseId)
      .then((value) => {
        if (!active) return;
        setHistory(value.history ?? []);
        setLoadError(false);
      })
      .catch(() => active && setLoadError(true));
    return () => {
      active = false;
    };
  }, [caseId, reload]);
  return (
    <section className="ros-rule-history">
      <p className="ros-eyebrow">CaseMonitor 版本历史</p>
      <h2>配置由谁、为何变更</h2>
      {loadError ? (
        <div className="ros-empty ros-empty--compact" role="alert">
          <strong>无法读取 CaseMonitor 版本历史</strong>
          <p>当前不会把不可读取的审计记录表示为“尚无配置”。</p>
          <button
            className="ros-button ros-button--secondary"
            type="button"
            onClick={() => setReload((value) => value + 1)}
          >
            重新读取监控版本历史
          </button>
        </div>
      ) : history.length ? (
        <ol>
          {history.map((monitor) => (
            <li key={monitor.id}>
              <strong>
                v{monitor.version} · {monitor.status}
              </strong>
              <span>
                {monitor.changed_by} · {monitor.created_at}
              </span>
              <p>{monitor.change_reason}</p>
              <small>
                {monitor.frequency} · {sourceTypeListLabel(monitor.allowed_source_types)}{" "}
                · 预算 {monitor.budget}
              </small>
            </li>
          ))}
        </ol>
      ) : (
        <div className="ros-empty ros-empty--compact">
          尚无可展示的监控配置版本。
        </div>
      )}
    </section>
  );
}
function MonitorConfigForm({ caseId }: { caseId: string }) {
  const [detail, setDetail] = useState<MonitorDetail | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [frequency, setFrequency] = useState("weekday_08_30");
  const [sources, setSources] = useState<string[]>(["licensed_provider"]);
  const [nextEvent, setNextEvent] = useState("");
  const [budget, setBudget] = useState(20);
  const [reason, setReason] = useState("调整监控范围");
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => {
    researchOsApi
      .monitor(caseId)
      .then((value) => {
        setDetail(value);
        setSelected(
          value.monitor?.factor_ids ??
            value.confirmed_factors.map((factor) => factor.id),
        );
        setFrequency(value.monitor?.frequency ?? "weekday_08_30");
        setSources(
          value.monitor?.allowed_source_types ?? ["licensed_provider"],
        );
        setNextEvent(value.monitor?.next_verification_event ?? "");
        setBudget(value.monitor?.budget ?? 20);
      })
      .catch(() => setMessage("无法读取当前可确认因素。"));
  }, [caseId]);
  const toggleFactor = (id: string) =>
    setSelected((current) =>
      current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id],
    );
  const toggleSource = (source: string) =>
    setSources((current) =>
      current.includes(source)
        ? current.filter((value) => value !== source)
        : [...current, source],
    );
  async function save() {
    if (
      !selected.length ||
      !sources.length ||
      !nextEvent.trim() ||
      !reason.trim()
    ) {
      setMessage(
        "请选择至少一个已确认因素、一个允许来源，并说明下一验证事件与变更原因。",
      );
      return;
    }
    try {
      const saved = await researchOsApi.saveMonitor(caseId, {
        actor: "human:researcher",
        frequency,
        factor_ids: selected,
        allowed_source_types: sources as (
          | "licensed_provider"
          | "company_disclosure"
          | "uploaded_file"
          | "pasted_snapshot"
        )[],
        next_verification_event: nextEvent,
        budget,
        change_reason: reason,
      });
      setDetail((current) =>
        current ? { ...current, monitor: saved } : current,
      );
      setSelected(saved.factor_ids);
      setFrequency(saved.frequency);
      setSources(saved.allowed_source_types);
      setNextEvent(saved.next_verification_event);
      setBudget(saved.budget);
      setReason(saved.change_reason);
      setMessage(`已保存监控版本 v${saved.version}；此前版本保持不变。`);
    } catch {
      setMessage("保存失败，未写入任何配置版本。");
    }
  }
  return (
    <section className="ros-panel ros-config">
      <p className="ros-eyebrow">配置监控</p>
      <h2>调整后会创建新的可复现版本</h2>
      <p>
        系统只使用这里明确允许的来源和已确认因素；立即补证与定时运行都引用同一版本。
      </p>
      {detail?.monitor && (
        <div className="ros-config-current" aria-live="polite">
          <strong>当前生效版本 v{detail.monitor.version}</strong>
          <span>
            {detail.monitor.status === "paused"
              ? "定时任务已暂停"
              : "定时任务已启用"}{" "}
            · {sourceTypeListLabel(detail.monitor.allowed_source_types)} · 下一验证：
            {detail.monitor.next_verification_event}
          </span>
        </div>
      )}
      {!detail ? (
        <div className="ros-empty ros-empty--compact">正在读取可配置因素…</div>
      ) : (
        <div className="ros-config-form">
          <fieldset>
            <legend>已确认关键因素</legend>
            {detail.confirmed_factors.map((factor) => (
              <label key={factor.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(factor.id)}
                  onChange={() => toggleFactor(factor.id)}
                />
                {factor.statement}
              </label>
            ))}
          </fieldset>
          <label>
            频率
            <select
              value={frequency}
              onChange={(event) => setFrequency(event.target.value)}
            >
              <option value="weekday_08_30">工作日 08:30</option>
              <option value="weekday_12_30">工作日 12:30</option>
              <option value="daily_20_00">每日 20:00</option>
            </select>
          </label>
          <fieldset>
            <legend>允许来源</legend>
            {[
              ["licensed_provider", "授权数据源"],
              ["company_disclosure", "公司披露"],
              ["uploaded_file", "人工上传"],
              ["pasted_snapshot", "粘贴快照"],
            ].map(([value, label]) => (
              <label key={value}>
                <input
                  type="checkbox"
                  checked={sources.includes(value)}
                  onChange={() => toggleSource(value)}
                />
                {label}
              </label>
            ))}
          </fieldset>
          <label>
            下一验证事件
            <input
              value={nextEvent}
              onChange={(event) => setNextEvent(event.target.value)}
              placeholder="例如：2026Q1 财报披露"
            />
          </label>
          <fieldset>
            <legend>定时任务</legend>
            <p>
              当前状态：
              {detail.monitor?.status === "paused"
                ? "已暂停，人工立即补证仍可单独执行"
                : "启用"}
            </p>
            <MonitorStatusControl
              caseId={caseId}
              status={detail.monitor?.status ?? "active"}
              onChanged={(monitor) => {
                setDetail({ ...detail, monitor });
                setMessage(`已保存监控版本 v${monitor.version}。`);
              }}
            />
          </fieldset>
          <label>
            检索预算
            <input
              type="number"
              min="1"
              value={budget}
              onChange={(event) => setBudget(Number(event.target.value))}
            />
          </label>
          <label>
            新版本变更原因
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          <button
            className="ros-button ros-button--primary"
            type="button"
            onClick={save}
          >
            保存为新监控版本
          </button>
        </div>
      )}
      {message && (
        <p
          className={message.startsWith("已保存") ? "ros-success" : "ros-error"}
        >
          {message}
        </p>
      )}
      <Link
        className="ros-button ros-button--secondary"
        to={`/events/${caseId}/monitor`}
      >
        返回运行记录
      </Link>
    </section>
  );
}

function MonitorStatusControl({
  caseId,
  status,
  onChanged,
}: {
  caseId: string;
  status: string;
  onChanged: (monitor: NonNullable<MonitorDetail["monitor"]>) => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = status === "paused" ? "active" : "paused";
  async function change() {
    if (!reason.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const monitor = await researchOsApi.setMonitorStatus(
        caseId,
        target,
        reason.trim(),
      );
      onChanged(monitor);
      setReason("");
    } catch {
      setError("无法变更定时任务状态；当前版本未被改写。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="ros-monitor-status">
      <label>
        变更原因
        <input
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder={
            target === "paused"
              ? "例如：等待下一次财报后再运行"
              : "例如：恢复常规验证"
          }
        />
      </label>
      <button
        className="ros-button ros-button--secondary"
        type="button"
        disabled={!reason.trim() || busy}
        onClick={change}
      >
        {busy
          ? "正在保存…"
          : target === "paused"
            ? "暂停未来定时任务"
            : "恢复定时任务"}
      </button>
      {error && <p className="ros-error">{error}</p>}
    </div>
  );
}
