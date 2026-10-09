import { describe, expect, it } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

const observation = {
  thread_id: "t", process_id: "p", provider: "chatgpt", model: "gpt", scope: "main",
  generation: 1, revision: 2, turn_id: null, request_id: "r", status: "last_request",
  input_tokens: 0, input_quality: "provider_reported", context_window: 100,
  capacity_source: "chatgpt_catalog", observed_at: "2026-10-08T10:00:00Z",
};
const live = async (context_usage: unknown, process_id: unknown = "p") => new HttpBackendClient({
  baseUrl: "http://x", fetchFn: async () => new Response(JSON.stringify({
    active_task_id: null, status: null, plan: null, process_id, context_usage,
    provider_access: { kind: "usage_limit", message: "retained" },
  }), { status: 200 }),
}).getThreadLiveState("t");

describe("context usage snapshot", () => {
  it("retains provider reported zero and independent process handshake", async () => {
    const state = await live(observation);
    expect(state).toMatchObject({ processId: "p", contextUsage: observation });
  });
  it.each([true, -1, 1.5, "12", Number.MAX_SAFE_INTEGER + 1])("isolates invalid input %s", async (input_tokens) => {
    const state = await live({ ...observation, input_tokens });
    expect(state.providerAccess?.message).toBe("retained");
    expect(state).toMatchObject({ processId: "p", contextUsage: null });
  });
  it.each([
    { input_tokens: null }, { input_quality: "unavailable" }, { context_window: null },
    { capacity_source: "unknown" }, { observed_at: "yesterday" }, { scope: "agent" },
    { status: "invalidated" }, { generation: -1 }, { revision: true },
    { provider: "chatgpt", capacity_source: "configured" },
  ])("rejects inconsistent metadata %j without losing unrelated state", async (patch) => {
    const state = await live({ ...observation, ...patch });
    expect(state.providerAccess?.message).toBe("retained");
    expect(state).toMatchObject({ contextUsage: null });
  });
  it("accepts unknown-capacity and versioned tombstones", async () => {
    const unknown = { ...observation, context_window: null, capacity_source: "unknown" };
    expect(await live(unknown)).toMatchObject({ contextUsage: unknown });
    const tombstone = { ...unknown, input_tokens: null, input_quality: "unavailable", observed_at: null, status: "invalidated" };
    expect(await live(tombstone)).toMatchObject({ contextUsage: tombstone });
  });
  it("isolates a malformed handshake", async () => {
    expect(await live(observation, 2)).toMatchObject({ processId: null, contextUsage: observation });
  });
});
