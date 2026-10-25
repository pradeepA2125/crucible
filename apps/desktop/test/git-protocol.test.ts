import { expect, it } from "vitest";
import { refusalMessages } from "../src/refusal.js";
import { parsePayload } from "../src/contract.js";
import { GIT_UNCOMPARED_LIMIT, GitReplyTooLarge, gitDiffResult, gitScopeResult, parseGitReply } from "../src/git-protocol.js";
import { GIT_LIMITS } from "../src/workspace-git.js";
const generation = "12345678-1234-4234-8234-123456789abc";
const ready = { state: "ready", generation, changes: [{ path: "b.ts", status: "R", from: "a.ts", mode: { from: "100664", to: "100755" } }],
  uncompared: [{ path: "unsupported:name", reason: "unsupported" }], partial: true, rawBytes: true, warnings: ["Raw bytes"] };
const text = { kind: "text", unified: "@@ -1 +1 @@\n-before\n+after", additions: 1, deletions: 1, truncated: false };
it("admits only generation/path request authority", () => {
  expect(parsePayload({ type: "desktop/gitScope", requestId: "r" })).toEqual({ type: "desktop/gitScope", requestId: "r" });
  const request = { type: "desktop/gitDiff", requestId: "r", generation, path: "src/a.ts" };
  expect(parsePayload(request)).toEqual(request);
  for (const key of ["root", "ref", "head", "status", "from"]) expect(() => parsePayload({ ...request, [key]: "injected" })).toThrow();
  for (const path of ["", "../secret", "/secret", "a:b", "bad\ufffdname"]) expect(() => parsePayload({ ...request, path })).toThrow();
  expect(() => parsePayload({ ...request, generation: "HEAD" })).toThrow();
});
it("preserves raw-byte, partial, rename, legacy mode and type facts", () => {
  expect(gitScopeResult.parse(ready)).toEqual(ready);
  expect(gitScopeResult.parse({ ...ready, head: "a".repeat(64) })).toMatchObject({ head: "a".repeat(64) });
  const diff = { ...text, type: { from: "file", to: "link" }, mode: { from: "100644", to: "100755" } };
  expect(gitDiffResult.parse(diff)).toEqual(diff);
});
it("rejects wrong-operation and unknown-field replies without confusing transport with Git states", () => {
  const reply = { projectId: "project", type: "desktop/gitResult", requestId: "r", operation: "scope", outcome: { status: "ok", result: ready } };
  expect(parseGitReply(reply, "scope")).toEqual(reply);
  expect(() => parseGitReply(reply, "diff")).toThrow();
  expect(() => parseGitReply({ ...reply, outcome: { status: "ok", result: text } }, "scope")).toThrow();
  expect(() => parseGitReply({ ...reply, untrusted: true }, "scope")).toThrow();
  expect(parseGitReply({ ...reply, outcome: { status: "busy", message: "Earlier reads are finishing" } }, "scope")).toMatchObject({ outcome: { status: "busy" } });
  expect(() => gitScopeResult.parse({ state: "busy" })).toThrow();
});
it("rejects contradictory rename/type facts and malformed or over-bound result data", () => {
  for (const change of [{ path: "b", status: "R" }, { path: "b", status: "M", from: "a" },
    { path: "b", status: "X" }, { path: "b", status: "M", type: { from: "file", to: "file" } }]) {
    expect(() => gitScopeResult.parse({ ...ready, changes: [change] })).toThrow();
  }
  for (const data of [{ ...ready, rawBytes: false }, { ...ready, head: "main" }, { ...ready, warnings: Array(33).fill("x") },
    { ...ready, changes: Array(70001).fill(ready.changes[0]) }, { ...ready, uncompared: [{ path: "a", reason: "missing" }] }]) {
    expect(() => gitScopeResult.parse(data)).toThrow();
  }
  for (const data of [{ ...text, additions: -1 }, { ...text, truncated: "yes" }, { ...text, unified: "x\n".repeat(3000) },
    { ...text, unified: "€".repeat(1500000) }, { kind: "binary", unexpected: true }]) expect(() => gitDiffResult.parse(data)).toThrow();
});

it("returns Git-specific refusals without enabling input or appending a transcript message", () => {
  for (const type of ["desktop/gitScope", "desktop/gitDiff"]) {
    const replies = refusalMessages({ type, requestId: "r" }, "Stale context", "project");
    expect(replies).toHaveLength(1);
    expect(parseGitReply(replies[0], type === "desktop/gitScope" ? "scope" : "diff")).toMatchObject({ projectId: "project", outcome: { status: "error", message: "Stale context" } });
  }
  expect(refusalMessages({ type: "desktop/gitScope" }, "invalid")).toEqual([]);
});

it("admits the reader's combined unsupported and uncomparable tracked phases, with the next entry refused", () => {
  expect(GIT_UNCOMPARED_LIMIT).toBe(2 * GIT_LIMITS.tracked);
  const uncompared = Array.from({ length: 2 * GIT_LIMITS.tracked }, (_, i) => ({ path: `file-${i}`, reason: i < GIT_LIMITS.tracked ? "unsupported" : "unreadable" }));
  expect(parseGitReply({ type: "desktop/gitResult", projectId: "p", requestId: "r", operation: "scope", outcome: { status: "ok", result: { ...ready, uncompared } } }, "scope").outcome.status).toBe("ok");
  expect(() => gitScopeResult.parse({ ...ready, uncompared: [...uncompared, { path: "extra", reason: "unsupported" }] })).toThrow();
});
it("types whole serialized UTF8/JSON-escaped overflow without weakening the result schemas", () => {
  const reply = (result: unknown) => ({ type: "desktop/gitResult", projectId: "p", requestId: "r", operation: "diff", outcome: { status: "ok", result } });
  expect(parseGitReply(reply({ ...text, unified: "\u0001".repeat(1024 * 1024) }), "diff").outcome.status).toBe("ok");
  expect(() => parseGitReply(reply({ ...text, unified: "\u0001".repeat(3 * 1024 * 1024) }), "diff")).toThrow(GitReplyTooLarge);
  const path = ("日".repeat(80) + "/").repeat(12) + "a.ts";
  const value = { type: "desktop/gitResult", projectId: "p", requestId: "r", operation: "scope", outcome: { status: "ok", result: { ...ready, uncompared: Array(6000).fill({ path, reason: "unsupported" }) } } };
  expect(JSON.stringify(value).length).toBeLessThan(16 * 1024 * 1024);
  expect(() => parseGitReply(value, "scope")).toThrow(GitReplyTooLarge);
  expect(parseGitReply({ ...value, outcome: { status: "too-large", message: "This comparison is too large." } }, "scope").outcome.status).toBe("too-large");
});
