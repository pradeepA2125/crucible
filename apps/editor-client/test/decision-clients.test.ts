import { describe, it, expect, vi } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client";

describe("mode/edit decision clients", () => {
  it("posts edit-decision to the right endpoint with decision + reason", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    await c.postEditDecision("th1", "reject", "wrong var");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://x/v1/chat/threads/th1/edit-decision",
      expect.objectContaining({ method: "POST" })
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ decision: "reject", reason: "wrong var" });
  });

  it("streams mode-decision from the streamed endpoint with the mode", async () => {
    const reader = {
      read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined),
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, body: { getReader: () => reader } });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    // Drain the async iterable (empty stream).
    for await (const _ of c.postModeDecision("th1", "edit")) {
      /* no events */
    }
    expect(fetchMock).toHaveBeenCalledWith(
      "http://x/v1/chat/threads/th1/mode-decision",
      expect.objectContaining({ method: "POST" })
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ mode: "edit" });
  });

  it("posts chat command-decision to the chat endpoint with snake_case body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    await c.postChatCommandDecision("th1", {
      approve: true, remember: true, scope: "binary", ruleValue: "pytest",
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://x/v1/chat/threads/th1/command-decision",
      expect.objectContaining({ method: "POST" })
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ approve: true, remember: true, scope: "binary", rule_value: "pytest" });
  });
});

describe("chat review preference client", () => {
  it("posts review-pref to the chat thread endpoint with a snake_case body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    await c.setChatReviewPref("th1", { autoAccept: true });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://x/v1/chat/threads/th1/review-pref",
      expect.objectContaining({ method: "POST" })
    );
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ auto_accept: true });
  });
});

describe("gate-addressed decisions (multi-gate)", () => {
  const ok = () => vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) });

  it("sends gate_id on an edit decision only when given", async () => {
    const fetchMock = ok();
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    await c.postEditDecision("th1", "accept", "", "g-edit");
    await c.postEditDecision("th1", "accept");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(
      { decision: "accept", reason: "", gate_id: "g-edit" });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(
      { decision: "accept", reason: "" });
  });

  it("sends gate_id on command and MCP decisions", async () => {
    const fetchMock = ok();
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn: fetchMock });
    await c.postChatCommandDecision("th1", { approve: false, remember: false, scope: "exact" }, "g-cmd");
    await c.postChatMcpDecision("th1", { approve: true, remember: false }, "g-mcp");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual(
      { approve: false, remember: false, scope: "exact", gate_id: "g-cmd" });
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(
      { approve: true, remember: false, gate_id: "g-mcp" });
  });
});
