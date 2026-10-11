import { describe, expect, test } from "vitest";
import type { ContextUsage } from "@crucible/editor-client";
import { ContextUsageState } from "../src/context-usage-state.js";
const usage = (revision = 1, generation = 1, process_id = "p1"): ContextUsage => ({
  thread_id: "a", provider: "chatgpt", model: "gpt", scope: "main", process_id,
  generation, revision, turn_id: "turn", request_id: "request", status: "in_flight",
  input_tokens: 0, input_quality: "estimated", context_window: 1000,
  capacity_source: "chatgpt_catalog", observed_at: "2026-10-08T00:00:00Z",
});
describe("ContextUsageState", () => {
  test("only guarded live establishes process, SSE requests hydration", () => {
    const state = new ContextUsageState(); const epoch = state.navigate("a", "url");
    expect(state.accept(usage(), epoch, "url", "sse")).toBe("hydrate");
    expect(state.usage).toBeNull();
    expect(state.accept(null, epoch, "url", "live", "p1")).toBe("accepted");
    expect(state.accept(usage(), epoch, "url", "sse")).toBe("accepted");
    expect(state.usage?.input_tokens).toBe(0);
  });
  test("rejects older revisions/generations and malformed or foreign observations", () => {
    const s = new ContextUsageState(); const e = s.navigate("a", "url");
    s.accept(usage(5, 3), e, "url", "live", "p1");
    for (const raw of [usage(4, 3), usage(4, 4), usage(99, 2), { ...usage(6, 3), thread_id: "b" }, { ...usage(6, 3), input_tokens: true }]) {
      expect(s.accept(raw, e, "url", "sse")).toBe("ignored");
    }
    expect(s.usage?.revision).toBe(5);
  });
  test("unchanged successful live clears stale without changing measurement time", () => {
    const s = new ContextUsageState(); const e = s.navigate("a", "url");
    s.accept(usage(), e, "url", "live", "p1"); s.fail(e, "url");
    expect(s.stale).toBe(true);
    s.accept(usage(), e, "url", "live", "p1");
    expect(s.stale).toBe(false); expect(s.usage?.observed_at).toBe(usage().observed_at);
  });
  test.each([
    ["missing", null],
    ["malformed", { ...usage(), input_tokens: true }],
    ["foreign thread", { ...usage(), thread_id: "b" }],
    ["foreign process", usage(2, 1, "p2")],
  ])("%s live telemetry retains the last measurement as stale until valid recovery", (_label, raw) => {
    const s = new ContextUsageState(); const e = s.navigate("a", "url");
    const previous = { ...usage(), input_tokens: 18000 };
    s.accept(previous, e, "url", "live", "p1");
    s.accept(raw, e, "url", "live", "p1");
    expect(s.usage).toEqual(previous);
    expect(s.stale).toBe(true);
    s.fail(e, "url");
    s.accept(raw, e, "url", "live", "p1");
    expect(s.stale).toBe(true);
    s.accept(previous, e, "url", "live", "p1");
    expect(s.stale).toBe(false);
    expect(s.usage).toEqual(previous);
  });
  test("an older live observation cannot clear a stale marker", () => {
    const s = new ContextUsageState(); const e = s.navigate("a", "url");
    s.accept(usage(5, 3), e, "url", "live", "p1");
    s.fail(e, "url");
    expect(s.accept(usage(4, 3), e, "url", "live", "p1")).toBe("ignored");
    expect(s.stale).toBe(true);
    expect(s.usage?.revision).toBe(5);
  });
  test("a new process with missing telemetry drops the old measurement", () => {
    const s = new ContextUsageState(); const e = s.navigate("a", "url");
    s.accept(usage(), e, "url", "live", "p1"); s.fail(e, "url");
    s.accept(null, e, "url", "live", "p2");
    expect(s.usage).toBeNull(); expect(s.stale).toBe(false);
    expect(s.accept(usage(1, 1, "p2"), e, "url", "sse")).toBe("accepted");
  });
  test("A-B-A and backend changes retire callbacks even for the same thread", () => {
    const s = new ContextUsageState(); const old = s.navigate("a", "url");
    s.navigate("b", "url"); const current = s.navigate("a", "url");
    expect(s.accept(usage(9), old, "url", "live", "p1")).toBe("ignored");
    expect(s.accept(usage(), current, "new-url", "live", "p1")).toBe("ignored");
    expect(s.usage).toBeNull();
  });
  test("process restart requires live; retired process can never handshake again", () => {
    const s = new ContextUsageState(); const e = s.navigate("a", "url");
    s.accept(usage(10), e, "url", "live", "p1");
    expect(s.accept(usage(1, 0, "p2"), e, "url", "sse")).toBe("hydrate");
    s.accept(usage(1, 0, "p2"), e, "url", "live", "p2");
    expect(s.usage?.process_id).toBe("p2");
    expect(s.accept(usage(99), e, "url", "live", "p1")).toBe("ignored");
  });
  test("new generation tombstone clears input and late callbacks cannot resurrect it", () => {
    const s = new ContextUsageState(); const e = s.navigate("a", "url");
    s.accept(usage(), e, "url", "live", "p1");
    s.accept({ ...usage(2, 2), status: "invalidated", input_tokens: null, input_quality: "unavailable", observed_at: null }, e, "url", "sse");
    expect(s.usage?.input_tokens).toBeNull();
    expect(s.accept(usage(99), e, "url", "sse")).toBe("ignored");
  });
});
