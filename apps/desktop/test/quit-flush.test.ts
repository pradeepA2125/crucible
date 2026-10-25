import { it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { DraftFlusher, projectReplyAllowed, quitFlushCommand } from "../src/draft-flush.js";
import { DraftSession } from "../../vscode-extension/src/drafts/draft-session.js";


it("saves the latest composer through the real protocol during the first Quit", async () => {
  // Guard wiring is in Electron main; run the actual shared save/ack protocol here.
  const source = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  expect(source).toContain("projectReplyAllowed(pool.isSelected(id) && host===selectedHost,closing,message.type)");
  expect(source).toContain("closing && !quitFlushCommand(payload.type)");
  const listeners = new Set<(event: { data: unknown }) => void>();
  const target = {
    addEventListener: (_: string, fn: (event: { data: unknown }) => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: (event: { data: unknown }) => void) => listeners.delete(fn),
    postMessage: (data: unknown) => { for (const fn of listeners) fn({ data }); },
  } as unknown as Window;
  let closing = false;
  const flusher = new DraftFlusher(100);
  const session = new DraftSession({ identity: () => "view", admission: () => {},
    post: message => { if (projectReplyAllowed(true, closing, message.type)) target.postMessage(message, "*"); } });
  // Keep the browser-only module out of the desktop NodeNext type graph.
  const rendererModule = "../../vscode-extension/webview-ui/src/drafts/draftClient.js";
  const { createDraftClient } = await import(rendererModule);
  const client = createDraftClient((raw: any) => {
    if (closing && !quitFlushCommand(raw.type)) throw new Error("new action refused");
    if (raw.type === "draft/flushAck") flusher.acknowledge("view", raw.requestId, raw.error);
    else session.handle(raw);
  }, target);
  try {
    await client.read("thread");
    client.onFlush(async () => { await client.save("thread", 1, { text: "latest before quit", imageAttachments: [], mentionedPaths: [] }); });
    closing = true;
    await flusher.beforeQuit("view", message => target.postMessage(message, "*"), true);
    closing = false;
    expect((await client.read("thread")).draft.text).toBe("latest before quit");
  } finally { client.dispose(); session.dispose(); }
});

it("does not reopen background publication or new work while closing", () => {
  for (const type of ["sendMessage", "draft/begin", "draft/reject", "desktop/selectProject", "gateDecision", "settings/setProvider", "stopTurn"]) {
    expect(quitFlushCommand(type)).toBe(false);
  }
  expect(quitFlushCommand("desktop/layoutSave")).toBe(true);
  expect(projectReplyAllowed(false, true, "draft/result")).toBe(false);
  expect(projectReplyAllowed(true, true, "liveStatus")).toBe(false);
  expect(projectReplyAllowed(true, false, "liveStatus")).toBe(true);
});
