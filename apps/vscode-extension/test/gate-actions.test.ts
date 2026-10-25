import { expect, it, vi } from "vitest";
import { createGateActions, gateDraftKey } from "../src/gate-actions.js";
import { DraftSession } from "../src/drafts/draft-session.js";
import type { CrucibleController } from "../src/controller.js";
const action = { type: "gateDecision" as const, threadId: "a", gateId: "g", kind: "mode" as const, value: "implement" };
it("lets a later user Plan Mode choice win over a delayed gate acceptance", async () => {
  let accept!: (isCurrent: () => boolean) => Promise<void>;
  const controller = { handleExactGateDecision: vi.fn(async (_a, callback) => { accept = callback; }) } as unknown as CrucibleController;
  const drafts = new DraftSession({ post: () => {}, admission: () => {}, identity: () => "v" });
  const persist = vi.fn(async () => {}), post = vi.fn();
  const host = createGateActions({ controller, drafts, setPlanMode: persist, post });
  await host.submit(action); await host.setPlanMode(true); await accept(() => true);
  expect(persist).toHaveBeenCalledExactlyOnceWith(true); expect(post).not.toHaveBeenCalledWith({ type: "planModeState", enabled: false });
});
it("does not persist a detached A receipt into B's composer", async () => {
  let accept!: (isCurrent: () => boolean) => Promise<void>;
  const controller = { handleExactGateDecision: vi.fn(async (_a, callback) => { accept = callback; }) } as unknown as CrucibleController;
  const drafts = new DraftSession({ post: () => {}, admission: () => {}, identity: () => "v" });
  const persist = vi.fn(async () => {}); const host = createGateActions({ controller, drafts, setPlanMode: persist, post: () => {} });
  await host.submit(action); await accept(() => false); expect(persist).not.toHaveBeenCalled();
});
it("uses distinct feedback identities across thread, gate and kind", () => {
  expect(new Set([gateDraftKey("a", "g", "mode"), gateDraftKey("a", "g", "clarify"), gateDraftKey("b", "g", "mode"), gateDraftKey("a", "next", "mode")]).size).toBe(4);
});


it("rechecks attachment when accepted Plan Mode work leaves the preference queue", async () => {
  let accept!: (current: () => boolean) => Promise<void>, release!: () => void, current = true;
  const controller = { handleExactGateDecision: vi.fn(async (_a, ack) => { accept = ack; }) } as unknown as CrucibleController;
  const drafts = new DraftSession({ post: () => {}, admission: () => {}, identity: () => "v" });
  const persist = vi.fn(async (value: boolean) => { if (value) await new Promise<void>(r => { release = r; }); });
  const post = vi.fn(), host = createGateActions({ controller, drafts, setPlanMode: persist, post });
  const prior = host.setPlanMode(true); await Promise.resolve(); await Promise.resolve();
  await host.submit(action); const accepted = accept(() => current); await Promise.resolve();
  current = false; release(); await prior; await accepted;
  expect(persist).toHaveBeenCalledExactlyOnceWith(true);
  expect(post).not.toHaveBeenCalledWith({ type: "planModeState", enabled: false });
});

it("provides a live guard through persistence and suppresses publication after mid-save detachment", async () => {
  let accept!: (current: () => boolean) => Promise<void>, release!: () => void, current = true;
  const controller = { handleExactGateDecision: vi.fn(async (_a, ack) => { accept = ack; }) } as unknown as CrucibleController;
  const drafts = new DraftSession({ post: () => {}, admission: () => {}, identity: () => "v" });
  const backend = vi.fn(), post = vi.fn();
  const persist = vi.fn(async (_value: boolean, isCurrent?: () => boolean) => {
    await new Promise<void>(r => { release = r; }); if (isCurrent?.()) backend();
  });
  const host = createGateActions({ controller, drafts, setPlanMode: persist, post }); await host.submit(action);
  const accepted = accept(() => current); await Promise.resolve(); await Promise.resolve();
  current = false; release(); await accepted;
  expect(backend).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled();
});

it.each(["extension", "desktop"])("fences the actual %s Plan Mode adapter across its runtime await", async host => {
  const { readFileSync } = await import("node:fs"), ts = await import("typescript");
  const source = readFileSync(host === "extension" ? "src/extension.ts" : "../desktop/src/host.ts", "utf8");
  const ast = ts.createSourceFile("adapter.ts", source, ts.ScriptTarget.Latest, true);
  const runtimeName = host === "extension" ? "runtimeManager" : "runtime";
  let callback = "";
  const visit = (node: import("typescript").Node) => {
    if (ts.isArrowFunction(node) && node.body.getText(ast).includes(`await ${runtimeName}.setPlanMode(enabled)`)
        && node.parameters[0]?.name.getText(ast) === "enabled") callback = node.getText(ast);
    ts.forEachChild(node, visit);
  };
  visit(ast); expect(callback).not.toBe("");
  const execute = new Function("controller", runtimeName, ts.transpile(`return (${callback});`, { target: ts.ScriptTarget.ES2022 }));
  let release!: () => void, current = true;
  const originalWrite = vi.fn(async () => {}), newerWrite = vi.fn(async () => {});
  let selected = { setPlanMode: originalWrite };
  const configClient = vi.fn(() => selected), runtime = { setPlanMode: vi.fn(async () => new Promise<void>(r => { release = r; })) };
  const adapter = execute({ configClient }, runtime);
  const pending = adapter(false, () => current);
  expect(configClient).toHaveBeenCalledOnce();
  selected = { setPlanMode: newerWrite }; current = false; release(); await pending;
  expect(configClient).toHaveBeenCalledOnce(); expect(originalWrite).not.toHaveBeenCalled(); expect(newerWrite).not.toHaveBeenCalled();
});
