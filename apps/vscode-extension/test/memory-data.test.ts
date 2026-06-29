import { describe, expect, test, vi } from "vitest";
import { handleMemoryMessage, type MemoryDataSource } from "../src/memory-data.js";
import type { RecallTrace, MemoryView } from "@ai-editor/editor-client";

function fakeTrace(): RecallTrace {
  return { query: "q", scopeKind: "workspace", scopeId: "/ws", k: 8, floor: 0.15, reranked: false, entries: [] };
}
function fakeMem(id: string): MemoryView {
  return {
    id, scopeKind: "workspace", scopeId: "/ws", kind: "semantic", content: "c", entities: [],
    importance: 5, validFrom: "x", validTo: null, supersededBy: null, sourceKind: "consolidation",
    sourceRef: "r", sourceSeqLo: null, sourceSeqHi: null, createdAt: "x",
  };
}

function source(over: Partial<MemoryDataSource> = {}): MemoryDataSource {
  return {
    getInspect: vi.fn(async () => fakeTrace()),
    browse: vi.fn(async () => [fakeMem("a")]),
    getChain: vi.fn(async () => [fakeMem("old"), fakeMem("a")]),
    ...over,
  };
}

describe("handleMemoryMessage", () => {
  test("ready fetches inspect + browse and posts trace + list", async () => {
    const posted: unknown[] = [];
    const src = source();
    await handleMemoryMessage({ type: "ready" }, src, "chat-1", "/ws", (m) => posted.push(m));
    expect(src.getInspect).toHaveBeenCalledWith("chat-1");
    expect(src.browse).toHaveBeenCalledWith(
      expect.objectContaining({ scopeKind: "workspace", scopeId: "/ws", includeRetired: false })
    );
    expect(posted).toContainEqual({ type: "trace", trace: fakeTrace() });
    expect(posted).toContainEqual({ type: "list", memories: [fakeMem("a")] });
  });

  test("browse fills empty scopeId from the workspace path", async () => {
    const src = source();
    await handleMemoryMessage(
      { type: "browse", filter: { scopeKind: "workspace", scopeId: "", kind: "episodic", includeRetired: true } },
      src, "chat-1", "/ws", () => {}
    );
    expect(src.browse).toHaveBeenCalledWith(
      expect.objectContaining({ scopeId: "/ws", kind: "episodic", includeRetired: true })
    );
  });

  test("loadChain posts a chain message keyed by memoryId", async () => {
    const posted: unknown[] = [];
    await handleMemoryMessage({ type: "loadChain", memoryId: "a" }, source(), "chat-1", "/ws", (m) => posted.push(m));
    expect(posted).toContainEqual({ type: "chain", memoryId: "a", chain: [fakeMem("old"), fakeMem("a")] });
  });

  test("a data-source throw posts an error message, not a throw", async () => {
    const posted: unknown[] = [];
    const src = source({ getInspect: vi.fn(async () => { throw new Error("boom"); }) });
    await handleMemoryMessage({ type: "ready" }, src, "chat-1", "/ws", (m) => posted.push(m));
    expect(posted.some((m) => (m as { type: string }).type === "error")).toBe(true);
  });
});
