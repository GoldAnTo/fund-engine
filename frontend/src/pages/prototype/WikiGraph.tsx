import { useMemo, useRef, useState } from "react";
import type {
  GraphEdgeView,
  GraphNodeView,
  RelationshipGraphView,
} from "../../domain/prototypeTypes";
import { exportGraphPng, exportResearchBrief } from "./exportGraph";

/**
 * Wiki 风格证据图谱：五层从左到右排布（证据 → 命题 → 因果链 → 公司 → 基金），
 * 每个节点是一张带关键数据的卡片，连线来自后端真实边（支持 / 反驳 / 因果 /
 * 持仓等），点击节点可聚焦其完整关系链。
 */

// ── 布局常量（px，SVG viewBox 坐标系） ──────────────────────────────────
const CARD_W = 184;
const CARD_H = 76;
const COL_GAP = 44;
const ROW_GAP = 24;
const HEADER_H = 40;
const PAD = 20;
const TITLE_CHARS_PER_LINE = 12;

type Tone =
  | "fact"
  | "thesis"
  | "contradict"
  | "ai"
  | "causal"
  | "company"
  | "fund";

/** fixture 与后端两套 kind 词表统一到同一套配色。 */
function toneOf(node: GraphNodeView): Tone {
  switch (node.kind) {
    case "contradictory":
      return "contradict";
    case "ai-proposed":
      return "ai";
    case "thesis":
    case "thesis-node":
      return "thesis";
    case "step":
    case "causal-node":
      return "causal";
    case "company":
    case "stock":
    case "company-node":
      return "company";
    case "fund":
    case "valuation":
    case "fund-node":
      return "fund";
    default:
      return "fact";
  }
}

type EdgeVariant = "support" | "contradict" | "proposed" | "projection";

function variantOf(edge: GraphEdgeView): EdgeVariant {
  const role = edge.role ?? "";
  if (role.includes("contradict")) return "contradict";
  if (
    edge.kind === "holding" ||
    edge.kind === "valuation" ||
    edge.kind === "company_stock"
  ) {
    return "projection";
  }
  if (edge.reviewState !== "reviewed") return "proposed";
  return "support";
}

interface PlacedNode {
  node: GraphNodeView;
  layerKey: string;
  layerIndex: number;
  x: number;
  y: number;
}

function wrapTitle(title: string): [string, string] {
  const first = title.slice(0, TITLE_CHARS_PER_LINE);
  const rest = title.slice(TITLE_CHARS_PER_LINE);
  if (!rest) return [first, ""];
  const second =
    rest.length > TITLE_CHARS_PER_LINE
      ? `${rest.slice(0, TITLE_CHARS_PER_LINE - 1)}…`
      : rest;
  return [first, second];
}

