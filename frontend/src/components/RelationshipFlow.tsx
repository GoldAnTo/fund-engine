import { useMemo } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  MarkerType,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type {
  GraphEdge,
  GraphNode,
  RelationshipGraph,
} from "../domain/types";
import { layoutGraph } from "./RelationshipGraph";
import {
  GraphFlowNode,
  type GraphFlowNodeData,
} from "./GraphFlowNode";
import { GraphFlowEdge } from "./GraphFlowEdge";

interface Props {
  data: RelationshipGraph;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  onSelectNode: (id: string | null) => void;
  onSelectEdge: (id: string | null) => void;
}

const NODE_TYPES = { card: GraphFlowNode };
const EDGE_TYPES = { relationship: GraphFlowEdge };

const KIND_COLOR: Record<string, string> = {
  evidence: "oklch(0.42 0.08 145)",
  causal: "oklch(0.6 0.12 70)",
  theme_role: "oklch(0.5 0.08 245)",
  holding: "oklch(0.5 0.06 295)",
};

const PROPOSITION_GROUP_KIND: Record<string, string> = {
  proposition: "causal",
};

// relationship canvas — wraps React Flow with our domain graph.
// React Flow handles pan/zoom, mini-map, layout measurement; we provide:
//   * dagre-laid-out L→R positions for the 5-column evidence → fund pipeline
//   * custom node (card) + custom edge (bezier + arrow marker)
//   * selection propagation back to the page (URL-driven)

export function RelationshipFlow({
  data,
  selectedNodeId,
  selectedEdgeId,
  onSelectNode,
  onSelectEdge,
}: Props) {
  const { rfNodes, rfEdges } = useMemo(() => {
    // Cap visible nodes to keep React Flow performant for the prototype.
    const cap = 60;
    const nodeSet = new Map<string, GraphNode>();
    data.nodes.slice(0, cap).forEach((n) => nodeSet.set(n.id, n));
    // Keep only edges whose endpoints are in the visible set.
    const visibleEdges: GraphEdge[] = data.edges
      .filter((e) => nodeSet.has(e.source) && nodeSet.has(e.target))
      .slice(0, 200);

    const positions = layoutGraph(
      Array.from(nodeSet.values()),
      visibleEdges
    );

    const nodes: Node<GraphFlowNodeData>[] = Array.from(nodeSet.values()).map(
      (n) => {
        // If the node group is "proposition" we use the causal palette so the
        // edges between propositions and causal steps look visually coherent.
        const overrideGroup = PROPOSITION_GROUP_KIND[n.group ?? ""];
        const nodeForCard = (overrideGroup
          ? { ...n, group: overrideGroup as GraphNode["group"] }
          : n) as GraphNode;
        return {
          id: n.id,
          type: "card",
          data: {
            node: nodeForCard,
            selected: n.id === selectedNodeId,
            onSelect: onSelectNode,
          },
          position: positions.get(n.id) ?? { x: 0, y: 0 },
        };
      }
    );

    const edges: Edge[] = visibleEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      type: "relationship",
      data: {
        kind: e.kind,
        role: e.role,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: KIND_COLOR[e.kind] ?? "oklch(0.45 0.02 145)",
      },
      selected: e.id === selectedEdgeId,
    }));

    return { rfNodes: nodes, rfEdges: edges };
  }, [data, selectedNodeId, selectedEdgeId, onSelectNode]);

  return (
    <div className="relationship-flow" data-testid="relationship-flow">
      <div className="relationship-flow__add-row" data-testid="relationship-add-row">
        <button
          type="button"
          className="btn btn--ghost btn--xs"
          data-testid="canvas-add-evidence"
          aria-label="添加证据"
        >
          + 添加证据
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--xs"
          data-testid="canvas-add-proposition"
          aria-label="添加命题"
        >
          + 添加命题
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--xs"
          data-testid="canvas-add-causal"
          aria-label="添加因果链"
        >
          + 添加因果链
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--xs"
          data-testid="canvas-add-company"
          aria-label="添加公司"
        >
          + 添加公司
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--xs"
          data-testid="canvas-add-fund"
          aria-label="添加基金"
        >
          + 添加基金
        </button>
      </div>
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.4}
        maxZoom={1.6}
        proOptions={{ hideAttribution: true }}
        nodesConnectable={false}
        elementsSelectable
        onNodeClick={(_, node) => onSelectNode(node.id)}
        onEdgeClick={(_, edge) => onSelectEdge(edge.id)}
        onPaneClick={() => {
          onSelectNode(null);
          onSelectEdge(null);
        }}
      >
        <Background gap={16} size={1} color="oklch(0.88 0.008 85)" />
        <Controls position="bottom-right" showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          position="bottom-left"
          nodeColor={(n) =>
            KIND_COLOR[
              ((n.data as GraphFlowNodeData | undefined)?.node?.group as string) ?? ""
            ] ?? "oklch(0.45 0.02 145)"
          }
          maskColor="oklch(0.97 0.008 85 / 0.7)"
        />
      </ReactFlow>
    </div>
  );
}