import { admittedFileRead, WorkspaceReadBusy } from "./workspace-read-budget.js";
export interface GitReadOptions { signal?: AbortSignal; deadline?: number; }
export class GitReadStopped extends Error {
  constructor(readonly reason: "timeout" | "cancelled" | "busy") { super(reason === "busy" ? "Earlier file reads from this or another project are still finishing. Retry when the filesystem responds." : reason === "timeout" ? "Git inspection timed out. Refresh to retry." : "Git inspection cancelled."); }
}
export interface GitRunOptions { signal?: AbortSignal; timeout?: number; }
type Runner = (args: string[], maxBuffer: number, options?: GitRunOptions) => Promise<Buffer>;

/** Logical cancellation stops a scan. Node cannot kill arbitrary kernel filesystem waits;
 * count their actual settlements across operations so repeated deadlines cannot pile them up. */
// Shared across project readers: reopening cannot accumulate more stalled filesystem calls.
export function createGitReadOperations() {
  return async <T>(milliseconds: number, options: GitReadOptions, task: (op: {
    check(): void; file<V>(work: () => Promise<V>): Promise<V>; run: Runner;
  }) => Promise<T>, runner: Runner): Promise<T> => {
    const controller = new AbortController(), deadline = Math.min(options.deadline ?? Infinity, Date.now() + milliseconds);
    let stopped: GitReadStopped | undefined;
    const stop = (reason: GitReadStopped["reason"]) => { stopped ??= new GitReadStopped(reason); controller.abort(); };
    const check = () => { if (!stopped && Date.now() >= deadline) stop("timeout"); if (stopped) throw stopped; };
    const cancelled = () => stop("cancelled");
    options.signal?.addEventListener("abort", cancelled, { once: true });
    if (options.signal?.aborted) cancelled();
    const timer = setTimeout(() => stop("timeout"), Math.max(0, deadline - Date.now()));
    const wait = <V>(work: () => Promise<V>): Promise<V> => new Promise((resolve, reject) => {
      const aborted = () => reject(stopped!);
      try { check(); } catch (error) { reject(error); return; }
      controller.signal.addEventListener("abort", aborted, { once: true });
      Promise.resolve().then(() => { check(); return work(); }).then(value => { check(); resolve(value); }).catch(reject)
        .finally(() => controller.signal.removeEventListener("abort", aborted));
    });
    const file = <V>(work: () => Promise<V>) => wait(async () => {
      try { return await admittedFileRead(work); }
      catch (error) { if (error instanceof WorkspaceReadBusy) { stop("busy"); check(); } throw error; }
    });
    const run: Runner = (args, maxBuffer) => wait(() => runner(args, maxBuffer, {
      signal: controller.signal, timeout: Math.max(1, Math.min(5000, deadline - Date.now())),
    }));
    try { check(); const result = await task({ check, file, run }); check(); return result; }
    finally { clearTimeout(timer); options.signal?.removeEventListener("abort", cancelled); }
  };
}
