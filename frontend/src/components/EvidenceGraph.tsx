import { useEffect, useRef } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import type { EdgeKind, EvidenceRole, WorkbenchResponse } from "../types";

export const edgeStyleByKind: Record<
  EdgeKind,
  { lineColor: string; lineStyle: "solid" | "dashed" | "dotted" }
> = {
  evidence: { lineColor: "#2e7a48", lineStyle: "solid" },
  causal: { lineColor: "#6f7cff", lineStyle: "dashed" },
  theme_role: { lineColor: "#9a6a12", lineStyle: "solid" },
  holding: { lineColor: "#9a6a12", lineStyle: "dotted" },
};

// evidence 边按 role 细化着色：支持绿 / 反对红加粗 / 背景灰
export const evidenceColorByRole: Record<EvidenceRole, string> = {
  supports: "#2e7a48",
  contradicts: "#c0392b",
  contextualizes: "#888888",
};

function stepLabel(node: { label: string; sequence?: number; description?: string }): string {
  const seq = node.sequence != null ? `${node.sequence}. ` : "";
  const desc = node.description ?? node.label;
  return `${seq}${desc}`.trim();
}

interface Props {
  data: WorkbenchResponse;
  onSelectEvidence: (linkId: string) => void;
  onSelectNode: (nodeId: string) => void;
}

export function EvidenceGraph({ data, onSelectEvidence, onSelectNode }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const elements: ElementDefinition[] = [
      ...data.graph.nodes.map((n) => ({
        data: {
          id: n.id,
          label: n.kind === "step" ? stepLabel(n) : n.label,
          kind: n.kind,
          sequence: n.sequence,
          description: n.description,
        },
      })),
      ...data.graph.edges.map((e) => ({
        data: {
          id: e.id,
          source: e.source,
          target: e.target,
          kind: e.kind,
          role: e.role,
        },
      })),
    ];
    let cy: Core | null = null;
    try {
      cy = cytoscape({
        container: containerRef.current,
        elements,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "text-wrap": "wrap",
              "background-color": "#4a5568",
              color: "#fff",
              "font-size": "10px",
              width: 60,
              height: 60,
            },
          },
          {
            selector: 'node[kind="step"]',
            style: {
              "background-color": "#d97706",
              shape: "rectangle",
              "text-valign": "center",
              "text-halign": "center",
              width: 90,
              height: 44,
            },
          },
          {
            selector: 'edge[kind="evidence"][role="supports"]',
            style: {
              "line-color": evidenceColorByRole.supports,
              "line-style": "solid",
              "target-arrow-color": evidenceColorByRole.supports,
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="evidence"][role="contradicts"]',
            style: {
              "line-color": evidenceColorByRole.contradicts,
              "line-style": "solid",
              width: 3,
              "target-arrow-color": evidenceColorByRole.contradicts,
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="evidence"][role="contextualizes"]',
            style: {
              "line-color": evidenceColorByRole.contextualizes,
              "line-style": "solid",
              "target-arrow-color": evidenceColorByRole.contextualizes,
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          // 兜底：无 role 的 evidence 边沿用绿色
          {
            selector: 'edge[kind="evidence"][!role]',
            style: {
              "line-color": evidenceColorByRole.supports,
              "line-style": "solid",
              "target-arrow-color": evidenceColorByRole.supports,
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="causal"]',
            style: {
              "line-color": "#6f7cff",
              "line-style": "dashed",
              "target-arrow-color": "#6f7cff",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="theme_role"]',
            style: {
              "line-color": "#9a6a12",
              "line-style": "solid",
              "target-arrow-color": "#9a6a12",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: 'edge[kind="holding"]',
            style: {
              "line-color": "#9a6a12",
              "line-style": "dotted",
              "target-arrow-color": "#9a6a12",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          // 因果链高亮 / 淡化
          {
            selector: ".causal-highlight",
            style: {
              "border-width": 3,
              "border-color": "#d97706",
              "line-color": "#6f7cff",
              "target-arrow-color": "#6f7cff",
              width: 4,
            },
          },
          {
            selector: ".faded",
            style: {
              opacity: 0.15,
            },
          },
        ],
        layout: { name: "cose" },
      });
      cy.on("tap", "edge", (evt) => onSelectEvidence(evt.target.id()));
      cy.on("tap", "node", (evt) => {
        const node = evt.target;
        onSelectNode(node.id());
        if (node.data("kind") === "step") {
          highlightCausalPath(cy!, node.id());
        } else {
          clearHighlight(cy!);
        }
      });
      cy.on("tap", (evt) => {
        if (evt.target === cy) clearHighlight(cy!);
      });
    } catch {
      // jsdom 或无渲染环境下降级，证据/步骤按钮列表仍可用
    }
    return () => {
      cy?.destroy();
    };
  }, [data, onSelectEvidence, onSelectNode]);

  const stepNodes = data.graph.nodes.filter((n) => n.kind === "step");

  return (
    <div className="evidence-graph-wrap">
      <div
        ref={containerRef}
        className="evidence-graph"
        style={{ width: "100%", height: "480px", border: "1px solid #e2e8f0" }}
      />
      {stepNodes.length > 0 && (
        <div className="step-buttons" role="group" aria-label="因果链步骤">
          <p className="step-buttons-title">因果链步骤</p>
          {stepNodes.map((n) => (
            <button
              key={n.id}
              type="button"
              onClick={() => onSelectNode(n.id)}
              data-node-id={n.id}
            >
              因果步骤 {n.sequence ?? ""}：{n.description ?? n.label}
            </button>
          ))}
        </div>
      )}
      <div className="evidence-buttons" role="group" aria-label="证据列表">
        {data.evidence_drawer_records.map((r) => (
          <button
            key={r.link_id}
            type="button"
            onClick={() => onSelectEvidence(r.link_id)}
            data-role={r.role}
          >
            查看证据：{r.statement_text ?? r.reason}
          </button>
        ))}
      </div>
    </div>
  );
}

// 沿 causal 边双向 BFS，高亮该 step 的因果路径（上下游 step 节点 + causal 边）
function highlightCausalPath(cy: Core, startId: string): void {
  const stepNodes = new Set<string>();
  const causalEdges = new Set<string>();
  const seen = new Set<string>([startId]);
  const queue: string[] = [startId];

  while (queue.length > 0) {
    const id = queue.shift()!;
    const node = cy.getElementById(id);
    if (node.data("kind") === "step") stepNodes.add(id);
    node.connectedEdges().forEach((edge) => {
      if (edge.data("kind") !== "causal") return;
      causalEdges.add(edge.id());
      const other = edge.source().id() === id ? edge.target().id() : edge.source().id();
      if (!seen.has(other)) {
        seen.add(other);
        queue.push(other);
      }
    });
  }

  cy.elements().removeClass("faded causal-highlight");
  cy.elements().forEach((el) => {
    const elId = el.id();
    const inPath = el.isNode() ? stepNodes.has(elId) : causalEdges.has(elId);
    if (inPath) el.addClass("causal-highlight");
    else el.addClass("faded");
  });
}

function clearHighlight(cy: Core): void {
  cy.elements().removeClass("faded causal-highlight");
}
