import { afterEach, describe, expect, it, vi } from "vitest";
import { CrucibleController, type ControllerUI } from "../src/controller.js";
import type { BackendTaskClient, ChatMessage, ThreadLiveState } from "@crucible/editor-client";
const rows = (text: string, metadata: Record<string, unknown> = {}): ChatMessage[] => [{ role: "agent", content: text, type: "text", metadata, timestamp: "x" }];
const flush = async () => { for (let i=0;i<60;i++) await Promise.resolve(); };
const controllers: CrucibleController[] = [];
afterEach(() => { for (const c of controllers.splice(0)) c.dispose(); vi.useRealTimers(); });
function fixture() {
  let process = "process";
  let read: (thread: string, signal?: AbortSignal) => Promise<ChatMessage[]> = async t => rows(t);
  const ui = new Proxy({ getWorkspacePath: () => "/project" } as Record<string, unknown>, { get(target, key: string) { return target[key] ??= vi.fn(); } }) as unknown as ControllerUI;
  const state: ThreadLiveState = { activeTaskId: null, status: null, plan: null, pendingGates: [], turnActive: true,
    agentsRunning: 0, teams: null, turnKind: "user", messageCount: 1, providerAccess: null };
  const backend = { getChatActivity: vi.fn(async () => ({ version: 1, supported: true, requestId: "fresh", requestedWorkspace: "/project", workspacePath: "/project", processId: process, threads: ["a", "b"].map(threadId => ({ threadId, turnActive: false, agentsRunning: 0, sessionsRunning: 0 })) })), getChatThread: vi.fn(async (threadId: string, signal?: AbortSignal) => ({ threadId, messages: await read(threadId, signal) })),
    getThreadLiveState: vi.fn(async () => ({ ...state, processId: process })), listAgents: async () => [], listTeams: async () => [],
    streamChannel: vi.fn(async function* () { for (let i=0;i<100;i++) yield { type: "chat_done", payload: {} }; }),
  } as unknown as BackendTaskClient;
  const controller = new CrucibleController(() => backend, { load: async () => null, save: async () => {}, clear: async () => {} },
    { getBackendBaseUrl: () => "http://backend", getDefaultMode: () => "project_edit", getPollIntervalMs: () => 1000 }, ui, { openDiff: async () => {} });
  controllers.push(controller);
  return { controller, ui, backend, state, setRead: (r: typeof read) => { read = r; }, setProcess: (p: string) => { process = p; } };
}
describe("persisted history following", () => {
  it("never replays durable SSE, preserves identical legitimate edits and same-count metadata updates", async () => {
    vi.useFakeTimers(); const f = fixture(); let revision = 1;
    f.setRead(async () => [...rows("same edit", { revision }), ...rows("same edit", { revision })]);
    await f.controller.switchChatThread("a"); await flush();
    expect(f.backend.streamChannel).not.toHaveBeenCalled();
    expect(f.ui.replaceChatMessages).toHaveBeenLastCalledWith([...rows("same edit", { revision: 1 }), ...rows("same edit", { revision: 1 })]);
    revision = 2; await vi.advanceTimersByTimeAsync(2100); await flush();
    expect(f.ui.replaceChatMessages).toHaveBeenLastCalledWith([...rows("same edit", { revision: 2 }), ...rows("same edit", { revision: 2 })]);
  });
  it("publishes B identity and clears A cards immediately; failed B cannot show A under B", async () => {
    const f = fixture(); await f.controller.switchChatThread("a"); await flush();
    vi.mocked(f.ui.replaceChatMessages).mockClear();
    let reject!: (e: Error) => void;
    f.setRead(async () => new Promise((_resolve, r) => { reject = r; }));
    const b = f.controller.switchChatThread("b"); await flush();
    expect(f.ui.clearChatThread).toHaveBeenLastCalledWith("b");
    expect(f.ui.renderHistoryState).toHaveBeenLastCalledWith(expect.objectContaining({ threadId: "b", phase: "loading", cached: false }));
    expect(f.ui.replaceChatMessages).not.toHaveBeenCalled();
    reject(new Error("offline")); await b; await flush();
    expect(f.ui.renderHistoryState).toHaveBeenLastCalledWith(expect.objectContaining({ threadId: "b", phase: "unavailable", cached: false }));
  });
  it("uses cache only as historical and keeps A→B→A reads generation-bound", async () => {
    const f = fixture(); await f.controller.switchChatThread("a"); await flush();
    let finish!: (value: ChatMessage[]) => void;
    f.setRead(async () => new Promise(r => { finish = r; }));
    const b = f.controller.switchChatThread("b"); await flush();
    const a = f.controller.switchChatThread("a"); await flush();
    expect(f.ui.replaceChatMessages).toHaveBeenLastCalledWith(rows("a"));
    finish(rows("late b")); await b; await a; await flush();
    expect(vi.mocked(f.ui.replaceChatMessages).mock.calls.flat().some(m => m[0]?.content === "late b")).toBe(false);
  });
  it("forces a final same-count refresh on fresh terminal authority without treating replayed done as proof", async () => {
    vi.useFakeTimers(); const f = fixture(); let done = false;
    f.setRead(async () => rows("a", { done })); await f.controller.switchChatThread("a"); await flush();
    done = true; f.state.turnActive = false;
    await f.controller.pollThreadLiveState(); await flush();
    expect(f.ui.replaceChatMessages).toHaveBeenLastCalledWith(rows("a", { done: true }));
    expect(f.backend.streamChannel).not.toHaveBeenCalled();
  });
  it("clears all action authority on process change, and never applies an older process history read", async () => {
    const f = fixture(); await f.controller.switchChatThread("a"); await flush();
    f.setProcess("new-process"); await f.controller.pollThreadLiveState(); await flush();
    expect(f.ui.clearLiveGates).toHaveBeenCalled();
    expect(f.ui.renderHistoryState).toHaveBeenLastCalledWith(expect.objectContaining({ threadId: "a", phase: "loading" }));
  });
});

