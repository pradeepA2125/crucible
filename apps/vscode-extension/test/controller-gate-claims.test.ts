import { afterEach, expect, it, vi } from "vitest";
import { CrucibleController, type ControllerUI } from "../src/controller.js";
import { ChatAdmissionUnknownError, gateDecisionIdentity, type BackendTaskClient, type ThreadLiveState, type GateDecisionIdentity } from "@crucible/editor-client";
const controllers: CrucibleController[] = [];
afterEach(() => { controllers.splice(0).forEach(c => c.dispose()); });
const flush = async () => { for (let n = 0; n < 30; ++n) await Promise.resolve(); };
const action = { type: "gateDecision" as const, threadId: "a", gateId: "g", kind: "mode" as const, value: "implement" };
async function fixture() {
  const ui = new Proxy({ getWorkspacePath: () => "/project" } as Record<string, unknown>, { get(target, key: string) { return target[key] ??= vi.fn(); } }) as unknown as ControllerUI;
  const live: ThreadLiveState = { activeTaskId: null, status: null, plan: null, pendingGates: [{ gateId: "g", kind: "mode", payload: { options: [{ mode: "implement" }] }, agent: null, team: null }], turnActive: false,
    agentsRunning: 0, teams: null, turnKind: null, messageCount: 0, providerAccess: null, processId: "p", gateClaimVersion: 1,
    approval: { supported: true, autoAccept: false, shellPolicy: "ask", owned: true, version: 1, processId: "p" } };
  const backend = { getChatThread: vi.fn(async () => ({ threadId: "a", messages: [] })), getThreadLiveState: vi.fn(async () => live),
    getChatActivity: vi.fn(async () => ({ supported: true, version: 1, requestId: "x", requestedWorkspace: "/project", workspacePath: "/canonical", processId: "p", threads: [{ threadId: "a", turnActive: false, agentsRunning: 0, sessionsRunning: 0 }] })),
    listAgents: async () => [], listTeams: async () => [],
    claimChatGate: vi.fn(async (thread, claim) => ({ ...await gateDecisionIdentity(thread, claim), status: "accepted" as const })),
    getGateDecisionReceipt: vi.fn(async () => ({ version: 1, nonce: "x", found: false, receipt: null })),
  } as unknown as BackendTaskClient;
  const c = new CrucibleController(() => backend, { load: async () => null, save: async () => {}, clear: async () => {} },
    { getBackendBaseUrl: () => "http://backend", getDefaultMode: () => "project_edit", getPollIntervalMs: () => 1000 }, ui, { openDiff: async () => {} });
  controllers.push(c);
  (c as any).approval = { ready: async () => "p", current: () => ({ confirmed: true }), confirmFirstSend: async () => true, observe: () => {} };
  await c.switchChatThread("a"); await flush();
  return { c, backend, live, ui };
}
it("accepts the exact current gate using canonical project/process without a legacy POST", async () => {
  const f = await fixture(); let wasCurrent = false; const accepted = vi.fn(async (isCurrent: () => boolean) => { wasCurrent = isCurrent(); });
  await f.c.handleExactGateDecision(action, accepted);
  expect(f.backend.claimChatGate).toHaveBeenCalledOnce();
  expect(f.backend.claimChatGate).toHaveBeenCalledWith("a", expect.objectContaining({ workspacePath: "/canonical", processId: "p", gateId: "g", value: "implement" }), expect.anything());
  expect(accepted).toHaveBeenCalledOnce(); expect(wasCurrent).toBe(true);
});
it("reserves before approval awaits, preventing duplicate card starts", async () => {
  const f = await fixture(); let release!: (value: string) => void;
  (f.c as any).approval.ready = () => new Promise(r => { release = r; });
  const first = f.c.handleExactGateDecision(action, async () => {}); await flush();
  await f.c.handleExactGateDecision(action, async () => {});
  expect(f.backend.claimChatGate).not.toHaveBeenCalled(); release("p"); await first;
  expect(f.backend.claimChatGate).toHaveBeenCalledOnce();
});
it("refuses a replaced gate and emits only its original exact retry", async () => {
  const f = await fixture(); f.live.pendingGates[0]!.gateId = "replacement";
  await f.c.handleExactGateDecision(action, async () => {});
  expect(f.backend.claimChatGate).not.toHaveBeenCalled();
  expect(f.ui.retryDecision).toHaveBeenCalledWith("a", "mode", "g");
  expect(f.ui.restoreDraft).not.toHaveBeenCalled();
});
it("holds unknown after missing receipts, then accepts only the pinned positive identity without resending", async () => {
  const f = await fixture(); let identity!: GateDecisionIdentity;
  f.backend.claimChatGate = vi.fn(async (thread, claim) => { identity = await gateDecisionIdentity(thread, claim); throw new ChatAdmissionUnknownError(new Error("lost reply")); });
  const accepted = vi.fn(async () => {}); await f.c.handleExactGateDecision(action, accepted);
  await f.c.checkChatReceipt(); expect(accepted).not.toHaveBeenCalled();
  expect(f.ui.renderChatAdmission).toHaveBeenLastCalledWith(expect.objectContaining({ state: "unknown" }));
  f.backend.getGateDecisionReceipt = vi.fn(async () => ({ version: 1, nonce: "fresh", found: true, receipt: { ...identity, status: "accepted" } }));
  await f.c.checkChatReceipt(); expect(accepted).toHaveBeenCalledOnce();
  expect(f.backend.claimChatGate).toHaveBeenCalledOnce();
});
it("does not treat a mismatched receipt as rejection or acceptance", async () => {
  const f = await fixture(); f.backend.claimChatGate = vi.fn(async (thread, claim) => ({ ...await gateDecisionIdentity(thread, claim), gateId: "other", status: "accepted" }));
  const accepted = vi.fn(async () => {}); await f.c.handleExactGateDecision(action, accepted);
  expect(accepted).not.toHaveBeenCalled(); expect(f.ui.renderChatAdmission).toHaveBeenLastCalledWith(expect.objectContaining({ state: "unknown" }));
});
it("refuses a process change during a held confirmation", async () => {
  const f = await fixture(); let release!: (v: boolean) => void;
  (f.c as any).approval.confirmFirstSend = () => new Promise(r => { release = r; });
  const pending = f.c.handleExactGateDecision(action, async () => {}); await flush();
  f.live.processId = "replacement"; await f.c.pollThreadLiveState(); release(true); await pending;
  expect(f.backend.claimChatGate).not.toHaveBeenCalled();
});


it("keeps the acceptance origin predicate live while queued preference work waits", async () => {
  const f = await fixture(); let entered!: () => void, release!: () => void;
  const waiting = new Promise<void>(r => { entered = r; }); let wasCurrent = true;
  const pending = f.c.handleExactGateDecision(action, async isCurrent => {
    entered(); await new Promise<void>(r => { release = r; }); wasCurrent = isCurrent();
  });
  await waiting; await f.c.switchChatThread("b"); release(); await pending; expect(wasCurrent).toBe(false);
});
