import { describe, expect, it, vi } from "vitest";
import { BackendAuthError, HttpBackendClient } from "../src/client/http-backend-client";

const okJson = (body: unknown) => ({ ok: true, status: 200, json: async () => body });

describe("auth options", () => {
  it("sends the bearer header and never follows redirects", async () => {
    const fetchFn = vi.fn().mockResolvedValue(okJson({ status: "ok" }));
    const c = new HttpBackendClient({ baseUrl: "http://127.0.0.1:9", fetchFn,
      authToken: () => "T".repeat(43) });
    await c.getConfig().catch(() => undefined);
    const init = fetchFn.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>).authorization).toBe(`Bearer ${"T".repeat(43)}`);
    expect(init.redirect).toBe("manual");
  });

  it("omits the header when authToken returns undefined or is absent", async () => {
    for (const authToken of [() => undefined, undefined]) {
      const fetchFn = vi.fn().mockResolvedValue(okJson({}));
      const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn,
        ...(authToken ? { authToken } : {}) });
      await c.getConfig().catch(() => undefined);
      const headers = (fetchFn.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
      expect(headers.authorization).toBeUndefined();
    }
  });

  it.each([401, 403, 421])("%i raises BackendAuthError and reports not ok", async (status) => {
    const onAuthStatus = vi.fn();
    const fetchFn = vi.fn().mockResolvedValue({ ok: false, status, statusText: "no",
      json: async () => ({}), text: async () => "" });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn, onAuthStatus });
    const err = await c.getConfig().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(BackendAuthError);
    expect((err as BackendAuthError).status).toBe(status);
    expect(onAuthStatus).toHaveBeenCalledWith(false, expect.stringContaining(String(status)));
  });

  it("reports ok on a non-auth response, including a mock with no status", async () => {
    const onAuthStatus = vi.fn();
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn, onAuthStatus });
    await c.getConfig().catch(() => undefined);
    expect(onAuthStatus).toHaveBeenCalledWith(true);
  });

  it("covers raw call sites: the chat channel stream carries the header", async () => {
    const reader = { read: vi.fn().mockResolvedValue({ done: true, value: undefined }),
      cancel: vi.fn().mockResolvedValue(undefined) };
    const fetchFn = vi.fn().mockResolvedValue({ ok: true, status: 200,
      body: { getReader: () => reader } });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn, authToken: () => "k" });
    for await (const _ of c.streamChannel("chat:t")) { /* empty */ }
    const headers = (fetchFn.mock.calls[0][1] as RequestInit).headers as Record<string, string>;
    expect(headers.authorization).toBe("Bearer k");
    expect(headers.accept).toBe("text/event-stream");
  });

  it("discardInlineChange surfaces a failed response", async () => {
    const fetchFn = vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "boom" });
    const c = new HttpBackendClient({ baseUrl: "http://x", fetchFn });
    await expect(c.discardInlineChange("i1")).rejects.toThrow(/500/);
  });
});
