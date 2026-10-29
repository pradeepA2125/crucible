import { describe, expect, it, vi } from "vitest";
import type { ApprovalSnapshot, BackendTaskClient } from "@crucible/editor-client";
import { ApprovalCoordinator, isApprovalMode, migrateApproval, modeFor, policyFor, type ApprovalMode, type FirstSendChoice } from "../src/approval-mode";
const state = (patch: Partial<ApprovalSnapshot> = {}): ApprovalSnapshot => ({ supported: true, autoAccept: false, shellPolicy: "ask", version: 0, owned: false, processId: "p", ...patch });
function setup(options: { saved?: ApprovalMode; review?: boolean; owned?: boolean; older?: boolean; legacy?: boolean;
  trusted?: boolean; firstSend?: FirstSendChoice; shellPolicy?: "ask" | "allow_all" | "auto"; policies?: string[] } = {}) {
  let snapshot = state({ owned: options.owned ?? false,
    ...(options.shellPolicy ? { shellPolicy: options.shellPolicy, autoAccept: options.shellPolicy !== "ask" } : {}),
    ...(options.policies ? { shellPolicies: options.policies } : {}) });
  let trusted = options.trusted ?? true;
  let acknowledged = false;
  const confirmFirstFullAccessSend = vi.fn(async () => options.firstSend ?? "continue");
  let saved = options.saved;
  let warning = false;
  const getConfig = vi.fn(async () => ({ approval: options.older || options.legacy ? undefined : snapshot,
    shellPolicy: snapshot.shellPolicy, chatControllerEnabled: !options.legacy }));
  const setApprovalMode = vi.fn(async (req) => {
    snapshot = { ...snapshot, autoAccept: req.autoAccept, shellPolicy: req.shellPolicy, owned: true, version: snapshot.version + 1 };
    return { ...snapshot, autoResolvedEdits: 0, background: 0 };
  });
  const setGlobalReviewPref = vi.fn(async () => ({ autoResolved: 0, background: 0 }));
  const saveMode = vi.fn(async (mode: ApprovalMode) => { saved = mode; });
  const render = vi.fn();
  const c = new ApprovalCoordinator({ client: () => ({ getConfig, setApprovalMode, setGlobalReviewPref } as unknown as BackendTaskClient),
    savedMode: () => saved, reviewOn: () => options.review ?? true, saveMode, render,
    warningDismissed: () => warning, saveWarningDismissed: async () => { warning = true; },
    isTrusted: () => trusted, firstSendAcknowledged: () => acknowledged,
    acknowledgeFirstSend: async () => { acknowledged = true; }, confirmFirstFullAccessSend });
  return { c, getConfig, setApprovalMode, setGlobalReviewPref, saveMode, render, confirmFirstFullAccessSend,
    setState: (s: ApprovalSnapshot) => { snapshot = s; }, trust: () => { trusted = true; }, acknowledged: () => acknowledged };
}
describe("approval migration", () => {
  it.each([["ask", true, "ask"], ["ask", false, "accept_edits"], ["allow_all", true, "ask"], ["allow_all", false, "full_access"]] as const)("%s/%s → %s", (shell, review, mode) => expect(migrateApproval(shell, review)).toBe(mode));
});
describe("approval coordinator", () => {
  it("reads config, initializes with no resolution, and persists only after acknowledgement", async () => {
    const t = setup({ saved: "accept_edits" });
    await t.c.connect();
    expect(t.setApprovalMode).toHaveBeenCalledWith(expect.objectContaining({ autoAccept: true, resolveWaiting: false, expectedProcessId: "p", expectedVersion: 0 }));
    expect(t.setGlobalReviewPref).not.toHaveBeenCalled();
    expect(t.saveMode).toHaveBeenCalledAfter(t.setApprovalMode);
    expect(t.c.current()).toMatchObject({ mode: "accept_edits", confirmed: true, pending: false });
  });
  it("adopts an owned backend without writing on reconnect", async () => {
    const t = setup({ saved: "full_access", owned: true });
    await t.c.connect(); await t.c.connect();
    expect(t.setApprovalMode).not.toHaveBeenCalled();
    expect(t.c.current().mode).toBe("ask");
  });
  it("serializes selection writes and uses the preceding acknowledgement's version", async () => {
    const t = setup(); await t.c.connect();
    await Promise.all([t.c.select("accept_edits"), t.c.select("full_access", true, true)]);
    expect(t.setApprovalMode.mock.calls.map(([r]) => r.expectedVersion)).toEqual([0, 1, 2]);
    expect(t.setApprovalMode.mock.calls.slice(1).every(([r]) => r.resolveWaiting)).toBe(true);
    expect(t.c.current()).toMatchObject({ mode: "full_access", warningDismissed: true });
  });
  it("adopts a version conflict without persisting a failed selection", async () => {
    const t = setup({ saved: "ask", owned: true }); await t.c.connect();
    t.setApprovalMode.mockRejectedValueOnce({ status: 409, detail: { supported: true, auto_accept: true, shell_policy: "ask", version: 4, owned: true, process_id: "p" } });
    await t.c.select("full_access", true);
    expect(t.c.current()).toMatchObject({ mode: "accept_edits", notice: expect.stringContaining("elsewhere") });
    expect(t.saveMode).not.toHaveBeenCalled();
  });
  it("never replays a user selection against a restarted process", async () => {
    const t = setup({ saved: "ask", owned: true }); await t.c.connect();
    t.setState(state({ processId: "new" }));
    t.setApprovalMode.mockRejectedValueOnce({ status: 409, detail: { supported: true, auto_accept: false, shell_policy: "ask", version: 0, owned: false, process_id: "new" } });
    await t.c.select("full_access", true);
    const writes = t.setApprovalMode.mock.calls;
    expect(writes).toHaveLength(2);
    expect(writes[1][0]).toMatchObject({ expectedProcessId: "new", autoAccept: false, resolveWaiting: false });
    expect(t.c.current().mode).toBe("ask");
  });
  it("keeps confirmed state and shows a failed selection", async () => {
    const t = setup({ owned: true }); await t.c.connect();
    t.setApprovalMode.mockRejectedValueOnce(new Error("offline"));
    await t.c.select("accept_edits");
    expect(t.c.current()).toMatchObject({ mode: "ask", error: expect.stringContaining("Not applied") });
  });
  it("requires first-time Full access confirmation", async () => {
    const t = setup({ owned: true }); await t.c.connect();
    await t.c.select("full_access");
    expect(t.setApprovalMode).not.toHaveBeenCalled();
    await t.c.select("full_access", true, true);
    await t.c.select("full_access");
    expect(t.setApprovalMode).toHaveBeenCalledTimes(2);
  });
  it.each([true, false])("older controller reconnect with review=%s is safe", async review => {
    const t = setup({ older: true, review }); await t.c.connect();
    expect(t.setGlobalReviewPref).toHaveBeenCalledTimes(review ? 1 : 0);
    expect(t.c.current()).toMatchObject({ tier: "older", confirmed: review });
    await t.c.select("full_access", true);
    expect(t.setApprovalMode).not.toHaveBeenCalled();
  });
  it("legacy agent has no approval writes", async () => {
    const t = setup({ legacy: true }); await t.c.connect();
    expect(t.c.current().tier).toBe("legacy");
    expect(await t.c.ready()).toBeUndefined();
    expect(t.setGlobalReviewPref).not.toHaveBeenCalled();
  });
  it("readiness waits for initialization and times out without starting work", async () => {
    const t = setup(); let release!: () => void;
    t.getConfig.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ approval: state(), shellPolicy: "ask", chatControllerEnabled: true }); }));
    await expect(t.c.ready(10)).rejects.toThrow("not confirmed");
    release(); await t.c.connect();
    expect(await t.c.ready()).toBe("p");
  });
  it("recovery recognizes admission 409s separately from stale/busy conflicts", async () => {
    const t = setup({ owned: true }); await t.c.connect();
    expect(await t.c.recover({ status: 409, detail: "busy" })).toBe(false);
    t.setState(state({ owned: true, processId: "new" }));
    expect(await t.c.recover({ status: 409, detail: { code: "approval_not_confirmed" } })).toBe(true);
    expect(await t.c.ready()).toBe("new");
  });
});

