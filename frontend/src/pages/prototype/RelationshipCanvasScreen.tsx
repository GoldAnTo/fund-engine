import { useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { researchClient } from "../../data/researchClient";
import type {
  GraphLayer,
  GraphNodeView,
  RelationshipGraphView,
} from "../../domain/prototypeTypes";

interface PageState {
  kind: "loading" | "error" | "ready";
  message?: string;
}

const LAYER_LABEL: Record<GraphLayer["key"], string> = {
  evidence: "证据",
  thesis: "命题",
  causal: "因果链",
  company: "公司",
  fund: "基金",
};

export function RelationshipCanvasScreen() {
  const params = useParams<{ caseId?: string }>();
  const caseId = params.caseId ?? "RC-AIC-2025-01";
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [view, setView] = useState<RelationshipGraphView | null>(null);
  const [selectedId, setSelectedId] = useState<string>("");
  const [zoom, setZoom] = useState<number>(100);

  useEffect(() => {
    let cancelled = false;
    researchClient
      .getRelationshipGraphView(caseId)
      .then((v) => {
        if (!cancelled) {
          setView(v);
          setSelectedId(v.selectedNodeId);
          setState({ kind: "ready" });
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setState({ kind: "error", message: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [caseId]);

  const selected = useMemo(() => {
    if (!view) return null;
    return view.nodes.find((n) => n.id === selectedId) ?? null;
  }, [view, selectedId]);

  if (state.kind === "loading") {
    return (
      <div className="prototype-screen" data-testid="relationship-loading">
        <p>正在加载证据图谱…</p>
      </div>
    );
  }
  if (state.kind === "error" || !view) {
    return (
      <div className="prototype-screen" data-testid="relationship-error">
        <div className="form-error">
          证据图谱加载失败：{state.message ?? "未知错误"}
        </div>
      </div>
    );
  }

  return (
    <div className="prototype-screen relationship-screen" data-testid="relationship-screen">
      <header className="relationship-header">
        <div>
          <div className="eyebrow">行业研究 · 证据图谱</div>
          <h1>{view.case.title}</h1>
          <p className="lede">{view.case.question}</p>
        </div>
        <div className="relationship-header__meta">
          <MetaCell label="证据截止" value={view.case.cutoff} />
          <MetaCell label="冻结快照" value={view.case.snapshotId} mono />
          <MetaCell label="正式判断" value="证据不足 · 继续验证" warn />
        </div>
      </header>

      <div className="relationship-layout">
        <section className="prototype-graph-canvas" aria-label="证据关系 canvas">
          <div className="prototype-section-header">
            <div>
              <p className="section-kicker">FIVE-LAYER RELATIONSHIP CANVAS</p>
              <h2>证据 → 命题 → 因果链 → 公司 → 基金</h2>
            </div>
            <span className="state-badge ai">
              {view.nodes.length} 个节点 · 已选 {selectedId}
            </span>
          </div>

          <svg
            className="prototype-graph-svg"
            viewBox="0 0 800 220"
            role="img"
            aria-label="五层关系连线示意"
          >
            {/* Cross-layer curves */}
            {view.layers.flatMap((layer, li) =>
              layer.nodes.flatMap((node, ni) =>
                li < view.layers.length - 1
                  ? view.layers[li + 1].nodes.slice(0, 2).map((target, ti) => {
                      const fromX = (li + 0.5) * (760 / view.layers.length) + 20;
                      const fromY = (ni + 1) * (180 / (layer.nodes.length + 1)) + 20;
                      const toX = (li + 1.5) * (760 / view.layers.length) + 20;
                      const toY = (ti + 1) * (180 / (view.layers[li + 1].nodes.length + 1)) + 20;
                      const variant =
                        node.kind === "contradictory"
                          ? "contradict"
                          : node.kind === "ai-proposed"
                            ? "proposed"
                            : layer.key === "thesis" && view.layers[li + 1].key === "causal"
                              ? "proposed"
                              : layer.key === "company" || layer.key === "fund"
                                ? "projection"
                                : "support";
                      return (
                        <path
                          key={`edge-${layer.key}-${node.id}-${target.id}-${ti}`}
                          className={`edge ${variant}`}
                          d={`M ${fromX} ${fromY} C ${(fromX + toX) / 2} ${fromY}, ${(fromX + toX) / 2} ${toY}, ${toX} ${toY}`}
                        />
                      );
                    })
                  : [],
              ),
            )}
            {/* Nodes */}
            {view.layers.flatMap((layer, li) =>
              layer.nodes.map((node, ni) => {
                const x = (li + 0.5) * (760 / view.layers.length) + 20;
                const y = (ni + 1) * (180 / (layer.nodes.length + 1)) + 20;
                const fill =
                  node.kind === "source-fact"
                    ? "var(--reviewed)"
                    : node.kind === "contradictory"
                      ? "var(--contradict)"
                      : node.kind === "ai-proposed"
                        ? "var(--ai-draft)"
                        : node.kind === "company-node"
                          ? "var(--provider-accent)"
                          : "var(--warning)";
                return (
                  <g key={`${layer.key}-${node.id}`} className="series-point usable">
                    <circle
                      cx={x}
                      cy={y}
                      r={selectedId === node.id ? 9 : 6}
                      fill={fill}
                      stroke="var(--paper)"
                      strokeWidth={selectedId === node.id ? 3 : 2}
                      onClick={() => setSelectedId(node.id)}
                      style={{ cursor: "pointer" }}
                    />
                    <text x={x + 10} y={y - 6} fontWeight="600">
                      {LAYER_LABEL[layer.key]}
                    </text>
                    <text x={x + 10} y={y + 8}>
                      {node.title.slice(0, 16)}
                    </text>
                  </g>
                );
              }),
            )}
          </svg>

          <div className="canvas-layers">
            {view.layers.map((layer) => (
              <div key={layer.key} className="canvas-layer">
                <h3>{layer.label}</h3>
                {layer.nodes.map((node) => (
                  <button
                    type="button"
                    key={node.id}
                    className={`prototype-graph-node ${node.kind}`}
                    aria-pressed={selectedId === node.id}
                    onClick={() => setSelectedId(node.id)}
                  >
                    <span className="graph-node-layer">{node.layer}</span>
                    <strong>{node.title}</strong>
                    <small>{node.meta}</small>
                    <span className="graph-node-kind">{node.kindLabel}</span>
                  </button>
                ))}
              </div>
            ))}
          </div>

          <div className="prototype-graph-legend" aria-label="图例">
            <span style={{ color: "var(--reviewed)" }}>证据 · 已审核</span>
            <span style={{ color: "var(--support)" }}>命题 / 因果 · 已审核关系</span>
            <span style={{ color: "var(--contradict)" }}>反驳关系 · 红线</span>
            <span style={{ color: "var(--ai-draft)" }}>绿色链路头 · AI 提议</span>
            <span style={{ color: "var(--provider-accent)" }}>实线链接 · 已人工复核</span>
            <span className="prototype-zoom-controls" aria-label="缩放">
              <button type="button" onClick={() => setZoom((z) => Math.max(60, z - 10))}>−</button>
              <span>{zoom}%</span>
              <button type="button" onClick={() => setZoom((z) => Math.min(160, z + 10))}>+</button>
            </span>
          </div>
        </section>

        <aside className="prototype-inspector relationship-inspector" aria-label="固定证据容器">
          {selected ? (
            <NodeInspector node={selected} />
          ) : (
            <p style={{ color: "var(--ink-muted)", fontSize: 12 }}>
              请选择任意节点以查看详情。
            </p>
          )}

          <div className="evidence-stack">
            <section>
              <h3>事实层</h3>
              <p className="muted">
                来源陈述以事实层展示；
                {view.layers[0]?.nodes.length ?? 0} 条来源事实。
              </p>
            </section>
            <section>
              <h3>反驳 · 共识</h3>
              <p className="muted">
                红线 = 反驳，灰链 = 共识。
              </p>
            </section>
            <section>
              <h3>AI 提议 · 证据来源</h3>
              <p className="muted">
                仅显示节点层；详细出处见右上证据容器。
              </p>
            </section>
          </div>
        </aside>
      </div>
    </div>
  );
}

function MetaCell({
  label,
  value,
  mono,
  warn,
}: {
  label: string;
  value: string;
  mono?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={`meta-cell${warn ? " meta-cell--warn" : ""}`}>
      <span>{label}</span>
      <strong style={mono ? { fontFamily: "ui-monospace, monospace" } : undefined}>
        {value}
      </strong>
    </div>
  );
}

function NodeInspector({ node }: { node: GraphNodeView }) {
  return (
    <div data-testid="node-inspector">
      <div className="inspector-kind">{node.layer}</div>
      <h2>{node.title}</h2>
      <p>{node.relation}</p>
      <dl>
        <div>
          <dt>种类</dt>
          <dd>{node.kindLabel}</dd>
        </div>
        <div>
          <dt>审核</dt>
          <dd>{node.review}</dd>
        </div>
        <div>
          <dt>来源</dt>
          <dd>{node.sourceName}</dd>
        </div>
        <div>
          <dt>出处</dt>
          <dd>{node.sourceSpan}</dd>
        </div>
        <div>
          <dt>所属附件</dt>
          <dd>{node.attachment}</dd>
        </div>
        <div>
          <dt>发表日期</dt>
          <dd>{node.publicationDate}</dd>
        </div>
        <div>
          <dt>截止日</dt>
          <dd>{node.asOf}</dd>
        </div>
        <div>
          <dt>范围</dt>
          <dd>{node.scope}</dd>
        </div>
        <div>
          <dt>引用</dt>
          <dd>{node.citations.join(" · ")}</dd>
        </div>
        {node.note && (
          <div>
            <dt>备注</dt>
            <dd>{node.note}</dd>
          </div>
        )}
      </dl>
      {node.sourceHref && (
        <a className="prototype-next-action" href={node.sourceHref}>
          跳转原文 →
        </a>
      )}
    </div>
  );
}