import { expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { once } from "node:events";
import { HttpBackendClient } from "../src/client/http-backend-client.js";
import { HistoryReader } from "../../vscode-extension/src/history.js";
const asset = "a".repeat(64);
const wire = (messages = 1) => ({ thread_id: "a", workspace_path: "/project", title: "A", messages: Array.from({ length: messages }, () => ({
  role: "user", content: "image", type: "text", timestamp: "x", metadata: { image_attachments: [
    { asset_id: asset, name: "first.png" }, { asset_id: "b".repeat(64), name: "second.png" },
  ] },
})) });

it.each(["json", "images"])("cancels real held %s transports at the history deadline", async kind => {
  let opened = 0, closed = 0;
  const server = createServer((req, res) => {
    if (kind === "json" || req.url?.includes("/images/")) {
      opened++; res.on("close", () => { closed++; });
      res.writeHead(200, { "content-type": kind === "json" ? "application/json" : "image/png" });
      res.write(kind === "json" ? "{" : Buffer.from([137, 80, 78, 71])); return;
    }
    res.writeHead(200, { "content-type": "application/json" }); res.end(JSON.stringify(wire(2)));
  });
  server.listen(0, "127.0.0.1"); await once(server, "listening");
  const address = server.address(); if (!address || typeof address === "string") throw new Error("Missing port");
  const reader = new HistoryReader(300);
  const client = new HttpBackendClient({ baseUrl: `http://127.0.0.1:${address.port}` });
  try {
    await expect(reader.read("a", async signal => (await client.getChatThread("a", signal)).messages)).rejects.toThrow();
    await vi.waitFor(() => expect(closed).toBe(kind === "json" ? 1 : 2));
    expect(opened).toBe(kind === "json" ? 1 : 2); // never starts the second message's previews
    // The physical transport has settled, so another attachment can acquire the slot.
    await vi.waitFor(async () => expect(await reader.read("b", async () => [])).toEqual([]));
  } finally {
    reader.dispose(); server.closeAllConnections(); await new Promise<void>(r => server.close(() => r()));
  }
});

it("keeps the physical slot until every started image cancellation settles", async () => {
  const releases: (() => void)[] = [];
  const cancelled = vi.fn(() => new Promise<void>(r => { releases.push(r); }));
  const previews = vi.fn(async () => new Response(new ReadableStream<Uint8Array>({
    start(c) { c.enqueue(new Uint8Array([1])); }, cancel: cancelled,
  }), { headers: { "content-type": "image/png" } }));
  const client = new HttpBackendClient({ baseUrl: "http://fixture", fetchFn: async url =>
    String(url).includes("/images/") ? previews() : Response.json(wire(2)) });
  const reader = new HistoryReader(30);
  try {
    await expect(reader.read("a", async signal => (await client.getChatThread("a", signal)).messages)).rejects.toThrow();
    expect(cancelled).toHaveBeenCalledTimes(2);
    const next = vi.fn(async () => []);
    await expect(reader.read("b", next)).rejects.toThrow(); expect(next).not.toHaveBeenCalled();
    releases[0]!(); await Promise.resolve();
    await expect(reader.read("b", next)).rejects.toThrow(); expect(next).not.toHaveBeenCalled();
    releases[1]!();
    await vi.waitFor(async () => expect(await reader.read("b", next)).toEqual([]));
    expect(previews).toHaveBeenCalledTimes(2);
  } finally { for (const release of releases) release(); reader.dispose(); }
});
