import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { researchOsApi, type Graph } from "../../app/researchOsApi";

function isCandidateNode(node: Graph["nodes"][number]) {
  return node.kind === "proposal" || node.properties?.review_state === "machine_generated";
}

function isCandidateEdge(edge: Graph["edges"][number]) {
  return edge.review_state === "machine_generated";
}

const PROPERTY_LABELS: Record<string, string> = {
  available_at: "可用时点",
  document_id: "冻结版本",
  locator: "定位",
  parser_version: "解析版本",
  permission_status: "许可",
  published_at: "发布日期",
  reason: "关系理由",
  relation_type: "关系分类",
  review_state: "审核状态",
  review_reason: "审核理由",
  reviewed_at: "审核时间",
  reviewer: "审核人",
  source_span_id: "原文定位",
  span_id: "原文片段",
  created_by: "创建者",
};

const EDGE_LABELS: Record<string, string> = {
  candidate_relation: "候选关系",
  case_relation: "关联 Case",
  contains_thesis: "Case 包含研究命题",
  quoted_by: "引用冻结原文",
  related_case: "相关 Case",
  supports: "支持关系",
};

function permissionLabel(value: unknown) {
  return value === "admitted" ? "已准入" : value === "denied" ? "未准入" : String(value);
}

function locatorLabel(value: unknown) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return String(value);
  const locator = value as Record<string, unknown>;
  const bits = [
    locator.page !== undefined ? `第 ${locator.page} 页` : null,
    typeof locator.section === "string" ? locator.section : null,
    locator.paragraph !== undefined ? `第 ${locator.paragraph} 段` : null,
  ].filter(Boolean);
  return bits.join(" · ") || JSON.stringify(locator);
}

