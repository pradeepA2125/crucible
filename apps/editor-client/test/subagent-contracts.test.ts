import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client";

function respond(body: unknown) {
  return vi.fn().mockResolvedValue({ ok: true, json: async () => body });
}

const SUMMARY = {
  agent_id: "agent-a", turn_id: "turn-1", parent_agent_id: null, depth: 1,
  name: "general-purpose", label: "impl", status: "completed", files_changed_count: 1,
  started_at: null, ended_at: null, report_preview: "done",
};

describe("sub-agent contracts", () => {
  it("parses a thread containing an agent_dispatch message", async () => {
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      thread_id: "t", workspace_path: "/w", title: "t", touched_files: [],
      messages: [{ role: "agent", content: "", type: "agent_dispatch",
                   timestamp: "2026-10-01T00:00:00Z",
                   metadata: { agent_ids: ["agent-a"], turn_id: "turn-1" } }],
    }) });
    const thread = await c.getChatThread("t");
    expect(thread.messages[0].type).toBe("agent_dispatch");
  });

  it("lists, gets and stops agents with camelCase mapping", async () => {
    const fetchFn = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ agents: [SUMMARY] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({
        ...SUMMARY, prompt: "p", report: "full report", files_changed: ["a.py"],
        stale_refusals: 2, last_seq: 9, transcript: [{
          role: "agent", content: "full report", type: "text",
          timestamp: "2026-10-01T00:00:00Z", metadata: { report: true, seq: 9 } }] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ok: true }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    const [agent] = await c.listAgents("t", "turn-1");
    expect(fetchFn.mock.calls[0][0]).toBe("http://x/v1/chat/threads/t/agents?turn_id=turn-1");
    expect(agent).toMatchObject({ agentId: "agent-a", filesChangedCount: 1, reportPreview: "done" });
    const detail = await c.getAgent("t", "agent-a");
    expect(detail).toMatchObject({ report: "full report", filesChanged: ["a.py"],
                                   staleRefusals: 2, lastSeq: 9 });
    expect(detail.transcript[0].metadata).toEqual({ report: true, seq: 9 });
    expect(await c.stopAgent("t", "agent-a")).toEqual({ ok: true });
    expect(fetchFn.mock.calls[2][0]).toBe("http://x/v1/chat/threads/t/agents/agent-a/stop");
  });

  it("maps /live agents and the config flag", async () => {
    const live = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      active_task_id: null, status: null, pending_gates: [], plan: null, turn_active: true,
      agents: [{ ...SUMMARY, status: "running", now: "read_file a.py", tool_count: 3 }],
    }) });
    const state = await live.getThreadLiveState("t");
    expect(state.agents?.[0]).toMatchObject({ status: "running", now: "read_file a.py",
                                               toolCount: 3 });
    const cfg = new HttpBackendClient({ baseUrl: "http://x", fetchFn: respond({
      task_subsystem_enabled: false, chat_controller_enabled: true, memory_enabled: false,
      skills_enabled: false, mcp_enabled: false, subagents_enabled: true, provider: null }) });
    expect((await cfg.getConfig()).subagentsEnabled).toBe(true);
  });
});
