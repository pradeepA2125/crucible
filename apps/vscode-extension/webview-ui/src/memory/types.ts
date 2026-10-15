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
export interface LegacyRecallTrace {
  query: string;
  scopeKind: string;
  scopeId: string;
  k: number;
  floor: number;
  reranked: boolean;
  entries: RecallTraceEntry[];
}

export interface RecallTraceV2 {
  version: 2;
  trigger: string;
  signals: { Q: string; C: string; R: string };
  candidates: { id: string; catalog: string; sources: string[] }[];
  scores: Record<string, Record<string, number>>;
  reasons: Record<string, string>;
  verdicts: Record<string, boolean>;
  delivered: string[];
  judge: string;
  status: string;
  invalidIds: string[];
  invalidatedIds: string[];
  rule?: string | undefined;
  selectionMethod?: string | undefined;
  rerankInput?: {
    query: string;
    truncatedSignals: string[];
    truncatedMemoryIds: string[];
    maxPairTokens: number;
  } | undefined;
  error?: string | null | undefined;
}
export type RecallTrace = LegacyRecallTrace | RecallTraceV2;
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

export interface CurationStatus {
  enabled: boolean; status: string; lastMseq: number; failures: number;
  saved: number; dropped: number; chunks: number;
  retryAt: string | null; error: string | null; updatedAt: string | null;
}

export interface DurableMemoryView {
  id: string; scope_kind: string; scope_id: string; kind: string; content: string;
  topic: string; applies_when: string; detail: string; entities: string[];
  provenance: string; source_thread_id: string; source_message_id: number; revision: number;
  valid_from: string; valid_to: string | null; superseded_by: string | null; created_at: string;
}
export interface MemorySnapshot {
  pipeline: "durable" | "legacy" | "disabled" | "unavailable";
  status: "ready" | "disabled" | "unavailable";
  memories: DurableMemoryView[];
}

// Host → webview.
export type HostToMemory =
  | { type: "snapshot"; snapshot: MemorySnapshot }
  | { type: "trace"; trace: RecallTrace | null }
  | { type: "curation"; status: CurationStatus | null; error?: string }
  | { type: "list"; memories: MemoryView[] }
  | { type: "chain"; memoryId: string; chain: MemoryView[] }
  | { type: "error"; message: string; resource?: string };

// Webview → host.
export type MemoryToHost =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "browse"; filter: MemoryBrowseFilter }
  | { type: "loadChain"; memoryId: string };
