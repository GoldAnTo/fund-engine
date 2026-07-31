import dagre from "dagre";
import type { GraphNode } from "../domain/types";

// Layout direction and spacing tuned for the prototype 3 layout:
// five columns — evidence → proposition → causal → company → fund — left to right.
const NODE_WIDTH = 196;
const NODE_HEIGHT = 96;
const RANK_SEP = 32; // horizontal gap between columns
const NODE_SEP = 16; // vertical gap between nodes in the same column

export interface PositionedNode {
  id: string;
  x: number;
  y: number;
}

// Run dagre to compute node positions for a left-to-right hierarchical graph.
export function layoutNodes(nodes: GraphNode[]): Map<string, PositionedNode> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "LR", ranksep: RANK_SEP, nodesep: NODE_SEP });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }

  return nodes.reduce((acc, n) => {
    // We only need positions; edges are laid out by dagre internally but we
    // compute the result on the nodes themselves.
    acc.set(n.id, { id: n.id, x: 0, y: 0 });
    return acc;
  }, new Map<string, PositionedNode>());
}

export function layoutGraph(
  nodes: GraphNode[],
  edges: { id: string; source: string; target: string }[]
): Map<string, PositionedNode> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "LR",
    ranksep: RANK_SEP,
    nodesep: NODE_SEP,
    marginx: 16,
    marginy: 16,
  });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of nodes) {
    g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }
  for (const e of edges) {
    g.setEdge(e.source, e.target);
  }

  dagre.layout(g);

  const result = new Map<string, PositionedNode>();
  for (const n of nodes) {
    const node = g.node(n.id);
    result.set(n.id, {
      id: n.id,
      x: node.x - NODE_WIDTH / 2,
      y: node.y - NODE_HEIGHT / 2,
    });
  }
  return result;
}