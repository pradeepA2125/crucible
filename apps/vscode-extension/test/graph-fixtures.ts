import type { RawGraphEdge, RawGraphNode, RawSnapshot } from "../src/graph/space-model.js";

export const ROOT = "/ws";

export function fileNode(rel: string): RawGraphNode {
  return { id: `file:${ROOT}/${rel}`, path: `${ROOT}/${rel}`, name: rel.split("/").pop()!, kind: "File" };
}

export function symNode(kind: string, rel: string, name: string, line = 1): RawGraphNode {
  return { id: `${kind.toLowerCase()}:file:${ROOT}/${rel}:${name}`, path: `${ROOT}/${rel}`, name, kind, line };
}

/** external:module node — NOTE: carries the IMPORTER's path (matches real snapshots). */
export function extModule(spec: string, importerRel: string): RawGraphNode {
  return { id: `external:module:${spec}`, path: `${ROOT}/${importerRel}`, name: spec, kind: "Module" };
}

export function edge(from: string, to: string, kind: string): RawGraphEdge {
  return { from, to, kind };
}

export function snap(nodes: RawGraphNode[], edges: RawGraphEdge[]): RawSnapshot {
  return { workspace_root: ROOT, generated_at_ms: 1000, graph: { nodes, edges } };
}
