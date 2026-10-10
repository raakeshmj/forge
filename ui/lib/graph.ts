// Layered layout for directed acyclic graphs (task DAGs, capability graphs). Pure and
// deterministic: the same graph always renders the same way.

export interface GraphNode {
  id: string;
  label: string;
  status?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  kind?: string;
}

export interface Placed extends GraphNode {
  x: number;
  y: number;
  depth: number;
}

export const COLUMN = 240;
export const ROW = 90;

/** Depth = longest path from a root; nodes in a column keep their input order. */
export function layout(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): Placed[] {
  const known = new Set(nodes.map((node) => node.id));
  const incoming = new Map<string, string[]>();
  for (const edge of edges) {
    if (!known.has(edge.from) || !known.has(edge.to)) continue;
    incoming.set(edge.to, [...(incoming.get(edge.to) ?? []), edge.from]);
  }
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const depthOf = (id: string): number => {
    const cached = depth.get(id);
    if (cached !== undefined) return cached;
    if (visiting.has(id)) return 0; // a cycle (only possible in bad data): break it here
    visiting.add(id);
    const parents = incoming.get(id) ?? [];
    const value = parents.length ? 1 + Math.max(...parents.map(depthOf)) : 0;
    visiting.delete(id);
    depth.set(id, value);
    return value;
  };
  const rows = new Map<number, number>();
  return nodes.map((node) => {
    const d = depthOf(node.id);
    const row = rows.get(d) ?? 0;
    rows.set(d, row + 1);
    return { ...node, depth: d, x: d * COLUMN, y: row * ROW };
  });
}