function propertyValue(key: string, value: unknown) {
  if (key === "permission_status") return permissionLabel(value);
  if (key === "locator") return locatorLabel(value);
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function EdgeInspector({
  caseId,
  edge,
  nodeById,
}: {
  caseId: string;
  edge: Graph["edges"][number];
  nodeById: Map<string, Graph["nodes"][number]>;
}) {
  const source = nodeById.get(edge.source);
  const target = nodeById.get(edge.target);
  const isCandidate = isCandidateEdge(edge);
  const properties = edge.properties ?? {};
  const sourceProperties = source?.properties ?? {};
  const frozenDocumentId = typeof sourceProperties.document_id === "string"
    ? sourceProperties.document_id
    : source?.kind === "document" ? source.id : null;
  const canLocateFrozenSource = sourceProperties.source_visible_in_case === true && Boolean(frozenDocumentId);
  const adjacentCase = [source, target].find(
    (node) => node?.kind === "case" && node.id !== caseId,
  );

  return <>
    <span className={`ros-pill ${isCandidate ? "ros-pill--human" : "ros-pill--system"}`}>
      {isCandidate ? "AI 候选，未经人工复核" : "已审核关系"}
    </span>
    <h3>关系：{source?.label ?? edge.source} → {target?.label ?? edge.target}</h3>
    <p>{EDGE_LABELS[edge.semantic_kind] ?? edge.semantic_kind}。这是一条可回放的关系记录，不会自动改写 Case 结论。</p>
    <dl>
      <div><dt>关系语义</dt><dd>{edge.semantic_kind}</dd></div>
      <div><dt>审核状态</dt><dd>{edge.review_state ?? "未记录"}</dd></div>
      <div><dt>可用时点</dt><dd>{edge.available_at ?? "未记录"}</dd></div>
      {edge.valid_interval && <div><dt>有效区间</dt><dd>{propertyValue("valid_interval", edge.valid_interval)}</dd></div>}
      {edge.source_refs?.length ? <div><dt>来源引用</dt><dd>{edge.source_refs.join(" · ")}</dd></div> : null}
      {Object.entries(properties).map(([key, value]) => <div key={key}><dt>{PROPERTY_LABELS[key] ?? key}</dt><dd>{propertyValue(key, value)}</dd></div>)}
    </dl>
    {canLocateFrozenSource && <Link className="ros-button ros-button--secondary" to={`/events/${caseId}/documents?document=${encodeURIComponent(frozenDocumentId!)}`}>定位到冻结原文</Link>}
    {isCandidate && <Link className="ros-button" to={edge.semantic_kind === "case_relation" ? `/events/${caseId}/relations` : `/events/${caseId}/review`}>
      {edge.semantic_kind === "case_relation" ? "审核关联 Case 候选" : "审核此候选关系"}
    </Link>}
    {adjacentCase && <Link className="ros-button ros-button--secondary" to={`/events/${adjacentCase.id}`}>打开关联 Case</Link>}
    <p className="ros-note">关联 Case 不继承当前 Case 的结论或审核状态。AI 候选也不继承市场表达或基金暴露。</p>
  </>;
}

export function WikiInspectorContent({ caseId }: { caseId: string }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [showCandidates, setShowCandidates] = useState(true);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    setGraph(null);
    setError(null);
    researchOsApi.graph(caseId)
      .then((value) => {
        if (!active) return;
        setGraph(value);
        setSelectedId(value.nodes[0]?.id ?? null);
        setSelectedEdgeId(null);
      })
      .catch(
        () =>
          active &&
          setError(
            "无法读取 Case Wiki 图谱；系统不会把读取失败表示为没有关系。",
          ),
      );
    return () => { active = false; };
  }, [caseId, reload]);

  if (error) {
    return <section className="ros-empty ros-page-gap" role="alert"><strong>Case Wiki 图谱暂不可读取</strong><p>{error}</p><button className="ros-button ros-button--secondary" type="button" onClick={() => setReload((value) => value + 1)}>重试读取 Case Wiki 图谱</button></section>;
  }
  if (!graph) {
    return <div className="ros-empty ros-page-gap">Case Wiki 暂无可读取图谱。系统不会以示例节点替代真实证据。</div>;
  }

  const nodes = graph.nodes.filter((node) => showCandidates || !isCandidateNode(node));
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => showCandidates || (!isCandidateEdge(edge) && visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)));
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0];
  const selectedEdges = edges.filter((edge) => edge.source === selected?.id || edge.target === selected?.id);
  const reviewedCount = graph.edges.filter((edge) => !isCandidateEdge(edge)).length;
  const candidateCount = graph.edges.filter(isCandidateEdge).length;
  const selectedProperties = selected?.properties ?? {};
  const frozenDocumentId = typeof selectedProperties.document_id === "string"
    ? selectedProperties.document_id
    : selected?.kind === "document" ? selected?.id : null;
  const canLocateFrozenSource = selectedProperties.source_visible_in_case === true && Boolean(frozenDocumentId);
  const hasCandidateCaseRelation = selected?.kind === "case" && selectedEdges.some(
    (edge) => edge.semantic_kind === "case_relation" && isCandidateEdge(edge),
  );

  return <section className="ros-wiki">
    <header className="ros-section-heading">
      <div>
        <p className="ros-eyebrow">Case Wiki · 可追溯检查器</p>
        <h2>从关系回到冻结原文与审核边界</h2>
      </div>
      <button className="ros-button ros-button--secondary" type="button" onClick={() => setShowCandidates((value) => !value)}>
        {showCandidates ? `隐藏 AI 候选 ${candidateCount}` : `显示 AI 候选 ${candidateCount}`}
      </button>
    </header>
    <div className="ros-wiki-grid">
      <div className="ros-wiki-wrap">
        <div className="ros-wiki-bar"><span>已审核关系 {reviewedCount}</span><span>实线：已审核 · 虚线：未经人工复核</span></div>
        <div className="ros-wiki-canvas" aria-label="Case Wiki 节点列表">
          {nodes.map((node) => <button type="button" onClick={() => { setSelectedId(node.id); setSelectedEdgeId(null); }} className={`ros-wiki-node ros-wiki-node--${node.kind}${!selectedEdge && node.id === selected?.id ? " is-selected" : ""}${isCandidateNode(node) ? " is-candidate" : ""}`} key={node.id}>
            <span>{node.kind}</span><strong>{node.label}</strong><small>{isCandidateNode(node) ? "AI 候选，未经人工复核" : "已进入 Case 图谱"}</small>
          </button>)}
        </div>
        <div className="ros-wiki-edges" aria-label="Case Wiki 关系">
          {edges.length ? edges.map((edge) => <button className={`ros-wiki-edge${isCandidateEdge(edge) ? " is-candidate" : ""}${selectedEdge?.id === edge.id ? " is-selected" : ""}`} type="button" onClick={() => { setSelectedId(edge.target); setSelectedEdgeId(edge.id); }} key={edge.id}>
            <span>{nodeById.get(edge.source)?.label ?? edge.source}</span><i aria-hidden="true">→</i><span>{nodeById.get(edge.target)?.label ?? edge.target}</span><b>{edge.semantic_kind}</b><small>{isCandidateEdge(edge) ? "AI 候选，未经人工复核" : `已审核关系 · 可用时点 ${edge.available_at ?? "未记录"}`}</small>
          </button>) : <p className="ros-empty ros-empty--compact">当前筛选下暂无可展示关系。</p>}
        </div>
      </div>
      <aside className="ros-wiki-inspector">
        {selectedEdge ? <EdgeInspector caseId={caseId} edge={selectedEdge} nodeById={nodeById} /> : selected ? <>
          <span className={`ros-pill ${isCandidateNode(selected) ? "ros-pill--human" : "ros-pill--system"}`}>{isCandidateNode(selected) ? "未经人工复核" : "已审核对象"}</span>
          <h3>{selected.label}</h3>
          <p>{selected.kind} · {selectedEdges.length} 条可见关联</p>
          <dl>{Object.entries(selectedProperties).filter(([key]) => key !== "verbatim_text" && key !== "source_visible_in_case").map(([key, value]) => <div key={key}><dt>{PROPERTY_LABELS[key] ?? key}</dt><dd>{propertyValue(key, value)}</dd></div>)}</dl>
          {typeof selected.properties?.verbatim_text === "string" && <blockquote className="ros-wiki-quote">{selected.properties.verbatim_text}</blockquote>}
          {canLocateFrozenSource && <Link className="ros-button ros-button--secondary" to={`/events/${caseId}/documents?document=${encodeURIComponent(frozenDocumentId!)}`}>定位到冻结原文</Link>}
          {isCandidateNode(selected) && <Link className="ros-button" to={`/events/${caseId}/review`}>审核此候选关系</Link>}
          {hasCandidateCaseRelation && <Link className="ros-button" to={`/events/${caseId}/relations`}>审核关联 Case 候选</Link>}
          {selected.kind === "case" && selected.id !== caseId && <Link className="ros-button ros-button--secondary" to={`/events/${selected.id}`}>打开关联 Case</Link>}
          {selectedEdges.length > 0 && <section className="ros-wiki-inspector__relations" aria-label="关联审核与时点">
            <h4>关联审核与时点</h4>
            <ul>{selectedEdges.map((edge) => <li key={edge.id}>
              {edge.semantic_kind} · {isCandidateEdge(edge) ? "AI 候选，未经人工复核" : `审核状态 ${edge.review_state ?? "未记录"}`}
              {typeof edge.properties?.reviewer === "string" && ` · 审核人 ${edge.properties.reviewer}`}
              {typeof edge.properties?.review_reason === "string" && ` · 审核理由 ${edge.properties.review_reason}`}
              {typeof edge.properties?.reviewed_at === "string" && ` · 审核时间 ${edge.properties.reviewed_at}`}
              {` · 可用时点 ${edge.available_at ?? "未记录"}`}
            </li>)}</ul>
          </section>}
          <p className="ros-note">AI 候选不继承结论、市场表达或基金暴露。关联 Case 不继承当前 Case 的结论或审核状态。</p>
        </> : <p>当前筛选下没有可检查的图谱对象。</p>}
      </aside>
    </div>
  </section>;
}