it("a delayed historical GET cannot remove a new optimistic echo or overwrite owned streaming", async () => {
  const f = fixture(); f.state.turnActive = false;
  await f.controller.switchChatThread("a"); await flush();
  let complete!: (value: ChatMessage[]) => void; let release!: () => void;
  const blocked = new Promise<void>(r => { release = r; });
  f.setRead(async () => new Promise(r => { complete = r; }));
  const before = vi.mocked(f.ui.replaceChatMessages).mock.calls.length;
  const old = (f.controller as any).reconcileTranscript("a", 1, true); await flush();
  f.backend.sendChatMessage = vi.fn(async (_tid, _text, _signal, options) => {
    options?.onAdmitted?.();
    return { kind: "stream", events: (async function* () { await blocked; yield { type: "chat_done" as const, payload: {} }; })() };
  });
  const send = f.controller.sendChatMessage("new local work"); await flush();
  expect(f.ui.appendChatMessage).toHaveBeenCalledWith(expect.objectContaining({ content: "new local work" }));
  complete(rows("stale saved copy")); await old; await flush();
  expect(vi.mocked(f.ui.replaceChatMessages).mock.calls.length).toBe(before);
  release(); await send;
});

it("a final history failure remains explicitly unsynced and blocks rewind", async () => {
  const f = fixture(); await f.controller.switchChatThread("a"); await flush();
  f.setRead(async () => { throw new Error("offline"); }); f.state.turnActive = false;
  await f.controller.pollThreadLiveState(); await flush();
  expect(f.ui.renderHistoryState).toHaveBeenLastCalledWith(expect.objectContaining({ phase: "unavailable", cached: true }));
  f.backend.rewindThread = vi.fn(); await f.controller.rewindTo("old-message");
  expect(f.backend.rewindThread).not.toHaveBeenCalled();
});


it("refuses both historical inline-change mutations at the host boundary", async () => {
  const f = fixture(); await f.controller.switchChatThread("a"); await flush();
  f.backend.getThreadLiveState = vi.fn(async () => { throw new Error("offline"); });
  await f.controller.pollThreadLiveState();
  f.backend.applyInlineChange = vi.fn(); f.backend.discardInlineChange = vi.fn();
  await f.controller.applyInlineChange("saved-change"); await f.controller.discardInlineChange("saved-change");
  expect(f.backend.applyInlineChange).not.toHaveBeenCalled();
  expect(f.backend.discardInlineChange).not.toHaveBeenCalled();
});