it("does not trust confirmation from a different backend URL", async () => {
  const t = setup({ owned: true });
  let endpoint = "first";
  (t.c as unknown as { deps: { endpoint(): string } }).deps.endpoint = () => endpoint;
  await t.c.connect(); endpoint = "second";
  t.setState(state({ processId: "second-process", owned: true }));
  expect(await t.c.ready()).toBe("second-process");
  expect(t.getConfig).toHaveBeenCalledTimes(2);
});
it("keeps acknowledged authority when persistence fails", async () => {
  const t = setup({ owned: true }); await t.c.connect();
  t.saveMode.mockRejectedValueOnce(new Error("Disk unavailable"));
  await t.c.select("accept_edits");
  expect(t.c.current()).toMatchObject({ mode: "accept_edits", confirmed: true, error: expect.stringContaining("Applied, but") });
});
it("reconnecting after another window changes mode adopts its decision", async () => {
  const t = setup({ owned: true }); await t.c.connect(); await t.c.select("full_access", true);
  t.setState(state({ owned: true, autoAccept: false, shellPolicy: "ask", version: 3 }));
  await t.c.connect(); expect(t.c.current().mode).toBe("ask");
  expect(t.setApprovalMode).toHaveBeenCalledTimes(1);
  t.c.observe(state({ owned: true, autoAccept: true, shellPolicy: "ask", version: 4 }));
  expect(t.c.current().mode).toBe("accept_edits");
  t.c.observe(state({ owned: true, autoAccept: false, shellPolicy: "ask", version: 2 }));
  expect(t.c.current().mode).toBe("accept_edits");
});