export function WikiGraph({
  view,
  selectedId,
  onSelect,
}: {
  view: RelationshipGraphView;
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  const [zoom, setZoom] = useState<number>(100);
  // 聚焦模式只在用户主动点击卡片后开启，初始默认全图完整可见
  const [focusId, setFocusId] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [exporting, setExporting] = useState<"png" | "pdf" | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);

  const runExport = (kind: "png" | "pdf") => {
    const svg = svgRef.current;
    if (!svg || exporting) return;
    setExporting(kind);
    setExportError(null);
    const safeTitle = (view.case.title || "证据图谱").replace(
      /[\\/:*?"<>|\s]+/g,
      "_",
    );
    const filename = `${safeTitle}_证据图谱`;
    setExportError(null);
    const task =
      kind === "png"
        ? () => exportGraphPng(svg, filename)
        : () =>
            exportResearchBrief(
              svg,
              view,
              selectedId,
              `${safeTitle}_研究简报`,
            );
    task()
      .catch((err: Error) =>
        setExportError(err.message || "导出失败，请重试"),
      )
      .finally(() => setExporting(null));
  };

  const { placed, width, height } = useMemo(() => {
    const layers = view.layers.filter((l) => l.nodes.length > 0);
    const rows = Math.max(1, ...layers.map((l) => l.nodes.length));
    const w =
      PAD * 2 +
      layers.length * CARD_W +
      Math.max(0, layers.length - 1) * COL_GAP;
    const h = HEADER_H + PAD * 2 + rows * (CARD_H + ROW_GAP) - ROW_GAP;
    const map = new Map<string, PlacedNode>();
    layers.forEach((layer, li) => {
      // 短列垂直居中，形成 Wiki/树状图的平衡感
      const offset =
        ((rows - layer.nodes.length) * (CARD_H + ROW_GAP)) / 2;
      layer.nodes.forEach((node, ni) => {
        map.set(node.id, {
          node,
          layerKey: layer.key,
          layerIndex: li,
          x: PAD + li * (CARD_W + COL_GAP),
          y: HEADER_H + PAD + offset + ni * (CARD_H + ROW_GAP),
        });
      });
    });
    return { placed: map, width: w, height: h };
  }, [view.layers]);

  // 点击节点后聚焦：只高亮与该节点直接相连的关系链，其余淡出
  const focusSet = useMemo(() => {
    if (!focusId) return null;
    const set = new Set<string>([focusId]);
    for (const e of view.edges) {
      if (e.source === focusId) set.add(e.target);
      if (e.target === focusId) set.add(e.source);
    }
    return set;
  }, [view.edges, focusId]);

  const renderedEdges = useMemo(() => {
    return view.edges.flatMap((edge) => {
      const a = placed.get(edge.source);
      const b = placed.get(edge.target);
      if (!a || !b) return [];
      const variant = variantOf(edge);
      const active =
        focusId !== null &&
        (edge.source === focusId || edge.target === focusId);
      const dim = focusSet !== null && !active;

      if (a.layerIndex !== b.layerIndex) {
        const left = a.layerIndex < b.layerIndex ? a : b;
        const right = a.layerIndex < b.layerIndex ? b : a;
        const x1 = left.x + CARD_W;
        const y1 = left.y + CARD_H / 2;
        const x2 = right.x;
        const y2 = right.y + CARD_H / 2;
        const mx = (x1 + x2) / 2;
        return [
          {
            edge,
            variant,
            dim,
            active,
            d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`,
            labelX: mx,
            labelY: (y1 + y2) / 2 - 5,
          },
        ];
      }
      // 同层边（如因果链步骤顺序）：从卡片下缘绕右侧弧线连到下一张卡片上缘
      const upper = a.y < b.y ? a : b;
      const lower = a.y < b.y ? b : a;
      const cx = upper.x + CARD_W / 2;
      const y1 = upper.y + CARD_H;
      const y2 = lower.y;
      const bulge = cx + COL_GAP * 0.75;
      return [
        {
          edge,
          variant,
          dim,
          active,
          d: `M ${cx} ${y1} C ${bulge} ${y1 + 18}, ${bulge} ${y2 - 18}, ${cx} ${y2}`,
          labelX: bulge + 4,
          labelY: (y1 + y2) / 2,
        },
      ];
    });
  }, [view.edges, placed, focusId, focusSet]);

  const handleCardClick = (id: string) => {
    onSelect(id);
    // 再点一次已聚焦的卡片 = 取消聚焦，恢复全图
    setFocusId((prev) => (prev === id ? null : id));
  };

  const visibleLayers = view.layers.filter((l) => l.nodes.length > 0);

  return (
    <div className="wiki-graph" data-testid="wiki-graph">
      <div
        className="wiki-graph__scroll"
        style={{ overflow: "auto", maxHeight: 560 }}
      >
        <svg
          ref={svgRef}
          className="wiki-graph__svg"
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: `${(width * zoom) / 100}px`, display: "block" }}
          role="img"
          aria-label="Wiki 风格证据关系图谱"
          onClick={() => setFocusId(null)}
        >
          <defs>
            {(["support", "contradict", "proposed", "projection"] as const).map(
              (v) => (
                <marker
                  key={v}
                  id={`wiki-arrow-${v}`}
                  viewBox="0 0 8 8"
                  refX="7"
                  refY="4"
                  markerWidth="7"
                  markerHeight="7"
                  orient="auto-start-reverse"
                >
                  <path d="M 0 0.5 L 7.5 4 L 0 7.5 z" className={`wiki-arrow ${v}`} />
                </marker>
              ),
            )}
          </defs>

          {/* 列头：层名 + 节点数 */}
          {visibleLayers.map((layer, li) => {
            const x = PAD + li * (CARD_W + COL_GAP);
            return (
              <g key={`head-${layer.key}`} className="wiki-graph__colhead">
                <text x={x} y={22} className="wiki-graph__colhead-label">
                  {layer.label}
                </text>
                <text x={x} y={36} className="wiki-graph__colhead-count">
                  {layer.nodes.length} 个节点
                </text>
                <line
                  x1={x}
                  y1={HEADER_H - 2}
                  x2={x + CARD_W}
                  y2={HEADER_H - 2}
                  className="wiki-graph__colhead-rule"
                />
              </g>
            );
          })}

          {/* 真实关系连线 */}
          {renderedEdges.map(
            ({ edge, variant, dim, active, d, labelX, labelY }) => (
              <g
                key={edge.id}
                className={`wiki-edge ${variant}${dim ? " is-dim" : ""}${active ? " is-active" : ""}`}
              >
                <path d={d} markerEnd={`url(#wiki-arrow-${variant})`} />
                <text x={labelX} y={labelY} className="wiki-edge__label">
                  {edge.label}
                </text>
              </g>
            ),
          )}

          {/* 节点卡片 */}
          {Array.from(placed.values()).map(({ node, x, y }) => {
            const tone = toneOf(node);
            const dim = focusSet !== null && !focusSet.has(node.id);
            const selected = selectedId === node.id;
            const [line1, line2] = wrapTitle(node.title);
            return (
              <g
                key={node.id}
                className={`wiki-node tone-${tone}${dim ? " is-dim" : ""}${selected ? " is-selected" : ""}`}
                onClick={(e) => {
                  e.stopPropagation();
                  handleCardClick(node.id);
                }}
                style={{ cursor: "pointer" }}
              >
                <rect
                  x={x}
                  y={y}
                  width={CARD_W}
                  height={CARD_H}
                  rx={8}
                  className="wiki-node__card"
                />
                <rect
                  x={x}
                  y={y}
                  width={4}
                  height={CARD_H}
                  rx={2}
                  className="wiki-node__bar"
                />
                <text x={x + 12} y={y + 15} className="wiki-node__kind">
                  {node.kindLabel}
                </text>
                <text x={x + 12} y={y + 32} className="wiki-node__title">
                  {line1}
                </text>
                {line2 ? (
                  <text x={x + 12} y={y + 46} className="wiki-node__title">
                    {line2}
                  </text>
                ) : null}
                <text x={x + 12} y={y + 66} className="wiki-node__meta">
                  {node.meta.slice(0, 20)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <div className="prototype-graph-legend" aria-label="图例">
        <span className="legend-item legend-support">支持 · 已复核</span>
        <span className="legend-item legend-contradict">反驳</span>
        <span className="legend-item legend-proposed">AI 提议 · 待复核</span>
        <span className="legend-item legend-projection">持仓 / 主体映射</span>
        <span className="legend-hint">
          {focusId
            ? "聚焦模式：仅显示相连关系链 · 再点卡片或空白处取消"
            : "点击节点可聚焦其关系链"}
        </span>
        <span className="wiki-graph__export" aria-label="导出">
          <button
            type="button"
            disabled={exporting !== null}
            onClick={() => runExport("png")}
          >
            {exporting === "png" ? "导出中…" : "导出 PNG"}
          </button>
          <button
            type="button"
            disabled={exporting !== null}
            onClick={() => runExport("pdf")}
          >
            {exporting === "pdf" ? "生成简报中…" : "导出研究简报"}
          </button>
        </span>
        <span className="prototype-zoom-controls" aria-label="缩放">
          <button
            type="button"
            onClick={() => setZoom((z) => Math.max(50, z - 10))}
          >
            −
          </button>
          <span>{zoom}%</span>
          <button
            type="button"
            onClick={() => setZoom((z) => Math.min(180, z + 10))}
          >
            +
          </button>
        </span>
      </div>
      {exportError ? (
        <p className="wiki-graph__export-error" role="alert">
          {exportError}
        </p>
      ) : null}
    </div>
  );
}
