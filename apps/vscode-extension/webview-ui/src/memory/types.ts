// Local camelCase mirrors of editor-client's RecallTrace/MemoryView (webview-ui does not
// import editor-client — same convention as src/types.ts ChatMsg). The host posts these in.
export interface RecallSignals {
  semantic: number;
  lexical: number;
  structural: number;
  importance: number;
  recency: number;
}
export interface RecallTraceEntry {
  memoryId: string;
  kind: string;
  content: string;
  importance: number;
  signals: RecallSignals;
  fusedScore: number;
  rerankScore: number | null;
  finalRank: number;
  injected: boolean;
}
export interface RecallTrace {
  query: string;
  scopeKind: string;
  scopeId: string;
  k: number;
  floor: number;
  reranked: boolean;
  entries: RecallTraceEntry[];
}
export interface MemoryView {
  id: string;
  scopeKind: string;
  scopeId: string;
  kind: string;
  content: string;
  entities: string[];
  importance: number;
  validFrom: string;
  validTo: string | null;
  supersededBy: string | null;
  sourceKind: string;
  sourceRef: string;
  sourceSeqLo: number | null;
  sourceSeqHi: number | null;
  createdAt: string;
}
export interface MemoryBrowseFilter {
  scopeKind: string;
  scopeId: string;
  kind?: string;
  includeRetired: boolean;
}

// Host → webview.
export type HostToMemory =
  | { type: "trace"; trace: RecallTrace | null }
  | { type: "list"; memories: MemoryView[] }
  | { type: "chain"; memoryId: string; chain: MemoryView[] }
  | { type: "error"; message: string };

// Webview → host.
export type MemoryToHost =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "browse"; filter: MemoryBrowseFilter }
  | { type: "loadChain"; memoryId: string };
