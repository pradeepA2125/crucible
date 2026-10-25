import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LiveSlot } from "../components/LiveSlot";
import { useGateDraft } from "../drafts/useGateDraft";
import { ModeGate } from "../components/messages/gates/ModeGate";
import { createDraftClient } from "../drafts/draftClient";
import { DraftStore } from "../../../src/drafts/draft-store";
import type { Draft } from "../drafts/types";
import { vscode } from "../vscodeApi";
vi.mock("../vscodeApi", () => ({ vscode: { postMessage: vi.fn() } }));
const cleanup: (() => void)[] = [];
afterEach(() => { cleanup.splice(0).forEach(f => f()); vi.mocked(vscode.postMessage).mockClear(); });
function bridge() {
  const store = new DraftStore();
  const port = createDraftClient(raw => {
    const message = raw as Record<string, unknown>;
    if (message.type !== "draft/read" && message.type !== "draft/save") return;
    const snapshot = message.type === "draft/read" ? store.read(message.threadId as string)
      : store.save(message.threadId as string, message.revision as number, message.draft as Draft);
    window.dispatchEvent(new MessageEvent("message", { data: { type: "draft/result", requestId: message.requestId, snapshot } }));
  });
  cleanup.push(() => { port.dispose(); store.clear(); }); return { store, port };
}
const props = { taskId: "a", threadId: "a", gateId: "g", payload: { options: [{ mode: "implement", label: "Implement" }] } };
it("saves and remounts exact feedback in the same host store, without changing the composer", async () => {
  const { port } = bridge();
  await port.save("a", 1, { text: "composer draft", imageAttachments: [{ name: "image", mediaType: "image/png", dataUrl: "data:image/png;base64,AQ==", bytes: 1 }], mentionedPaths: ["src/a.ts"] });
  let view = render(<ModeGate {...props} draftStore={port} />);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "refine the plan" } });
  await act(() => port.flush!()); view.unmount();
  view = render(<ModeGate {...props} draftStore={port} />);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("refine the plan"));
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Enter" });
  await waitFor(() => expect(vscode.postMessage).toHaveBeenCalledWith(expect.objectContaining({ type: "gateDecision", gateId: "g", kind: "feedback", value: "refine the plan", draft: expect.objectContaining({ revision: expect.any(Number) }) })));
  const composer = await port.read("a"); expect(composer.draft.text).toBe("composer draft");
  expect(composer.draft.imageAttachments).toHaveLength(1); expect(composer.draft.mentionedPaths).toEqual(["src/a.ts"]);
});
it("flushes composer and gate independently; removing the auxiliary callback preserves composer registration", async () => {
  const { port } = bridge(), composer = vi.fn(async () => {}), card = vi.fn(async () => {});
  const stopComposer = port.onFlush(composer), stopCard = port.onAuxiliaryFlush!(card);
  await port.flush!(); expect(composer).toHaveBeenCalledOnce(); expect(card).toHaveBeenCalledOnce();
  stopCard(); await port.flush!(); expect(composer).toHaveBeenCalledTimes(2); expect(card).toHaveBeenCalledOnce(); stopComposer();
});
it("retries only the exact rejected gate while preserving its input", async () => {
  render(<ModeGate {...props} />);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "keep feedback" } });
  fireEvent.click(screen.getByRole("button", { name: "Implement" }));
  await waitFor(() => expect(screen.queryByRole("textbox")).toBeNull());
  act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "decisionRetry", threadId: "a", gateId: "other", kind: "mode" } })));
  expect(screen.queryByRole("textbox")).toBeNull();
  act(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "decisionRetry", threadId: "a", gateId: "g", kind: "mode" } })));
  expect(screen.getByRole("textbox")).toHaveValue("keep feedback");
});


it("hydrates B's own feedback when the same gate ID/payload crosses root threads without an empty render", async () => {
  const { port } = bridge();
  await port.save('@gate:["b","g","mode"]', 1, { text: "B feedback", imageAttachments: [], mentionedPaths: [] });
  const gate = { gateId: "g", taskId: "a", kind: "mode" as const, payload: props.payload, agent: null, team: null };
  const slot = (threadId: string) => <LiveSlot threadId={threadId} draftStore={port} liveGates={[gate]} livePlan={null} liveReview={null} liveError={null} onDismissError={() => {}} />;
  const view = render(slot("a"));
  await act(() => port.flush!()); fireEvent.change(screen.getByRole("textbox"), { target: { value: "A feedback" } });
  await act(() => port.flush!()); view.rerender(slot("b"));
  await act(() => port.flush!());
  expect((await port.read('@gate:["b","g","mode"]')).draft.text).toBe("B feedback");
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("B feedback"));
  await act(() => port.flush!());
  expect((await port.read('@gate:["b","g","mode"]')).draft.text).toBe("B feedback");
  expect((await port.read('@gate:["a","g","mode"]')).draft.text).toBe("A feedback");
});

it("refuses delayed feedback preparation when its root or port changes", async () => {
  const { port } = bridge(); let prepare!: () => Promise<unknown>;
  function Probe({ threadId }: { threadId: string }) {
    const draft = useGateDraft(port, threadId, "g", "mode"); prepare = draft.prepare;
    return <input aria-label="feedback" value={draft.text} onChange={e => draft.setText(e.target.value)} />;
  }
  const view = render(<Probe threadId="a" />); await act(() => port.flush!());
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "A feedback" } }); await act(() => port.flush!());
  let release!: () => void;
  const original = port.flush; port.flush = () => new Promise<void>(r => { release = r; });
  let pending!: Promise<unknown>;
  await act(async () => { pending = prepare(); await Promise.resolve(); });
  const rejected = expect(pending).rejects.toThrow();
  view.rerender(<Probe threadId="b" />); await act(async () => { release(); await rejected; }); port.flush = original;
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
});