it("drops the supported chip when a restarted backend has an older capability", async () => {
  const t = setup({ owned: true, saved: "full_access" }); await t.c.connect();
  t.getConfig.mockResolvedValueOnce({ approval: undefined, shellPolicy: "ask", chatControllerEnabled: true } as never);
  t.c.observe(undefined, "older-process");
  expect(t.c.current()).toMatchObject({ tier: "unknown", confirmed: false });
  await t.c.connect();
  expect(t.c.current()).toMatchObject({ tier: "older", mode: "accept_edits", confirmed: false });
  expect(t.setGlobalReviewPref).not.toHaveBeenCalled();
});

describe("auto mode", () => {
  const withAuto = ["ask", "allow_all", "auto"];
  it("maps auto both ways", () => {
    expect(policyFor("auto")).toEqual({ autoAccept: true, shellPolicy: "auto" });
    expect(modeFor(state({ autoAccept: true, shellPolicy: "auto" }))).toBe("auto");
    expect(modeFor(state({ autoAccept: false, shellPolicy: "auto" }))).toBeNull();
    expect(isApprovalMode("auto")).toBe(true);
  });
  it("applies a saved auto on a backend that offers it", async () => {
    const t = setup({ saved: "auto", policies: withAuto });
    await t.c.connect();
    expect(t.setApprovalMode).toHaveBeenCalledWith(expect.objectContaining({ autoAccept: true, shellPolicy: "auto" }));
    expect(t.c.current().mode).toBe("auto");
  });
  it("falls back to accept_edits for auto in an untrusted workspace and keeps the saved mode", async () => {
    const t = setup({ saved: "auto", policies: withAuto, trusted: false });
    await t.c.connect();
    expect(t.setApprovalMode).toHaveBeenCalledWith(expect.objectContaining({ shellPolicy: "ask", autoAccept: true }));
    expect(t.saveMode).not.toHaveBeenCalled();
    expect(t.c.current().mode).toBe("accept_edits");
  });
  it("falls back when the backend does not offer auto", async () => {
    const t = setup({ saved: "auto" });
    await t.c.connect();
    expect(t.setApprovalMode).toHaveBeenCalledWith(expect.objectContaining({ shellPolicy: "ask" }));
    expect(t.c.current().mode).toBe("accept_edits");
  });
  it("refuses an auto selection in an untrusted workspace", async () => {
    const t = setup({ policies: withAuto, trusted: false });
    await t.c.connect();
    t.setApprovalMode.mockClear();
    await t.c.select("auto");
    expect(t.setApprovalMode).not.toHaveBeenCalled();
    expect(t.c.current().error).toContain("Auto needs a trusted workspace");
  });
  it("needs no first-send confirmation", async () => {
    const t = setup({ saved: "auto", policies: withAuto });
    await t.c.connect();
    expect(await t.c.confirmFirstSend()).toBe(true);
    expect(t.confirmFirstFullAccessSend).not.toHaveBeenCalled();
  });
});
describe("approval view publishing", () => {
  it("does not re-render an unchanged view on every /live poll", async () => {
    const t = setup({ owned: true }); await t.c.connect();
    const renders = t.render.mock.calls.length;
    t.c.observe(state({ owned: true }), "p");
    t.c.observe(state({ owned: true }), "p");
    expect(t.render).toHaveBeenCalledTimes(renders);
    t.c.observe(state({ owned: true, autoAccept: true, version: 1 }), "p");
    expect(t.render).toHaveBeenCalledTimes(renders + 1);
  });
});

