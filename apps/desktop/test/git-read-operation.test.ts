import { afterEach, expect, it, vi } from "vitest";
import { createGitReadOperations, type GitRunOptions } from "../src/git-read-operation.js";
import { isWorkspacePath } from "../src/workspace-path.js";
import { parsePayload } from "../src/contract.js";
afterEach(() => vi.useRealTimers());
it("bounds a nonsettling runner, signals cancellation, and accepts a later operation", async () => {
  vi.useFakeTimers(); const perform = createGitReadOperations(); let options: GitRunOptions | undefined;
  const hung = perform(30, {}, op => op.run(["--version"], 10), async (_args, _max, value) => { options = value; return new Promise<Buffer>(() => {}); });
  const rejected = expect(hung).rejects.toMatchObject({ reason: "timeout" });
  await vi.advanceTimersByTimeAsync(31); await rejected;
  expect(options?.signal?.aborted).toBe(true); expect(options?.timeout).toBeLessThanOrEqual(30);
  await expect(perform(30, {}, op => op.run([], 10), async () => Buffer.from("next"))).resolves.toEqual(Buffer.from("next"));
});
it("aborts a filesystem wait without accepting a late result and caps unsettled waits across scans", async () => {
  vi.useFakeTimers(); const perform = createGitReadOperations(), late: Array<() => void> = [];
  const run = async () => Buffer.alloc(0); let advanced = 0;
  for (let i = 0; i < 4; i++) {
    const result = perform(10, {}, async op => { await op.file(() => new Promise<void>(resolve => late.push(resolve))); advanced++; }, run);
    const rejected = expect(result).rejects.toMatchObject({ reason: "timeout" });
    await vi.advanceTimersByTimeAsync(11); await rejected;
  }
  const reopened = createGitReadOperations();
  await expect(reopened(10, {}, op => op.file(async () => 1), run)).rejects.toMatchObject({ reason: "busy", message: expect.stringMatching(/this or another project.*still finishing/) });
  late.forEach(resolve => resolve()); await vi.advanceTimersByTimeAsync(0); expect(advanced).toBe(0);
  await expect(perform(10, {}, op => op.file(async () => 2), run)).resolves.toBe(2);
});
it("propagates caller cancellation and never starts an already-aborted operation", async () => {
  const perform = createGitReadOperations(), controller = new AbortController(); let signal: AbortSignal | undefined;
  let started!: () => void; const ready = new Promise<void>(resolve => { started = resolve; });
  const run = vi.fn(async (_args: string[], _max: number, options?: GitRunOptions) => { signal = options?.signal; started(); return new Promise<Buffer>(() => {}); });
  const result = perform(1000, { signal: controller.signal }, op => op.run([], 1), run);
  const rejected = expect(result).rejects.toMatchObject({ reason: "cancelled" }); await ready; controller.abort(); await rejected;
  expect(signal?.aborted).toBe(true);
  await expect(perform(1000, { signal: controller.signal }, op => op.run([], 1), run)).rejects.toMatchObject({ reason: "cancelled" });
  expect(run).toHaveBeenCalledTimes(1);
});
it("shares strict browser-safe path authority with IPC, including unsupported Git names", () => {
  for (const path of ["a:b.txt", "a\\b", "bad\ufffdname", "a\nb", "/a", "a/../b", "a//b"]) {
    expect(isWorkspacePath(path, false)).toBe(false);
    expect(() => parsePayload({ type: "desktop/filesPreview", requestId: "r", path })).toThrow();
  }
  expect(isWorkspacePath("src/a.ts", false)).toBe(true); expect(isWorkspacePath("", false)).toBe(false);
  expect(isWorkspacePath("")).toBe(true);
});
