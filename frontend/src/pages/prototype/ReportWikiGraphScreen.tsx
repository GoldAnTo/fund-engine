import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { configuredEmbedResearchClient, EMBED_API_CONFIGURATION_ERROR } from "../../data/httpResearchAdapter";
import { researchClient } from "../../data/researchClient";
import type { ReportEmbedWikiGraph, ReportWikiGraph, ReportWikiNode } from "../../domain/eventResearch";
import { formatReportSourceLocator } from "../../domain/reportDisplay";

function GraphCanvas({ graph, onSelect }: { graph: Pick<ReportWikiGraph, "nodes" | "edges">; onSelect: (node: ReportWikiNode) => void }) {
  const layout = useMemo(() => {
    const columns: Record<ReportWikiNode["kind"], number> = { report_claim: 0, company: 1, evidence: 2, market_window: 3, fund: 4 };
    const rowByColumn = new Map<number, number>();
    return graph.nodes.map((node) => { const column = columns[node.kind]; const row = rowByColumn.get(column) ?? 0; rowByColumn.set(column, row + 1); return { node, x: 92 + column * 164, y: 58 + row * 94 }; });
  }, [graph.nodes]);
  const point = new Map(layout.map((item) => [item.node.id, item]));
  const height = Math.max(330, ...layout.map((item) => item.y + 54));
  return <svg className="report-wiki-svg" viewBox={`0 0 820 ${height}`} role="img" aria-label="当前研究范围的关系图谱，另有下方结构化路径列表">
    {graph.edges.map((edge) => { const from = point.get(edge.sourceId); const to = point.get(edge.targetId); return from && to ? <line key={edge.id} x1={from.x + 54} y1={from.y} x2={to.x - 54} y2={to.y} className={`report-wiki-svg__edge report-wiki-svg__edge--${edge.status}`} /> : null; })}
    {layout.map(({ node, x, y }) => <g key={node.id} className={`report-wiki-svg__node report-wiki-svg__node--${node.status}`} role="button" tabIndex={0} aria-label={`查看 ${node.label} 的来源定位或状态`} onClick={() => onSelect(node)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node); } }}><rect x={x - 54} y={y - 25} width="108" height="50" rx="6" /><text x={x} y={y - 3} textAnchor="middle">{node.kind === "report_claim" ? "研报主张" : node.kind === "market_window" ? "市场窗口" : node.kind === "fund" ? "中国基金" : node.kind === "evidence" ? "证据/反证" : "公司"}</text><text x={x} y={y + 14} textAnchor="middle">{node.label.length > 13 ? `${node.label.slice(0, 12)}…` : node.label}</text></g>)}
  </svg>;
}

