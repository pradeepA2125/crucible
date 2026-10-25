import { act } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createDraftClient } from "../desktop/draftClient";
const reply = (requestId: string, snapshot: unknown) => window.dispatchEvent(new MessageEvent("message", { data: { type: "draft/result", requestId, snapshot } }));
describe("shared asynchronous draft port", () => {
  it("matches replies and does not acknowledge native flush before its save finishes", async () => {
    const send = vi.fn(); const port = createDraftClient(send); const read = port.read("a");
    const request = send.mock.calls[0]![0];
    reply("wrong", {}); reply(request.requestId, { threadId: "a", revision: 1, version: 0, draft: { text: "kept", imageAttachments: [], mentionedPaths: [] } });
    await expect(read).resolves.toMatchObject({ draft: { text: "kept" } });
    let saved!: () => void; const stop = port.onFlush(() => new Promise<void>(resolve => { saved = resolve; }));
    window.dispatchEvent(new MessageEvent("message", { data: { type: "draft/flush", requestId: "native" } }));
    expect(send).not.toHaveBeenCalledWith(expect.objectContaining({ type: "draft/flushAck" }));
    await act(async () => saved()); expect(send).toHaveBeenCalledWith({ type: "draft/flushAck", requestId: "native" });
    stop(); port.dispose();
  });
});

it("omits unchanged image bytes for typing and reconstructs compact acknowledgements", async () => {
  const send = vi.fn(); const port = createDraftClient(send);
  const image = { name: "image", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 };
  const read = port.read("a"); reply(send.mock.calls[0]![0].requestId, { threadId: "a", revision: 1, version: 0, draft: { text: "first", imageAttachments: [image], mentionedPaths: [] } }); await read;
  const save = port.save("a", 2, { text: "typed", imageAttachments: [image], mentionedPaths: [] });
  const request = send.mock.calls[1]![0]; expect(request.draft).toEqual({ text: "typed", mentionedPaths: [] }); expect(request.version).toBe(0);
  reply(request.requestId, { threadId: "a", revision: 2, version: 0, draft: { text: "typed", mentionedPaths: [] }, imagesUnchanged: true, returnedUnchanged: true });
  await expect(save).resolves.toMatchObject({ draft: { text: "typed", imageAttachments: [image] } }); port.dispose();
});


it("exposes the same flush for renderer-only history navigation and refuses to leave after a save failure", async () => {
  const before = vi.fn(async () => {}); const port = createDraftClient(vi.fn(), window, before);
  let finish!: () => void; const save = vi.fn(() => new Promise<void>(resolve => { finish = resolve; }));
  const unregister = port.onFlush(save); let finished = false;
  const pending = port.flush!().then(() => { finished = true; });
  await Promise.resolve(); expect(before).toHaveBeenCalledTimes(1); expect(save).toHaveBeenCalledTimes(1); expect(finished).toBe(false);
  finish(); await pending; expect(finished).toBe(true); unregister();
  port.onFlush(async () => { throw new Error("Draft not saved"); });
  await expect(port.flush!()).rejects.toThrow("Draft not saved"); port.dispose();
});

it("bounds renderer image retention by evicting old snapshots while current typing still carries its images", async () => {
  const send = vi.fn(); const port = createDraftClient(send);
  const image = { name: "image", mediaType: "image/png", dataUrl: "data:image/png;base64,AA==", bytes: 1 };
  for (let i = 0; i < 33; ++i) {
    const read = port.read(String(i)); reply(send.mock.calls[send.mock.calls.length - 1]![0].requestId, { threadId: String(i), revision: 1, version: 0, draft: { text: "", imageAttachments: [image], mentionedPaths: [] } }); await read;
  }
  const current = port.save("32", 2, { text: "current", imageAttachments: [image], mentionedPaths: [] });
  let request = send.mock.calls[send.mock.calls.length - 1]![0]; expect(request.draft.imageAttachments).toBeUndefined();
  reply(request.requestId, { threadId: "32", revision: 2, version: 0, draft: { text: "current", mentionedPaths: [] }, imagesUnchanged: true }); await current;
  const evicted = port.save("0", 2, { text: "older", imageAttachments: [image], mentionedPaths: [] });
  request = send.mock.calls[send.mock.calls.length - 1]![0]; expect(request.draft.imageAttachments).toEqual([image]);
  reply(request.requestId, { threadId: "0", revision: 2, version: 0, draft: request.draft }); await evicted; port.dispose();
});
