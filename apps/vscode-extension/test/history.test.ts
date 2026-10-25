import { afterEach, describe, expect, it, vi } from "vitest";
import type { ChatMessage } from "@crucible/editor-client";
import { HistoryCache, HistoryReader } from "../src/history.js";
const messages = (value: string): ChatMessage[] => [{ role: "agent", type: "text", content: value, metadata: {}, timestamp: "x" }];
afterEach(() => vi.useRealTimers());
describe("saved history cache", () => {
  it("isolates project/backend/thread identities, clones arrays, and keeps8 LRU entries", () => {
    const cache = new HistoryCache(); const a = HistoryCache.key("url", "/a", "same");
    const original = messages("original"); cache.put(a, original); original[0]!.content = "mutated";
    expect(cache.get(a)![0]!.content).toBe("original");
    cache.get(a)![0]!.content = "mutated again";
    expect(cache.get(a)![0]!.content).toBe("original");
    expect(cache.get(HistoryCache.key("other", "/a", "same"))).toBeUndefined();
    expect(cache.get(HistoryCache.key("url", "/b", "same"))).toBeUndefined();
    for (let i = 0; i < 7; i++) cache.put(String(i), messages("v"));
    cache.get(a); cache.put("new", messages("v"));
    expect(cache.get("0")).toBeUndefined(); expect(cache.get(a)).toBeDefined();
  });
  it("counts retained UTF8 previews and removes obsolete copies on oversize", () => {
    const cache = new HistoryCache(250);
    cache.put("a", messages("old"));
    const image = messages("small"); image[0]!.metadata = { image_previews: [{ dataUrl: "中".repeat(100) }] };
    expect(cache.put("a", image)).toBe(false); expect(cache.get("a")).toBeUndefined();
    cache.put("b", messages("one")); cache.put("c", messages("two")); cache.clear();
    expect(cache.get("b")).toBeUndefined();
  });
});
describe("one actual history read slot", () => {
  it("coalesces a matching request and preserves physical occupancy after navigation cancellation", async () => {
    const reader = new HistoryReader(); let resolve!: (value: ChatMessage[]) => void;
    const fetch = vi.fn((_signal: AbortSignal) => new Promise<ChatMessage[]>(r => { resolve = r; }));
    const first = reader.read("a", fetch); const firstCheck = expect(first).rejects.toThrow("interrupted");
    expect(reader.read("a", fetch)).toBe(first);
    await Promise.resolve(); reader.cancel(); await firstCheck;
    await expect(reader.read("b", fetch)).rejects.toThrow("previous"); expect(fetch).toHaveBeenCalledTimes(1);
    resolve(messages("late a")); await Promise.resolve(); await Promise.resolve();
    expect(await reader.read("b", async () => messages("b"))).toEqual(messages("b")); reader.dispose();
  });
  it("aborts at deadline but never opens a second slot until a cancellation-ignoring read settles", async () => {
    vi.useFakeTimers(); const reader = new HistoryReader(100); let resolve!: (v: ChatMessage[]) => void; let signal!: AbortSignal;
    const pending = reader.read("a", s => { signal = s; return new Promise(r => { resolve = r; }); });
    const check = expect(pending).rejects.toThrow(); await vi.advanceTimersByTimeAsync(101); await check;
    expect(signal.aborted).toBe(true); await expect(reader.read("b", async () => [])).rejects.toThrow("previous");
    resolve([]); await Promise.resolve(); await Promise.resolve();
    expect(await reader.read("b", async () => [])).toEqual([]); reader.dispose();
  });
});
