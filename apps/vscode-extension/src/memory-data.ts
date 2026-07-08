import type { RecallTrace, MemoryView } from "@crucible/editor-client";

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
  try {
    if (msg.type === "ready" || msg.type === "refresh") {
      const trace = await source.getInspect(threadId);
      post({ type: "trace", trace });
      const memories = await source.browse({
        scopeKind: "workspace",
        scopeId: workspacePath,
        includeRetired: false,
      });
      post({ type: "list", memories });
    } else if (msg.type === "browse") {
      // The webview doesn't own scopeId (the workspace path) — fill it when empty.
      const filter: MemoryBrowseFilter = {
        ...msg.filter,
        scopeId: msg.filter.scopeId || workspacePath,
      };
      post({ type: "list", memories: await source.browse(filter) });
    } else if (msg.type === "loadChain") {
      post({ type: "chain", memoryId: msg.memoryId, chain: await source.getChain(msg.memoryId) });
    }
  } catch (err) {
    post({ type: "error", message: err instanceof Error ? err.message : String(err) });
  }
}
