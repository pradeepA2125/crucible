import { afterEach, expect, it, vi } from "vitest";
import { WorkAuthority } from "../src/work-start.js";
import { CrucibleController, type ControllerUI } from "../src/controller.js";
import type { BackendTaskClient, ThreadLiveState } from "@crucible/editor-client";
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const controllers: CrucibleController[] = [];
afterEach(() => { controllers.splice(0).forEach(c => c.dispose()); vi.useRealTimers(); });
function fixture() {
  let busy = "a", process = "p";
  const state: ThreadLiveState = { activeTaskId: null, status: null, plan: null, pendingGates: [], turnActive: false, turnKind: null, agentsRunning: 0, teams: null, messageCount: 0, processId: "p" };
  const ui = new Proxy({ getWorkspacePath: () => "/project" } as Record<string, unknown>, { get(target, key: string) { return target[key] ??= vi.fn(); } }) as unknown as ControllerUI;
  const backend = { getChatThread: vi.fn(async (threadId: string) => ({ threadId, messages: [] })),
    getThreadLiveState: vi.fn(async () => ({ ...state, processId: process })),
    getChatActivity: vi.fn(async () => ({ supported: true, version: 1, requestId: "fresh", requestedWorkspace: "/project", workspacePath: "/project", processId: process,
      threads: ["a", "b"].map(threadId => ({ threadId, turnActive: threadId === busy, agentsRunning: 0, sessionsRunning: 0 })) })),
    listAgents: async () => [], listTeams: async () => [], sendChatMessage: vi.fn(async () => ({ kind: "queued", messageId: "receipt" })),
    postChatCommandDecision: vi.fn(async () => {}), stopChatTurn: vi.fn(async () => ({ ok: true })),
  } as unknown as BackendTaskClient;
  const c = new CrucibleController(() => backend, { load: async () => null, save: async () => {}, clear: async () => {} }, { getBackendBaseUrl: () => "http://backend", getDefaultMode: () => "project_edit", getPollIntervalMs: () => 1000 }, ui, { openDiff: async () => {} });
  controllers.push(c);
  return { c, backend, ui, state, setBusy: (v: string) => { busy = v; }, setProcess: (v: string) => { process = v; } };
}
it("allows A→B browsing but blocks B work while A is active, then permits B after fresh idle authority", async () => {
  vi.useFakeTimers(); const f = fixture(); await f.c.switchChatThread("a"); await flush(); await f.c.switchChatThread("b"); await flush(); await vi.advanceTimersByTimeAsync(2100);
  expect(f.backend.stopChatTurn).not.toHaveBeenCalled();
  await f.c.sendChatMessage("B draft"); expect(f.backend.sendChatMessage).not.toHaveBeenCalled(); expect(f.ui.restoreDraft).toHaveBeenCalledWith("B draft", undefined);
  f.setBusy(""); vi.mocked(f.ui.showWarning).mockClear(); await f.c.sendChatMessage("B work"); expect(vi.mocked(f.ui.showWarning).mock.calls).toEqual([]); expect(f.backend.sendChatMessage).toHaveBeenCalledOnce();
});
it("allows the matching A gate continuation while A's held turn remains active, refusing stale/other-thread gates", async () => {
  const f = fixture(); f.state.pendingGates = [{ gateId: "command-gate", kind: "command", payload: {}, agent: null, team: null }];
  await f.c.switchChatThread("a"); await flush();
  await f.c.handleCommandDecisionFromChat("a", { approve: true, remember: false, scope: "exact" }, "command-gate"); expect(f.backend.postChatCommandDecision).toHaveBeenCalledOnce();
  await f.c.handleCommandDecisionFromChat("b", { approve: true, remember: false, scope: "exact" }, "command-gate");
  await f.c.handleCommandDecisionFromChat("a", { approve: true, remember: false, scope: "exact" }, "replaced"); expect(f.backend.postChatCommandDecision).toHaveBeenCalledOnce();
});
it("reserves synchronously before authority awaits and refuses a second local start", async () => {
  const f = fixture(); f.setBusy(""); await f.c.switchChatThread("a"); await flush();
  let release!: (value: any) => void;
  f.backend.getChatActivity = vi.fn(async () => new Promise(r => { release = r; }));
  const first = f.c.sendChatMessage("first"); await flush();
  await f.c.sendChatMessage("second"); expect(f.backend.sendChatMessage).not.toHaveBeenCalled();
  release({ supported: false, version: 1, requestId: "x", requestedWorkspace: "/project", reason: "unknown" }); await first;
  expect(f.backend.sendChatMessage).not.toHaveBeenCalled();
});
it("retains physical activity occupancy after a visible deadline until ignored cancellation settles", async () => {
  vi.useFakeTimers(); const reader = new WorkAuthority(); let finish!: (v: any) => void;
  const backend = { getChatActivity: vi.fn(async () => new Promise(r => { finish = r; })) } as unknown as BackendTaskClient;
  const first = reader.read("p", backend, "/project"), refusal = expect(first).rejects.toThrow("timed out"); await flush();
  await vi.advanceTimersByTimeAsync(4001); await refusal;
  await expect(reader.read("other", backend, "/project")).rejects.toThrow("previous"); expect(backend.getChatActivity).toHaveBeenCalledOnce();
  finish({ supported: false }); await flush(); reader.dispose();
});

