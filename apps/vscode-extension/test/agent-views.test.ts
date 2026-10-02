import type { AgentDetail, SequencedStreamEvent } from "@crucible/editor-client";
import { describe, expect, test } from "vitest";

import { AgentViewManager, TERMINAL_AGENT_STATUSES, agentChannel } from "../src/agent-views.js";

function detail(status: string, lastSeq: number, report = ""): AgentDetail {
  return {
    agentId: "agent-a", parentAgentId: null, depth: 1, name: "explore", label: "survey",
    status, now: "", toolCount: 0, filesChangedCount: 0, startedAt: null, endedAt: null,
    reportPreview: "", prompt: "p", report, filesChanged: [], staleRefusals: 0,
    transcript: [], lastSeq,
  };
}

/** A channel that yields `events`, then either ends or waits until aborted. */
function channel(events: SequencedStreamEvent[], hold: boolean) {
  return async function* (_id: string, signal?: AbortSignal) {
    for (const e of events) yield e;
    if (hold) {
      await new Promise<void>((_, reject) => signal?.addEventListener(
        "abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }))));
    }
  };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe("AgentViewManager", () => {
  test("backfills, then forwards only events newer than the backfill", async () => {
    const seen: Array<[string, unknown]> = [];
    const client = {
      getAgent: async () => detail("running", 4),
      streamChannel: channel([
        { type: "tool_call", payload: { tool: "read_file" } as never, seq: 4 },
        { type: "tool_call", payload: { tool: "search_code" } as never, seq: 5 },
        { type: "agent_dispatch", payload: { message: {
          role: "agent", content: "", type: "agent_dispatch", task_id: null,
          timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-z"] } } }, seq: 6 },
      ], true),
    };
    const m = new AgentViewManager(() => client, {
      detail: (id, d) => seen.push(["detail", d.status]),
      event: (id, e) => seen.push([e.type, e.seq]),
    }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    expect(seen).toEqual([["detail", "running"], ["tool_call", 5], ["agent_dispatch", 6]]);
    m.closeAll();
  });

  test("a nested roster arrives as a contract ChatMessage", async () => {
    const events: SequencedStreamEvent[] = [];
    const client = {
      getAgent: async () => detail("running", 0),
      streamChannel: channel([{ type: "agent_dispatch", payload: { message: {
        role: "agent", content: "", type: "agent_dispatch", task_id: null,
        timestamp: "2026-10-01T00:00:00Z", metadata: { agent_ids: ["agent-z"] } } }, seq: 1 }], true),
    };
    const m = new AgentViewManager(() => client, { detail: () => {}, event: (_, e) => events.push(e) }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    expect(events[0].payload).toEqual({ message: expect.objectContaining({ taskId: null, type: "agent_dispatch" }) });
    m.closeAll();
  });

  test("an idle channel re-backfills; a terminal backfill ends the view", async () => {
    const statuses = ["running", "completed"];
    let subscriptions = 0;
    const client = {
      getAgent: async () => detail(statuses.shift() ?? "completed", 0, "full report"),
      streamChannel: (id: string, signal?: AbortSignal) => {
        subscriptions += 1;
        return channel([], false)(id, signal);  // ends at once, like an idle timeout
      },
    };
    const details: string[] = [];
    const m = new AgentViewManager(() => client, { detail: (_, d) => details.push(d.status), event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    for (let i = 0; i < 6; i++) await flush();
    expect(details).toEqual(["running", "completed"]);
    expect(subscriptions).toBe(1);
  });

  test("closing a view aborts its subscription", async () => {
    let signal: AbortSignal | undefined;
    const client = {
      getAgent: async () => detail("running", 0),
      streamChannel: (id: string, s?: AbortSignal) => { signal = s; return channel([], true)(id, s); },
    };
    const m = new AgentViewManager(() => client, { detail: () => {}, event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    expect(signal?.aborted).toBe(false);
    m.setOpen("t", []);
    expect(signal?.aborted).toBe(true);
  });

  test("a terminal status cuts the stream so the final backfill runs", async () => {
    const statuses = ["running", "completed"];
    const client = {
      getAgent: async () => detail(statuses.shift() ?? "completed", 0),
      streamChannel: channel([], true),
    };
    const details: string[] = [];
    const m = new AgentViewManager(() => client, { detail: (_, d) => details.push(d.status), event: () => {} }, 0);
    m.setOpen("t", ["agent-a"]);
    await flush(); await flush();
    m.noteStatus("agent-a", "completed");
    for (let i = 0; i < 4; i++) await flush();
    expect(details).toEqual(["running", "completed"]);
  });

  test("agentChannel names the child's channel", () => {
    expect(agentChannel("t", "agent-a")).toBe("chat:t:agent:agent-a");
  });
});

test("v2 idle statuses end a view; v1 waiting does not", () => {
  expect(TERMINAL_AGENT_STATUSES.has("failed_transient")).toBe(true);
  expect(TERMINAL_AGENT_STATUSES.has("awaiting_peer")).toBe(true);
  expect(TERMINAL_AGENT_STATUSES.has("waiting")).toBe(false);
});
