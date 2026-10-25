import { expect, it, vi } from "vitest";
import { createThreadMetadataClient } from "../desktop/threadMetadataClient";
it("serializes preference edits and correlates validated replies", async () => {
  const send = vi.fn(), client = createThreadMetadataClient(send);
  const first = client.change("a", "pin", true), second = client.change("a", "archive", true);
  await Promise.resolve(); await Promise.resolve(); expect(send).toHaveBeenCalledOnce();
  const respond = (requestId: string, metadata: unknown) => window.dispatchEvent(new MessageEvent("message", { source: window, origin: "app://app", data: { type: "desktop/threadsResult", requestId, metadata } }));
  respond("other", {}); respond(send.mock.calls[0]![0].requestId, { a: { pinned: true, archived: false } }); await first;
  await Promise.resolve(); expect(send).toHaveBeenCalledTimes(2);
  respond(send.mock.calls[1]![0].requestId, { a: { pinned: true, archived: true } });
  await expect(second).resolves.toMatchObject({ a: { archived: true } });
});
it("refuses malformed replies without fabricating empty preferences", async () => {
  const send = vi.fn(), client = createThreadMetadataClient(send), pending = client.read(); await Promise.resolve(); await Promise.resolve();
  window.dispatchEvent(new MessageEvent("message", { source: window, origin: "app://app", data: { type: "desktop/threadsResult", requestId: send.mock.calls[0]![0].requestId, metadata: { a: { name: 2 } } } }));
  await expect(pending).rejects.toThrow("Invalid");
});

it("ignores matching foreign preferences and waits for the owned window reply", async () => {
  const send = vi.fn(), client = createThreadMetadataClient(send); let settled = false;
  const pending = client.read().then(value => { settled = true; return value; }); await Promise.resolve(); await Promise.resolve();
  const data = { type: "desktop/threadsResult", requestId: send.mock.calls[0]![0].requestId, metadata: { a: { pinned: true, archived: false } } };
  window.dispatchEvent(new MessageEvent("message", { source: window, origin: "https://foreign.invalid", data }));
  window.dispatchEvent(new MessageEvent("message", { source: null, origin: "app://app", data }));
  await Promise.resolve(); expect(settled).toBe(false);
  window.dispatchEvent(new MessageEvent("message", { source: window, origin: "app://app", data }));
  await expect(pending).resolves.toMatchObject({ a: { pinned: true } });
});