it("keeps an uncertain continuation reserved after authoritative idle and never resubmits it", async () => {
  const f = fixture(); f.state.pendingGates = [{ gateId: "g", kind: "command", payload: {}, agent: null, team: null }];
  await f.c.switchChatThread("a"); await flush();
  f.backend.postChatCommandDecision = vi.fn(async () => { throw new Error("connection lost after dispatch"); });
  await f.c.handleCommandDecisionFromChat("a", { approve: true, remember: false, scope: "exact" }, "g");
  f.setBusy(""); await f.c.checkChatReceipt(); await f.c.sendChatMessage("do not start");
  expect(f.backend.postChatCommandDecision).toHaveBeenCalledOnce(); expect(f.backend.sendChatMessage).not.toHaveBeenCalled();
  expect(f.ui.renderChatAdmission).toHaveBeenLastCalledWith(expect.objectContaining({ state: "unknown" }));
});

it("independent: project prompt may not start stale A work after navigation to B",async()=>{
 const f=fixture();f.setBusy("");await f.c.switchChatThread("a");await flush();let answer!:(v:string)=>void;
 f.ui.promptForGoal=vi.fn(async()=>new Promise(r=>answer=r));f.backend.submitTask=vi.fn(async()=>({taskId:"task-created-after-detach"}));
 f.backend.getTask=vi.fn(async()=>({taskId:"task-created-after-detach",status:"SUCCEEDED",modifiedFiles:[],diagnostics:[]}));
 const start=f.c.startTask();await flush();await f.c.switchChatThread("b");await flush();answer("A original goal");await start;
 expect(f.backend.submitTask).not.toHaveBeenCalled();
});

it("independent: project identity change during authority read invalidates default composer work",async()=>{
 const f=fixture();f.setBusy("");let workspace="/project";f.ui.getWorkspacePath=()=>workspace;await f.c.switchChatThread("a");await flush();
 let release!:(v:any)=>void;let calls=0;const idle={supported:true,version:1,requestId:"fresh",requestedWorkspace:"/project",workspacePath:"/project",processId:"p",threads:[{threadId:"a",turnActive:false,agentsRunning:0,sessionsRunning:0}]};
 f.backend.getChatActivity=vi.fn(async()=>++calls===1?new Promise(r=>release=r):idle);
 const sending=f.c.sendChatMessage("do not write after project changes");await flush();workspace="/different-project";release(idle);await sending;
 expect(f.backend.sendChatMessage).not.toHaveBeenCalled();
});

it("builds the shared dependency index only with connected idle project authority", async () => {
  const f = fixture(), build = vi.fn(async () => {});
  await expect(f.c.buildDependencyIndex(build)).rejects.toThrow("connected");
  await f.c.switchChatThread("a"); await flush();
  await expect(f.c.buildDependencyIndex(build)).rejects.toThrow("current project work"); expect(build).not.toHaveBeenCalled();
  f.setBusy(""); await f.c.buildDependencyIndex(build); expect(build).toHaveBeenCalledOnce();
});

it("propagates rejected and unknown index builds to the original Graph error reply",async()=>{
 const f=fixture();f.setBusy("");await f.c.switchChatThread("a");await flush();
 const {handleGraphMessage}=await import("../src/graph/graph-messages.js");const post=vi.fn();
 const failed=new Error("503 index unavailable");await expect(f.c.buildDependencyIndex(async()=>{throw failed;})).rejects.toBe(failed);
 await handleGraphMessage({type:"buildIndex"},{buildIndex:()=>f.c.buildDependencyIndex(async()=>{throw failed;})} as any,post);
 expect(post).toHaveBeenCalledWith({type:"error",message:"503 index unavailable"});expect(post.mock.calls.some(([m])=>m.building)).toBe(false);
 f.setBusy("a");await expect(f.c.buildDependencyIndex(async()=>{})).rejects.toThrow("current project work");
});