function PathList({ graph, onSelect }: { graph: Pick<ReportWikiGraph, "nodes" | "edges">; onSelect: (node: ReportWikiNode) => void }) {
  const byId = useMemo(() => new Map(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);
  if (!graph.edges.length) return <p className="report-empty">当前没有可展示的关系路径。图谱不补充或推断缺失关系。</p>;
  return <ol className="report-path-list">{graph.edges.map((edge) => { const from = byId.get(edge.sourceId); const to = byId.get(edge.targetId); if (!from || !to) return null; return <li key={edge.id}><button type="button" onClick={() => onSelect(edge.sourceLocator ? { ...from, sourceLocator: edge.sourceLocator } : to)}><strong>{from.label}</strong><span>{edge.kind}</span><strong>{to.label}</strong></button></li>; })}</ol>;
}

export function ReportWikiGraphScreen() {
  const { caseId = "" } = useParams();
  const [graph, setGraph] = useState<ReportWikiGraph | null>(null);
  const [mode, setMode] = useState<"path" | "all">("path");
  const [selectedRelationId, setSelectedRelationId] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<ReportWikiNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (nextMode: "path" | "all", relationId?: string | null) => {
    if (!caseId) return;
    setError(null);
    try {
      const next = await researchClient.getReportWikiGraph!(caseId, nextMode === "path" && relationId ? { relationId } : undefined);
      setGraph(next); setMode(nextMode); setSelectedRelationId(nextMode === "path" ? relationId ?? null : null);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法加载图谱。请稍后重试。"); }
  }, [caseId]);
  const loadInitial = useCallback(async () => {
    if (!caseId) return;
    setError(null);
    try {
      const base = await researchClient.getReportWikiGraph!(caseId);
      const relationId = base.factors.find((factor) => factor.relationId)?.relationId ?? null;
      setSelectedRelationId(relationId);
      if (relationId) {
        const selected = await researchClient.getReportWikiGraph!(caseId, { relationId });
        setGraph(selected); setMode("path");
      } else { setGraph(base); setMode("all"); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "无法加载图谱。请稍后重试。"); }
  }, [caseId]);
  useEffect(() => { void loadInitial(); }, [loadInitial]);
  if (error) return <main className="prototype-screen report-wiki-screen"><section className="report-state report-state--error" role="alert"><h1>无法加载图谱</h1><p>{error}</p><button className="prototype-button primary" type="button" onClick={() => void loadInitial()}>重新加载图谱</button></section></main>;
  if (!graph) return <main className="prototype-screen report-wiki-screen"><section className="report-skeleton" aria-label="正在加载关系图谱"><span /><span /><span /></section></main>;
  const source = formatReportSourceLocator(selectedNode?.sourceLocator ?? null);
  return <main className="prototype-screen report-wiki-screen">
    <header className="report-page-header"><div><p className="section-kicker">Wiki 图谱 · 范围 v{graph.scopeVersion}</p><h1>研报关系图谱</h1><p>{graph.scope.researchQuestion}</p></div><Link className="prototype-button" to={`/reports/${caseId}`}>返回研究工作台</Link></header>
    <section className="report-wiki-toolbar" aria-label="图谱范围"><div role="group" aria-label="图谱范围"><button type="button" className="report-toggle" aria-pressed={mode === "path"} onClick={() => { const relationId = selectedRelationId ?? graph.factors.find((factor) => factor.relationId)?.relationId ?? null; void load("path", relationId); }}>当前路径</button><button type="button" className="report-toggle" aria-pressed={mode === "all"} onClick={() => void load("all")}>所有关系</button></div><span>{mode === "path" ? "只显示选中因素的可核验路径" : "显示当前不可变研究范围的全部关系"}</span></section>
    <div className="report-wiki-layout"><section className="report-wiki-canvas" aria-labelledby="report-wiki-canvas-title"><header><p className="section-kicker">关系概览</p><h2 id="report-wiki-canvas-title">从主张到资产映射</h2><p>图谱用于定位路径，不替代来源与证据判断。</p></header><GraphCanvas graph={graph} onSelect={setSelectedNode} /></section><aside className="report-wiki-inspector" aria-live="polite"><p className="section-kicker">路径定位</p><h2>{selectedNode ? selectedNode.label : "选择一个节点或路径"}</h2>{selectedNode ? <>{source.href ? <a href={source.href} target="_blank" rel="noopener noreferrer">{source.label}</a> : <p role="status">{source.label}</p>}</> : <p>点击图谱节点或下方结构化路径，查看其安全来源定位或当前状态。</p>}</aside></div>
    <section className="report-wiki-paths" aria-labelledby="report-path-list-title"><header><div><p className="section-kicker">可访问替代视图</p><h2 id="report-path-list-title">结构化路径</h2></div><span>键盘可逐条查看</span></header><PathList graph={graph} onSelect={setSelectedNode} /></section>
  </main>;
}

function readHashToken(): string | null {
  const hash = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(hash);
  return params.get("token") || (hash && !hash.includes("=") ? hash : null);
}

export function ReportEmbedScreen({ embedClient }: { embedClient?: Pick<ReturnType<typeof configuredEmbedResearchClient>, "getReportEmbedWiki"> } = {}) {
  const { caseId = "" } = useParams();
  const [graph, setGraph] = useState<ReportEmbedWikiGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const token = readHashToken();
    if (!token || !caseId) { setError("嵌入访问未获授权或已失效。请向提供方获取新的只读链接。"); return; }
    window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
    const mockClient = new URLSearchParams(window.location.search).get("client") === "mock" ? { getReportEmbedWiki: researchClient.getReportEmbedWiki! } : null;
    const client = embedClient ?? mockClient ?? configuredEmbedResearchClient();
    client.getReportEmbedWiki(caseId, token).then(setGraph).catch((reason) => setError(reason instanceof Error && reason.message === EMBED_API_CONFIGURATION_ERROR ? EMBED_API_CONFIGURATION_ERROR : "嵌入访问未获授权或已失效。请向提供方获取新的只读链接。"));
  }, [caseId, embedClient]);
  return <main className="report-embed-screen"><header><p>只读嵌入</p><h1>只读研究关系图谱</h1></header>{error ? <section role="alert"><strong>无法显示嵌入图谱</strong><p>{error}</p></section> : !graph ? <section className="report-skeleton" aria-label="正在加载只读图谱"><span /><span /></section> : <><section className="report-embed-summary"><strong>{graph.factors.some((factor) => factor.classification === "key") ? "存在已验证关键因素" : "当前没有可确认的关键因素"}</strong><span>{graph.factors.length ? graph.factors[0].explanation : "没有可用因素分类。"}</span></section><section><h2>关系路径</h2><ol className="report-embed-paths">{graph.edges.map((edge) => <li key={edge.id}>{edge.kind} · {edge.status === "verified" ? "已验证" : "待核验"}</li>)}</ol>{!graph.edges.length ? <p>当前没有可公开展示的关系路径。</p> : null}</section></>}</main>;
}
