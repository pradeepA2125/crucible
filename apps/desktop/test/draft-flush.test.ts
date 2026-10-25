import { afterEach, describe, expect, it, vi } from "vitest";
import { DraftFlusher } from "../src/draft-flush.js";
afterEach(() => vi.useRealTimers());
describe("native draft flush", () => {
  it("waits for the matching view and nonce, and surfaces a renderer save failure", async () => {
    const flush = new DraftFlusher(); const post = vi.fn(); const pending = flush.request("view", post);
    const id = post.mock.calls[0]![0].requestId;
    flush.acknowledge("other", id); flush.acknowledge("view", "other");
    flush.acknowledge("view", id); await expect(pending).resolves.toBeUndefined();
    const failed = flush.request("view", post); flush.acknowledge("view", post.mock.calls[1]![0].requestId, "Save failed");
    await expect(failed).rejects.toThrow("Save failed");
  });
  it("requires an explicit choice after every failed quit save; no second-quit discard", async () => {
    vi.useFakeTimers(); const flush = new DraftFlusher(300); const post = vi.fn();
    const quit = expect(flush.beforeQuit("view", post, true)).rejects.toThrow("explicitly discard");
    await vi.advanceTimersByTimeAsync(301); await quit;
    const retry=expect(flush.beforeQuit("view",post,true)).rejects.toThrow("explicitly discard");
    await vi.advanceTimersByTimeAsync(301);await retry;expect(post).toHaveBeenCalledTimes(2);
    await expect(flush.beforeQuit("view", post, false)).resolves.toBeUndefined();
    const navigation = expect(flush.request("view", post)).rejects.toThrow("Navigation was cancelled");
    await vi.advanceTimersByTimeAsync(301); await navigation;
  });
});
