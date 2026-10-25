import { afterEach, expect, it, vi } from "vitest";
import { createWorkspaceGitRequests } from "../src/workspace-git-requests.js";
import { GitReadStopped, type GitScope, type GitDiff } from "../src/workspace-git.js";
const generation = "12345678-1234-4234-8234-123456789abc";
const scope: GitScope = { state: "ready", generation, changes: [], uncompared: [], partial: false, rawBytes: true, warnings: ["Raw bytes"] };
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { promise, resolve }; };
const setup = () => {
  const identity = { viewId: "view", projectId: "project" as string | null }, post = vi.fn();
  const reader = { scope: vi.fn(async (_options?: { signal?: AbortSignal; deadline?: number }): Promise<GitScope> => scope), diff: vi.fn(async (_generation: string, _path: string, _options?: { signal?: AbortSignal; deadline?: number }): Promise<GitDiff> => ({ kind: "binary" })) };
  return { identity, post, reader, broker: createWorkspaceGitRequests(reader, () => identity, post) };
};
afterEach(() => vi.useRealTimers());
it("is lazy, bounds each operation independently and lets diff run during scope", async () => {
  const { broker, reader, post } = setup(), pending = deferred<GitScope>();
  expect(reader.scope).not.toHaveBeenCalled(); expect(reader.diff).not.toHaveBeenCalled();
  reader.scope.mockImplementationOnce(() => pending.promise);
  const scan = broker.handle({ type: "desktop/gitScope", requestId: "s" }); await Promise.resolve();
  await broker.handle({ type: "desktop/gitScope", requestId: "busy" });
  await broker.handle({ type: "desktop/gitDiff", requestId: "d", generation, path: "a.ts" });
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ requestId: "busy", outcome: expect.objectContaining({ status: "busy" }) }));
  expect(reader.scope).toHaveBeenCalledTimes(1); expect(reader.diff).toHaveBeenCalledWith(generation, "a.ts", expect.objectContaining({ signal: expect.any(AbortSignal) }));
  pending.resolve(scope); await scan; expect(post).toHaveBeenCalledTimes(3);
});
it("enforces host deadlines on nonsettling readers and frees the slot without late duplicates", async () => {
  vi.useFakeTimers(); const { broker, reader, post } = setup(), pending = deferred<GitScope>();
  reader.scope.mockImplementationOnce(() => pending.promise);
  const scan = broker.handle({ type: "desktop/gitScope", requestId: "s" }); await Promise.resolve();
  const options = reader.scope.mock.calls[0]![0]!;
  expect(options.deadline).toBe(Date.now() + 30000);
  await vi.advanceTimersByTimeAsync(30001); await scan;
  expect(options.signal?.aborted).toBe(true); expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ outcome: expect.objectContaining({ status: "timeout" }) }));
  await broker.handle({ type: "desktop/gitScope", requestId: "next" });
  pending.resolve(scope); await vi.advanceTimersByTimeAsync(0); expect(post).toHaveBeenCalledTimes(2);
});
it("suppresses stale-view results, cancels project-mismatch results and aborts on dispose", async () => {
  const { broker, reader, identity, post } = setup(), first = deferred<GitScope>(), second = deferred<GitScope>();
  reader.scope.mockImplementationOnce(() => first.promise);
  const scan = broker.handle({ type: "desktop/gitScope", requestId: "old" }); await Promise.resolve();
  identity.viewId = "new"; first.resolve(scope); await scan; expect(post).not.toHaveBeenCalled();
  reader.scope.mockImplementationOnce(() => second.promise);
  const changed = broker.handle({ type: "desktop/gitScope", requestId: "project" }); await Promise.resolve();
  identity.projectId = "different"; second.resolve(scope); await changed;
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ outcome: expect.objectContaining({ status: "cancelled" }) }));
  reader.scope.mockImplementationOnce(() => new Promise(() => {}));
  const stopped = broker.handle({ type: "desktop/gitScope", requestId: "dispose" }); await Promise.resolve();
  const signal = reader.scope.mock.calls[2]![0]!.signal; broker.dispose(); await stopped;
  expect(signal?.aborted).toBe(true); expect(post).toHaveBeenCalledTimes(1);
});
it("preserves typed busy, rejects malformed reader results and returns one safe error", async () => {
  const { broker, reader, post } = setup(); reader.scope.mockRejectedValueOnce(new GitReadStopped("busy"));
  await broker.handle({ type: "desktop/gitScope", requestId: "busy" });
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ outcome: expect.objectContaining({ status: "busy", message: expect.stringMatching(/earlier/i) }) }));
  reader.scope.mockResolvedValueOnce({ kind: "binary" } as unknown as GitScope);
  await broker.handle({ type: "desktop/gitScope", requestId: "invalid" });
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ outcome: expect.objectContaining({ status: "error" }) }));
  expect(post).toHaveBeenCalledTimes(2);
});
it("cancels navigation in-flight reads and allows the next view to inspect", async () => {
  const { broker, reader, post, identity } = setup(), pending = deferred<GitScope>();
  reader.scope.mockImplementationOnce(() => pending.promise);
  const scan = broker.handle({ type: "desktop/gitScope", requestId: "old" }); await Promise.resolve();
  broker.cancel(); identity.viewId = "new"; await scan;
  await broker.handle({ type: "desktop/gitScope", requestId: "new" });
  pending.resolve(scope); await Promise.resolve(); expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ requestId: "new", outcome: expect.objectContaining({ status: "ok" }) }));
});
it("bounds the diff slot at 15 seconds independently of the 30-second scope slot", async () => {
  vi.useFakeTimers(); const { broker, reader, post } = setup(), pendingScope = deferred<GitScope>(), pendingDiff = deferred<GitDiff>();
  reader.scope.mockImplementationOnce(() => pendingScope.promise); reader.diff.mockImplementationOnce(() => pendingDiff.promise);
  const scan = broker.handle({ type: "desktop/gitScope", requestId: "scan" });
  const diff = broker.handle({ type: "desktop/gitDiff", requestId: "diff", generation, path: "a.ts" }); await Promise.resolve();
  await broker.handle({ type: "desktop/gitDiff", requestId: "busy", generation, path: "b.ts" });
  expect(reader.diff).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(15001); await diff;
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ requestId: "diff", outcome: expect.objectContaining({ status: "timeout" }) }));
  expect(post.mock.calls.some(([reply]) => reply.requestId === "scan")).toBe(false);
  await broker.handle({ type: "desktop/gitDiff", requestId: "next", generation, path: "b.ts" });
  await vi.advanceTimersByTimeAsync(15000); await scan;
  expect(post).toHaveBeenCalledWith(expect.objectContaining({ requestId: "scan", outcome: expect.objectContaining({ status: "timeout" }) }));
  pendingScope.resolve(scope); pendingDiff.resolve({ kind: "binary" }); await vi.advanceTimersByTimeAsync(0);
  expect(post).toHaveBeenCalledTimes(4);
});