describe("workspace trust", () => {
  it("applies Accept edits instead of a saved Full access and keeps the preference", async () => {
    const t = setup({ saved: "full_access", trusted: false });
    await t.c.connect();
    expect(t.setApprovalMode).toHaveBeenCalledWith(expect.objectContaining({ autoAccept: true, shellPolicy: "ask", resolveWaiting: false }));
    expect(t.saveMode).not.toHaveBeenCalled();
    expect(t.c.current()).toMatchObject({ mode: "accept_edits", trusted: false, notice: null });
  });
  it("downgrades a backend already running with Full access", async () => {
    const t = setup({ owned: true, shellPolicy: "allow_all", trusted: false });
    await t.c.connect();
    expect(t.setApprovalMode).toHaveBeenCalledWith(expect.objectContaining({ shellPolicy: "ask", resolveWaiting: false }));
    expect(t.c.current().mode).toBe("accept_edits");
  });
  it("refuses a Full access selection", async () => {
    const t = setup({ owned: true, trusted: false }); await t.c.connect();
    t.setApprovalMode.mockClear();
    await t.c.select("full_access", true);
    expect(t.setApprovalMode).not.toHaveBeenCalled();
    expect(t.c.current().error).toContain("trusted workspace");
  });
  it("re-applies the saved Full access once trust is granted", async () => {
    const t = setup({ saved: "full_access", trusted: false }); await t.c.connect();
    t.trust(); await t.c.trustGranted();
    expect(t.c.current()).toMatchObject({ mode: "full_access", trusted: true });
    expect(t.acknowledged()).toBe(false);
  });
});

describe("first send under Full access", () => {
  async function fullAccess(firstSend: FirstSendChoice) {
    const t = setup({ saved: "full_access", firstSend }); await t.c.connect();
    expect(t.c.current().mode).toBe("full_access");
    return t;
  }
  it("asks once per workspace and remembers Continue", async () => {
    const t = await fullAccess("continue");
    expect(await t.c.confirmFirstSend()).toBe(true);
    expect(await t.c.confirmFirstSend()).toBe(true);
    expect(t.confirmFirstFullAccessSend).toHaveBeenCalledTimes(1);
  });
  it("does not send on Cancel and asks again next time", async () => {
    const t = await fullAccess("cancel");
    expect(await t.c.confirmFirstSend()).toBe(false);
    expect(t.acknowledged()).toBe(false);
  });
  it("switches to Accept edits and sends", async () => {
    const t = await fullAccess("accept_edits");
    expect(await t.c.confirmFirstSend()).toBe(true);
    expect(t.c.current().mode).toBe("accept_edits");
    expect(t.setApprovalMode).toHaveBeenLastCalledWith(expect.objectContaining({ shellPolicy: "ask", autoAccept: true }));
  });
  it("never asks outside Full access or after the user chose it here", async () => {
    const t = setup({ owned: true }); await t.c.connect();
    expect(await t.c.confirmFirstSend()).toBe(true);
    await t.c.select("full_access", true); await t.c.acknowledgeUserSelection();
    expect(await t.c.confirmFirstSend()).toBe(true);
    expect(t.confirmFirstFullAccessSend).not.toHaveBeenCalled();
  });
});