it("rechecks readiness after rewind metadata and suppresses late previews", async () => {
  const f = fixture(); f.state.turnActive = false;
  await f.controller.switchChatThread("a"); await flush();
  let finishRead!: (messages: ChatMessage[]) => void, finishPreview!: (preview: any) => void;
  f.setRead(async () => new Promise(r => { finishRead = r; }));
  f.backend.rewindThread = vi.fn();
  f.backend.previewRewind = vi.fn(async () => new Promise(r => { finishPreview = r; }));
  const rewind = f.controller.rewindTo("old-id"), preview = f.controller.previewRewind("old-id");
  await flush();
  f.backend.getThreadLiveState = vi.fn(async () => { throw new Error("offline"); });
  await f.controller.pollThreadLiveState();
  finishRead([{ ...rows("old")[0]!, role: "user", id: "old-id" }]);
  finishPreview({ messageId: "old-id", affectedFiles: [] });
  await rewind; await preview;
  expect(f.backend.rewindThread).not.toHaveBeenCalled();
  expect(f.ui.showRewindPreview).not.toHaveBeenCalled();
});

it("returns the original draft if freshness is lost during first-send confirmation", async () => {
  const f = fixture(); f.state.turnActive = false;
  await f.controller.switchChatThread("a"); await flush();
  let confirm!: (value: boolean) => void;
  (f.controller as any).approval = { ready: async () => "process", confirmFirstSend: () => new Promise(r => { confirm = r; }), observe: () => {} };
  f.backend.sendChatMessage = vi.fn();
  const send = f.controller.sendChatMessage("expanded payload", undefined, undefined, undefined, undefined, undefined, "draft", "original text");
  await flush();
  f.backend.getThreadLiveState = vi.fn(async () => { throw new Error("offline"); });
  await f.controller.pollThreadLiveState(); confirm(true); await send;
  expect(f.backend.sendChatMessage).not.toHaveBeenCalled();
  expect(f.ui.restoreDraft).toHaveBeenCalledWith("original text", expect.objectContaining({ draftId: "draft" }));
});

it("requires a new saved read under the first observed process before becoming current", async () => {
  const f = fixture(); let calls = 0, finish!: (messages: ChatMessage[]) => void;
  f.setRead(async () => {
    if (++calls === 1) { f.setProcess("after-restart"); return rows("pre-binding copy"); }
    return new Promise(r => { finish = r; });
  });
  await f.controller.switchChatThread("a"); await flush();
  expect(f.ui.renderHistoryState).toHaveBeenLastCalledWith(expect.objectContaining({ phase: "loading" }));
  expect(calls).toBe(2);
  finish(rows("bound copy")); await flush();
  expect(f.ui.replaceChatMessages).toHaveBeenLastCalledWith(rows("bound copy"));
  expect(f.ui.renderHistoryState).toHaveBeenLastCalledWith(expect.objectContaining({ phase: "current" }));
});

it("project detach cancels only local history attachment, never backend work, and fences late A reads",async()=>{
 vi.useFakeTimers();const f=fixture();f.backend.stopChatTurn=vi.fn(async()=>({ok:true}));
 await f.controller.switchChatThread("a");await flush();
 let finish!:(value:ChatMessage[])=>void;f.setRead(async()=>new Promise(r=>finish=r));
 const old=(f.controller as any).reconcileTranscript("a",1,true);await flush();
 f.controller.detachProjectView();vi.mocked(f.ui.replaceChatMessages).mockClear();finish(rows("stale background A"));await old;await flush();
 expect(f.backend.stopChatTurn).not.toHaveBeenCalled();expect(f.ui.replaceChatMessages).not.toHaveBeenCalled();
 f.setRead(async()=>rows("fresh A"));await f.controller.switchChatThread("a");await flush();expect(f.ui.replaceChatMessages).toHaveBeenLastCalledWith(rows("fresh A"));
});
