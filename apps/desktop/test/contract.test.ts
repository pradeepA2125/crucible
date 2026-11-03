import { readFileSync } from "node:fs";
import { expect, it } from "vitest";
import { parsePayload } from "../src/contract.js";
import { refusalMessages } from "../src/refusal.js";

const isKnownCommand = (type: string) => {
  try { parsePayload({ type }); return true; }
  catch (error) { return !String(error).includes("Unsupported desktop command"); }
};

it("admits every approval mode the shared menu can pick", () => {
  for (const mode of ["ask", "accept_edits", "auto", "full_access"]) {
    expect(parsePayload({ type: "setApprovalMode", mode })).toMatchObject({ mode });
  }
  expect(() => parsePayload({ type: "setApprovalMode", mode: "yolo" })).toThrow();
});

it("admits the sandbox settings with their exact fields", () => {
  expect(parsePayload({ type: "settings/sandbox" })).toEqual({ type: "settings/sandbox" });
  expect(parsePayload({ type: "settings/setSandboxEnabled", enabled: false })).toMatchObject({ enabled: false });
  expect(parsePayload({ type: "settings/setSandboxHosts", hosts: ["example.com"] })).toMatchObject({ hosts: ["example.com"] });
  expect(parsePayload({ type: "settings/setSandboxFolders", folders: ["/tmp/x"] })).toMatchObject({ folders: ["/tmp/x"] });
  expect(() => parsePayload({ type: "settings/setSandboxEnabled", enabled: "no" })).toThrow();
  expect(() => parsePayload({ type: "settings/setSandboxHosts", hosts: "example.com" })).toThrow();
  expect(() => parsePayload({ type: "settings/setSandboxFolders", folders: ["/tmp/x"], extra: true })).toThrow();
});

// The contract matches on runtime strings, so a message added to the shared Settings
// handler type-checks and passes its own tests while the desktop app refuses it.
it("admits every message the shared Settings handler accepts", () => {
  const source = readFileSync(new URL("../../vscode-extension/src/settings-data.ts", import.meta.url), "utf8");
  const inbound = source.slice(source.indexOf("export type SettingsInMsg"), source.indexOf("export type SettingsOutMsg"));
  const types = [...inbound.matchAll(/type: "(settings\/[^"]+)"/g)].map(match => match[1]!);
  expect(types.length).toBeGreaterThan(20);
  expect(types.filter(type => !isKnownCommand(type))).toEqual([]);
});

it("validates the exact card boundary and returns only gate-specific refusal", () => {
  const action = { type: "gateDecision", threadId: "a", gateId: "g", kind: "feedback", value: "refine", draft: { revision: 1, version: 0 } };
  expect(parsePayload(action)).toEqual(action);
  expect(() => parsePayload({ ...action, gateId: "" })).toThrow();
  expect(() => parsePayload({ ...action, text: "composer" })).toThrow();
  const replies = refusalMessages(action, "stale view");
  expect(replies).toContainEqual(expect.objectContaining({ type: "decisionRetry", gateId: "g", kind: "mode" }));
  expect(replies.some(m => ["setInputEnabled", "draftRecovery", "composerPrefill"].includes(String(m.type)))).toBe(false);
});

it("allows only bounded local thread preferences through the desktop envelope", () => {
  expect(parsePayload({ type: "desktop/threadsUpdate", requestId: "r", threadId: "a", action: "archive", value: true })).toMatchObject({ action: "archive" });
  expect(() => parsePayload({ type: "desktop/threadsUpdate", requestId: "r", threadId: "a", action: "delete" })).toThrow();
  expect(() => parsePayload({ type: "desktop/threadsUpdate", requestId: "r", threadId: "a", action: "name", value: "x".repeat(201) })).toThrow();
});

it("validates exact background-attention destinations and explicit confirmation boundaries",()=>{
 const projectId="a".repeat(64);expect(parsePayload({type:"desktop/selectProject",projectId,threadId:"B2"})).toMatchObject({threadId:"B2"});expect(()=>parsePayload({type:"desktop/selectProject",projectId,threadId:""})).toThrow();expect(()=>parsePayload({type:"desktop/removeProject",projectId})).toThrow();expect(()=>parsePayload({type:"desktop/recoverDraft",confirmed:false})).toThrow();
});
