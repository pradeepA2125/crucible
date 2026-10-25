import { expect, it } from "vitest";
import { parsePayload } from "../src/contract.js";
import { refusalMessages } from "../src/refusal.js";
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
