import type { RecallTrace, MemoryView, CurationStatus, MemorySnapshot } from "@crucible/editor-client";

// vscode-free so it's unit-testable (the extension's vitest runs in a node env with no
// vscode module). MemoryPanel (memory-panel.ts) consumes handleMemoryMessage from here.

export interface MemoryBrowseFilter {
  scopeKind: string;
  scopeId: string;
  kind?: string;
  includeRetired: boolean;
}

export interface MemoryDataSource {
  getInspect(threadId: string): Promise<RecallTrace | null>;
  browse(filter: MemoryBrowseFilter): Promise<MemoryView[]>;
  getChain(memoryId: string): Promise<MemoryView[]>;
  getCuration?(threadId: string): Promise<CurationStatus>;
  getSnapshot?(threadId: string): Promise<MemorySnapshot>;
}

// Webview → host messages (mirror webview-ui/src/memory/types.ts MemoryToHost).
export type MemoryToHost =
  | { type: "ready" }
  | { type: "refresh" }
  | { type: "browse"; filter: MemoryBrowseFilter }
  | { type: "loadChain"; memoryId: string };

type PostFn = (msg: unknown) => void;

/** Pure message handler — no VS Code deps, so it's unit-testable. Best-effort: any data-source
 * throw becomes an `error` message rather than an exception. */
export async function handleMemoryMessage(
  msg: MemoryToHost,
  source: MemoryDataSource,
  threadId: string,
  workspacePath: string,
  post: PostFn
): Promise<void> {
  const fail = (resource: string, err: unknown) => post({ type: "error", resource,
    message: err instanceof Error ? err.message : String(err) });
  if (msg.type === "ready" || msg.type === "refresh") {
    await Promise.all([
      (async () => {
        try { post({ type: "trace", trace: await source.getInspect(threadId) }); }
        catch (err) { fail("trace", err); }
      })(),
      (async () => {
        try {
          post({ type: "curation", status: threadId && source.getCuration
            ? await source.getCuration(threadId) : null });
        } catch (err) {
          post({ type: "curation", status: null,
            error: err instanceof Error ? err.message : String(err) });
        }
      })(),
      (async () => {
        try {
          if (source.getSnapshot) {
            if (!threadId) throw new Error("Open the inspector from a conversation to inspect active memory.");
            const snapshot = await source.getSnapshot(threadId);
            post({ type: "snapshot", snapshot });
            if (snapshot.pipeline !== "legacy" || snapshot.status !== "ready") return;
          }
          post({ type: "list", memories: await source.browse({
            scopeKind: "workspace", scopeId: workspacePath, includeRetired: true,
          }) });
        } catch (err) { fail("browser", err); }
      })(),
    ]);
  } else if (msg.type === "browse") {
    try {
      if (!["workspace", "thread"].includes(msg.filter.scopeKind)) throw new Error("Unsupported legacy scope.");
      const filter = { ...msg.filter,
        scopeId: msg.filter.scopeKind === "thread" ? threadId : workspacePath };
      post({ type: "list", memories: await source.browse(filter) });
    } catch (err) { fail("browser", err); }
  } else if (msg.type === "loadChain") {
    try {
      const chain = await source.getChain(msg.memoryId);
      if (chain.some(m => !(m.scopeKind === "workspace" && m.scopeId === workspacePath)
        && !(m.scopeKind === "thread" && m.scopeId === threadId))) throw new Error("Memory is outside this inspector scope.");
      post({ type: "chain", memoryId: msg.memoryId, chain });
    } catch (err) { fail("chain", err); }
  }
}