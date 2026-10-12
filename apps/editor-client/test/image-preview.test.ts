import { describe, expect, it, vi } from "vitest";
import { HttpBackendClient } from "../src/client/http-backend-client.js";

const digest = "a".repeat(64);
describe("image preview boundary", () => {
  it("hydrates refs with authenticated previews and preserves unavailable refs", async () => {
    const refs = [{ asset_id: digest, name: "ok.png" }, { asset_id: "b".repeat(64), name: "missing.png" }];
    const fetchFn = vi.fn(async (url: string, init?: RequestInit) => {
      expect((init?.headers as Record<string, string>).authorization).toBe("Bearer secret");
      if (url.endsWith(`/images/${digest}`)) return new Response(new Uint8Array([1]), { headers: { "content-type": "image/png" } });
      if (url.includes("/images/")) return new Response("missing", { status: 404 });
      return Response.json({ thread_id: "t", workspace_path: "/ws", title: "Images", messages: [{ role: "user", content: "look", timestamp: "2026-10-09T00:00:00Z", metadata: { image_attachments: refs } }] });
    });
    const client = new HttpBackendClient({ baseUrl: "http://localhost:8000", fetchFn, authToken: () => "secret" });
    const thread = await client.getChatThread("t");
    expect(thread.messages[0].metadata.image_attachments).toEqual(refs);
    expect(thread.messages[0].metadata.image_previews).toEqual([
      { asset_id: digest, name: "ok.png", dataUrl: "data:image/png;base64,AQ==" },
      { asset_id: "b".repeat(64), name: "missing.png", unavailable: true },
    ]);
    expect(JSON.stringify(thread)).not.toContain("secret");
  });
  it("rejects unknown asset identities without issuing a request", async () => {
    const fetchFn = vi.fn();
    const client = new HttpBackendClient({ baseUrl: "http://localhost:8000", fetchFn });
    await expect(client.getChatImagePreview("t", "../image")).rejects.toThrow("identity");
    expect(fetchFn).not.toHaveBeenCalled();
  });
  it("fetches scoped normalized bytes and creates a display-only URL", async () => {
    const fetchFn = vi.fn(async () => new Response(new Uint8Array([1, 2, 3]), { headers: { "content-type": "image/png" } }));
    const client = new HttpBackendClient({ baseUrl: "http://localhost:8000", fetchFn });
    expect(await client.getChatImagePreview("thread name", digest)).toBe("data:image/png;base64,AQID");
    expect(String(fetchFn.mock.calls[0]?.[0])).toContain(`/v1/chat/threads/thread%20name/images/${digest}`);
  });
  it("rejects oversized actual stream even without content-length", async () => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(new Uint8Array(8 * 1024 * 1024 + 1)); }, cancel });
    const client = new HttpBackendClient({ baseUrl: "http://localhost:8000", fetchFn: async () => new Response(body, { headers: { "content-type": "image/png" } }) });
    await expect(client.getChatImagePreview("t", digest)).rejects.toThrow("8 MiB");
    expect(cancel).toHaveBeenCalled();
  });
});
