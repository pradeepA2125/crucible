import { expect, test, vi } from "vitest";
import { handleMemoryMessage, type MemoryDataSource } from "../src/memory-data.js";

const source = (): MemoryDataSource => ({
  getInspect: vi.fn(async () => null), browse: vi.fn(async () => []),
  getChain: vi.fn(async () => []),
  getSnapshot: vi.fn(async () => ({ pipeline: "durable", status: "ready", memories: [] })),
});
test("durable snapshot never reads legacy", async () => {
  const src = source(); const messages: unknown[] = [];
  await handleMemoryMessage({ type: "ready" }, src, "thread", "/ws", m => messages.push(m));
  expect(src.getSnapshot).toHaveBeenCalledWith("thread");
  expect(src.browse).not.toHaveBeenCalled();
  expect(messages).toContainEqual({ type: "snapshot", snapshot: { pipeline: "durable", status: "ready", memories: [] } });
});
test("snapshot failure does not fall back; recall still loads", async () => {
  const src = source(); src.getSnapshot = async () => { throw new Error("unavailable"); };
  const messages: unknown[] = [];
  await handleMemoryMessage({ type: "refresh" }, src, "thread", "/ws", m => messages.push(m));
  expect(src.browse).not.toHaveBeenCalled();
  expect(messages).toContainEqual({ type: "trace", trace: null });
  expect(messages).toContainEqual({ type: "error", resource: "browser", message: "unavailable" });
});
test("legacy scope identity cannot be supplied by webview", async () => {
  const src = source();
  await handleMemoryMessage({ type: "browse", filter: { scopeKind: "thread", scopeId: "foreign", includeRetired: true } }, src, "current", "/ws", () => {});
  expect(src.browse).toHaveBeenCalledWith({ scopeKind: "thread", scopeId: "current", includeRetired: true });
});
