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
});
