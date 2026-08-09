import { useEffect, useState } from "react";

import { researchOsApi, type Graph } from "../../app/researchOsApi";

function isCandidateNode(node: Graph["nodes"][number]) {
  return node.kind === "proposal" || node.properties?.review_state === "machine_generated";
}

function isCandidateEdge(edge: Graph["edges"][number]) {
  return edge.review_state === "machine_generated";
}

export function WikiInspectorContent({ caseId }: { caseId: string }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCandidates, setShowCandidates] = useState(true);

  useEffect(() => {
    let active = true;
    researchOsApi.graph(caseId)
      .then((value) => {
        if (!active) return;
        setGraph(value);
        setSelectedId(value.nodes[0]?.id ?? null);
      })
      .catch(() => active && setGraph(null));
    return () => { active = false; };
  }, [caseId]);

  if (!graph) {
    return <div className="ros-empty ros-page-gap">Case Wiki 暂无可读取图谱。系统不会以示例节点替代真实证据。</div>;
  }

  const nodes = graph.nodes.filter((node) => showCandidates || !isCandidateNode(node));
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => showCandidates || (!isCandidateEdge(edge) && visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target)));
  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0];
  const selectedEdges = edges.filter((edge) => edge.source === selected?.id || edge.target === selected?.id);
  const reviewedCount = graph.edges.filter((edge) => !isCandidateEdge(edge)).length;
  const candidateCount = graph.edges.filter(isCandidateEdge).length;

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
          {nodes.map((node) => <button type="button" onClick={() => setSelectedId(node.id)} className={`ros-wiki-node ros-wiki-node--${node.kind}${node.id === selected?.id ? " is-selected" : ""}${isCandidateNode(node) ? " is-candidate" : ""}`} key={node.id}>
            <span>{node.kind}</span><strong>{node.label}</strong><small>{isCandidateNode(node) ? "AI 候选，未经人工复核" : "已进入 Case 图谱"}</small>
          </button>)}
        </div>
        <div className="ros-wiki-edges" aria-label="Case Wiki 关系">
          {edges.length ? edges.map((edge) => <button className={`ros-wiki-edge${isCandidateEdge(edge) ? " is-candidate" : ""}`} type="button" onClick={() => setSelectedId(edge.target)} key={edge.id}>
            <span>{nodeById.get(edge.source)?.label ?? edge.source}</span><i aria-hidden="true">→</i><span>{nodeById.get(edge.target)?.label ?? edge.target}</span><b>{edge.semantic_kind}</b><small>{isCandidateEdge(edge) ? "AI 候选，未经人工复核" : `已审核关系 · 可用时点 ${edge.available_at ?? "未记录"}`}</small>
          </button>) : <p className="ros-empty ros-empty--compact">当前筛选下暂无可展示关系。</p>}
        </div>
      </div>
      <aside className="ros-wiki-inspector">
        {selected ? <>
          <span className={`ros-pill ${isCandidateNode(selected) ? "ros-pill--human" : "ros-pill--system"}`}>{isCandidateNode(selected) ? "未经人工复核" : "已审核对象"}</span>
          <h3>{selected.label}</h3>
          <p>{selected.kind} · {selectedEdges.length} 条可见关联</p>
          <dl>{Object.entries(selected.properties ?? {}).filter(([key]) => key !== "verbatim_text").map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd></div>)}</dl>
          {typeof selected.properties?.verbatim_text === "string" && <blockquote className="ros-wiki-quote">{selected.properties.verbatim_text}</blockquote>}
          <p className="ros-note">AI 候选不继承结论、市场表达或基金暴露。关联 Case 不继承当前 Case 的结论或审核状态。</p>
        </> : <p>当前筛选下没有可检查的图谱对象。</p>}
      </aside>
    </div>
  </section>;
}