it("returns one typed oversize acknowledgement, releases the slot and permits explicit retry", async () => {
  const { broker, reader, post } = setup();
  const path = ("folder".repeat(16) + "/").repeat(6);
  reader.scope.mockResolvedValueOnce({ ...scope, changes: Array.from({ length: 40000 }, (_, i) => ({ path: path + i, status: "M" })) });
  await broker.handle({ type: "desktop/gitScope", requestId: "large" });
  expect(post).toHaveBeenCalledTimes(1); expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ outcome: { status: "too-large", message: expect.stringMatching(/16 MiB.*complete file list/) } }));
  await broker.handle({ type: "desktop/gitScope", requestId: "retry" }); expect(post).toHaveBeenCalledTimes(2);
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ requestId: "retry", outcome: expect.objectContaining({ status: "ok" }) }));
  reader.diff.mockResolvedValueOnce({ kind: "text", unified: "\u0001".repeat(3 * 1024 * 1024), additions: 1, deletions: 0, truncated: false });
  await broker.handle({ type: "desktop/gitDiff", requestId: "diff", generation, path: "a.ts" });
  expect(post).toHaveBeenLastCalledWith(expect.objectContaining({ outcome: { status: "too-large", message: expect.stringMatching(/16 MiB.*file could not be compared/) } }));
});
