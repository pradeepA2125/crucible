import { describe, expect, it } from "vitest";
import { appendDurable, applyAgentEvent, formatElapsed, isTerminalAgent, viewFromDetail } from "../agents";
import type { AgentDetailView, ChatMsg } from "../types";

const AT = "2026-10-01T00:00:00.000Z";

function detail(transcript: ChatMsg[] = []): AgentDetailView {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status: "running", now: "", toolCount: 0, filesChangedCount: 0, startedAt: null,
    endedAt: null, reportPreview: "", prompt: "p", report: "", filesChanged: [],
    staleRefusals: 0, transcript, lastSeq: 0,
  };
}

const roster = (ids: string[]): ChatMsg => ({
  role: "agent", content: "", type: "agent_dispatch", timestamp: AT, metadata: { agent_ids: ids } });

describe("applyAgentEvent", () => {
  it("pairs a tool result with its call by call_index", () => {
    let v = viewFromDetail(detail());
    v = applyAgentEvent(v, { type: "tool_call", payload: { tool: "read_file", args: { path: "a.py" }, call_index: 0 } }, AT);
    v = applyAgentEvent(v, { type: "tool_call", payload: { tool: "search_code", args: {}, call_index: 1 } }, AT);
    v = applyAgentEvent(v, { type: "tool_result", payload: { output: "ok", is_error: false, call_index: 0 } }, AT);
    expect(v.live.map((t) => [t.tool, t.done])).toEqual([["read_file", true], ["search_code", false]]);
    expect(v.live[0].output).toBe("ok");
  });

  it("seals live pills before a durable message", () => {
    let v = viewFromDetail(detail());
    v = applyAgentEvent(v, { type: "tool_call", payload: { tool: "read_file", args: {}, call_index: 0 } }, AT);
    v = applyAgentEvent(v, { type: "chat_progress", payload: { note: "halfway" } }, AT);
    v = applyAgentEvent(v, { type: "chat_breadcrumb", payload: { text: "✓ Edit accepted: a.py" } }, AT);
    v = applyAgentEvent(v, { type: "diff_ready", payload: { diff_entries: [{ path: "a.py", additions: 1, deletions: 0 }], resolved: "applied" } }, AT);
    expect(v.live).toEqual([]);
    expect(v.messages.map((m) => [m.type, m.content])).toEqual([
      ["text", ""], ["text", "halfway"], ["text", "✓ Edit accepted: a.py"], ["diff_card", ""]]);
    expect(v.messages[0].metadata.tool_events).toHaveLength(1);
    expect(v.messages[1].metadata.progress).toBe(true);
    expect(v.messages[2].metadata.breadcrumb).toBe(true);
    expect(v.messages[3].metadata).toMatchObject({ resolved: "applied" });
  });

  it("adds a nested roster once, even if the backfill already had it", () => {
    let v = viewFromDetail(detail([roster(["agent-z"])]));
    v = applyAgentEvent(v, { type: "agent_dispatch", payload: { message: roster(["agent-z"]) } }, AT);
    expect(v.messages.filter((m) => m.type === "agent_dispatch")).toHaveLength(1);
  });

  it("ignores events the view does not render", () => {
    const v = viewFromDetail(detail());
    expect(applyAgentEvent(v, { type: "token_progress", payload: { thinking: 9 } }, AT)).toBe(v);
  });
});

describe("helpers", () => {
  it("appendDurable dedups roster messages by agent ids", () => {
    const once = appendDurable([], roster(["a", "b"]));
    expect(appendDurable(once, roster(["a", "b"]))).toBe(once);
    expect(appendDurable(once, roster(["c"]))).toHaveLength(2);
  });

  it("formatElapsed", () => {
    expect(formatElapsed(38_000)).toBe("38s");
    expect(formatElapsed(72_000)).toBe("1m12s");
    expect(formatElapsed(3_905_000)).toBe("1h05m");
    expect(formatElapsed(-5)).toBe("0s");
  });
});

describe("isTerminalAgent", () => {
  it("treats the v2 idle statuses as finished and v1 waiting as live", () => {
    expect(isTerminalAgent("awaiting_peer")).toBe(true);
    expect(isTerminalAgent("failed_transient")).toBe(true);
    expect(isTerminalAgent("waiting")).toBe(false);
  });
});
